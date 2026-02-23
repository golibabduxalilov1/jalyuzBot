import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from typing import Optional

from services.google_sheet import GoogleSheetService, get_file_id_for_code, get_image_url_for_code
from services.product_utils import normalize_code
from services.message_utils import is_result_message_text, delete_main_menu_message, delete_message_safe, store_content_message

try:
    from config import DEFAULT_IMAGE_URL
except ImportError:
    DEFAULT_IMAGE_URL = ""

router = Router()
logger = logging.getLogger(__name__)

# In-memory tracking (chat_id -> message_id)
_MENU_MESSAGES: dict[int, int] = {}      # faqat hozirgi "menu" xabarining id sini saqlaydi
_RESULT_MESSAGES: dict[int, int] = {}    # oxirgi natija xabarini saqlaydi (saqlansin)
_ERROR_MESSAGES: dict[int, int] = {}     # "Ma'lumot topilmadi" xabarlarini saqlaydi

from config import HELP_PHONE
from services.settings import get_error_message, get_contact_phone, is_user_blocked, increment_user_request_count

def get_error_message_with_request(error_request: str = "") -> str:
    """
    Get error message from settings and add user request if provided.
    
    Args:
        error_request: User's request text or button (optional)
    
    Returns:
        Formatted error message
    """
    contact_phone = get_contact_phone()
    error_text = (
        f"❌ Bu mahsulot topilmadi yoki kodni noto'g'ri yozdingiz.\n\n"
        f"📞 Mahsulot omborda qolgan-qolmaganini bilish uchun:\n"
        f"{contact_phone}"
    )
    if error_request:
        error_text = f"{error_text}\n\n🔎 Kiritilgan so'rov: {error_request}"
    return error_text


async def _edit_menu_message(bot: Bot, chat_id: int, new_text: str, new_keyboard: InlineKeyboardMarkup):
    """
    Menu xabarini edit qilish (afzal usul).
    Agar edit muvaffaqiyatsiz bo'lsa, delete qilish (oxirgi chora).
    """
    msg_id = _MENU_MESSAGES.get(chat_id)
    if not msg_id:
        return False
    
    try:
        # Avval edit qilishga urinish (afzal usul)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=new_text,
            reply_markup=new_keyboard
        )
        return True
    except TelegramBadRequest:
        # Edit muvaffaqiyatsiz bo'lsa, delete qilish (oxirgi chora)
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        _MENU_MESSAGES.pop(chat_id, None)
        return False
    except Exception as e:
        logger.warning(f"Edit menu message failed: {e}")
        # Edit muvaffaqiyatsiz bo'lsa, delete qilish (oxirgi chora)
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        _MENU_MESSAGES.pop(chat_id, None)
        return False


async def _delete_menu_message(bot: Bot, chat_id: int):
    """Agar chat uchun oldingi menu message mavjud bo'lsa, uni tozalaydi (o'chiradi yoki reply_markupni olib qo'yadi)."""
    msg_id = _MENU_MESSAGES.get(chat_id)
    if not msg_id:
        return
    
    try:
        await bot.delete_message(chat_id, msg_id)
    except TelegramBadRequest:
        # Agar delete bo'lmasa — reply_markup ni olib tashlashga urinish
        try:
            await bot.edit_message_reply_markup(chat_id, msg_id, reply_markup=None)
        except TelegramBadRequest:
            pass
    except Exception as e:
        logger.warning(f"Delete menu message failed: {e}")
    finally:
        _MENU_MESSAGES.pop(chat_id, None)


def _store_menu_message(chat_id: int, message_obj):
    """Yangi menu xabarini store qiling."""
    if message_obj and hasattr(message_obj, "message_id"):
        _MENU_MESSAGES[chat_id] = message_obj.message_id


def _store_result_message(chat_id: int, message_obj):
    """Oxirgi natija message id sini saqlash (natija saqlansin)."""
    if message_obj and hasattr(message_obj, "message_id"):
        _RESULT_MESSAGES[chat_id] = message_obj.message_id


def _create_msg_obj(message_id: int):
    """Helper function to create a message-like object with message_id attribute."""
    class MsgObj:
        def __init__(self, msg_id):
            self.message_id = msg_id
    return MsgObj(message_id)


def _store_error_message(chat_id: int, message_obj):
    """'Ma'lumot topilmadi' xabarini saqlash."""
    if message_obj and hasattr(message_obj, "message_id"):
        _ERROR_MESSAGES[chat_id] = message_obj.message_id


async def _delete_error_message(bot: Bot, chat_id: int):
    """'Ma'lumot topilmadi' xabarini o'chirish."""
    error_msg_id = _ERROR_MESSAGES.get(chat_id)
    if error_msg_id:
        try:
            await bot.delete_message(chat_id, error_msg_id)
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning(f"Delete error message failed: {e}")
        finally:
            _ERROR_MESSAGES.pop(chat_id, None)


def _clear_error_message_tracking(chat_id: int):
    """Xato xabarini tracking dan o'chirish (xabarni o'chirmasdan)."""
    _ERROR_MESSAGES.pop(chat_id, None)


def make_back_keyboard() -> InlineKeyboardMarkup:
    """Create keyboard with back button"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="menu_astatka"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Asosiy menyu",
                    callback_data="menu_main"
                )
            ]
        ]
    )


def make_astatka_menu_keyboard() -> InlineKeyboardMarkup:
    """Create Astatka main menu keyboard"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Umumiy qoldiqdan bilish",
                    callback_data="astatka_general"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📁 Kolleksiya bo'yicha bilish",
                    callback_data="astatka_collection"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔍 O'xshash kodlar bo'yicha qoldiqni bilish",
                    callback_data="astatka_similar_codes"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="astatka_back_main"
                )
            ]
        ]
    )


def make_result_keyboard_general() -> InlineKeyboardMarkup:
    """Create keyboard for general result"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="astatka_general"
                )
            ]
        ]
    )


class AstatkaStates(StatesGroup):
    waiting_for_code_general = State()  # Umumiy qoldiqdan bilish
    waiting_for_code_similar = State()  # O'xshash kodlar bo'yicha qoldiqni bilish


@router.callback_query(F.data == "menu_astatka")
async def callback_astatka_menu(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle Astatka menu selection - UI menu doim edit qilinadi"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.clear()
    chat_id = callback_query.message.chat.id
    
    keyboard = make_astatka_menu_keyboard()
    menu_text = (
        "🟩 <b>Astatka</b>\n\n"
        "Qanday usulda qidirmoqchisiz?"
    )
    
    # UI menu doim edit qilinadi (yangi xabar yuborilmaydi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
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
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)


@router.callback_query(F.data == "astatka_general")
async def callback_astatka_general(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start general astatka mode yoki natijadan ORQAGA - HAR DOIM edit_message orqali"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.set_state(AstatkaStates.waiting_for_code_general)
    chat_id = callback_query.message.chat.id
    
    # "Ma'lumot topilmadi" xabarini o'chirish
    await _delete_error_message(bot, chat_id)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="menu_astatka"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Asosiy menyu",
                    callback_data="menu_main"
                )
            ]
        ]
    )

    menu_text = (
        "📊 <b>Umumiy qoldiqdan bilish</b>\n\n"
        "Mahsulot kodini yuboring:"
    )
    
    # Hozirgi xabarni tekshirish - natija sahifasidan kelayotgan bo'lsa ham, EDIT qilish
    callback_text = callback_query.message.text or callback_query.message.caption or ""
    has_photo = callback_query.message.photo is not None
    from services.message_utils import is_result_message_text
    is_result_page = has_photo or is_result_message_text(callback_text)
    
    # AGAR NATIJA SAHIFASIDAN KELAYOTGAN BO'LSA:
    # Natija xabarini EDIT qilish (rasm xabarni matn xabarga o'zgartirish qiyin, shuning uchun o'chirib yangi yuborish)
    if is_result_page:
        # Natija xabari CONTENT sifatida belgilangan, lekin navigatsiya uchun o'zgartirish kerak
        # Rasm xabarni matn xabarga o'zgartirib bo'lmaydi, shuning uchun o'chirib yangi yuborish
        try:
            # Oldingi natija xabarini o'chirish (navigatsiya uchun)
            await bot.delete_message(chat_id, callback_query.message.message_id)
            # Natija xabarini tracking dan olib tashlash
            if chat_id in _RESULT_MESSAGES and _RESULT_MESSAGES[chat_id] == callback_query.message.message_id:
                _RESULT_MESSAGES.pop(chat_id, None)
        except Exception as e:
            logger.warning(f"Error deleting result message: {e}")
        
        # Eski menu xabarlarni tozalash
        await _delete_menu_message(bot, chat_id)
        
        # Yangi "Umumiy qoldiqdan bilish" sahifasini yuborish
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)
        return
    
    # AGAR MENYU SAHIFASIDAN KELAYOTGAN BO'LSA:
    # UI menu doim edit qilinadi (yangi xabar yuborilmaydi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
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
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)


@router.message(AstatkaStates.waiting_for_code_general)
async def process_product_code(message: Message, state: FSMContext, bot: Bot):
    """Process product code and fetch data from Google Sheets"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # ============================================================
    # KEYINGI HAR QANDAY KODDA: Avval xato xabarni o'chirish
    # (Hamma narsadan oldin, hatto kod tekshirishdan oldin ham)
    # ============================================================
    await _delete_error_message(bot, chat_id)
    
    # Check if user is blocked
    if is_user_blocked(user_id):
        await message.answer("❌ Siz bloklangan foydalanuvchisiz.")
        return
    
    # Check user limit
    if not increment_user_request_count(user_id):
        await message.answer("❌ Kunlik so'rovlar limitiga yetdingiz. Ertaga qayta urinib ko'ring.")
        return
    
    # Foydalanuvchi kiritgan kodni normalizatsiya qilish
    user_raw = message.text
    user_code = normalize_code(user_raw or "")
    
    if not user_code:
        # Foydalanuvchi xabarini o'chirish
        try:
            await message.delete()
        except Exception:
            pass
        
        # Xato xabarini yuborish va saqlash
        error_msg = await bot.send_message(chat_id, "❌ Iltimos, to'g'ri mahsulot kodini yuboring.")
        _store_error_message(chat_id, error_msg)
        return

    try:
        # Foydalanuvchi xabarini o'chirish
        try:
            await message.delete()
        except Exception:
            pass
        
        # Oldingi menu xabarlarini tozalash (faqat menu)
        await _delete_menu_message(bot, chat_id)
        
        # Xato xabari allaqachon funksiya boshida o'chirilgan
        # (Har safar kod yuborilganda avval xato xabarni o'chirish)
        
        # Fetch data from Google Sheets
        sheet_service = GoogleSheetService()
        product_data = await sheet_service.get_product_data(user_raw)
        
        # Record statistics
        from services.stats import record_request
        matched_rows = product_data.get("matched_rows", []) if product_data else []
        matched_count = len(matched_rows) if matched_rows else 0
        record_request(
            chat_id, 
            user_code, 
            found=bool(product_data and matched_rows),
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            matched_count=matched_count
        )

        # CONTENT natijasi uchun reply_markup (tugmalar)
        result_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="astatka_general"
                    ),
                    InlineKeyboardButton(
                        text="🏠 Asosiy menyu",
                        callback_data="menu_main"
                    )
                ]
            ]
        )

        # Oldingi natija xabarini olish
        previous_result_msg_id = _RESULT_MESSAGES.get(chat_id)

        if not product_data:
            # ============================================================
            # KOD XATO BO'LSA: Xato xabarni ALOHIDA yuborish
            # ============================================================
            error_text = get_error_message_with_request(user_raw)
            
            # Xato xabarni alohida send_message() bilan yuborish (natija xabariga tegmasdan)
            error_msg = await bot.send_message(
                chat_id,
                error_text,
                reply_markup=result_keyboard
            )
            
            # Xato xabarini saqlash (keyingi kod yuborilganda o'chirish uchun)
            _store_error_message(chat_id, error_msg)
            
            # Natija xabariga TEGMA (o'zgartirish yoki o'chirish yo'q)
            
            await state.set_state(AstatkaStates.waiting_for_code_general)
            return

        matched_rows = product_data.get("matched_rows", [])
        if not matched_rows:
            # ============================================================
            # KOD XATO BO'LSA: Xato xabarni ALOHIDA yuborish
            # ============================================================
            error_text = get_error_message_with_request(user_raw)
            
            # Xato xabarni alohida send_message() bilan yuborish (natija xabariga tegmasdan)
            error_msg = await bot.send_message(
                chat_id,
                error_text,
                reply_markup=result_keyboard
            )
            
            # Xato xabarini saqlash (keyingi kod yuborilganda o'chirish uchun)
            _store_error_message(chat_id, error_msg)
            
            # Natija xabariga TEGMA (o'zgartirish yoki o'chirish yo'q)
            
            await state.set_state(AstatkaStates.waiting_for_code_general)
            return

        # Rasm URL ni olish - matched_rows dan (get_product_data() allaqachon image_url qo'shgan)
        # Birinchi matched_row dan image_url ni olish
        image_url = ""
        if matched_rows and len(matched_rows) > 0:
            # Birinchi qatordan image_url ni olish
            image_url = matched_rows[0].get("image_url", "")
            # Agar bo'sh bo'lsa, boshqa qatorlardan qidirish
            if not image_url:
                for row in matched_rows:
                    img = row.get("image_url", "")
                    if img and img.strip():
                        image_url = img
                        break
        
        # Agar hali ham image_url bo'sh bo'lsa, DEFAULT_IMAGE_URL ishlatish
        if not image_url or not image_url.strip():
            image_url = DEFAULT_IMAGE_URL
        
        # RASM YUBORISH PRIORITETI: 1) file_id, 2) URL, 3) Rasm topilmadi
        image_file_id = None
        if user_code:
            # 1. Avval file_id ni tekshirish (eng tez)
            image_file_id = get_file_id_for_code(user_code)
        
        # 2. Agar file_id yo'q bo'lsa, URL ni tekshirish (fallback)
        if not image_file_id:
            if not image_url or not image_url.strip():
                # URL ni qidirish
                image_url = get_image_url_for_code(user_code) if user_code else None
                if not image_url and DEFAULT_IMAGE_URL:
                    image_url = DEFAULT_IMAGE_URL
            
            # Google Drive linkini to'g'ri formatga o'tkazish
            if image_url and image_url.strip():
                image_url = sheet_service._convert_google_drive_link(image_url)
            else:
                image_url = None

        # Model nomini olish - kod bo'yicha sheets3 dan qidirish
        model_name = ""
        try:
            prices = await sheet_service.read_prices_from_sheets3()
            # Birinchi topilgan kod bo'yicha model nomini olish
            for price in prices:
                price_code = price.get("code", "")
                price_code_normalized = normalize_code(str(price_code)) if price_code else ""
                if price_code_normalized == user_code:
                    model_name = price.get("model_name", "").strip()
                    break
        except Exception:
            pass
        
        # Maxsus modellarni aniqlash
        model_name_upper = model_name.upper().strip() if model_name else ""
        model_name_lower = model_name.lower().strip() if model_name else ""
        model_name_original = model_name.strip() if model_name else ""
        
        is_rollo_shtor = (
            "ROLLO SHTOR" in model_name_upper or 
            "rollo shtor" in model_name_lower or 
            "ролло штор" in model_name_lower or
            ("ROLLO" in model_name_upper and "SHTOR" in model_name_upper)
        )
        
        is_dikey = (
            "ДИКИЙ" in model_name_upper or
            "Дикий" in model_name_original or
            "дикий" in model_name_lower or
            "ДИКЕЙ" in model_name_upper or
            "ДИКЕЙ" in model_name_original or
            "dikey" in model_name_lower or 
            "дикей" in model_name_lower or
            "дикей" in model_name_original
        )
        
        is_plise = (
            "PLISE" in model_name_upper or 
            "plise" in model_name_lower or 
            "плисе" in model_name_lower or
            "плисе" in model_name_original
        )
        
        # Qoldiqlarni guruhlash - Asosiy va Mini bo'yicha
        asosiy_qoldiq = []
        mini_qoldiq = []
        kasetniy_qoldiq = []
        
        for row in matched_rows:
            code_disp = row.get("code_original") or row.get("code") or user_code
            qty = row.get("quantity") or ""
            qty_str = str(qty) if qty is not None else ""
            
            # Collection nomidan Asosiy/Mini/Kasetniy ni aniqlash
            row_collection = str(row.get("collection", "")).strip()
            
            # Asosiy/Mini/Kasetniy ni aniqlash (collection nomidan)
            if "asosiy" in row_collection.lower() or "основной" in row_collection.lower():
                asosiy_qoldiq.append((code_disp, qty_str))
            elif "mini" in row_collection.lower() or "мини" in row_collection.lower():
                mini_qoldiq.append((code_disp, qty_str))
            elif "kasetniy" in row_collection.lower() or "кассетный" in row_collection.lower() or "kaset" in row_collection.lower():
                kasetniy_qoldiq.append((code_disp, qty_str))
            else:
                # Agar aniqlanmagan bo'lsa, birinchi bo'limga qo'shish (Asosiy)
                asosiy_qoldiq.append((code_disp, qty_str))
        
        collection = product_data.get("collection") or "N/A"
        date = product_data.get("date") or "N/A"
        original_code = product_data.get("original_code") or product_data.get("code") or user_code

        # Header va qatorlar
        header = f"🔹 Model nomi: {model_name if model_name else original_code}\n\n"
        header += f"🔸 Model kodi: {original_code}\n"
        
        # Maxsus modellar uchun kolleksiya ko'rsatilmaydi (faqat Дикий uchun)
        if not is_dikey:
            header += f"📂 Kolleksiya: {collection}\n"
        header += f"📅 Sana: {date}\n\n"
        header += "Qoldiq:\n"
        
        # ROLLO SHTOR uchun maxsus format
        if is_rollo_shtor:
            if asosiy_qoldiq:
                lines_list = [f"• 50%lik: {qty_str}" for _, qty_str in asosiy_qoldiq]
                header += "\n".join(lines_list) + "\n"
            if mini_qoldiq:
                lines_list = [f"• 100%lik: {qty_str}" for _, qty_str in mini_qoldiq]
                header += "\n".join(lines_list) + "\n"
        
        # Дикий uchun maxsus format
        elif is_dikey:
            if asosiy_qoldiq:
                lines_list = [f"• To'ldi uzi bo'lsa: {qty_str}" for _, qty_str in asosiy_qoldiq]
                header += "\n".join(lines_list) + "\n"
            if mini_qoldiq:
                lines_list = [f"• Yoniga porter bo'lsa: {qty_str}" for _, qty_str in mini_qoldiq]
                header += "\n".join(lines_list) + "\n"
        
        # PLISE uchun maxsus format
        elif is_plise:
            if asosiy_qoldiq:
                lines_list = [f"• 0,50 kv: {qty_str}" for _, qty_str in asosiy_qoldiq]
                header += "\n".join(lines_list) + "\n"
            if mini_qoldiq:
                lines_list = [f"• 1,00 kv: {qty_str}" for _, qty_str in mini_qoldiq]
                header += "\n".join(lines_list) + "\n"
        
        # Boshqa modellar uchun standart format
        else:
            if asosiy_qoldiq:
                header += "• Asosiy:\n"
                for code_disp, qty_str in asosiy_qoldiq:
                    header += f"  - {code_disp} — {qty_str}\n"
            if mini_qoldiq:
                header += "• Mini:\n"
                for code_disp, qty_str in mini_qoldiq:
                    header += f"  - {code_disp} — {qty_str}\n"
            if kasetniy_qoldiq:
                header += "• Kasetniy:\n"
                for code_disp, qty_str in kasetniy_qoldiq:
                    header += f"  - {code_disp} — {qty_str}\n"
        
        text = header

        # CONTENT natijasi uchun reply_markup (tugmalar)
        result_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="astatka_general"
                    ),
                    InlineKeyboardButton(
                        text="🏠 Asosiy menyu",
                        callback_data="menu_main"
                    )
                ]
            ]
        )

        # Oldingi natija xabarini olish (agar mavjud bo'lsa)
        previous_result_msg_id = _RESULT_MESSAGES.get(chat_id)
        
        # Xato xabari allaqachon funksiya boshida o'chirilgan
        # (Har safar kod yuborilganda avval xato xabarni o'chirish)

        # Rasm bilan yoki rasmisiz natija yuborish (rasm faqat kod bo'yicha qidirilganda chiqadi)
        # Natija CONTENT sifatida yuboriladi (reply_markup bilan)
        # PRIORITET: 1) file_id, 2) URL, 3) Rasm topilmadi
        if image_file_id:
            # 1. file_id mavjud - eng tez variant
            has_image = True
        elif image_url and image_url.strip():
            # 2. URL mavjud - fallback
            has_image = True
        else:
            # 3. Rasm topilmadi
            has_image = False
        
        if has_image:
            # Rasm bilan natija
            try:
                # Agar oldingi natija xabari bo'lsa, uni EDIT qilish
                if previous_result_msg_id:
                    try:
                        # Rasm xabarni yangilash (edit_message_media yoki edit_message_caption)
                        from aiogram.types import InputMediaPhoto
                        if isinstance(image_url, bytes):
                            # Bytes uchun media edit qilish qiyin, shuning uchun o'chirib yangi yuborish
                            try:
                                await bot.delete_message(chat_id, previous_result_msg_id)
                            except Exception:
                                pass
                            previous_result_msg_id = None
                            _RESULT_MESSAGES.pop(chat_id, None)
                        else:
                            # file_id yoki URL uchun media edit qilish
                            if image_file_id:
                                media = InputMediaPhoto(media=image_file_id, caption=text)
                            else:
                                media = InputMediaPhoto(media=image_url, caption=text)
                            try:
                                await bot.edit_message_media(
                                    chat_id=chat_id,
                                    message_id=previous_result_msg_id,
                                    media=media,
                                    reply_markup=result_keyboard
                                )
                                # Astatka natijasini CONTENT sifatida belgilash (hech qachon o'chirilmaydi)
                                store_content_message(chat_id, previous_result_msg_id)
                                await state.set_state(AstatkaStates.waiting_for_code_general)
                                return
                            except Exception as edit_error:
                                logger.warning(f"Error editing photo message: {edit_error}, trying to delete and send new")
                                # Edit muvaffaqiyatsiz bo'lsa, o'chirib yangi yuborish
                                try:
                                    await bot.delete_message(chat_id, previous_result_msg_id)
                                except Exception:
                                    pass
                                previous_result_msg_id = None
                                _RESULT_MESSAGES.pop(chat_id, None)
                    except Exception as e:
                        logger.warning(f"Error processing previous result message: {e}")
                        previous_result_msg_id = None
                        _RESULT_MESSAGES.pop(chat_id, None)
                
                # Agar oldingi natija yo'q bo'lsa yoki o'chirilgan bo'lsa, yangi xabar yuborish
                if not previous_result_msg_id:
                    # file_id yoki URL ishlatish
                    if image_file_id:
                        # file_id ishlatish (eng tez)
                        result_msg = await bot.send_photo(
                            chat_id=chat_id,
                            photo=image_file_id,
                            caption=text,
                            reply_markup=result_keyboard
                        )
                    elif isinstance(image_url, bytes):
                        # Bytes uchun BufferedInputFile
                        photo = BufferedInputFile(image_url, filename="image.jpg")
                        result_msg = await bot.send_photo(
                            chat_id=chat_id,
                            photo=photo,
                            caption=text,
                            reply_markup=result_keyboard
                        )
                    else:
                        # URL ishlatish
                        result_msg = await bot.send_photo(
                            chat_id=chat_id,
                            photo=image_url,
                            caption=text,
                            reply_markup=result_keyboard
                        )
                else:
                    # previous_result_msg_id bor, lekin edit qilindi, shuning uchun result_msg ni None qilish
                    result_msg = None
            except Exception as photo_error:
                logger.error(f"Error sending photo: {photo_error}, sending text instead")
                # Rasm yuborishda xatolik bo'lsa, oldingi xabarni o'chirib matn yuborish
                if previous_result_msg_id:
                    try:
                        await bot.delete_message(chat_id, previous_result_msg_id)
                    except Exception:
                        pass
                    previous_result_msg_id = None
                    _RESULT_MESSAGES.pop(chat_id, None)
                
                # Matn xabar yuborish
                result_msg = await bot.send_message(
                    chat_id,
                    text,
                    reply_markup=result_keyboard
                )
        else:
            # Rasmisiz natija (faqat matn)
            if previous_result_msg_id:
                try:
                    # Oldingi xabarni matn sifatida EDIT qilish
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=previous_result_msg_id,
                            text=text,
                            reply_markup=result_keyboard
                        )
                        # Astatka natijasini CONTENT sifatida belgilash (hech qachon o'chirilmaydi)
                        store_content_message(chat_id, previous_result_msg_id)
                        await state.set_state(AstatkaStates.waiting_for_code_general)
                        return
                    except Exception as edit_error:
                        logger.warning(f"Error editing text message: {edit_error}")
                        # Edit muvaffaqiyatsiz bo'lsa, oldingi xabarni o'chirib yangi yuborish
                        # Lekin rasm xabar bo'lsa, o'chirish kerak
                        try:
                            await bot.delete_message(chat_id, previous_result_msg_id)
                        except Exception:
                            pass
                        previous_result_msg_id = None
                        _RESULT_MESSAGES.pop(chat_id, None)
                except Exception as e:
                    logger.warning(f"Error processing previous result message: {e}")
                    previous_result_msg_id = None
                    _RESULT_MESSAGES.pop(chat_id, None)
            
            # Agar oldingi natija yo'q bo'lsa yoki o'chirilgan bo'lsa, yangi xabar yuborish
            if not previous_result_msg_id:
                result_msg = await bot.send_message(
                    chat_id,
                    text,
                    reply_markup=result_keyboard
                )
            else:
                # previous_result_msg_id bor, lekin edit qilindi, shuning uchun result_msg ni None qilish
                result_msg = None
        
        # Agar yangi xabar yuborilgan bo'lsa, uni CONTENT sifatida belgilash va saqlash
        if result_msg:
            store_content_message(chat_id, result_msg.message_id)
            _store_result_message(chat_id, result_msg)
        
        await state.set_state(AstatkaStates.waiting_for_code_general)

    except Exception as e:
        logger.error(f"Error processing product code: {e}", exc_info=True)
        result_msg = await bot.send_message(
            chat_id,
            f"❌ Xatolik yuz berdi: {str(e)}\n\nIltimos, qayta urinib ko'ring."
        )
        _store_result_message(chat_id, result_msg)
        await state.set_state(AstatkaStates.waiting_for_code_general)


def make_collection_keyboard() -> InlineKeyboardMarkup:
    """Create collection selection keyboard"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="0-start",
                    callback_data="collection_0-start"
                )
            ],
            [
                InlineKeyboardButton(
                    text="1-stage",
                    callback_data="collection_1-stage"
                )
            ],
            [
                InlineKeyboardButton(
                    text="2-middle",
                    callback_data="collection_2-middle"
                )
            ],
            [
                InlineKeyboardButton(
                    text="3-optimal",
                    callback_data="collection_3-optimal"
                )
            ],
            [
                InlineKeyboardButton(
                    text="4-top",
                    callback_data="collection_4-top"
                )
            ],
            [
                InlineKeyboardButton(
                    text="5-perfect",
                    callback_data="collection_5-perfect"
                )
            ],
            [
                InlineKeyboardButton(
                    text="6-exclusive",
                    callback_data="collection_6-exclusive"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Плиссе 1-коллекция",
                    callback_data="collection_Плиссе 1-коллекция"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Плиссе 2-коллекция",
                    callback_data="collection_Плиссе 2-коллекция"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Плиссе 3-коллекция",
                    callback_data="collection_Плиссе 3-коллекция"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Плиссе 4-коллекция",
                    callback_data="collection_Плиссе 4-коллекция"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Турк лента 1 (ески турклар)",
                    callback_data="collection_Турк лента 1 (ески турклар)"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Турк лента 2 (йанги турклар)",
                    callback_data="collection_Турк лента 2 (йанги турклар)"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Ролло штор",
                    callback_data="collection_Ролло штор"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Дикей",
                    callback_data="collection_Дикей"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="menu_astatka"
                )
            ]
        ]
    )


@router.callback_query(F.data == "astatka_collection")
async def callback_astatka_collection(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start collection-based astatka mode - UI menu doim edit qilinadi"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.clear()
    chat_id = callback_query.message.chat.id
    
    # "Ma'lumot topilmadi" xabarini o'chirish
    await _delete_error_message(bot, chat_id)
    
    keyboard = make_collection_keyboard()
    menu_text = (
        "📁 <b>Kolleksiya bo'yicha bilish</b>\n\n"
        "Kolleksiyani tanlang:"
    )
    
    # UI menu doim edit qilinadi (yangi xabar yuborilmaydi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
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
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)


@router.callback_query(F.data.startswith("collection_"))
async def callback_collection_selected(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle collection selection - HAR DOIM EDIT orqali natija chiqaradi"""
    collection_key = callback_query.data.replace("collection_", "")
    chat_id = callback_query.message.chat.id
    user_id = callback_query.from_user.id
    
    # Check if user is blocked
    if is_user_blocked(user_id):
        await callback_query.answer("❌ Siz bloklangan foydalanuvchisiz.", show_alert=True)
        return
    
    # Check user limit
    if not increment_user_request_count(user_id):
        await callback_query.answer("❌ Kunlik so'rovlar limitiga yetdingiz. Ertaga qayta urinib ko'ring.", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing (CRITICAL FIX!)
    await callback_query.answer()
    
    # Xato xabarini o'chirish (agar mavjud bo'lsa)
    await _delete_error_message(bot, chat_id)
    
    # Oldingi natija xabarini olish (EDIT uchun)
    previous_result_msg_id = _RESULT_MESSAGES.get(chat_id)
    current_msg_id = callback_query.message.message_id
    
    # Hozirgi xabarni tekshirish - natija yoki menyu
    callback_text = callback_query.message.text or callback_query.message.caption or ""
    from services.message_utils import is_result_message_text
    has_photo = callback_query.message.photo is not None
    is_current_result = has_photo or is_result_message_text(callback_text)
    
    # Oldingi menu xabarlarini tozalash (faqat menu)
    await _delete_menu_message(bot, chat_id)
    
    try:
        # Kolleksiyani index dan tezkor olish
        from services.google_sheet import CACHE
        
        # Tanlangan kolleksiyaga tegishli qatorlarni topish (yumshoq qidiruv)
        matched = []
        collection_index = CACHE.get("collection_index", {})
        
        # Avval to'g'ri kolleksiyani topish
        if collection_key in collection_index:
            matched = collection_index[collection_key].copy()
        else:
            # Agar to'g'ri topilmasa, yumshoq qidiruv (fuzzy match)
            for coll_name, products in collection_index.items():
                if collections_match(coll_name, collection_key):
                    matched.extend(products)
                    break
        
        # Record collection selection statistics (before checking if matched)
        from services.stats import record_collection_selection
        matched_count = len(matched) if matched else 0
        record_collection_selection(
            user_id,
            collection_key,
            found=bool(matched),
            username=callback_query.from_user.username,
            first_name=callback_query.from_user.first_name,
            matched_count=matched_count
        )
        
        # CONTENT natijasi uchun reply_markup (tugmalar)
        result_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="astatka_collection_back"
                    ),
                    InlineKeyboardButton(
                        text="🏠 Asosiy menyu",
                        callback_data="menu_main"
                    )
                ]
            ]
        )

        if not matched:
            # Xato holati - xato xabari alohida sendMessage bilan (qoidaga binoan)
            error_text = get_error_message_with_request(collection_key)
            error_msg = await bot.send_message(
                chat_id,
                error_text
            )
            _store_error_message(chat_id, error_msg)
            
            # Asosiy natija xabarini EDIT qilish - xato natijasini ko'rsatish
            # Bu keyingi harakatda EDIT bo'lib davom etishi uchun kerak
            target_edit_id = None
            if is_current_result:
                target_edit_id = current_msg_id
            elif previous_result_msg_id:
                target_edit_id = previous_result_msg_id
            
            # Xato natijasini ko'rsatish (asosiy natija xabari)
            error_result_text = f"📁 Kolleksiya: {collection_key}\n\n❌ Mahsulot topilmadi."
            
            if target_edit_id:
                try:
                    if has_photo and target_edit_id == current_msg_id:
                        # Rasm xabarni matn xabarga o'zgartirish - o'chirib yangi yuborish kerak
                        try:
                            await bot.delete_message(chat_id, target_edit_id)
                            result_msg = await bot.send_message(
                                chat_id,
                                error_result_text,
                                reply_markup=result_keyboard
                            )
                            store_content_message(chat_id, result_msg.message_id)
                            _store_result_message(chat_id, result_msg)
                        except Exception:
                            pass
                    else:
                        # Matn xabarni EDIT qilish - xato natijasini ko'rsatish
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=target_edit_id,
                            text=error_result_text,
                            reply_markup=result_keyboard
                        )
                        store_content_message(chat_id, target_edit_id)
                        # Result message ni saqlash
                        if target_edit_id == current_msg_id:
                            _store_result_message(chat_id, callback_query.message)
                        else:
                            # Oldingi natija xabari uchun
                            _store_result_message(chat_id, _create_msg_obj(target_edit_id))
                except Exception as e:
                    logger.warning(f"Error editing result message for error: {e}")
                    # Edit muvaffaqiyatsiz bo'lsa, yangi natija xabari yuborish
                    try:
                        result_msg = await bot.send_message(
                            chat_id,
                            error_result_text,
                            reply_markup=result_keyboard
                        )
                        store_content_message(chat_id, result_msg.message_id)
                        _store_result_message(chat_id, result_msg)
                    except Exception:
                        pass
            else:
                # Agar hech qanday natija xabari yo'q bo'lsa, yangi natija xabari yuborish
                try:
                    result_msg = await bot.send_message(
                        chat_id,
                        error_result_text,
                        reply_markup=result_keyboard
                    )
                    store_content_message(chat_id, result_msg.message_id)
                    _store_result_message(chat_id, result_msg)
                except Exception:
                    pass
            
            return
        
        # Har bir qatorni formatlash - faqat code — quantity (quantity original string sifatida)
        lines = []
        for p in matched:
            code_disp = p.get("code_original") or p.get("code") or ""
            qty = p.get("quantity") or ""
            # Quantity ni original string sifatida saqlash (hech qanday o'zgartirishsiz)
            qty_str = str(qty) if qty is not None else ""
            lines.append(f"- {code_disp} — {qty_str}")
        
        # Sana ni olish (birinchi topilgan qatordagi date)
        date_value = matched[0].get("date") or "N/A" if matched else "N/A"
        
        # Xabar formatini yaratish
        header = f"📁 Kolleksiya: {collection_key}\n📅 Sana: {date_value}\n\nTopilgan mahsulotlar:\n\n"
        text = header + "\n".join(lines)
        
        # Telegram xabar uzunligi cheklovini tekshirish (4096 belgi)
        # Agar uzun bo'lsa, birinchi qismini ko'rsatish
        if len(text) > 4096:
            # Faqat birinchi qismini ko'rsatish (4096 belgigacha)
            text = text[:4090] + "\n..."
        
        # HAR DOIM EDIT orqali natija chiqarish
        # Avval hozirgi xabarni yoki oldingi natija xabarini EDIT qilishga urinish
        edit_success = False
        target_msg_id = None
        
        # 1. Agar hozirgi xabar natija xabari bo'lsa, uni EDIT qilish
        if is_current_result:
            target_msg_id = current_msg_id
            try:
                if has_photo:
                    # Rasm xabarni caption orqali EDIT qilish
                    await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=current_msg_id,
                        caption=text,
                        reply_markup=result_keyboard
                    )
                else:
                    # Matn xabarni EDIT qilish
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=current_msg_id,
                        text=text,
                        reply_markup=result_keyboard
                    )
                store_content_message(chat_id, current_msg_id)
                _store_result_message(chat_id, callback_query.message)
                edit_success = True
            except Exception as edit_error:
                logger.warning(f"Error editing current result message: {edit_error}")
                # Agar rasm xabarni matn xabarga o'zgartirib bo'lmasa, o'chirish
                if has_photo:
                    try:
                        await bot.delete_message(chat_id, current_msg_id)
                        target_msg_id = None
                    except Exception:
                        target_msg_id = None
        
        # 2. Agar hozirgi xabar EDIT qilinmagan bo'lsa va oldingi natija xabari bo'lsa
        if not edit_success and previous_result_msg_id and previous_result_msg_id != current_msg_id:
            target_msg_id = previous_result_msg_id
            try:
                # Matn xabarni EDIT qilish (oldingi natija har doim matn bo'ladi)
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=previous_result_msg_id,
                    text=text,
                    reply_markup=result_keyboard
                )
                store_content_message(chat_id, previous_result_msg_id)
                # Natija xabarini saqlash
                _store_result_message(chat_id, _create_msg_obj(previous_result_msg_id))
                edit_success = True
            except Exception as edit_error:
                logger.warning(f"Error editing previous result message: {edit_error}")
                # Edit muvaffaqiyatsiz bo'lsa, o'chirish
                try:
                    await bot.delete_message(chat_id, previous_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
        
        # 3. Agar hozirgi xabar menyu bo'lsa va EDIT qilinmagan bo'lsa
        if not edit_success and not is_current_result:
            target_msg_id = current_msg_id
            try:
                # Hozirgi menyu xabarini EDIT qilib natija ko'rsatish
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=current_msg_id,
                    text=text,
                    reply_markup=result_keyboard
                )
                store_content_message(chat_id, current_msg_id)
                _store_result_message(chat_id, callback_query.message)
                edit_success = True
            except Exception as edit_error:
                logger.warning(f"Error editing menu message to result: {edit_error}")
        
        # 4. Oxirgi chora - agar hech qanday EDIT muvaffaqiyatli bo'lmagan bo'lsa, yangi xabar yuborish
        # Bu faqat birinchi marta natija ko'rsatilganda yoki barcha EDIT muvaffaqiyatsiz bo'lganida
        if not edit_success:
            result_msg = await bot.send_message(
                chat_id,
                text,
                reply_markup=result_keyboard
            )
            store_content_message(chat_id, result_msg.message_id)
            _store_result_message(chat_id, result_msg)
        
    except Exception as e:
        logger.error(f"Error processing collection: {e}", exc_info=True)
        # Xato holati - xato xabari alohida sendMessage bilan
        error_msg = await bot.send_message(
            chat_id,
            f"❌ Xatolik yuz berdi: {str(e)}\n\nIltimos, qayta urinib ko'ring."
        )
        _store_error_message(chat_id, error_msg)


@router.callback_query(F.data == "astatka_similar_codes")
async def callback_astatka_similar_codes(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start similar codes astatka mode - HAR DOIM EDIT orqali input sahifasiga o'tadi"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.set_state(AstatkaStates.waiting_for_code_similar)
    chat_id = callback_query.message.chat.id
    
    # "Ma'lumot topilmadi" xabarini o'chirish
    await _delete_error_message(bot, chat_id)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="menu_astatka"
                )
            ]
        ]
    )
    
    menu_text = (
        "🔍 <b>O'xshash kodlar bo'yicha qoldiqni bilish</b>\n\n"
        "Kod yuboring:"
    )
    
    # Hozirgi xabarni tekshirish - natija yoki menyu
    callback_text = callback_query.message.text or callback_query.message.caption or ""
    has_photo = callback_query.message.photo is not None
    from services.message_utils import is_result_message_text
    is_result_page = has_photo or is_result_message_text(callback_text)
    current_msg_id = callback_query.message.message_id
    
    # Oldingi natija xabarini olish
    previous_result_msg_id = _RESULT_MESSAGES.get(chat_id)
    
    # HAR DOIM EDIT orqali hozirgi xabarni o'zgartirish
    # 1. Agar hozirgi xabar natija xabari bo'lsa, uni EDIT qilish
    if is_result_page:
        try:
            if has_photo:
                # Rasm xabarni matn xabarga o'zgartirish - o'chirib yangi yuborish kerak
                try:
                    await bot.delete_message(chat_id, current_msg_id)
                    # Result message tracking dan olib tashlash
                    if chat_id in _RESULT_MESSAGES and _RESULT_MESSAGES[chat_id] == current_msg_id:
                        _RESULT_MESSAGES.pop(chat_id, None)
                    # Yangi menyu xabar yuborish
                    menu_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=menu_text,
                        reply_markup=keyboard
                    )
                    _store_menu_message(chat_id, menu_msg)
                    await state.update_data(bot_message_id=menu_msg.message_id)
                    return
                except Exception as e:
                    logger.warning(f"Error deleting photo message: {e}")
                    # O'chirish muvaffaqiyatsiz bo'lsa, yangi xabar yuborish
                    menu_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=menu_text,
                        reply_markup=keyboard
                    )
                    _store_menu_message(chat_id, menu_msg)
                    await state.update_data(bot_message_id=menu_msg.message_id)
                    return
            else:
                # Matn xabarni EDIT qilish
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=current_msg_id,
                    text=menu_text,
                    reply_markup=keyboard
                )
                _store_menu_message(chat_id, callback_query.message)
                await state.update_data(bot_message_id=current_msg_id)
                # Result message ni yangilash (hozirgi xabar menyu bo'ldi)
                if chat_id in _RESULT_MESSAGES and _RESULT_MESSAGES[chat_id] == current_msg_id:
                    _RESULT_MESSAGES.pop(chat_id, None)
                return
        except Exception as e:
            logger.warning(f"Error editing result message: {e}")
            # Edit muvaffaqiyatsiz bo'lsa, o'chirib yangi yuborish
            try:
                await bot.delete_message(chat_id, current_msg_id)
                if chat_id in _RESULT_MESSAGES and _RESULT_MESSAGES[chat_id] == current_msg_id:
                    _RESULT_MESSAGES.pop(chat_id, None)
            except Exception:
                pass
    
    # 2. Agar hozirgi xabar menyu bo'lsa, uni EDIT qilish
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=current_msg_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, callback_query.message)
        await state.update_data(bot_message_id=current_msg_id)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa, yangi menyu yuborish
        await _delete_menu_message(bot, chat_id)
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)
        await state.update_data(bot_message_id=menu_msg.message_id)


# --- astatka_similar_codes_back tugma handleri (O'xshash kodlar natijalaridan input sahifasiga qaytish) ---
@router.callback_query(F.data == "astatka_similar_codes_back")
async def on_astatka_similar_codes_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    O'xshash kodlar natijalaridan orqaga qaytish: HAR DOIM EDIT orqali input sahifasiga qaytadi.
    Hozirgi xabar EDIT qilinadi, yangi xabar yuborilmaydi.
    """
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.set_state(AstatkaStates.waiting_for_code_similar)
    chat_id = callback_query.message.chat.id
    
    # "Ma'lumot topilmadi" xabarini o'chirish
    await _delete_error_message(bot, chat_id)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="menu_astatka"
                )
            ]
        ]
    )
    
    menu_text = (
        "🔍 <b>O'xshash kodlar bo'yicha qoldiqni bilish</b>\n\n"
        "Kod yuboring:"
    )
    
    # HAR DOIM EDIT orqali hozirgi xabarni o'zgartirish
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, callback_query.message)
        await state.update_data(bot_message_id=callback_query.message.message_id)
        # Result message ni yangilash (hozirgi xabar menyu bo'ldi)
        _RESULT_MESSAGES.pop(chat_id, None)
    except Exception as e:
        logger.warning(f"Error editing message for back: {e}")
        # Edit muvaffaqiyatsiz bo'lsa, oxirgi chora - yangi menyu yuborish
        await _delete_menu_message(bot, chat_id)
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)
        await state.update_data(bot_message_id=menu_msg.message_id)
        _RESULT_MESSAGES.pop(chat_id, None)


def normalize_collection(value: str) -> str:
    """
    Normalize collection value for comparison:
    - lowercase
    - remove whitespace
    - remove characters: - _ . ,
    - keep only letters and numbers
    - basic Cyrillic to Latin conversion for common characters
    """
    if not value:
        return ""
    import re
    
    # Convert to lowercase
    value = str(value).lower()
    
    # Basic Cyrillic to Latin mapping for common characters
    cyrillic_to_latin = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
        'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i',
        'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
        'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
        'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch',
        'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '',
        'э': 'e', 'ю': 'yu', 'я': 'ya'
    }
    
    # Convert Cyrillic to Latin
    result = []
    for char in value:
        if char in cyrillic_to_latin:
            result.append(cyrillic_to_latin[char])
        else:
            result.append(char)
    value = ''.join(result)
    
    # Remove whitespace
    value = value.replace(' ', '').replace('\t', '').replace('\n', '').replace('\r', '')
    
    # Remove characters: - _ . ,
    value = value.replace('-', '').replace('_', '').replace('.', '').replace(',', '')
    
    # Keep only letters and numbers
    value = re.sub(r'[^a-z0-9]', '', value)
    
    return value


def collections_match(collection1: str, collection2: str) -> bool:
    """
    Check if two collections match using exact match after normalization.
    collection1: sheet collection value
    collection2: button text (may contain parentheses)
    
    Before normalization, removes parentheses and everything after from collection2 (button text).
    """
    # Remove parentheses and everything after from collection2 (button text)
    # Example: "Turk lenta 1 (eski turklar)" -> "Turk lenta 1"
    if '(' in collection2:
        collection2 = collection2.split('(')[0].strip()
    
    norm1 = normalize_collection(collection1)
    norm2 = normalize_collection(collection2)
    
    if not norm1 or not norm2:
        return False
    
    # Exact match only
    return norm1 == norm2


def find_similar_codes(query: str, records: list) -> list:
    """
    Find records where code_normalized contains the query.
    
    Args:
        query: Search query (normalized and uppercased)
        records: List of product records from sheets1
        
    Returns:
        List of matched records
    """
    matched = []
    query_upper = query.strip().upper()
    
    for row in records:
        code_norm = row.get("code_normalized", "")
        if code_norm and query_upper in code_norm:
            matched.append(row)
    
    return matched


@router.message(AstatkaStates.waiting_for_code_similar)
async def process_similar_code_query(message: Message, state: FSMContext, bot: Bot):
    """Process similar code query and fetch data from Google Sheets - FAQAT EDIT orqali natija chiqaradi"""
    chat_id = message.chat.id
    user_query = message.text.strip()
    
    # ============================================================
    # KEYINGI HAR QANDAY KODDA: Avval xato xabarni o'chirish
    # ============================================================
    await _delete_error_message(bot, chat_id)
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    # Oldingi menu xabarlarini tozalash (faqat menu)
    await _delete_menu_message(bot, chat_id)
    
    # Oldingi natija xabarini olish (_RESULT_MESSAGES dan)
    previous_result_msg_id = _RESULT_MESSAGES.get(chat_id)
    
    try:
        # Foydalanuvchi kiritgan query ni normalizatsiya qilish
        query_normalized = user_query.strip().upper()
        if not query_normalized:
            error_text = "❌ Kod kiritilmadi. Iltimos, kod yuboring."
            
            # Xato xabari alohida sendMessage bilan yuboriladi (qoidaga binoan)
            error_msg = await bot.send_message(chat_id, error_text)
            _store_error_message(chat_id, error_msg)
            
            # Natija xabariga TEGMA - agar oldin natija bo'lsa, uni EDIT qilishga urinish
            # Lekin bu holatda natija yo'q, shuning uchun keyingi qidiruvda EDIT bo'ladi
            await state.set_state(AstatkaStates.waiting_for_code_similar)
            return
        
        # sheets1 dan barcha mahsulotlarni olish
        sheet_service = GoogleSheetService()
        products = await sheet_service.read_products()
        
        # O'xshash kodlarni topish
        matched = find_similar_codes(query_normalized, products)
        
        # CONTENT natijasi uchun reply_markup (tugmalar)
        result_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="astatka_similar_codes_back"
                    ),
                    InlineKeyboardButton(
                        text="🏠 Asosiy menyu",
                        callback_data="menu_main"
                    )
                ]
            ]
        )

        if not matched:
            error_text = get_error_message_with_request(user_query)
            
            # Oldingi natija xabarini o'chirish (agar mavjud bo'lsa)
            if previous_result_msg_id:
                try:
                    await bot.delete_message(chat_id, previous_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
            
            # Xato xabari tugmalar bilan yuboriladi (faqat bitta xabar)
            error_msg = await bot.send_message(
                chat_id,
                error_text,
                reply_markup=result_keyboard
            )
            _store_error_message(chat_id, error_msg)
            
            await state.set_state(AstatkaStates.waiting_for_code_similar)
            return
        
        # Sana ni olish (birinchi topilgan qatordagi date)
        date_value = matched[0].get("date") or "N/A"
        
        # Har bir qatorni formatlash - faqat code — quantity
        lines = []
        for p in matched:
            code_disp = p.get("code_original") or p.get("code") or ""
            qty = p.get("quantity") or ""
            # Quantity ni original string sifatida saqlash (hech qanday o'zgartirishsiz)
            qty_str = str(qty) if qty is not None else ""
            lines.append(f"- {code_disp} — {qty_str}")
        
        # Xabar formatini yaratish
        header = f"📦 Model: {user_query}\n📅 Sana: {date_value}\n\nUmumiy qoldiq:\n"
        text = header + "\n".join(lines)
        
        # Telegram xabar uzunligi cheklovini tekshirish (4096 belgi)
        if len(text) > 4096:
            # Uzun xabarlar uchun birinchi qismini ko'rsatish
            text = text[:4090] + "\n..."
        
        # FAQAT EDIT orqali natija chiqarish
        edit_success = False
        result_msg = None
        
        # 1. Agar oldingi natija xabari bo'lsa, uni EDIT qilish
        if previous_result_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=previous_result_msg_id,
                    text=text,
                    reply_markup=result_keyboard
                )
                store_content_message(chat_id, previous_result_msg_id)
                _store_result_message(chat_id, _create_msg_obj(previous_result_msg_id))
                edit_success = True
            except Exception as edit_error:
                logger.warning(f"Error editing previous result message: {edit_error}")
                # Edit muvaffaqiyatsiz bo'lsa, o'chirish
                try:
                    await bot.delete_message(chat_id, previous_result_msg_id)
                except Exception:
                    pass
                _RESULT_MESSAGES.pop(chat_id, None)
        
        # 2. Agar EDIT muvaffaqiyatsiz bo'lsa yoki oldingi natija yo'q bo'lsa, yangi xabar yuborish
        if not edit_success:
            result_msg = await bot.send_message(
                chat_id,
                text,
                reply_markup=result_keyboard
            )
            store_content_message(chat_id, result_msg.message_id)
            _store_result_message(chat_id, result_msg)
        
        await state.set_state(AstatkaStates.waiting_for_code_similar)
        
    except Exception as e:
        logger.error(f"Error processing similar code query: {e}", exc_info=True)
        # Xato holati - xato xabari alohida sendMessage bilan
        error_msg = await bot.send_message(
            chat_id,
            f"❌ Xatolik yuz berdi: {str(e)}\n\nIltimos, qayta urinib ko'ring."
        )
        _store_error_message(chat_id, error_msg)
        await state.set_state(AstatkaStates.waiting_for_code_similar)


# --- astatka_back tugma handleri (CONTENT natijalaridan orqaga qaytish) ---
@router.callback_query(F.data == "astatka_back")
async def on_astatka_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Astatka natijalaridan orqaga qaytish: Astatka menyusiga qaytadi.
    CONTENT xabar saqlanib qoladi, faqat menyu ko'rsatiladi.
    Menyu xabarlari edit qilinadi, CONTENT xabarlar esa yangi menyu yuboriladi.
    """
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.clear()
    chat_id = callback_query.message.chat.id
    
    # Hozirgi xabarni tekshirish
    callback_text = callback_query.message.text or callback_query.message.caption or ""
    from services.message_utils import is_result_message_text
    
    keyboard = make_astatka_menu_keyboard()
    menu_text = (
        "🟩 <b>Astatka</b>\n\n"
        "Qanday usulda qidirmoqchisiz?"
    )
    
    # Agar hozirgi xabar CONTENT bo'lsa, yangi menyu yuborish (CONTENT saqlanib qoladi)
    if is_result_message_text(callback_text):
        # CONTENT xabar saqlanib qoladi, faqat yangi menyu yuboriladi
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)
        return
    
    # Agar hozirgi xabar menyu bo'lsa (error message yoki boshqa menyu), edit qilish
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, callback_query.message)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa, yangi menyu yuborish (delete ishlatilmaydi)
        await _delete_menu_message(bot, chat_id)
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)


# --- astatka_collection_back tugma handleri (kolleksiya natijalaridan kolleksiya tanlash menyusiga qaytish) ---
@router.callback_query(F.data == "astatka_collection_back")
async def on_astatka_collection_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Kolleksiya natijalaridan orqaga qaytish: HAR DOIM EDIT orqali kolleksiya tanlash menyusiga qaytadi.
    Hozirgi xabar EDIT qilinadi, yangi xabar yuborilmaydi.
    """
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.clear()
    chat_id = callback_query.message.chat.id
    
    # "Ma'lumot topilmadi" xabarini o'chirish
    await _delete_error_message(bot, chat_id)
    
    keyboard = make_collection_keyboard()
    menu_text = (
        "📁 <b>Kolleksiya bo'yicha bilish</b>\n\n"
        "Kolleksiyani tanlang:"
    )
    
    # Hozirgi xabarni tekshirish
    callback_text = callback_query.message.text or callback_query.message.caption or ""
    has_photo = callback_query.message.photo is not None
    current_msg_id = callback_query.message.message_id
    
    # HAR DOIM EDIT orqali hozirgi xabarni o'zgartirish
    # Agar rasm xabar bo'lsa, uni o'chirib matn yuborish kerak (rasm xabarni matn xabarga o'zgartirib bo'lmaydi)
    if has_photo:
        try:
            # Rasm xabarni o'chirish
            await bot.delete_message(chat_id, current_msg_id)
            # Yangi matn xabar yuborish (bu faqat oxirgi chora)
            menu_msg = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, menu_msg)
            # Result message ni yangilash (hozirgi xabar menyu bo'ldi)
            _RESULT_MESSAGES.pop(chat_id, None)
            return
        except Exception as e:
            logger.warning(f"Error deleting photo message for back: {e}")
            # O'chirish muvaffaqiyatsiz bo'lsa, caption orqali urinish (lekin bu ishlamaydi menyu uchun)
            # Shuning uchun oxirgi chora - yangi xabar
            menu_msg = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            _store_menu_message(chat_id, menu_msg)
            _RESULT_MESSAGES.pop(chat_id, None)
            return
    
    # Matn xabarni EDIT qilish
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=current_msg_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, callback_query.message)
        # Result message ni yangilash (hozirgi xabar menyu bo'ldi)
        _RESULT_MESSAGES.pop(chat_id, None)
    except Exception as e:
        logger.warning(f"Error editing message for back: {e}")
        # Edit muvaffaqiyatsiz bo'lsa, oxirgi chora - yangi menyu yuborish
        await _delete_menu_message(bot, chat_id)
        menu_msg = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, menu_msg)
        _RESULT_MESSAGES.pop(chat_id, None)


# --- BACK tugma handleri (hamma joyda bitta qadam orqaga) ---
@router.callback_query(F.data == "BACK")
async def on_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Astatka bo'limidan chiqish: faqat 1 bosqich orqaga (Astatka menyusiga).
    Asosiy menyuga qaytish uchun menu_main umumiy handler ishlatiladi.
    """
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.clear()
    chat_id = callback_query.message.chat.id
    
    # Hozirgi xabarni tekshirish - CONTENT bo'lsa o'chirilmaydi
    callback_text = callback_query.message.text or callback_query.message.caption or ""
    from services.message_utils import is_result_message_text
    
    # Astatka menyusiga qaytish
    keyboard = make_astatka_menu_keyboard()
    menu_text = (
        "🟩 <b>Astatka</b>\n\n"
        "Qanday usulda qidirmoqchisiz?"
    )
    
    # Agar hozirgi xabar CONTENT bo'lsa, faqat yangi menu yuboriladi
    if is_result_message_text(callback_text):
        menu_msg = await bot.send_message(chat_id, menu_text, reply_markup=keyboard)
        _store_menu_message(chat_id, menu_msg)
        return
    
    # Agar menu bo'lsa, edit qilishga urinish (menyu yo'qolmaydi, faqat edit qilinadi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        _store_menu_message(chat_id, callback_query.message)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa, yangi menyu yuborish (eski menyu o'chirilmaydi)
        await _delete_menu_message(bot, chat_id)
        menu_msg = await bot.send_message(chat_id, menu_text, reply_markup=keyboard)
        _store_menu_message(chat_id, menu_msg)


# --- astatka_back_main tugma handleri (Astatka menyusidan asosiy menyuga qaytish) ---
@router.callback_query(F.data == "astatka_back_main")
async def on_astatka_back_main(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Astatka menyusidan asosiy menyuga qaytish: HAR DOIM EDIT orqali asosiy menyuni ko'rsatadi.
    Hozirgi xabar EDIT qilinadi, yangi xabar yuborilmaydi.
    """
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    await state.clear()
    chat_id = callback_query.message.chat.id
    user_id = callback_query.from_user.id
    
    # Asosiy menyu keyboard va matn
    from handlers.start import make_main_menu_keyboard
    keyboard = make_main_menu_keyboard(user_id)
    menu_text = (
        "Assalomu alaykum! TIZIMGA  xush kelibsiz.\n\n"
        "Quyidagi menyulardan birini tanlang:"
    )
    
    # HAR DOIM EDIT orqali hozirgi xabarni o'zgartirish
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        # Asosiy menyu xabarini track qilish
        from services.message_utils import store_main_menu_message
        store_main_menu_message(chat_id, callback_query.message.message_id)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa, yangi menyu yuborish
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            from services.message_utils import store_main_menu_message
            store_main_menu_message(chat_id, sent.message_id)
        except Exception:
            pass
