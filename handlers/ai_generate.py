import logging
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
)

from services.ai_service import AIResult, get_openai_service
from services.message_utils import store_content_message
from handlers.start import make_main_menu_keyboard

logger = logging.getLogger(__name__)

router = Router()


class AIGenerateStates(StatesGroup):
    AI_GEN_WAIT_DERAZA = State()  # Deraza rasmini kutish holati
    AI_GEN_WAIT_JALYUZI = State()  # Jalyuzi modeli rasmini kutish holati


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="ai_generate_back")]]
    )


def result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Yana generatsiya", callback_data="menu_ai_generate")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="ai_generate_back")],
        ]
    )


@router.callback_query(F.data == "menu_ai_generate")
async def callback_ai_generate_menu(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """AI Generatsiya bo'limiga kirish"""
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    text = (
        "🎨 AI Generatsiya\n\n"
        "Jalyuzi modelini derazaga o'rnatilgan holatda ko'rish uchun:\n\n"
        "1️⃣ Avval DERAZA rasmini yuboring\n"
        "2️⃣ Keyin JALYUZI MODELINING rasmini yuboring\n\n"
        "AI model sizga natijani yaratib beradi."
    )
    chat_id = callback_query.message.chat.id

    # UI menu doim edit qilinadi (yangi xabar yuborilmaydi)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=back_keyboard()
        )
        await state.set_state(AIGenerateStates.AI_GEN_WAIT_DERAZA)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa (masalan, xabar CONTENT bo'lsa), yangi menyu yuborish
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=back_keyboard()
        )
        await state.set_state(AIGenerateStates.AI_GEN_WAIT_DERAZA)


@router.message(AIGenerateStates.AI_GEN_WAIT_DERAZA, F.photo)
async def process_deraza_photo(message: Message, bot: Bot, state: FSMContext):
    """Deraza rasmi qabul qilindi: jalyuzi rasmini so'rash"""
    photo: PhotoSize = message.photo[-1]

    try:
        file_info = await bot.get_file(photo.file_id)
    except Exception as exc:
        logger.error("Failed to get Telegram file info: %s", exc)
        await message.answer("❌ Rasmni olishda xatolik. Qayta urinib ko'ring.", reply_markup=back_keyboard())
        return

    if not file_info or not file_info.file_path:
        await message.answer("❌ Rasm faylini olishning iloji bo'lmadi. Qayta yuboring.", reply_markup=back_keyboard())
        return

    # Deraza rasmini saqlash
    await state.update_data(deraza_photo_file_path=file_info.file_path)
    await state.set_state(AIGenerateStates.AI_GEN_WAIT_JALYUZI)

    # Foydalanuvchi xabarini track qilish
    data = await state.get_data()
    ai_message_ids = data.get("ai_message_ids", [])
    ai_message_ids.append(message.message_id)
    await state.update_data(ai_message_ids=ai_message_ids)

    # Jalyuzi rasmini so'rash
    prompt_msg = await message.answer(
        "✅ Deraza rasmi qabul qilindi.\n\n"
        "🪟 Endi JALYUZI MODELINING rasmini yuboring.",
        reply_markup=back_keyboard(),
    )
    # Prompt so'rash xabarini track qilish
    ai_message_ids.append(prompt_msg.message_id)
    await state.update_data(ai_message_ids=ai_message_ids)


@router.message(AIGenerateStates.AI_GEN_WAIT_DERAZA)
async def process_deraza_photo_invalid(message: Message):
    """Deraza rasmi emas, boshqa narsa yuborilgan"""
    await message.answer(
        "Iltimos, deraza rasmini yuboring.",
        reply_markup=back_keyboard()
    )


@router.message(AIGenerateStates.AI_GEN_WAIT_JALYUZI, F.photo)
async def process_jalyuzi_photo(message: Message, bot: Bot, state: FSMContext):
    """Jalyuzi rasmi qabul qilindi: AI Vision generatsiyani boshlash"""
    photo: PhotoSize = message.photo[-1]

    try:
        file_info = await bot.get_file(photo.file_id)
    except Exception as exc:
        logger.error("Failed to get Telegram file info: %s", exc)
        await message.answer("❌ Rasmni olishda xatolik. Qayta urinib ko'ring.", reply_markup=back_keyboard())
        return

    if not file_info or not file_info.file_path:
        await message.answer("❌ Rasm faylini olishning iloji bo'lmadi. Qayta yuboring.", reply_markup=back_keyboard())
        return

    data = await state.get_data()
    deraza_photo_file_path = data.get("deraza_photo_file_path")

    # Deraza rasmi borligini tekshirish
    if not deraza_photo_file_path:
        logger.error("Deraza photo missing in state")
        await message.answer(
            "❌ Ma'lumotlar topilmadi. Menyudan qayta boshlang.",
            reply_markup=back_keyboard()
        )
        await state.clear()
        return

    # Foydalanuvchi xabarini track qilish
    ai_message_ids = data.get("ai_message_ids", [])
    ai_message_ids.append(message.message_id)
    await state.update_data(ai_message_ids=ai_message_ids)

    # "Ishlanmoqda..." xabarini yuborish
    processing_msg = await message.answer("⏳ AI generatsiya jarayoni boshlandi. Iltimos kuting...")
    processing_msg_id = processing_msg.message_id
    ai_message_ids.append(processing_msg_id)
    await state.update_data(ai_message_ids=ai_message_ids)

    try:
        # Ikkala rasmini yuklab olish
        deraza_bytes = await _download_photo_bytes(bot, deraza_photo_file_path)
        jalyuzi_bytes = await _download_photo_bytes(bot, file_info.file_path)
    except Exception as exc:
        logger.error("Photo download failed: %s", exc, exc_info=True)
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=processing_msg_id)
            ai_message_ids.remove(processing_msg_id)
            await state.update_data(ai_message_ids=ai_message_ids)
        except Exception:
            pass
        await processing_msg.edit_text(
            "❌ Rasmlarni yuklab bo'lmadi. Qaytadan rasm yuboring.",
            reply_markup=back_keyboard()
        )
        return

    try:
        ai_service = get_openai_service()
    except ValueError:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=processing_msg_id)
            ai_message_ids.remove(processing_msg_id)
            await state.update_data(ai_message_ids=ai_message_ids)
        except Exception:
            pass
        await processing_msg.edit_text(
            "❌ OPENAI_API_KEY sozlanmagan. Admin bilan bog'laning.",
            reply_markup=back_keyboard()
        )
        return

    try:
        # AI Vision generatsiyani boshlash (ikkala rasm birga)
        ai_result: AIResult = await ai_service.generate_from_images(
            room_bytes=deraza_bytes,
            model_bytes=jalyuzi_bytes,
        )
    except Exception as exc:
        logger.error("OpenAI Vision generation failed: %s", exc, exc_info=True)
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=processing_msg_id)
            ai_message_ids.remove(processing_msg_id)
            await state.update_data(ai_message_ids=ai_message_ids)
        except Exception:
            pass
        await processing_msg.edit_text(
            "❌ AI generatsiyada xatolik. Keyinroq qayta urinib ko'ring.",
            reply_markup=back_keyboard()
        )
        return

    # "Ishlanmoqda..." xabarini o'chirish
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=processing_msg_id)
        ai_message_ids.remove(processing_msg_id)
    except Exception:
        pass

    # State ni tozalash
    await state.clear()

    # Natijani yuborish
    description = ai_result.description or "✅ Natija tayyor."

    if ai_result.image_bytes:
        photo = BufferedInputFile(ai_result.image_bytes, filename="result.png")
        result_msg = await message.answer_photo(photo=photo, caption=description, reply_markup=result_keyboard())
    else:
        result_msg = await message.answer(description, reply_markup=result_keyboard())
    
    # AI natijasini CONTENT sifatida belgilash (hech qachon o'chirilmaydi)
    store_content_message(message.chat.id, result_msg.message_id)


@router.message(AIGenerateStates.AI_GEN_WAIT_JALYUZI)
async def process_jalyuzi_photo_invalid(message: Message):
    """Jalyuzi rasmi emas, boshqa narsa yuborilgan"""
    await message.answer(
        "Iltimos, jalyuzi modeli rasmini yuboring.",
        reply_markup=back_keyboard()
    )


@router.callback_query(F.data == "ai_generate_back")
async def callback_ai_generate_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Orqaga bosilganda: Barcha AI generatsiya xabarlarini o'chirish va state ni tozalash"""
    chat_id = callback_query.message.chat.id
    
    # Track qilingan barcha xabarlarni olish
    data = await state.get_data()
    ai_message_ids = data.get("ai_message_ids", [])
    
    # Hozirgi xabar ID sini ham ro'yxatga qo'shish (agar yo'q bo'lsa)
    current_msg_id = callback_query.message.message_id
    if current_msg_id not in ai_message_ids:
        ai_message_ids.append(current_msg_id)
    
    # Barcha track qilingan xabarlarni o'chirish (foydalanuvchi rasmlari, bot xabarlari)
    for msg_id in ai_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            # Xabar allaqachon o'chirilgan yoki o'chirib bo'lmaydi
            pass
    
    # State ni tozalash
    await state.clear()
    
    # Asosiy menyuni yangi xabar sifatida yuborish
    from services.admin_utils import is_any_admin
    user_id = callback_query.from_user.id
    keyboard = make_main_menu_keyboard(user_id)
    
    menu_text = (
        "👋 Assalomu alaykum! Botga xush kelibsiz.\n\n"
        "Quyidagi menyulardan birini tanlang:"
    )
    
    from services.message_utils import store_main_menu_message
    sent = await bot.send_message(
        chat_id=chat_id,
        text=menu_text,
        reply_markup=keyboard
    )
    store_main_menu_message(chat_id, sent.message_id)
    
    await callback_query.answer()


async def _download_photo_bytes(bot: Bot, file_path: str) -> bytes:
    """Telegram faylini bytes ga o'girish"""
    tg_file = await bot.download_file(file_path)
    if hasattr(tg_file, "read"):
        tg_file.seek(0)
        data = tg_file.read()
    else:
        data = bytes(tg_file)

    if not data:
        raise ValueError("Downloaded photo is empty.")

    return data
