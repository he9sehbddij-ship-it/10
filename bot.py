import os
import asyncio
import io
import fitz  # PyMuPDF
import img2pdf
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = "8666952088:AAF-9q5sGfeaD5djRz-ZVdn4g4V3a0YY2ko"

MAX_PHOTOS = 250
user_photos_store = {}
user_pdf_store = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ أرسل الصور وسيظهر لك زر التحويل في الأسفل فور اكتمال الوصول!")

async def finalize_photo_message(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    # ننتظر 0.8 ثانية حتى تكتمل جميع دفعات الصور في تليجرام (مثلاً الـ 74 صورة)
    await asyncio.sleep(0.8)

    if user_id not in user_photos_store:
        return

    data = user_photos_store[user_id]
    count = len(data["file_ids"])

    if count == 0 or data.get("is_processing"):
        return

    if count > MAX_PHOTOS:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ الحد الأقصى هو {MAX_PHOTOS} صورة فقط!"
        )
        user_photos_store.pop(user_id, None)
        return

    # 1. حذف الرسالة القديمة المرتفعة بالداخل إن وجدت
    if data.get("ctrl_msg_id"):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=data["ctrl_msg_id"])
        except Exception:
            pass

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Done (تحويل {count} صورة إلى PDF)", callback_data="done_photos")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])

    # 2. إرسال الرسالة النهائية أسفل الشات تماماً
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📥 تم استلام **{count}** صورة بالكامل!\nاضغط على Done للتحويل المباشر إلى PDF:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        data["ctrl_msg_id"] = msg.message_id
    except Exception:
        pass

async def handle_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if user_id not in user_photos_store:
        user_photos_store[user_id] = {
            "file_ids": [],
            "ctrl_msg_id": None,
            "chat_id": chat_id,
            "is_processing": False,
            "timer_task": None
        }

    data = user_photos_store[user_id]
    if data["is_processing"]:
        return

    photo_file_id = update.message.photo[-1].file_id
    data["file_ids"].append(photo_file_id)

    # إلغاء المهمة السابقة طالما الصور مستمرة بالوصول
    if data.get("timer_task"):
        data["timer_task"].cancel()

    # بدء العد التنازلي لإرسال الرسالة النهائية في الأسفل بمجرد توقف وصول الصور
    data["timer_task"] = asyncio.create_task(finalize_photo_message(user_id, chat_id, context))

# --- قسم الـ PDF ---
async def show_pdf_button(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(0.4)
    if user_id not in user_pdf_store:
        return

    data = user_pdf_store[user_id]
    count = len(data["file_ids"])
    if count == 0 or data.get("is_processing"):
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🖼️ استخراج كافة الصور من ({count}) ملف PDF", callback_data="extract_from_pdf")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])

    if data.get("ctrl_msg_id"):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=data["ctrl_msg_id"])
        except Exception:
            pass

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📄 تم استلام ({count}) ملف PDF...",
            reply_markup=keyboard
        )
        data["ctrl_msg_id"] = msg.message_id
    except Exception:
        pass

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc and (doc.mime_type == 'application/pdf' or doc.file_name.lower().endswith('.pdf')):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if user_id not in user_pdf_store:
            user_pdf_store[user_id] = {
                "file_ids": [],
                "ctrl_msg_id": None,
                "chat_id": chat_id,
                "is_processing": False,
                "timer_task": None
            }

        data = user_pdf_store[user_id]
        if data["is_processing"]:
            return

        data["file_ids"].append(doc.file_id)

        if data.get("timer_task"):
            data["timer_task"].cancel()

        data["timer_task"] = asyncio.create_task(show_pdf_button(user_id, chat_id, context))

# --- المعالجة والتحويل ---
async def download_file(context, file_id):
    file_obj = await context.bot.get_file(file_id)
    return bytes(await file_obj.download_as_bytearray())

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "done_photos":
        if user_id not in user_photos_store or not user_photos_store[user_id]["file_ids"]:
            try:
                await query.delete_message()
            except Exception:
                pass
            return

        data = user_photos_store[user_id]
        data["is_processing"] = True
        file_ids = list(data["file_ids"])
        total = len(file_ids)

        try:
            await query.edit_message_text(text=f"⚡ جاري تحويل {total} صورة إلى PDF بأقصى سرعة...")
        except Exception:
            pass

        try:
            download_tasks = [download_file(context, fid) for fid in file_ids]
            photos_bytes = await asyncio.gather(*download_tasks)

            pdf_bytes = img2pdf.convert(photos_bytes)
            pdf_stream = io.BytesIO(pdf_bytes)
            pdf_stream.name = f"Converted_{total}_Images.pdf"

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_stream,
                caption=f"✅ تم تحويل جميع الصور ({total} صورة) بنجاح!"
            )
            try:
                await query.message.delete()
            except Exception:
                pass
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ حدث خطأ، حاول مرة أخرى.")

        user_photos_store.pop(user_id, None)

    elif query.data == "extract_from_pdf":
        if user_id not in user_pdf_store or not user_pdf_store[user_id]["file_ids"]:
            try:
                await query.delete_message()
            except Exception:
                pass
            return

        data = user_pdf_store[user_id]
        data["is_processing"] = True
        file_ids = list(data["file_ids"])

        try:
            await query.edit_message_text(text="⚡ جاري استخراج الصور...")
        except Exception:
            pass

        try:
            download_tasks = [download_file(context, fid) for fid in file_ids]
            pdf_bytes_list = await asyncio.gather(*download_tasks)

            all_extracted_images = []
            for pdf_bytes in pdf_bytes_list:
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                for page in doc:
                    pix = page.get_pixmap(dpi=120)
                    all_extracted_images.append(pix.tobytes("jpg"))

            for i in range(0, len(all_extracted_images), 10):
                chunk = all_extracted_images[i:i + 10]
                media_group = [InputMediaPhoto(media=io.BytesIO(img)) for img in chunk]
                await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)

            try:
                await query.message.delete()
            except Exception:
                pass
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ حدث خطأ أثناء استخراج الصور.")

        user_pdf_store.pop(user_id, None)

    elif query.data == "cancel_action":
        user_photos_store.pop(user_id, None)
        user_pdf_store.pop(user_id, None)
        try:
            await query.delete_message()
        except Exception:
            pass

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photos))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()

if __name__ == "__main__":
    main()