import logging
import asyncio

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError

from services.google_sheet import GoogleSheetService, get_file_id_for_code, get_image_url_for_code
from services.product_utils import normalize_code
from services.admin_utils import get_all_main_admin_ids
from services.message_utils import track_bot_message, track_bot_messages
from handlers.start import make_main_menu_keyboard
from config import ADMINS, HELPER_ADMINS

logger = logging.getLogger(__name__)

router = Router()

# Menu xabarlarni track qilish (Astatka kabi)
_MENU_MESSAGES: dict[int, int] = {}
# Xato xabarlarni track qilish (O'xshash modellar uchun)
_ERROR_MESSAGES: dict[int, int] = {}
# Natija xabarlarni track qilish (EDIT orqali natija chiqarish uchun)
_RESULT_MESSAGES: dict[int, int] = {}


class ModelImageStates(StatesGroup):
    ModelImageMenu = State()
    SingleModelImage = State()
    SimilarModelImages = State()


def back_keyboard() -> InlineKeyboardMarkup:
    """Orqaga tugmasi - Modellar rasmi menyusiga qaytaradi"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="model_images_back")]]
    )


def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖼 Bitta model rasmi", callback_data="model_single")],
            [InlineKeyboardButton(text="📸 O'xshash modelllar", callback_data="model_similar")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="model_images_back")],
        ]
    )


def _store_menu_message(chat_id: int, message_obj):
    """Yangi menu xabarini store qilish (Astatka kabi)"""
    if message_obj and hasattr(message_obj, "message_id"):
        _MENU_MESSAGES[chat_id] = message_obj.message_id
        # Track bot message for /start cleanup
        track_bot_message(chat_id, message_obj.message_id)


def _store_result_message(chat_id: int, message_obj):
    """Oxirgi natija message id sini saqlash (EDIT orqali natija chiqarish uchun)"""
    if message_obj and hasattr(message_obj, "message_id"):
        _RESULT_MESSAGES[chat_id] = message_obj.message_id
        # Track bot message for /start cleanup
        track_bot_message(chat_id, message_obj.message_id)


def _create_msg_obj(message_id: int):
    """Helper function to create a message-like object with message_id attribute."""
    class MsgObj:
        def __init__(self, msg_id):
            self.message_id = msg_id
    return MsgObj(message_id)


async def _delete_menu_message(bot: Bot, chat_id: int):
    """Menu xabarini o'chirish (Astatka kabi)"""
    msg_id = _MENU_MESSAGES.get(chat_id)
    if not msg_id:
        return
    
    try:
        await bot.delete_message(chat_id, msg_id)
    except TelegramBadRequest:
        try:
            await bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
        except TelegramBadRequest:
            pass
    except Exception as e:
        logger.warning(f"Delete menu message failed: {e}")
    finally:
        _MENU_MESSAGES.pop(chat_id, None)


async def _delete_error_message(bot: Bot, chat_id: int):
    """Xato xabarini o'chirish (O'xshash modellar uchun)"""
    msg_id = _ERROR_MESSAGES.get(chat_id)
    if not msg_id:
        return
    
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass
    finally:
        _ERROR_MESSAGES.pop(chat_id, None)


def result_keyboard() -> InlineKeyboardMarkup:
    """Natija uchun keyboard - Orqaga va Asosiy menyu"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Orqaga", callback_data="model_images_back"),
                InlineKeyboardButton(text="🏠 Asosiy menyu", callback_data="menu_main"),
            ]
        ]
    )


def similar_models_result_keyboard() -> InlineKeyboardMarkup:
    """O'xshash modellar natijasi uchun keyboard - faqat Orqaga"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Orqaga", callback_data="model_images_back"),
            ]
        ]
    )


def _explain_image_error(error: Exception, image_url: str = "") -> dict:
    """
    Comprehensive error explanation system for image sending errors.
    Detects error type, translates to simple Uzbek, explains cause, suggests action.
    
    Returns:
        {
            "explanation": "Simple explanation in Uzbek",
            "reason": "Why it happened",
            "action": "What admin should do",
            "error_type": "Error category"
        }
    """
    error_str = str(error).lower()
    error_type = "unknown"
    explanation = ""
    reason = ""
    action = ""
    
    # ========== WEB PAGE CONTENT ERRORS ==========
    if "wrong type of the web page content" in error_str or "web page content" in error_str:
        error_type = "web_page_content"
        explanation = "Telegram rasm formatini qabul qilmadi"
        reason = "Telegram bu linkni rasm deb qabul qilmadi. Ko'pincha Google Drive preview sahifasi yoki HTML sahifa bo'lganda chiqadi."
        action = "Rasmni JPG/PNG formatga o'tkazing yoki to'g'ridan-to'g'ri rasm linkidan foydalaning."
    
    # ========== HTTP URL CONTENT ERRORS ==========
    elif "failed to get http url content" in error_str or "failed to get url" in error_str or "http url content" in error_str:
        error_type = "http_fetch_failed"
        explanation = "Rasm URL mavjud, lekin rasm sifatida ochilmadi"
        reason = "Rasm linkidan ma'lumot olishda muammo. Internet aloqasi uzilgan, server javob bermayapti yoki link ishlamayapti."
        action = "Internet aloqasini tekshiring. Linkni brauzerda ochib ko'ring. Agar ishlamasa, yangi link yarating."
    
    # ========== TIMEOUT ERRORS ==========
    elif "timeout" in error_str or "timed out" in error_str or "time out" in error_str:
        error_type = "timeout"
        explanation = "Rasm juda katta / noto'g'ri formatda"
        reason = "Rasm yuklash vaqti tugadi. Internet aloqasi sekin yoki rasm juda katta."
        action = "Internet aloqasini tekshiring. Kattaroq rasmlarni siqib, kichikroq qiling."
    
    # ========== 404 NOT FOUND ERRORS ==========
    elif "not found" in error_str or "404" in error_str or "file not found" in error_str:
        error_type = "not_found"
        explanation = "Rasm topilmadi yoki o'chirilgan"
        reason = "Link ishlamayapti yoki rasm Google Drive'dan o'chirilgan."
        action = "Rasmni Google Drive'da tekshiring. Agar o'chirilgan bo'lsa, yangi rasm yuklab, yangi link yarating."
    
    # ========== 403 FORBIDDEN ERRORS ==========
    elif "forbidden" in error_str or "403" in error_str or "access denied" in error_str:
        error_type = "forbidden"
        explanation = "Rasmga kirish taqiqlangan"
        reason = "Google Drive linki yopiq yoki ruxsatsiz. Faqat link egasi ko'ra oladi."
        action = "Google Drive'da rasmni 'Har kim ko'rishi mumkin' deb sozlang."
    
    # ========== UNSUPPORTED MEDIA TYPE ERRORS ==========
    elif "unsupported media type" in error_str or "media type" in error_str or "content type" in error_str:
        error_type = "unsupported_media"
        explanation = "Rasm formati qo'llab-quvvatlanmaydi"
        reason = "Telegram faqat JPG, PNG, GIF, WEBP formatlarini qabul qiladi. Boshqa formatlar ishlamaydi."
        action = "Rasmni JPG yoki PNG formatga o'tkazing."
    
    # ========== BAD REQUEST / INVALID ERRORS ==========
    elif "bad request" in error_str or "invalid" in error_str or "bad url" in error_str:
        error_type = "bad_request"
        explanation = "Rasm linki noto'g'ri yoki o'chirilgan"
        reason = "Link formati noto'g'ri, rasm o'chirilgan yoki Telegram uni tushuna olmayapti."
        action = "Linkni brauzerda ochib tekshiring. Agar ishlamasa, yangi rasm yuklab, yangi link yarating."
    
    # ========== DEFAULT / UNKNOWN ERRORS ==========
    else:
        error_type = "unknown"
        explanation = "Rasm yuborishda muammo yuz berdi"
        reason = f"Texnik xato: {str(error)[:100]}"
        action = "Xatoni log faylida ko'rib chiqing. Agar takrorlansa, rasmni boshqa formatga o'tkazing."
    
    return {
        "explanation": explanation,
        "reason": reason,
        "action": action,
        "error_type": error_type
    }




@router.callback_query(F.data == "menu_model_images")
async def callback_model_images_menu(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Modellar rasmi bo'limiga kirish - UI menu doim edit qilinadi"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.set_state(ModelImageStates.ModelImageMenu)
    
    text = "📷 Modellar rasmi\n\nQuyidagi bo'limlardan birini tanlang:"
    chat_id = callback_query.message.chat.id
    
    # UI menu doim edit qilinadi (yangi xabar yuborilmaydi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=menu_keyboard()
        )
        # Edit muvaffaqiyatli bo'lsa, menyu xabarini yangilash
        _store_menu_message(chat_id, callback_query.message)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa (masalan, xabar CONTENT bo'lsa), yangi menyu yuborish
        # Avvalgi menu xabarlarini tozalash
        await _delete_menu_message(bot, chat_id)
        
        # Yangi menyu yuborish
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=menu_keyboard()
        )
        _store_menu_message(chat_id, menu_msg)


@router.callback_query(F.data == "model_single")
async def callback_single_model(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Bitta model rasmi bo'limiga kirish - UI menu doim edit qilinadi"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.set_state(ModelImageStates.SingleModelImage)
    
    text = "🖼 Bitta model rasmi\n\nModel kodini yuboring:"
    chat_id = callback_query.message.chat.id
    
    # UI menu doim edit qilinadi (yangi xabar yuborilmaydi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=back_keyboard()
        )
        # Edit muvaffaqiyatli bo'lsa, menyu xabarini yangilash va state ga saqlash
        _store_menu_message(chat_id, callback_query.message)
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa (masalan, xabar CONTENT bo'lsa), yangi menyu yuborish
        # Avvalgi menu xabarlarini tozalash
        await _delete_menu_message(bot, chat_id)
        
        # Yangi menyu yuborish
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=back_keyboard()
        )
        _store_menu_message(chat_id, menu_msg)
        await state.update_data(bot_message_id=menu_msg.message_id)


@router.message(ModelImageStates.SingleModelImage, F.text)
async def process_single_model_code(message: Message, state: FSMContext, bot: Bot):
    """Bitta model kodini qayta ishlash - FAQAT EDIT orqali natija chiqaradi"""
    user_raw = message.text.strip()
    chat_id = message.chat.id
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    # State dan bot_message_id ni olish (AVVAL - menyu xabarini o'chirishdan oldin)
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    # Oldingi menu xabarlarini tozalash (faqat menu)
    await _delete_menu_message(bot, chat_id)
    
    # Oldingi natija xabarini olish (_RESULT_MESSAGES dan) - "O'xshash modellar" logikasi bilan bir xil
    single_image_result_msg_id = _RESULT_MESSAGES.get(chat_id)
    
    try:
        # Model kodini normalizatsiya qilish (Astatka kabi - normalize_code funksiyasini ishlat)
        user_code_norm = normalize_code(user_raw or "")
        
        if not user_code_norm:
            # Xato xabari matni: kod normalizatsiya qilib bo'lmadi (bazadan topilmadi)
            error_text = (
                "❌ Model bazadan topilmadi\n\n"
                f"Siz yuborgan matn:\n"
                f"👉 \"{user_raw}\"\n\n"
                "Sabab:\n"
                "Bu kod ma'lumotlar bazasida mavjud emas.\n\n"
                "ℹ️ Davom etish uchun:\n"
                "Yangi model kodini yuboring — bot ishlashda davom etadi."
            )
            
            # QAT'IY QOIDA: DELETE oldingi rasm xabarini, keyin SEND yangi xato xabari (EDIT emas)
            if single_image_result_msg_id:
                try:
                    await bot.delete_message(chat_id, single_image_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # YANGI xato xabarini yuborish
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=error_text,
                reply_markup=result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
            return
        
        sheet_service = GoogleSheetService()
        await sheet_service._ensure_client()
        
        # Sheets2 dan model va rasm mavjudligini tekshirish (Astatkadagi get_product_data logikasi)
        def _find_best_matching_model():
            """
            Qidiruv logikasi:
            - Exact match (eng yuqori prioritet)
            - startswith
            - endswith  
            - contains
            """
            try:
                spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
                worksheet = spreadsheet.worksheet(sheet_service.images_sheet_name)
                raw_values = worksheet.get_all_values()
                if not raw_values or len(raw_values) < 2:
                    return None, None, None, None
                
                headers = raw_values[0]
                code_idx = None
                image_idx = None
                
                for idx, header in enumerate(headers):
                    header_lower = (header or "").strip().lower()
                    if header_lower == "code":
                        code_idx = idx
                    elif header_lower in ["image_url", "imageurl", "image url", "image"]:
                        image_idx = idx
                
                if code_idx is None:
                    return None, None, None, None
                
                # Barcha mos keladigan modellarni topish (4 ta shart)
                matched_models = []
                
                for row_num, row in enumerate(raw_values[1:], start=2):  # start=2 chunki header 1-qator
                    if code_idx >= len(row):
                        continue
                    code_value = row[code_idx] if code_idx < len(row) else ""
                    if not code_value:
                        continue
                    
                    # Sheet'dagi kodni normalizatsiya qilish
                    code_original = code_value.strip()
                    sheet_code_norm = normalize_code(code_original)
                    
                    if not sheet_code_norm:
                        continue
                    
                    # Universal qidiruv qoidasi - 4 ta shartdan biri bajarilsa kifoya
                    match_priority = None  # 1=exact, 2=startswith, 3=endswith, 4=contains
                    
                    # Shart 1: Exact match (eng yuqori prioritet)
                    if sheet_code_norm == user_code_norm:
                        match_priority = 1
                    # Shart 2: startswith
                    elif sheet_code_norm.startswith(user_code_norm):
                        match_priority = 2
                    # Shart 3: endswith
                    elif sheet_code_norm.endswith(user_code_norm):
                        match_priority = 3
                    # Shart 4: contains
                    elif user_code_norm in sheet_code_norm:
                        match_priority = 4
                    
                    if match_priority:
                        # Rasmni olish
                        image_url = ""
                        if image_idx is not None and image_idx < len(row):
                            image_url = (row[image_idx] or "").strip()
                        
                        matched_models.append({
                            'code_original': code_original,
                            'code_normalized': sheet_code_norm,
                            'image_url': image_url,
                            'priority': match_priority,
                            'row_number': row_num
                        })
                
                if not matched_models:
                    return None, None, None, None
                
                # ENG MOS KELADIGAN BITTA MODELNI TANLASH (prioritet bo'yicha tartiblash)
                # 1. Exact match
                # 2. startswith (qisqa kodlar uchun)
                # 3. endswith
                # 4. contains
                matched_models.sort(key=lambda x: (x['priority'], len(x['code_normalized'])))
                best_match = matched_models[0]
                
                return best_match['code_original'], best_match['code_normalized'], best_match['image_url'], best_match['row_number']
                
            except Exception as e:
                logger.error(f"Error finding model: {e}")
                return None, None, None, None
        
        found_code_original, found_code_norm, image_url, row_number = await asyncio.to_thread(_find_best_matching_model)
        
        # 3 ta holatni tekshirish
        
        # ❌ HOLAT 1: MODEL BAZADAN TOPILMADI (NOT_IN_DATABASE)
        if found_code_original is None:
            # Xato xabari matni: bazadan topilmadi
            error_text = (
                "❌ Model bazadan topilmadi\n\n"
                f"Siz yuborgan matn:\n"
                f"👉 \"{user_raw}\"\n\n"
                "Sabab:\n"
                "Bu kod ma'lumotlar bazasida mavjud emas.\n\n"
                "ℹ️ Davom etish uchun:\n"
                "Yangi model kodini yuboring — bot ishlashda davom etadi."
            )
            
            # QAT'IY QOIDA: DELETE oldingi rasm xabarini, keyin SEND yangi xato xabari (EDIT emas)
            if single_image_result_msg_id:
                try:
                    await bot.delete_message(chat_id, single_image_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # YANGI xato xabarini yuborish
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=error_text,
                reply_markup=result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
            return
        
        # ✅ MODEL BOR + RASM BOR
        # RASM YUBORISH PRIORITETI: 1) file_id, 2) URL, 3) Rasm topilmadi
        image_file_id = None
        if found_code_norm:
            # 1. Avval file_id ni tekshirish (eng tez)
            image_file_id = get_file_id_for_code(found_code_norm)
        
        # 2. Agar file_id yo'q bo'lsa, URL ni tekshirish (fallback)
        if not image_file_id:
            if not image_url or not image_url.strip():
                # URL ni qidirish
                image_url = get_image_url_for_code(found_code_norm) if found_code_norm else None
            
            # Google Drive linkini formatlash
            if image_url and image_url.strip():
                # Validatsiya: "rasm kerak" kabi matnlarni o'tkazib yuborish
                clean_url = image_url.strip()
                if " " in clean_url and not clean_url.lower().startswith("http"):
                    image_url = None
                else:
                    image_url = sheet_service._convert_google_drive_link(image_url)
            else:
                image_url = None
        
        # Rasm yuborish
        if image_file_id:
            # 1. file_id mavjud - eng tez variant
            caption_text = f"📸 Model: {found_code_original}"
            
            # QAT'IY QOIDA: DELETE oldingi rasm xabarini, keyin SEND yangi (EDIT emas)
            if single_image_result_msg_id:
                try:
                    await bot.delete_message(chat_id, single_image_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # YANGI rasm xabarini yuborish (file_id bilan)
            result_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=image_file_id,
                caption=caption_text,
                reply_markup=result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
        elif image_url and image_url.strip():
            # 2. URL mavjud - fallback
            caption_text = f"📸 Model: {found_code_original}"
            
            # QAT'IY QOIDA: DELETE oldingi rasm xabarini, keyin SEND yangi (EDIT emas)
            if single_image_result_msg_id:
                try:
                    await bot.delete_message(chat_id, single_image_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # YANGI rasm xabarini yuborish (URL bilan) - XATO HANDLING BILAN
            try:
                result_msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=caption_text,
                    reply_markup=result_keyboard()
                )
                _store_result_message(chat_id, result_msg)
            except (TelegramAPIError, Exception) as photo_error:
                # Telegram rasmni qabul qilmadi - xato handling
                error_info = _explain_image_error(photo_error, image_url)
                
                # Sheet nomi va qator raqami
                sheet_name = sheet_service.images_sheet_name or "sheets2"
                row_info = f"{row_number + 1}" if row_number is not None else "(topilmadi)"
                
                # Logger'ga yozish (texnik xato saqlanadi)
                logger.error(
                    f"Image send failed - Model: {found_code_original}, "
                    f"Sheet: {sheet_name}, Qator: {row_info}, URL: {image_url[:100]}, "
                    f"Error: {str(photo_error)[:200]}"
                )
                
                # Foydalanuvchiga tushunarli xabar
                user_reason = error_info['explanation']
                if "Telegram rasm formatini qabul qilmadi" in user_reason:
                    reason_text = "• Telegram rasm formatini qabul qilmadi"
                elif "Rasm URL mavjud" in user_reason:
                    reason_text = "• Rasm URL ochildi, lekin rasm sifatida olinmadi"
                elif "juda katta" in user_reason or "noto'g'ri formatda" in user_reason:
                    reason_text = "• Rasm juda katta yoki noto'g'ri formatda"
                elif "timeout" in str(photo_error).lower() or "timed out" in str(photo_error).lower():
                    reason_text = "• Google Drive vaqtinchalik javob bermadi"
                else:
                    reason_text = "• Telegram rasm formatini qabul qilmadi"
                
                user_error_text = (
                    "❌ Rasm yuborishda muammo yuz berdi\n\n"
                    f"📦 Model kodi: {found_code_original}\n\n"
                    f"📍 Muammo tafsiloti:\n"
                    f"- Sheet: {sheet_name}\n"
                    f"- Qator: {row_info}\n"
                    f"- Sabab:\n"
                    f"  {reason_text}\n\n"
                    f"ℹ️ Izoh:\n"
                    f"Rasm Google Drive'da mavjud, lekin Telegram serveri uni rasm sifatida qabul qilmadi.\n"
                    f"Iltimos, admin bu rasmni tekshiradi."
                )
                
                # Foydalanuvchiga xato xabarini yuborish
                error_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=user_error_text,
                    reply_markup=result_keyboard()
                )
                _store_result_message(chat_id, error_msg)
                
                # Asosiy admin'ga texnik xato ma'lumotini yuborish
                main_admin_ids = get_all_main_admin_ids()
                if not main_admin_ids:
                    # Agar asosiy admin topilmasa, barcha adminlarga yuborish
                    main_admin_ids = list(set(ADMINS + HELPER_ADMINS))
                
                user_info = message.from_user
                username = user_info.username if user_info and user_info.username else "(username yo'q)"
                
                admin_error_text = (
                    "🚨 Rasm yuborishda xato\n\n"
                    f"👤 Foydalanuvchi:\n"
                    f"- ID: {chat_id}\n"
                    f"- Username: @{username}\n\n"
                    f"📂 Bo'lim: Bitta model rasmi\n"
                    f"🔎 Qidiruv kodi: {user_raw}\n\n"
                    f"❌ Xato tafsilotlari:\n"
                    f"- Sheet: {sheet_name}\n"
                    f"- Qator: {row_info}\n"
                    f"- Model kodi: {found_code_original}\n"
                    f"- Sabab: {error_info['reason']}\n\n"
                    f"📊 Natija:\n"
                    f"- Yuborildi: 0\n"
                    f"- Xato: 1"
                )
                
                for admin_id in main_admin_ids:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=admin_error_text
                        )
                    except Exception as admin_err:
                        logger.warning(f"Failed to send error to admin {admin_id}: {admin_err}")
        else:
            # 3. Rasm topilmadi
            image_url = None
        
        # ⚠️ HOLAT 2: MODEL BAZADA BOR, LEKIN RASM YO'Q (NO_IMAGE)
        if not image_url:
            # Xato xabari matni: model bor, lekin rasm yo'q
            warning_text = (
                "⚠️ Model topildi, lekin rasmi mavjud emas\n\n"
                f"Model kodi:\n"
                f"👉 \"{user_raw}\"\n\n"
                "Sabab:\n"
                "Bu model bazada bor, ammo rasm yuklanmagan.\n\n"
                "ℹ️ Davom etish uchun:\n"
                "Yangi model kodini yuboring yoki admin bilan bog'laning."
            )
            
            # QAT'IY QOIDA: DELETE oldingi rasm xabarini, keyin SEND yangi xato xabari (EDIT emas)
            if single_image_result_msg_id:
                try:
                    await bot.delete_message(chat_id, single_image_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # YANGI ogohlantirish xabarini yuborish
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=warning_text,
                reply_markup=result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
    
    except Exception as exc:
        logger.error(f"Error processing single model code: {exc}", exc_info=True)
        # Xato holati - tushunarli xabar va tugmalar bilan
        error_text = (
            "❌ Xatolik yuz berdi\n\n"
            "Botda texnik muammo yuz berdi.\n"
            "Iltimos, keyinroq qayta urinib ko'ring."
        )
        # QAT'IY QOIDA: DELETE oldingi rasm xabarini, keyin SEND yangi xato xabari (EDIT emas)
        if single_image_result_msg_id:
            try:
                await bot.delete_message(chat_id, single_image_result_msg_id)
            except Exception:
                pass
            _RESULT_MESSAGES.pop(chat_id, None)
        
        # YANGI xato xabarini yuborish (tugmalar bilan)
        error_msg = await bot.send_message(
            chat_id=chat_id,
            text=error_text,
            reply_markup=result_keyboard()
        )
        _store_result_message(chat_id, error_msg)


@router.callback_query(F.data == "model_similar")
async def callback_similar_models(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """O'xshash modellar bo'limiga kirish - UI menu doim edit qilinadi"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.set_state(ModelImageStates.SimilarModelImages)
    chat_id = callback_query.message.chat.id
    
    # Eski xato xabarlarni tozalash va state ni reset qilish
    await _delete_error_message(bot, chat_id)
    await state.update_data(bot_message_id=None, last_error_message_id=None)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="model_images_back")]]
    )
    
    menu_text = (
        "📸 O'xshash modelllar\n\n"
        "Model kodini yuboring:"
    )
    
    # UI menu doim edit qilinadi (yangi xabar yuborilmaydi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        # Edit muvaffaqiyatli bo'lsa, menyu xabarini yangilash va state ga saqlash
        _store_menu_message(chat_id, callback_query.message)
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa (masalan, xabar CONTENT bo'lsa), yangi menyu yuborish
        # Avvalgi menu xabarlarini tozalash
        await _delete_menu_message(bot, chat_id)
        
        # Yangi menyu yuborish
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)
        await state.update_data(bot_message_id=menu_msg.message_id)


@router.message(ModelImageStates.SimilarModelImages, F.text)
async def process_similar_models_code(message: Message, state: FSMContext, bot: Bot):
    """O'xshash modellar kodini qayta ishlash - FAQAT EDIT orqali natija chiqaradi"""
    chat_id = message.chat.id
    user_query = message.text.strip()
    user_code_display = user_query  # Foydalanuvchi kiritgan asl kod (xabar uchun)
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    # Oldingi xato xabarlarni o'chirish
    await _delete_error_message(bot, chat_id)
    
    # Oldingi menu xabarlarini tozalash (faqat menu)
    await _delete_menu_message(bot, chat_id)
    
    # Oldingi natija xabarlarini olish (EDIT rejimi uchun)
    state_data = await state.get_data()
    previous_image_ids = state_data.get("previous_image_ids", [])  # Oldingi rasm xabarlari ID lari
    previous_result_msg_id = _RESULT_MESSAGES.get(chat_id)  # Oldingi natija xabari ID si
    
    # State dan oldingi ma'lumotlarni tozalash (keyin yangilash)
    await state.update_data(previous_image_ids=[], bot_message_id=None)
    
    try:
        # 1) FOYDALANUVCHI KODINI YANGI NORMALIZATSIYA QILISH
        def normalize_code_for_search(code_str: str) -> str:
            """
            Normalize code for flexible search: 
            - Convert to lowercase
            - Convert Cyrillic to Latin
            - Remove all spaces
            - Remove all special characters (-, _, ., /, etc.)
            """
            if not code_str:
                return ""
            
            # Kirill harflarni lotinga o'tkazish (katta va kichik harflar)
            cyrillic_to_latin = {
                # Katta harflar
                'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'К': 'K', 
                'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P', 'Т': 'T', 
                'У': 'Y', 'Х': 'X', 'Я': 'YA', 'Ю': 'YU', 'Ё': 'YO',
                # Kichik harflar
                'а': 'a', 'в': 'b', 'с': 'c', 'е': 'e', 'к': 'k',
                'м': 'm', 'н': 'h', 'о': 'o', 'р': 'p', 'т': 't',
                'у': 'y', 'х': 'x', 'я': 'ya', 'ю': 'yu', 'ё': 'yo'
            }
            
            # Kirill harflarni lotinga o'tkazish
            normalized = str(code_str).strip()
            for cyr, lat in cyrillic_to_latin.items():
                normalized = normalized.replace(cyr, lat)
            
            # lower() qil
            normalized = normalized.lower()
            
            # Barcha bo'sh joylarni olib tashla
            normalized = normalized.replace(" ", "")
            
            # Barcha maxsus belgilarni olib tashla (-, _, ., /, va boshqalar)
            for char in ["-", ".", "/", "_", "—", "–", "•", "·"]:
                normalized = normalized.replace(char, "")
            
            return normalized
        
        def extract_numbers_only(code_str: str) -> str:
            """Extract only numbers from code string"""
            if not code_str:
                return ""
            return ''.join(filter(str.isdigit, code_str))
        
        user_code_normalized = normalize_code_for_search(user_query)
        user_code_numbers = extract_numbers_only(user_query)
        
        if not user_code_normalized and not user_code_numbers:
            # Kod bo'sh bo'lsa - kod bazada yo'q
            error_text = (
                f"❌ Bu kod mavjud emas: {user_code_display}\n"
                "Iltimos, kodni tekshirib qayta yuboring.\n"
                "Bot ishlashda davom etadi."
            )
            
            # AVVAL: Barcha eski rasmlarni o'chirish
            for img_id in previous_image_ids:
                try:
                    await bot.delete_message(chat_id, img_id)
                except Exception:
                    pass
            
            # Keyin: Oldingi natija xabarini o'chirish
            if previous_result_msg_id:
                try:
                    await bot.delete_message(chat_id, previous_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # Faqat 1 ta xato xabarini yuborish (tugmalar bilan) - oddiy text
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=error_text,
                reply_markup=similar_models_result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
            await state.update_data(previous_image_ids=[], result_message_id=result_msg.message_id)
            return
        
        sheet_service = GoogleSheetService()
        await sheet_service._ensure_client()
        
        # 2-4) SHEETS2 DAGI BARCHA KODLARNI TEKSHIRISH VA O'XSHASH MODELLARNI TOPISH
        def _find_similar_models_by_prefix():
            try:
                spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
                worksheet = spreadsheet.worksheet(sheet_service.images_sheet_name)
                raw_values = worksheet.get_all_values()
                if not raw_values or len(raw_values) < 2:
                    return []
                
                headers = raw_values[0]
                code_idx = None
                image_idx = None
                
                for idx, header in enumerate(headers):
                    header_lower = (header or "").strip().lower()
                    if header_lower == "code":
                        code_idx = idx
                    elif header_lower in ["image_url", "imageurl", "image url", "image"]:
                        image_idx = idx
                
                if code_idx is None:
                    return []
                
                # Barcha kodlarni oldindan normalize qilish va saqlash
                all_codes = []
                for row_index, row in enumerate(raw_values[1:], start=2):  # start=2 chunki 1-indexed va header bor
                    if code_idx >= len(row):
                        continue
                    code_value = row[code_idx] if code_idx < len(row) else ""
                    if not code_value:
                        continue
                    
                    code_original = code_value.strip()
                    sheet_code_normalized = normalize_code_for_search(code_value)
                    sheet_code_numbers = extract_numbers_only(code_value)
                    
                    image_url = ""
                    if image_idx is not None and image_idx < len(row):
                        image_url = (row[image_idx] or "").strip()
                    
                    all_codes.append({
                        "original": code_original,
                        "normalized": sheet_code_normalized,
                        "numbers": sheet_code_numbers,
                        "image_url": image_url,
                        "row_number": row_index  # Google Sheets qator raqami (1-indexed)
                    })
                
                # 1-BOSQICH: ENG ANIQLARI (To'liq moslik va startswith)
                stage1_matches = []
                
                for code_data in all_codes:
                    if not user_code_normalized or not code_data["normalized"]:
                        continue
                    
                    # A) To'liq moslik
                    if user_code_normalized == code_data["normalized"]:
                        stage1_matches.append((code_data["original"], code_data["normalized"], code_data["image_url"], code_data["row_number"], 1))  # Priority 1
                    # B) Boshlanishi mos
                    elif code_data["normalized"].startswith(user_code_normalized):
                        stage1_matches.append((code_data["original"], code_data["normalized"], code_data["image_url"], code_data["row_number"], 2))  # Priority 2
                
                # Agar 1-bosqichda natija topilsa, faqat shularni qaytar
                if stage1_matches:
                    # Dublikatlarni olib tashlash (original code bo'yicha)
                    seen_codes = set()
                    unique_matches = []
                    for code_orig, code_norm, img_url, row_num, priority in stage1_matches:
                        if code_orig not in seen_codes:
                            seen_codes.add(code_orig)
                            unique_matches.append((code_orig, code_norm, img_url, row_num, priority))
                    
                    # Saralash: priority bo'yicha (1 = to'liq mos, 2 = startswith)
                    unique_matches.sort(key=lambda x: x[4])
                    
                    # 20 tagacha cheklash
                    if len(unique_matches) > 20:
                        unique_matches = unique_matches[:20]
                    
                    # Format: (code_original, code_normalized, image_url, row_number)
                    return [(code_orig, code_norm, img_url, row_num) for code_orig, code_norm, img_url, row_num, _ in unique_matches]
                
                # 2-BOSQICH: ICHIDA BORLIGI (Cheklangan - faqat uzunligi >= 4 bo'lsa)
                if len(user_code_normalized) >= 4:
                    stage2_matches = []
                    for code_data in all_codes:
                        if not user_code_normalized or not code_data["normalized"]:
                            continue
                        
                        # Ichida borligi
                        if user_code_normalized in code_data["normalized"]:
                            stage2_matches.append((code_data["original"], code_data["normalized"], code_data["image_url"], code_data["row_number"], 3))  # Priority 3
                    
                    if stage2_matches:
                        # Dublikatlarni olib tashlash
                        seen_codes = set()
                        unique_matches = []
                        for code_orig, code_norm, img_url, row_num, priority in stage2_matches:
                            if code_orig not in seen_codes:
                                seen_codes.add(code_orig)
                                unique_matches.append((code_orig, code_norm, img_url, row_num, priority))
                        
                        # 20 tagacha cheklash
                        if len(unique_matches) > 20:
                            unique_matches = unique_matches[:20]
                        
                        return [(code_orig, code_norm, img_url, row_num) for code_orig, code_norm, img_url, row_num, _ in unique_matches]
                
                # 3-BOSQICH: FAQAT RAQAMLAR (Jiddiy cheklov)
                if user_code_numbers and len(user_code_numbers) >= 3:
                    stage3_matches = []
                    for code_data in all_codes:
                        if not code_data["numbers"]:
                            continue
                        
                        # Faqat to'liq teng bo'lsa (ichida bor EMAS)
                        if user_code_numbers == code_data["numbers"]:
                            stage3_matches.append((code_data["original"], code_data["normalized"], code_data["image_url"], code_data["row_number"], 4))  # Priority 4
                    
                    if stage3_matches:
                        # Dublikatlarni olib tashlash
                        seen_codes = set()
                        unique_matches = []
                        for code_orig, code_norm, img_url, row_num, priority in stage3_matches:
                            if code_orig not in seen_codes:
                                seen_codes.add(code_orig)
                                unique_matches.append((code_orig, code_norm, img_url, row_num, priority))
                        
                        # 20 tagacha cheklash
                        if len(unique_matches) > 20:
                            unique_matches = unique_matches[:20]
                        
                        return [(code_orig, code_norm, img_url, row_num) for code_orig, code_norm, img_url, row_num, _ in unique_matches]
                
                # Hech narsa topilmadi
                return []
                
            except Exception as e:
                logger.error(f"Error finding matching models: {e}")
                return []
        
        matching_models = await asyncio.to_thread(_find_similar_models_by_prefix)
        
        # ❌ HECH QANDAY O'XSHASH MODEL TOPILMADI (kod bazada yo'q)
        if not matching_models:
            error_text = (
                f"❌ Bu kod mavjud emas: {user_code_display}\n"
                "Iltimos, kodni tekshirib qayta yuboring.\n"
                "Bot ishlashda davom etadi."
            )
            
            # AVVAL: Barcha eski rasmlarni o'chirish
            for img_id in previous_image_ids:
                try:
                    await bot.delete_message(chat_id, img_id)
                except Exception:
                    pass
            
            # Keyin: Oldingi natija xabarini o'chirish
            if previous_result_msg_id:
                try:
                    await bot.delete_message(chat_id, previous_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # Faqat 1 ta xato xabarini yuborish (tugmalar bilan) - oddiy text
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=error_text,
                reply_markup=similar_models_result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
            await state.update_data(previous_image_ids=[], result_message_id=result_msg.message_id)
            return
        
        # Rasmlari bo'lgan modellarni ajratish (qator raqami bilan)
        models_with_images = []
        for item in matching_models:
            if len(item) >= 4:  # (code_orig, code_norm, img_url, row_number)
                code_orig, code_norm, img_url, row_num = item[0], item[1], item[2], item[3]
                if img_url:
                    models_with_images.append((code_orig, code_norm, img_url, row_num))
            elif len(item) >= 3:  # Eski format (backward compatibility)
                code_orig, code_norm, img_url = item[0], item[1], item[2]
                if img_url:
                    models_with_images.append((code_orig, code_norm, img_url, None))
        
        # ✅ KAMIDA 1 TA O'XSHASH MODEL TOPILDI VA RASMLAR BOR
        if models_with_images:
            # Oldingi rasmlarni o'chirish (barcha holatlar uchun)
            for img_id in previous_image_ids:
                try:
                    await bot.delete_message(chat_id, img_id)
                except Exception:
                    pass
            if previous_result_msg_id:
                try:
                    await bot.delete_message(chat_id, previous_result_msg_id)
                except Exception:
                    pass
            
            # ========== 1️⃣ RASMLARNI YIG'ISH ==========
            # To'g'ri rasmlar uchun bitta list
            valid_media = []  # InputMediaPhoto objectlar ro'yxati
            valid_media_with_code = []  # (code_orig, media_source, row_number) - xatolarni kuzatish uchun
            errors = []  # Xatolar: {"code": "...", "reason": "...", "row_number": ...}
            
            for code_orig, code_norm, image_url, row_number in models_with_images:
                try:
                    # Avval file_id ni tekshirish
                    image_file_id = get_file_id_for_code(code_norm) if code_norm else None
                    
                    if image_file_id:
                        # AGAR file_id bo'lsa:
                        valid_media.append(InputMediaPhoto(media=image_file_id))
                        valid_media_with_code.append((code_orig, image_file_id, row_number))
                    else:
                        # Validatsiya: "rasm kerak" kabi matnlarni o'tkazib yuborish
                        clean_url = image_url.strip()
                        if " " in clean_url and not clean_url.lower().startswith("http"):
                            continue

                        # AKS HOLDA: URL dan foydalanish
                        converted_url = sheet_service._convert_google_drive_link(image_url)
                        if not converted_url:
                            errors.append({
                                "code": code_orig,
                                "reason": "Rasm linki noto'g'ri formatda",
                                "row_number": row_number
                            })
                            continue
                        
                        # URL ni tekshirish (faqat formatlash, yuborish keyinroq)
                        valid_media.append(InputMediaPhoto(media=converted_url))
                        valid_media_with_code.append((code_orig, converted_url, row_number))
                    
                except Exception as e:
                    # Xato bo'lsa, ro'yxatga yozish
                    error_info = _explain_image_error(e, image_url)
                    errors.append({
                        "code": code_orig,
                        "reason": error_info['explanation'],
                        "row_number": row_number
                    })
                    logger.warning(f"Image format error for {code_orig}: {e}")
                    continue
            
            # ========== 2️⃣ YUBORISH QOIDASI ==========
            # HAMMA rasmlar faqat sendMediaGroup orqali yuboriladi
            image_message_ids = []  # Yuborilgan rasm xabarlari ID lari
            
            # AGAR valid_media bo'sh bo'lmasa:
            if valid_media:
                # Telegram limiti: 1 media group = maksimum 10 ta rasm
                # valid_media ni 10 tadan bo'lib chiqish
                chunk_size = 10
                
                for i in range(0, len(valid_media), chunk_size):
                    chunk = valid_media[i:i + chunk_size]
                    
                    # Har bir chunk ni BITTA sendMediaGroup orqali yuborish
                    try:
                        sent_messages = await bot.send_media_group(
                            chat_id=chat_id,
                            media=chunk
                        )
                        message_ids = [msg.message_id for msg in sent_messages]
                        image_message_ids.extend(message_ids)
                        # Track bot messages for /start cleanup
                        track_bot_messages(chat_id, message_ids)
                    except Exception as e:
                        # Agar media group muvaffaqiyatsiz bo'lsa, xatolarni aniqlash
                        logger.warning(f"Media group failed for chunk {i//chunk_size + 1}: {e}")
                        # Bu chunk'dagi rasmlar uchun xatolarni ro'yxatga yozish
                        chunk_start_idx = i
                        chunk_end_idx = min(i + chunk_size, len(valid_media_with_code))
                        for idx in range(chunk_start_idx, chunk_end_idx):
                            if idx < len(valid_media_with_code):
                                code_orig, media_source, row_num = valid_media_with_code[idx]
                                error_info = _explain_image_error(e, media_source)
                                errors.append({
                                    "code": code_orig,
                                    "reason": error_info['explanation'],
                                    "row_number": row_num
                                })
                                logger.warning(f"Image failed in media group for {code_orig}: {e}")
            
            # ========== 3️⃣ TEXT MESSAGE YUBORISH ==========
            # HAR DOIM oxirida alohida matnli xabar yuborish (xatolar bilan)
            total_found = len(matching_models)
            total_successful = len(image_message_ids)  # Haqiqiy yuborilgan rasmlar soni
            total_errors = len(errors)
            
            # Text xabar tarkibini tayyorlash
            result_text_parts = [f"🔍 {user_query}"]
            
            if total_successful > 0:
                result_text_parts.append(f"\n✅ {total_successful} ta model rasmi yuborildi")
            
            # Agar xatolar bo'lsa, foydalanuvchiga tushunarli xabar
            if total_errors > 0:
                if total_successful == 0:
                    # Hech qanday rasm yuborilmagan - to'liq xato xabari
                    result_text_parts = [
                        "❌ Rasm yuborishda muammo yuz berdi\n\n"
                        f"📦 Model kodi: {user_query}\n\n"
                        "📍 Muammo tafsiloti:\n"
                    ]
                    
                    # Birinchi xatoni batafsil ko'rsatish
                    first_error = errors[0]
                    sheet_name = sheet_service.images_sheet_name or "sheets2"
                    row_info = f"{first_error.get('row_number', '(topilmadi)')}" if first_error.get('row_number') else "(topilmadi)"
                    
                    reason_text = first_error.get('reason', 'Noma\'lum xato')
                    if "Telegram rasm formatini qabul qilmadi" in reason_text:
                        reason_display = "• Telegram rasm formatini qabul qilmadi"
                    elif "Rasm URL mavjud" in reason_text:
                        reason_display = "• Rasm URL ochildi, lekin rasm sifatida olinmadi"
                    elif "juda katta" in reason_text or "noto'g'ri formatda" in reason_text:
                        reason_display = "• Rasm juda katta yoki noto'g'ri formatda"
                    else:
                        reason_display = "• Telegram rasm formatini qabul qilmadi"
                    
                    result_text_parts.append(
                        f"- Sheet: {sheet_name}\n"
                        f"- Qator: {row_info}\n"
                        f"- Sabab:\n"
                        f"  {reason_display}\n\n"
                    )
                    
                    # Qolgan xatolar ro'yxati
                    if len(errors) > 1:
                        result_text_parts.append(f"Qolgan {len(errors) - 1} ta model ham chiqarilmadi.\n\n")
                    
                    result_text_parts.append(
                        "ℹ️ Izoh:\n"
                        "Rasm Google Drive'da mavjud, lekin Telegram serveri uni rasm sifatida qabul qilmadi.\n"
                        "Iltimos, admin bu rasmlarni tekshiradi."
                    )
                else:
                    # Qisman yuborilgan - qisqa xabar
                    result_text_parts.append(f"\n❌ {total_errors} ta model chiqarilmadi")
                    if len(errors) <= 3:
                        for err in errors:
                            result_text_parts.append(f"\n• {err['code']}")
                    else:
                        result_text_parts.append(f"\n• va yana {len(errors) - 3} ta model")
            
            # Agar hech qanday rasm yuborilmagan va xatolar ham yo'q bo'lsa
            if total_successful == 0 and total_errors == 0:
                result_text_parts.append("\n⚠️ Hech qanday rasm yuborilmadi")
            
            if total_successful > 0 or total_errors == 0:
                result_text_parts.append("\nYana kod yuboring — bot ishlashda davom etadi.")
            
            result_text = "".join(result_text_parts)
            
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=result_text,
                reply_markup=similar_models_result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
            
            # Agar kamida bitta xato bo'lsa, asosiy admin'ga xabar yuborish
            if total_errors > 0:
                main_admin_ids = get_all_main_admin_ids()
                if not main_admin_ids:
                    # Agar asosiy admin topilmasa, barcha adminlarga yuborish
                    main_admin_ids = list(set(ADMINS + HELPER_ADMINS))
                
                user_info = message.from_user
                username = user_info.username if user_info and user_info.username else "(username yo'q)"
                sheet_name = sheet_service.images_sheet_name or "sheets2"
                
                # Barcha xatolarni bitta xabarda yuborish
                admin_error_parts = [
                    "🚨 Rasm yuborishda xato\n\n",
                    f"👤 Foydalanuvchi:\n",
                    f"- ID: {chat_id}\n",
                    f"- Username: @{username}\n\n",
                    f"📂 Bo'lim: O'xshash modellar\n",
                    f"🔎 Qidiruv kodi: {user_query}\n\n",
                    f"❌ Xato tafsilotlari:\n"
                ]
                
                # Har bir xatoni alohida ko'rsatish
                for err in errors[:5]:  # Maksimum 5 ta xato
                    row_info = f"{err.get('row_number', '(topilmadi)')}" if err.get('row_number') else "(topilmadi)"
                    reason_tech = err.get('reason', 'Noma\'lum xato')
                    admin_error_parts.append(
                        f"- Sheet: {sheet_name}\n"
                        f"- Qator: {row_info}\n"
                        f"- Model kodi: {err['code']}\n"
                        f"- Sabab: {reason_tech}\n\n"
                    )
                
                if len(errors) > 5:
                    admin_error_parts.append(f"... va yana {len(errors) - 5} ta xato\n\n")
                
                admin_error_parts.append(
                    f"📊 Natija:\n"
                    f"- Yuborildi: {total_successful}\n"
                    f"- Xato: {total_errors}"
                )
                
                admin_error_text = "".join(admin_error_parts)
                
                for admin_id in main_admin_ids:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=admin_error_text
                        )
                    except Exception as admin_err:
                        logger.warning(f"Failed to send error to admin {admin_id}: {admin_err}")
            
            # State ga rasm xabarlari ID larni va text xabar ID sini saqlash (orqaga/asosiy menyu uchun)
            # Muhim: barcha media group message_id larni saqlash kerak
            await state.update_data(
                previous_image_ids=image_message_ids,  # Barcha media group message_ids
                result_message_id=result_msg.message_id  # Natija matn xabari ID si
            )
        
        # ⚠️ O'XSHASH MODELLAR TOPILDI, LEKIN RASMLAR YO'Q (kod bazada bor, lekin rasm yo'q)
        else:
            warning_text = (
                f"⚠️ Bu kod bazada mavjud, lekin rasmi yo'q: {user_code_display}\n"
                "Iltimos, boshqa kod yuboring.\n"
                "Bot ishlashda davom etadi."
            )
            
            # Oldingi natija xabarini o'chirish
            if previous_result_msg_id:
                try:
                    await bot.delete_message(chat_id, previous_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # Faqat 1 ta ogohlantirish xabarini yuborish (tugmalar bilan)
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=warning_text,
                reply_markup=similar_models_result_keyboard()
            )
            _store_result_message(chat_id, result_msg)
            await state.update_data(previous_image_ids=[], result_message_id=result_msg.message_id)
    
    except Exception as exc:
        logger.error(f"Error processing similar models code: {exc}", exc_info=True)
        error_text = (
            f"❌ Bu kod mavjud emas: {user_code_display}\n"
            "Iltimos, kodni tekshirib qayta yuboring.\n"
            "Bot ishlashda davom etadi."
        )
        
        # State dan oldingi rasmlarni olish
        state_data = await state.get_data()
        previous_image_ids = state_data.get("previous_image_ids", [])
        
        # AVVAL: Barcha eski rasmlarni o'chirish
        for img_id in previous_image_ids:
            try:
                await bot.delete_message(chat_id, img_id)
            except Exception:
                pass
        
        # Keyin: Oldingi natija xabarini o'chirish
        previous_result_msg_id = _RESULT_MESSAGES.get(chat_id)
        if previous_result_msg_id:
            try:
                await bot.delete_message(chat_id, previous_result_msg_id)
            except Exception:
                pass
            _RESULT_MESSAGES.pop(chat_id, None)
        
        # Faqat 1 ta xato xabarini yuborish (tugmalar bilan) - oddiy text
        result_msg = await bot.send_message(
            chat_id=chat_id,
            text=error_text,
            reply_markup=similar_models_result_keyboard()
        )
        _store_result_message(chat_id, result_msg)
        await state.update_data(previous_image_ids=[], result_message_id=result_msg.message_id)


@router.callback_query(F.data == "model_images_back")
async def callback_model_images_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Orqaga bosilganda: HAR DOIM faqat 1 bosqich orqaga qaytaradi (edit_message orqali)"""
    chat_id = callback_query.message.chat.id
    current_state = await state.get_state()
    message = callback_query.message
    
    # NATIJA SAHIFASINI ANIQLASH
    # Natija sahifasi quyidagilar bilan aniqlanadi:
    # - Xabar rasmi bo'lishi (photo/caption)
    # - Xabar matni "•" bo'lishi (O'xshash modellar natijasi)
    # - Xato/Ogohlantirish xabarlari (❌, ⚠️ bilan boshlanadi)
    # - "Yana kod yuboring" matni (O'xshash modellar text xabari)
    callback_text = message.text or message.caption or ""
    has_photo = message.photo is not None
    is_dot_message = callback_text.strip() == "•"
    is_error_message = callback_text.startswith("❌") or callback_text.startswith("⚠️")
    is_result_text = "Yana kod yuboring" in callback_text
    
    is_result_page = has_photo or is_dot_message or is_error_message or is_result_text
    
    # ============================================================
    # 0) "O'XSHASH MODELLAR" - XATO XABARIDAN ORQAGA (state: SimilarModelImages, is_error_message)
    # → "Model kodini yuboring" sahifasiga qayt (EDIT)
    # ============================================================
    if is_error_message and current_state == ModelImageStates.SimilarModelImages:
        # QAT'IY QOIDA: Xato xabarini EDIT qilib oldingi bosqichga qaytish
        menu_text = (
            "📸 O'xshash modelllar\n\n"
            "Model kodini yuboring:"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="model_images_back")]]
        )
        
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, message)
            await state.update_data(bot_message_id=message.message_id, previous_image_ids=[], result_message_id=None)
            _RESULT_MESSAGES.pop(chat_id, None)
            await callback_query.answer()
            return
        except Exception:
            # Edit muvaffaqiyatsiz bo'lsa, DELETE + SEND
            try:
                await bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass
            menu_msg = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, menu_msg)
            await state.update_data(bot_message_id=menu_msg.message_id, previous_image_ids=[], result_message_id=None)
            _RESULT_MESSAGES.pop(chat_id, None)
            await callback_query.answer()
            return
    
    # ============================================================
    # 1a) "O'XSHASH MODELLAR" NATIJA SAHIFASIDAN ORQAGA (state: SimilarModelImages)
    # → "Modellar rasmi" menyusiga qayt (DELETE + SEND)
    # ============================================================
    if is_result_page and current_state == ModelImageStates.SimilarModelImages:
        # QAT'IY QOIDA: DELETE barcha rasm xabarlarini va text xabarini, keyin "Modellar rasmi" menyusini yuborish
        state_data = await state.get_data()
        previous_image_ids = state_data.get("previous_image_ids", [])
        result_message_id = state_data.get("result_message_id")
        
        # Barcha rasm xabarlarini o'chirish
        for img_id in previous_image_ids:
            try:
                await bot.delete_message(chat_id, img_id)
            except Exception:
                pass
        
        # Text natija xabarini o'chirish
        if result_message_id:
            try:
                await bot.delete_message(chat_id, result_message_id)
            except Exception:
                pass
        
        # Hozirgi xabarni ham o'chirish (agar rasm bo'lsa)
        if has_photo:
            try:
                await bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass
        
        # "Modellar rasmi" menyusini yuborish
        menu_text = "📷 Modellar rasmi\n\nQuyidagi bo'limlardan birini tanlang:"
        keyboard = menu_keyboard()
        
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        
        # State ni yangilash
        await state.set_state(ModelImageStates.ModelImageMenu)
        _store_menu_message(chat_id, menu_msg)
        await state.update_data(bot_message_id=menu_msg.message_id, previous_image_ids=[], result_message_id=None)
        _RESULT_MESSAGES.pop(chat_id, None)
        await callback_query.answer()
        return
    
    # ============================================================
    # 1b) "BITTA MODEL RASMI" NATIJA SAHIFASIDAN ORQAGA (state: SingleModelImage)
    # → "Model kodini yuboring" sahifasiga qayt (DELETE + SEND)
    # ============================================================
    if is_result_page and current_state == ModelImageStates.SingleModelImage:
        # QAT'IY QOIDA: DELETE hozirgi rasm/text xabarini, keyin "Model kodini yuboring" sahifasini yuborish
        menu_text = "🖼 Bitta model rasmi\n\nModel kodini yuboring:"
        keyboard = back_keyboard()
        
        # Hozirgi natija xabarini o'chirish (rasm yoki text)
        try:
            await bot.delete_message(chat_id, message.message_id)
        except Exception:
            pass
        
        # Result message tracking dan olib tashlash
        _RESULT_MESSAGES.pop(chat_id, None)
        
        # "Model kodini yuboring" sahifasini yuborish
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        
        # State ni yangilash (SingleModelImage saqlanadi)
        _store_menu_message(chat_id, menu_msg)
        await state.update_data(bot_message_id=menu_msg.message_id)
        await callback_query.answer()
        return
    
    # ============================================================
    # 2a) "O'XSHASH MODELLAR" - "MODEL KODINI YUBORING" SAHIFASIDAN ORQAGA (state: SimilarModelImages)
    # → "Modellar rasmi" menyusiga qayt (edit_message)
    # ============================================================
    if current_state == ModelImageStates.SimilarModelImages and not is_result_page:
        # State ni "Modellar rasmi" menyusiga mos qilib o'zgartirish (clear EMAS)
        await state.set_state(ModelImageStates.ModelImageMenu)
        
        # Xato xabarlarni tozalash
        await _delete_error_message(bot, chat_id)
        
        menu_text = "📷 Modellar rasmi\n\nQuyidagi bo'limlardan birini tanlang:"
        keyboard = menu_keyboard()
        
        # edit_message bilan "Modellar rasmi" menyusiga qayt
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, message)
            await state.update_data(bot_message_id=message.message_id)
            await callback_query.answer()
            return
        except Exception:
            # Edit muvaffaqiyatsiz bo'lsa, yangi xabar yuborish
            await _delete_menu_message(bot, chat_id)
            menu_msg = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, menu_msg)
            await state.update_data(bot_message_id=menu_msg.message_id)
            await callback_query.answer()
            return
    
    # ============================================================
    # 2b) "BITTA MODEL RASMI" - "MODEL KODINI YUBORING" SAHIFASIDAN ORQAGA (state: SingleModelImage)
    # → "Modellar rasmi" menyusiga qayt (edit_message)
    # ============================================================
    if current_state == ModelImageStates.SingleModelImage and not is_result_page:
        # State ni "Modellar rasmi" menyusiga mos qilib o'zgartirish (clear EMAS)
        await state.set_state(ModelImageStates.ModelImageMenu)
        
        menu_text = "📷 Modellar rasmi\n\nQuyidagi bo'limlardan birini tanlang:"
        keyboard = menu_keyboard()
        
        # edit_message bilan "Modellar rasmi" menyusiga qayt
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, message)
            await state.update_data(bot_message_id=message.message_id)
            await callback_query.answer()
            return
        except Exception:
            # Edit muvaffaqiyatsiz bo'lsa, yangi xabar yuborish
            await _delete_menu_message(bot, chat_id)
            menu_msg = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, menu_msg)
            await state.update_data(bot_message_id=menu_msg.message_id)
            await callback_query.answer()
            return
    
    # ============================================================
    # 3) "MODELLAR RASMI" MENYUSIDAN ORQAGA (state: ModelImageMenu yoki None)
    # → Asosiy menyuga qayt (HAR DOIM EDIT orqali)
    # ============================================================
    if current_state == ModelImageStates.ModelImageMenu or current_state is None:
        # State ni tozalash
        await state.clear()
        
        # Asosiy menyu keyboard va matn
        from handlers.start import make_main_menu_keyboard
        from services.message_utils import store_main_menu_message
        user_id = callback_query.from_user.id
        keyboard = make_main_menu_keyboard(user_id)
        menu_text = (
            "Assalomu alaykum! TIZIMGA  xush kelibsiz.\n\n"
            "Quyidagi menyulardan birini tanlang:"
        )
        
        # HAR DOIM EDIT orqali hozirgi xabarni o'zgartirish
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message.message_id,
                text=menu_text,
                reply_markup=keyboard
            )
            # Asosiy menyu xabarini track qilish
            store_main_menu_message(chat_id, message.message_id)
            await callback_query.answer()
            return
        except Exception:
            # Edit muvaffaqiyatsiz bo'lsa, yangi menyu yuborish
            try:
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=menu_text,
                    reply_markup=keyboard
                )
                store_main_menu_message(chat_id, sent.message_id)
            except Exception as e:
                logger.error(f"Failed to send main menu to chat {chat_id}: {e}")
            await callback_query.answer()
            return


@router.callback_query(F.data == "menu_main")
async def callback_menu_main_from_similar_models(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    "O'xshash modellar" natijalaridan "Asosiy menyu" tugmasi bosilganda.
    Barcha rasm natijalarini o'chirib, asosiy menyuga qaytaradi.
    Faqat SimilarModelImages state bo'lganda ishlaydi.
    """
    current_state = await state.get_state()
    chat_id = callback_query.message.chat.id
    
    # "O'xshash modellar" natijalaridan kelayotgan bo'lsa, barcha rasmlarni o'chirish
    if current_state == ModelImageStates.SimilarModelImages:
        state_data = await state.get_data()
        previous_image_ids = state_data.get("previous_image_ids", [])
        result_message_id = state_data.get("result_message_id")
        
        logger.info(f"Deleting images for chat {chat_id}: {len(previous_image_ids)} images, result_msg_id={result_message_id}")
        
        # 1. Barcha rasm xabarlarini o'chirish (media group xabarlari)
        # Try/except bilan - agar xato bo'lsa ham davom etadi
        for img_id in previous_image_ids:
            try:
                await bot.delete_message(chat_id, img_id)
            except TelegramBadRequest:
                # Message not found / can't delete - ignore va davom et
                pass
            except Exception as e:
                # Boshqa xatolar - log qil, lekin davom et
                logger.warning(f"Failed to delete image {img_id}: {e}")
        
        # 2. Text natija xabarini o'chirish (tugmalar bilan birga)
        if result_message_id:
            try:
                await bot.delete_message(chat_id, result_message_id)
            except TelegramBadRequest:
                # Message not found / can't delete - ignore va davom et
                pass
            except Exception as e:
                # Boshqa xatolar - log qil, lekin davom et
                logger.warning(f"Failed to delete result message {result_message_id}: {e}")
        
        # 3. Hozirgi xabarni ham o'chirish (tugmalar bilan birga)
        # Bu natija ostidagi "🏠 Asosiy menyu" tugmasi bo'lgan xabar
        try:
            await bot.delete_message(chat_id, callback_query.message.message_id)
        except TelegramBadRequest:
            # Message not found / can't delete - ignore va davom et
            pass
        except Exception as e:
            # Boshqa xatolar - log qil, lekin davom et
            logger.warning(f"Failed to delete current message: {e}")
        
        # 4. State va tracking ni tozalash
        await state.clear()
        _RESULT_MESSAGES.pop(chat_id, None)
        
        # 5. Asosiy menyuni yuborish (delete qilib keyin yangi yuboriladi, edit emas)
        from handlers.start import make_main_menu_keyboard, store_main_menu_message
        user_id = callback_query.from_user.id
        keyboard = make_main_menu_keyboard(user_id)
        menu_text = (
            "Assalomu alaykum! TIZIMGA  xush kelibsiz.\n\n"
            "Quyidagi menyulardan birini tanlang:"
        )
        
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            store_main_menu_message(chat_id, sent.message_id)
        except Exception as e:
            logger.error(f"Failed to send main menu to chat {chat_id}: {e}")
        
        await callback_query.answer()
        return
    
    # Agar SimilarModelImages state bo'lmasa, boshqa handler (start.py dagi) ishlashi kerak
    # Bu handler faqat "O'xshash modellar" uchun, boshqa holatlar uchun start.py handler ishlaydi
    # Shuning uchun bu yerda hech narsa qilmaymiz - boshqa handler ishlashi uchun
    pass
