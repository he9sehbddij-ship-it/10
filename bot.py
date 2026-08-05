import os
import asyncio
import io
import fitz  # PyMuPDF
import img2pdf
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# التوكن الخاص بك
TOKEN = "8666952088:AAF-9q5sGfeaD5djRz-ZVdn4g4V3a0YY2ko"

MAX_PHOTOS = 250
user_photos_store = {}
user_pdf_store = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ البوت يعمل بأعلى سرعة صاروخية! أرسل الصور أو الـ PDF الآن.")

async def update_photo_message_at_bottom(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    # تأخير أجزاء صغيرة جداً من الثانية لتجميع الصور المتدفقة بنفس الميكروثانية
    await asyncio.sleep(0.12)

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

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Done (تحويل {count} صورة)", callback_data="done_photos")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_action")]
    ])

    # 1. مسح الرسالة القديمة فوراً لكي لا تبقى معلقة بالكروب/الشات في الأعلى
    old_msg_id = data.get("ctrl_msg_id")
    if old_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception:
            pass

    # 2. إرسال رسالة جديدة فورية في الأسفل تماماً تحت آخر صورة أرسلتها
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📥 تم استلام **{count}** صورة حتى الآن...\nاضغط على Done للتحويل المباشر إلى PDF:",
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

    # إلغاء المهمة السابقة وإعادة تشغيل التحديث الفوري بأسفل الشات
    if data.get("timer_task"):
        data["timer_task"].cancel()

    data["timer_task"] = asyncio.create_task(update_photo_message_at_bottom(user_id, chat_id, context))

# --- قسم الـ PDF ---
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
                "is_processing": False
            }

        data = user_pdf_store[user_id]
        if data["is_processing"]:
            return

        data["file_ids"].append(doc.file_id)
        count = len(data["file_ids"])

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🖼️ استخراج الصور من ({count}) ملف PDF", callback_data="extract_from_pdf")],
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

# --- التنزيل السريع جداً بالتوازي عبر aiohttp ---
async def fetch_file(session, file_path):
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    async with session.get(url) as response:
        return await response.read()

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
            await query.edit_message_text(text="⚡ جاري تحويل الصور بسرعة الصاروخ...")
        except Exception:
            pass

        try:
            # 1. جلب مسارات الصور في الميكروثانية نفسها
            file_objs = await asyncio.gather(*[context.bot.get_file(fid) for fid in file_ids])
            
            # 2. تنزيل كافة الصور بالتوازي بطلب واحد موحد (سرعة ثانية إلى ثانيتين)
            async with aiohttp.ClientSession() as session:
                download_tasks = [fetch_file(session, f.file_path) for f in file_objs]
                photos_bytes = await asyncio.gather(*download_tasks)

            # 3. التحويل بالذاكرة العشوائية فوراً
            pdf_bytes = img2pdf.convert(photos_bytes)
            pdf_stream = io.BytesIO(pdf_bytes)
            pdf_stream.name = f"Converted_{total}_Images.pdf"

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_stream,
                caption=f"✅ تم تحويل ({total} صورة) بنجاح!"
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
            file_objs = await asyncio.gather(*[context.bot.get_file(fid) for fid in file_ids])
            async with aiohttp.ClientSession() as session:
                download_tasks = [fetch_file(session, f.file_path) for f in file_objs]
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