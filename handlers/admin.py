"""
Admin panel handlers.
All admin panel interactions use edit_message_text/edit_message_reply_markup (UI logic).
"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from typing import Tuple
from datetime import datetime, timedelta
import time
import os
import asyncio

from services.admin_utils import is_admin, is_helper_admin, is_any_admin
from services.stats import (
    get_stats,
    record_request,
    get_all_users,
    get_user_details,
    record_admin_action,
    get_activity_log,
    clear_all_log_files,
    get_log_entry_by_index,
    get_response_time_stats,
    clear_users_cache_for_admin_users,      # foydalanuvchilar keshini qo'lda tozalash
    compact_users_stats_for_admin_users,    # foydalanuvchi tarixini avtomatik kompaktlash
    clear_general_stats_cache,  # YANGI
    check_and_auto_clear_stats,  # YANGI
    get_stats_memory_info,  # YANGI
)
from services.message_utils import store_main_menu_message, delete_main_menu_message
from services.google_sheet import (
    GoogleSheetService, 
    reload_cache,
    load_sheets1_to_cache,
    load_sheets2_to_cache,
    load_sheets3_to_cache,
    load_sheets4_to_cache,
    load_sheets5_to_cache,
    load_sheets6_to_cache,
    CACHE
)
from services.ai_service import get_ai_stats, get_initial_balance
from config import OPENAI_API_KEY, OPENAI_MODEL
from services.settings import (
    get_contact_phone, set_contact_phone, get_error_message, set_error_message,
    get_broadcast_enabled, toggle_broadcast_enabled,
    get_broadcast_active_users_only, toggle_broadcast_active_users_only,
    get_broadcast_skip_blocked, toggle_broadcast_skip_blocked,
    get_history_logging_enabled, toggle_history_logging_enabled,
    get_daily_stats_auto_reset, toggle_daily_stats_auto_reset,
    get_max_logs_per_user, set_max_logs_per_user,
    get_user_request_history_enabled, toggle_user_request_history_enabled,
    get_show_first_name, toggle_show_first_name,
    get_show_username, toggle_show_username,
    is_user_blocked, block_user, unblock_user,
    get_user_limit, set_user_limit, remove_user_limit,
    delete_user_data, increment_user_request_count,
    get_api_users, grant_api_access, revoke_api_access,
    set_admin_name, get_admin_name, remove_admin_name,
    set_helper_admin_name, get_helper_admin_name, remove_helper_admin_name
)
from config import ADMINS, HELPER_ADMINS
from services.admin_storage import (
    add_super_admin, add_admin,
    remove_super_admin, remove_admin,
    get_super_admin_name, get_admin_name_storage,
    get_super_admins, get_admins,
    is_super_admin, is_admin_storage,
    get_main_admins, get_helper_admins,  # backward compatibility
    get_main_admin_name, get_helper_admin_name,  # backward compatibility
    is_main_admin, is_helper_admin_storage,  # admin checking functions
    add_main_admin, add_helper_admin,  # backward compatibility
    remove_main_admin, remove_helper_admin,  # backward compatibility
    get_sellers, add_seller, remove_seller, get_seller_name, is_seller,  # sellers functions
    get_partners, add_partner, remove_partner, has_partner  # partners functions
)
from services.admin_utils import is_super_admin as is_super_admin_util, is_admin as is_admin_util
import logging

logger = logging.getLogger(__name__)

router = Router()


# ==================== STATES ====================

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_broadcast_text = State()
    waiting_for_contact_number = State()
    waiting_for_error_text = State()
    waiting_for_max_logs = State()
    waiting_for_user_limit = State()
    waiting_for_api_user_id = State()
    waiting_for_api_user_name = State()  # API ruxsat ismi uchun
    waiting_for_store_access_user_id = State()  # Magazin ruxsati ID uchun
    waiting_for_store_access_user_name = State()  # Magazin ruxsati ismi uchun
    waiting_for_admin_name = State()  # Admin ismi uchun
    waiting_for_admin_id = State()  # Admin ID uchun (ismdan keyin)
    waiting_for_admin_type = State()  # Admin turi uchun (main yoki helper)
    waiting_for_helper_admin_name = State()  # Yordamchi admin ismi uchun
    waiting_for_helper_admin_id = State()  # Yordamchi admin ID uchun (ismdan keyin)
    waiting_for_seller_name = State()  # Sotuvchi ismi uchun
    waiting_for_seller_id = State()  # Sotuvchi ID yoki username uchun (ismdan keyin)
    waiting_for_partner_seller = State()  # Hamkor qo'shishda sotuvchi tanlash
    waiting_for_partner_name = State()  # Hamkor ismi uchun
    waiting_for_partner_id = State()  # Hamkor ID yoki username uchun


# ==================== HELPER FUNCTIONS ====================

def make_admin_main_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    """Create admin main menu keyboard"""
    from services.admin_storage import is_seller
    
    # Agar sotuvchi bo'lsa, faqat 2 ta tugma ko'rsatiladi
    if user_id is not None and is_seller(user_id) and not is_any_admin(user_id):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🤝 Hamkorlar",
                        callback_data="admin_partners"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Chiqish",
                        callback_data="admin_exit"
                    )
                ]
            ]
        )
    
    # Adminlar uchun to'liq menyu
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Statistika",
                    callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Foydalanuvchilar",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Adminlar",
                    callback_data="admin_admins"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖼️ Rasmlarni file_id qilish",
                    callback_data="admin_images_fileid"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Bazani yangilash",
                    callback_data="admin_reload_cache"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🩺 Bot holati",
                    callback_data="admin_bot_status"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Botni restart qilish",
                    callback_data="admin_restart_bot"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔎 Bo'limlar holati",
                    callback_data="section_status"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛠 Narxlar bo'limi uchun API ruxsat",
                    callback_data="admin_api_access"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏬 Magazindagi tayyor razmerlar ruxsati",
                    callback_data="admin_store_access"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Xabar yuborish",
                    callback_data="admin_broadcast"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ Cache fayllarni tozalash",
                    callback_data="admin_clear_cache_files"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Sozlamalar",
                    callback_data="admin_settings"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Sotuvchilar",
                    callback_data="admin_sellers"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤝 Hamkorlar",
                    callback_data="admin_partners"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Chiqish",
                    callback_data="admin_exit"
                )
            ]
        ]
    )


def make_admin_back_keyboard() -> InlineKeyboardMarkup:
    """Create back keyboard for admin panels"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_main"
                )
            ]
        ]
    )


def make_restart_bot_keyboard() -> InlineKeyboardMarkup:
    """Create restart bot submenu keyboard"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Start bot",
                    callback_data="admin_restart_start"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏹ Stop bot",
                    callback_data="admin_restart_stop"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Restart bot",
                    callback_data="admin_restart_restart"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Status (Holat)",
                    callback_data="admin_restart_status"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Log (oxirgi loglar)",
                    callback_data="admin_restart_log"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_main"
                )
            ]
        ]
    )


# ==================== ENTRY POINTS ====================

@router.message(Command("admin"))
async def cmd_admin(message: Message, bot: Bot):
    """Handle /admin command"""
    user_id = message.from_user.id
    from services.admin_storage import is_seller
    
    # Admin yoki sotuvchi bo'lishi kerak
    if not is_any_admin(user_id) and not is_seller(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    menu_text = (
        "🛠 <b>Admin panel</b>\n"
        "────────────\n\n"
        "Quyidagi bo'limlardan birini tanlang:"
    )
    
    keyboard = make_admin_main_keyboard(user_id)
    
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        # If edit fails, send new message
        await message.answer(menu_text, reply_markup=keyboard)


@router.callback_query(F.data == "admin_panel")
async def callback_admin_panel(callback_query: CallbackQuery, bot: Bot):
    """Handle admin panel button from main menu"""
    user_id = callback_query.from_user.id
    from services.admin_storage import is_seller
    
    # Admin yoki sotuvchi bo'lishi kerak
    if not is_any_admin(user_id) and not is_seller(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    menu_text = (
        "🛠 <b>Admin panel</b>\n"
        "────────────\n\n"
        "Quyidagi bo'limlardan birini tanlang:"
    )
    
    keyboard = make_admin_main_keyboard(user_id)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        # If edit fails, send new message
        await callback_query.message.answer(menu_text, reply_markup=keyboard)


# ==================== MAIN MENU ====================

@router.callback_query(F.data == "admin_main")
async def callback_admin_main(callback_query: CallbackQuery, bot: Bot):
    """Return to admin main menu"""
    user_id = callback_query.from_user.id
    from services.admin_storage import is_seller
    
    # Admin yoki sotuvchi bo'lishi kerak
    if not is_any_admin(user_id) and not is_seller(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    menu_text = (
        "🛠 <b>Admin panel</b>\n"
        "────────────\n\n"
        "Quyidagi bo'limlardan birini tanlang:"
    )
    
    keyboard = make_admin_main_keyboard(user_id)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


# ==================== SELLERS ====================

@router.callback_query(F.data == "admin_sellers")
async def callback_admin_sellers(callback_query: CallbackQuery, bot: Bot):
    """Show sellers menu"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    sellers_dict = get_sellers()
    sellers_list = []
    for seller_id_str, seller_name in sellers_dict.items():
        seller_id = int(seller_id_str)
        sellers_list.append(f"• {seller_name} — {seller_id}")
    sellers_text = "\n".join(sellers_list) if sellers_list else "Hozircha sotuvchilar mavjud emas"
    
    sellers_menu_text = (
        "👤 <b>Sotuvchilar</b>\n"
        "────────────\n\n"
        f"{sellers_text}"
    )
    
    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text="➕ Sotuvchi qo'shish",
                callback_data="admin_add_seller"
            )
        ],
        [
            InlineKeyboardButton(
                text="➖ Sotuvchini o'chirish",
                callback_data="admin_remove_seller"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Sotuvchilar ro'yxati",
                callback_data="admin_list_sellers"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="admin_main"
            )
        ]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=sellers_menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_add_seller")
async def callback_admin_add_seller(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start adding seller process - Step 1: Ask for name"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    text = "Sotuvchi ismini yuboring:"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_sellers"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_seller_name)


@router.message(AdminStates.waiting_for_seller_name)
async def process_seller_name(message: Message, state: FSMContext, bot: Bot):
    """Process seller name input - Step 1"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    seller_name = message.text.strip()
    
    # Ism tekshiruvi
    if not seller_name:
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "⚠️ <b>Xato</b>\n"
            "────────────\n\n"
            "Ism bo'sh bo'lmasligi kerak."
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_sellers"
                    )
                ]
            ]
        )
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        return
    
    # State ga ismni saqlash
    await state.update_data(seller_name=seller_name)
    
    # Endi ID so'rash - Step 2
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    text = (
        "➕ <b>Sotuvchi qo'shish</b>\n"
        "────────────\n\n"
        "Sotuvchining Telegram ID yoki @username'ini yuboring:\n\n"
        "⚠️ Eslatma: ID yoki username ni to'g'ri kiriting."
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_sellers"
                )
            ]
        ]
    )
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_seller_id)


@router.message(AdminStates.waiting_for_seller_id)
async def process_add_seller(message: Message, state: FSMContext, bot: Bot):
    """Process adding seller (ID or username) - Step 2"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    input_text = message.text.strip()
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    seller_name = state_data.get("seller_name")  # State'dan ismni olish
    
    if not seller_name:
        # Agar ism yo'q bo'lsa, jarayonni qayta boshlash
        await state.clear()
        return
    
    # ID yoki username ni parse qilish
    seller_id = None
    
    try:
        # Agar raqam bo'lsa, ID deb hisoblaymiz
        if input_text.isdigit():
            seller_id = int(input_text)
        # Agar @username bo'lsa
        elif input_text.startswith('@'):
            username = input_text[1:]
            # Bot API orqali username orqali foydalanuvchi ma'lumotlarini olish
            try:
                user_info = await bot.get_chat(f"@{username}")
                seller_id = user_info.id
            except Exception as e:
                error_text = (
                    "⚠️ <b>Xato</b>\n"
                    "────────────\n\n"
                    "Bunday username topilmadi yoki xato kiritildi."
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ Orqaga",
                                callback_data="admin_sellers"
                            )
                        ]
                    ]
                )
                try:
                    if bot_message_id:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=bot_message_id,
                            text=error_text,
                            reply_markup=keyboard
                        )
                except Exception:
                    pass
                await state.clear()
                return
        else:
            # Boshqa holatda ID deb sinab ko'ramiz
            try:
                seller_id = int(input_text)
            except ValueError:
                error_text = (
                    "⚠️ <b>Xato</b>\n"
                    "────────────\n\n"
                    "ID yoki username ni to'g'ri kiriting.\n"
                    "Misol: 123456789 yoki @username"
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ Orqaga",
                                callback_data="admin_sellers"
                            )
                        ]
                    ]
                )
                try:
                    if bot_message_id:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=bot_message_id,
                            text=error_text,
                            reply_markup=keyboard
                        )
                except Exception:
                    pass
                await state.clear()
                return
    except Exception as e:
        error_text = (
            "⚠️ <b>Xato</b>\n"
            "────────────\n\n"
            "Xatolik yuz berdi. Qayta urinib ko'ring."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_sellers"
                    )
                ]
            ]
        )
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        await state.clear()
        return
    
    # Tekshirish: allaqachon sotuvchi bo'lsa
    if is_seller(seller_id):
        error_text = (
            "⚠️ <b>Xato</b>\n"
            "────────────\n\n"
            "Bu foydalanuvchi allaqachon sotuvchi."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_sellers"
                    )
                ]
            ]
        )
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        await state.clear()
        return
    
    # Tekshirish: admin bo'lsa
    if is_any_admin(seller_id):
        error_text = (
            "⚠️ <b>Xato</b>\n"
            "────────────\n\n"
            "Admin sotuvchi sifatida qo'shilmaydi."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_sellers"
                    )
                ]
            ]
        )
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        await state.clear()
        return
    
    # Sotuvchini qo'shish
    add_seller(seller_id, seller_name)
    
    success_text = (
        "✅ <b>Muvaffaqiyatli</b>\n"
        "────────────\n\n"
        f"Sotuvchi muvaffaqiyatli qo'shildi:\n"
        f"• {seller_name} — {seller_id}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_sellers"
                )
            ]
        ]
    )
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=success_text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.clear()


@router.callback_query(F.data == "admin_remove_seller")
async def callback_admin_remove_seller(callback_query: CallbackQuery, bot: Bot):
    """Show sellers list for removal"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    sellers_dict = get_sellers()
    
    if not sellers_dict:
        text = (
            "➖ <b>Sotuvchini o'chirish</b>\n"
            "────────────\n\n"
            "Hozircha sotuvchilar mavjud emas."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_sellers"
                    )
                ]
            ]
        )
    else:
        text = (
            "➖ <b>Sotuvchini o'chirish</b>\n"
            "────────────\n\n"
            "O'chirish uchun sotuvchini tanlang:"
        )
        keyboard_buttons = []
        for seller_id_str, seller_name in sellers_dict.items():
            seller_id = int(seller_id_str)
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"❌ {seller_name} — {seller_id}",
                    callback_data=f"admin_remove_seller_confirm:{seller_id}"
                )
            ])
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="admin_sellers"
            )
        ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_remove_seller_confirm:"))
async def callback_admin_remove_seller_confirm(callback_query: CallbackQuery, bot: Bot):
    """Confirm and remove seller"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        seller_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("⚠️ Xato", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer("✅ Sotuvchi o'chirildi")
    
    seller_name = get_seller_name(seller_id)
    remove_seller(seller_id)
    
    success_text = (
        "❌ <b>Sotuvchi olib tashlandi</b>\n"
        "────────────\n\n"
        f"Sotuvchi ro'yxatdan o'chirildi:\n"
        f"• {seller_name} — {seller_id}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_sellers"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=success_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_list_sellers")
async def callback_admin_list_sellers(callback_query: CallbackQuery, bot: Bot):
    """Show sellers list"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    sellers_dict = get_sellers()
    sellers_list = []
    for seller_id_str, seller_name in sellers_dict.items():
        seller_id = int(seller_id_str)
        sellers_list.append(f"• {seller_name} — {seller_id}")
    
    sellers_text = "\n".join(sellers_list) if sellers_list else "Hozircha sotuvchilar mavjud emas"
    
    list_text = (
        "📋 <b>Sotuvchilar ro'yxati</b>\n"
        "────────────\n\n"
        f"{sellers_text}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_sellers"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=list_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


# ==================== PARTNERS ====================

@router.callback_query(F.data == "admin_partners")
async def callback_admin_partners(callback_query: CallbackQuery, bot: Bot):
    """Show partners menu"""
    user_id = callback_query.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Agar sotuvchi bo'lsa, faqat o'z hamkorlarini ko'radi
    if is_seller_user and not is_admin_user:
        partners_list = get_partners(user_id)
    else:
        # Admin barcha hamkorlarni ko'radi
        partners_list = get_partners()
    
    if not partners_list:
        partners_text = "Hozircha hamkorlar mavjud emas"
    else:
        partners_display = []
        for partner in partners_list:
            seller_id = partner.get("seller_id", "N/A")
            seller_name = partner.get("seller_name", "N/A")
            partner_name = partner.get("partner_name", "N/A")
            partner_id = partner.get("partner_id", "N/A")
            # Har bir hamkor uchun sotuvchi ismini ko'rsatish
            partners_display.append(f"• {partner_name} — {partner_id}\n  ↳ Sotuvchi: {seller_name}")
        partners_text = "\n".join(partners_display)
    
    partners_menu_text = (
        "🤝 <b>Hamkorlar</b>\n"
        "────────────\n\n"
        f"{partners_text}"
    )
    
    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text="➕ Hamkor qo'shish",
                callback_data="admin_add_partner"
            )
        ],
        [
            InlineKeyboardButton(
                text="➖ Hamkorni o'chirish",
                callback_data="admin_remove_partner"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Hamkorlar ro'yxati",
                callback_data="admin_list_partners"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="admin_main"
            )
        ]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=partners_menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_add_partner")
async def callback_admin_add_partner(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start adding partner process - Step 1: Select seller"""
    user_id = callback_query.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Agar sotuvchi bo'lsa, faqat o'zini tanlay oladi
    if is_seller_user and not is_admin_user:
        # Sotuvchilar ro'yxatini ko'rsatish, lekin faqat o'zini tanlash mumkin
        sellers_dict = get_sellers()
        
        if str(user_id) not in sellers_dict:
            await callback_query.answer("⚠️ Xato", show_alert=True)
            return
        
        # Answer immediately to prevent bot freezing
        await callback_query.answer()
        
        text = (
            "➕ <b>Hamkor qo'shish</b>\n"
            "────────────\n\n"
            "Sotuvchini tanlang:"
        )
        keyboard_buttons = []
        # Faqat o'zini ko'rsatish
        seller_name = sellers_dict[str(user_id)]
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"👤 {seller_name} — {user_id}",
                callback_data=f"admin_select_seller_for_partner:{user_id}"
            )
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="admin_partners"
            )
        ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=text,
                reply_markup=keyboard
            )
            await state.update_data(bot_message_id=callback_query.message.message_id)
        except Exception:
            pass
        
        await state.set_state(AdminStates.waiting_for_partner_seller)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Admin uchun sotuvchilar ro'yxati
    sellers_dict = get_sellers()
    
    if not sellers_dict:
        text = (
            "➕ <b>Hamkor qo'shish</b>\n"
            "────────────\n\n"
            "Hozircha sotuvchilar mavjud emas."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_partners"
                    )
                ]
            ]
        )
    else:
        text = (
            "➕ <b>Hamkor qo'shish</b>\n"
            "────────────\n\n"
            "Sotuvchini tanlang:"
        )
        keyboard_buttons = []
        for seller_id_str, seller_name in sellers_dict.items():
            seller_id = int(seller_id_str)
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"👤 {seller_name} — {seller_id}",
                    callback_data=f"admin_select_seller_for_partner:{seller_id}"
                )
            ])
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="admin_partners"
            )
        ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_partner_seller)


@router.callback_query(F.data.startswith("admin_select_seller_for_partner:"))
async def callback_select_seller_for_partner(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Select seller for partner"""
    user_id = callback_query.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        seller_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("⚠️ Xato", show_alert=True)
        return
    
    # Agar sotuvchi bo'lsa, faqat o'zini tanlay oladi
    if is_seller_user and not is_admin_user:
        if seller_id != user_id:
            await callback_query.answer("❌ Siz faqat o'zingiz uchun hamkor biriktira olasiz.", show_alert=True)
            return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # State'ga sotuvchi ID ni saqlash
    await state.update_data(seller_id=seller_id)
    
    text = "Hamkor ismini yuboring:"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_partners"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_partner_name)


@router.message(AdminStates.waiting_for_partner_name)
async def process_partner_name(message: Message, state: FSMContext, bot: Bot):
    """Process partner name input"""
    user_id = message.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    partner_name = message.text.strip()
    
    if not partner_name:
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "⚠️ <b>Xato</b>\n"
            "────────────\n\n"
            "Ism bo'sh bo'lmasligi kerak."
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_partners"
                    )
                ]
            ]
        )
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        return
    
    # State ga ismni saqlash
    await state.update_data(partner_name=partner_name)
    
    # Endi ID so'rash
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    text = "Hamkorning Telegram ID yoki @username'ini yuboring:"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_partners"
                )
            ]
        ]
    )
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_partner_id)


@router.message(AdminStates.waiting_for_partner_id)
async def process_add_partner(message: Message, state: FSMContext, bot: Bot):
    """Process adding partner (ID or username)"""
    user_id = message.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    input_text = message.text.strip()
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    partner_name = state_data.get("partner_name")
    seller_id = state_data.get("seller_id")
    
    # Agar sotuvchi bo'lsa, seller_id user_id bo'ladi
    if is_seller_user and not is_admin_user:
        seller_id = user_id
    
    if not partner_name or seller_id is None:
        await state.clear()
        return
    
    # ID yoki username ni parse qilish
    partner_id = None
    
    try:
        # Agar raqam bo'lsa, ID deb hisoblaymiz
        if input_text.isdigit():
            partner_id = input_text
        # Agar @username bo'lsa
        elif input_text.startswith('@'):
            username = input_text[1:]
            partner_id = f"@{username}"
        else:
            # Boshqa holatda ID deb sinab ko'ramiz
            try:
                partner_id = str(int(input_text))
            except ValueError:
                error_text = (
                    "⚠️ <b>Xato</b>\n"
                    "────────────\n\n"
                    "ID yoki username ni to'g'ri kiriting.\n"
                    "Misol: 123456789 yoki @username"
                )
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ Orqaga",
                                callback_data="admin_partners"
                            )
                        ]
                    ]
                )
                try:
                    if bot_message_id:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=bot_message_id,
                            text=error_text,
                            reply_markup=keyboard
                        )
                except Exception:
                    pass
                await state.clear()
                return
    except Exception as e:
        error_text = (
            "⚠️ <b>Xato</b>\n"
            "────────────\n\n"
            "Xatolik yuz berdi. Qayta urinib ko'ring."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_partners"
                    )
                ]
            ]
        )
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        await state.clear()
        return
    
    # Tekshirish: allaqachon hamkor bo'lsa
    if has_partner(seller_id, partner_id):
        error_text = (
            "⚠️ <b>Xato</b>\n"
            "────────────\n\n"
            "Bu hamkor allaqachon qo'shilgan."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_partners"
                    )
                ]
            ]
        )
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        await state.clear()
        return
    
    # Hamkorni qo'shish
    add_partner(seller_id, partner_name, partner_id)
    
    success_text = (
        "✅ <b>Muvaffaqiyatli</b>\n"
        "────────────\n\n"
        f"Hamkor muvaffaqiyatli qo'shildi:\n"
        f"• {partner_name} — {partner_id}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_partners"
                )
            ]
        ]
    )
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=success_text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.clear()


@router.callback_query(F.data.startswith("admin_remove_partner_confirm:"))
async def callback_admin_remove_partner_confirm(callback_query: CallbackQuery, bot: Bot):
    """Confirm and remove partner"""
    user_id = callback_query.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        data_parts = callback_query.data.split(":")[1:]
        if len(data_parts) == 2:
            # Admin uchun: seller_id:partner_id
            seller_id = int(data_parts[0])
            partner_id = data_parts[1]
        else:
            # Sotuvchi uchun: partner_id
            seller_id = user_id
            partner_id = data_parts[0]
    except (ValueError, IndexError):
        await callback_query.answer("⚠️ Xato", show_alert=True)
        return
    
    # Agar sotuvchi bo'lsa, faqat o'z hamkorlarini o'chira oladi
    if is_seller_user and not is_admin_user:
        if seller_id != user_id:
            await callback_query.answer("❌ Siz faqat o'zingiz uchun hamkor biriktira olasiz.", show_alert=True)
            return
    
    # Hamkorni topish
    partners = get_partners(seller_id)
    partner_to_remove = None
    for p in partners:
        if p.get("partner_id") == partner_id:
            partner_to_remove = p
            break
    
    if not partner_to_remove:
        await callback_query.answer("⚠️ Hamkor topilmadi", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer("✅ Hamkor o'chirildi")
    
    partner_name = partner_to_remove.get("partner_name", "N/A")
    remove_partner(seller_id, partner_id)
    
    success_text = (
        "❌ <b>Hamkor olib tashlandi</b>\n"
        "────────────\n\n"
        f"Hamkor ro'yxatdan o'chirildi:\n"
        f"• {partner_name} — {partner_id}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_partners"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=success_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_remove_partner")
async def callback_admin_remove_partner(callback_query: CallbackQuery, bot: Bot):
    """Show partners list for removal"""
    user_id = callback_query.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Agar sotuvchi bo'lsa, faqat o'z hamkorlarini ko'radi
    if is_seller_user and not is_admin_user:
        partners_list = get_partners(user_id)
    else:
        # Admin barcha hamkorlarni ko'radi
        partners_list = get_partners()
    
    if not partners_list:
        text = (
            "➖ <b>Hamkorni o'chirish</b>\n"
            "────────────\n\n"
            "Hozircha hamkorlar mavjud emas."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_partners"
                    )
                ]
            ]
        )
    else:
        text = (
            "➖ <b>Hamkorni o'chirish</b>\n"
            "────────────\n\n"
            "O'chirish uchun hamkorni tanlang:"
        )
        keyboard_buttons = []
        for partner in partners_list:
            seller_id = partner.get("seller_id", "N/A")
            seller_name = partner.get("seller_name", "N/A")
            partner_name = partner.get("partner_name", "N/A")
            partner_id = partner.get("partner_id", "N/A")
            if is_seller_user and not is_admin_user:
                # Sotuvchi faqat o'z hamkorlarini o'chira oladi
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"❌ {partner_name} — {partner_id}",
                        callback_data=f"admin_remove_partner_confirm:{partner_id}"
                    )
                ])
            else:
                # Admin barcha hamkorlarni o'chira oladi
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"❌ {partner_name} ({seller_name}) — {partner_id}",
                        callback_data=f"admin_remove_partner_confirm:{seller_id}:{partner_id}"
                    )
                ])
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="admin_partners"
            )
        ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_list_partners")
async def callback_admin_list_partners(callback_query: CallbackQuery, bot: Bot):
    """Show partners list"""
    user_id = callback_query.from_user.id
    is_admin_user = is_any_admin(user_id)
    is_seller_user = is_seller(user_id)
    
    if not is_admin_user and not is_seller_user:
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Agar sotuvchi bo'lsa, faqat o'z hamkorlarini ko'radi
    if is_seller_user and not is_admin_user:
        partners_list = get_partners(user_id)
    else:
        # Admin barcha hamkorlarni ko'radi
        partners_list = get_partners()
    
    if not partners_list:
        partners_text = "Hozircha hamkorlar mavjud emas"
    else:
        partners_display = []
        for partner in partners_list:
            seller_id = partner.get("seller_id", "N/A")
            seller_name = partner.get("seller_name", "N/A")
            partner_name = partner.get("partner_name", "N/A")
            partner_id = partner.get("partner_id", "N/A")
            # Har bir hamkor uchun sotuvchi ismini ko'rsatish
            partners_display.append(f"• {partner_name} — {partner_id}\n  ↳ Sotuvchi: {seller_name}")
        partners_text = "\n".join(partners_display)
    
    list_text = (
        "📋 <b>Hamkorlar ro'yxati</b>\n"
        "────────────\n\n"
        f"{partners_text}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_partners"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=list_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


# ==================== IMAGES FILE_ID CONVERSION ====================

def _validate_image_url(image_url: str) -> Tuple[bool, str]:
    """
    Validate image_url before processing.
    
    Returns:
        (is_valid, error_message)
    """
    if not image_url or not isinstance(image_url, str):
        return False, "Rasm linki mavjud emas"
    
    image_url = image_url.strip()
    if not image_url:
        return False, "Rasm linki bo'sh"
    
    # Check if it's a valid URL
    if not (image_url.startswith('http://') or image_url.startswith('https://')):
        return False, "Rasm linki noto'g'ri formatda"
    
    # Check if it's Google Drive link
    if 'drive.google.com' not in image_url:
        return False, "Rasm linki Google Drive formatida emas"
    
    # Try to extract file_id
    file_id = None
    if '/file/d/' in image_url:
        try:
            file_id = image_url.split('/file/d/')[1].split('/')[0]
        except Exception:
            pass
    elif 'id=' in image_url:
        try:
            file_id = image_url.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
        except Exception:
            pass
    
    if not file_id or len(file_id) < 10:
        return False, "Drive linkdan rasm ID si topilmadi yoki noto'g'ri"
    
    return True, ""


def _explain_error_detailed(error: Exception, image_url: str = "") -> dict:
    """
    Comprehensive error explanation system.
    Detects error type, translates to simple Uzbek, explains cause, suggests action.
    
    Returns:
        {
            "explanation": "Simple explanation in Uzbek",
            "reason": "Why it happened",
            "action": "What admin should do",
            "error_type": "Error category for grouping"
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
        explanation = "Telegram bu linkni rasm deb qabul qilmadi"
        reason = "Ko'pincha Google Drive preview sahifasi yoki HTML sahifa bo'lganda chiqadi. Telegram faqat to'g'ridan-to'g'ri rasm fayllarini qabul qiladi."
        action = "Rasmni JPG/PNG formatga o'tkazing yoki to'g'ridan-to'g'ri rasm linkidan foydalaning. Google Drive linkini 'uc?export=view&id=' formatiga o'tkazing."
    
    # ========== HTTP URL CONTENT ERRORS ==========
    elif "failed to get http url content" in error_str or "failed to get url" in error_str or "http url content" in error_str:
        error_type = "http_fetch_failed"
        explanation = "Rasm linkidan ma'lumot olishda muammo"
        reason = "Internet aloqasi uzilgan, server javob bermayapti yoki link ishlamayapti."
        action = "Internet aloqasini tekshiring. Linkni brauzerda ochib ko'ring. Agar ishlamasa, yangi link yarating."
    
    # ========== TIMEOUT ERRORS ==========
    elif "timeout" in error_str or "timed out" in error_str or "time out" in error_str:
        error_type = "timeout"
        explanation = "Rasm yuklash vaqti tugadi"
        reason = "Internet aloqasi sekin yoki server javob bermayapti. Telegram 30 soniyadan keyin kutishni to'xtatadi."
        action = "Internet aloqasini tekshiring. Kattaroq rasmlarni siqib, kichikroq qiling. Yoki rasmni boshqa joyga yuklab, yangi link yarating."
    
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
        action = "Google Drive'da rasmni 'Har kim ko'rishi mumkin' deb sozlang. Yoki yangi ochiq link yarating."
    
    # ========== UNSUPPORTED MEDIA TYPE ERRORS ==========
    elif "unsupported media type" in error_str or "media type" in error_str or "content type" in error_str:
        error_type = "unsupported_media"
        explanation = "Rasm formati qo'llab-quvvatlanmaydi"
        reason = "Telegram faqat JPG, PNG, GIF, WEBP formatlarini qabul qiladi. Boshqa formatlar (PDF, DOCX, va h.k.) ishlamaydi."
        action = "Rasmni JPG yoki PNG formatga o'tkazing. Online konvertorlardan foydalaning (masalan, convertio.co)."
    
    # ========== FLOOD/RATE LIMIT ERRORS ==========
    elif "flood" in error_str or "retry" in error_str or "rate limit" in error_str or "retry after" in error_str:
        error_type = "rate_limit"
        explanation = "Telegram limiti: juda ko'p so'rov"
        reason = "Telegram bir vaqtning o'zida juda ko'p rasm yuborishni taqiqlaydi. Bu himoya mexanizmi."
        action = "Biroz kutib turing (1-2 daqiqa), keyin qayta urinib ko'ring. Jarayon avtomatik davom etadi."
    
    # ========== BAD REQUEST / INVALID ERRORS ==========
    elif "bad request" in error_str or "invalid" in error_str or "bad url" in error_str:
        error_type = "bad_request"
        explanation = "Rasm linki noto'g'ri yoki o'chirilgan"
        reason = "Link formati noto'g'ri, rasm o'chirilgan yoki Telegram uni tushuna olmayapti."
        action = "Linkni brauzerda ochib tekshiring. Agar ishlamasa, yangi rasm yuklab, yangi link yarating."
    
    # ========== GOOGLE DRIVE SPECIFIC ERRORS ==========
    elif "drive" in error_str or "google drive" in error_str:
        error_type = "drive_error"
        explanation = "Google Drive linki ishlamayapti"
        reason = "Link noto'g'ri formatda, yopiq yoki rasm o'chirilgan."
        action = "Google Drive'da rasmni 'Har kim ko'rishi mumkin' deb sozlang. Linkni 'uc?export=view&id=FILE_ID' formatiga o'tkazing."
    
    # ========== CONNECTION ERRORS ==========
    elif "connection" in error_str or "network" in error_str or "connect" in error_str:
        error_type = "connection"
        explanation = "Internet aloqasi muammosi"
        reason = "Internet aloqasi uzilgan yoki serverga ulanib bo'lmadi."
        action = "Internet aloqasini tekshiring. Keyinroq qayta urinib ko'ring."
    
    # ========== DEFAULT / UNKNOWN ERRORS ==========
    else:
        error_type = "unknown"
        explanation = "Rasm yuborishda muammo yuz berdi"
        reason = f"Texnik xato: {str(error)[:100]}"
        action = "Xatoni log faylida ko'rib chiqing. Agar takrorlansa, rasmni boshqa formatga o'tkazing yoki yangi link yarating."
    
    return {
        "explanation": explanation,
        "reason": reason,
        "action": action,
        "error_type": error_type
    }


def _get_user_friendly_error(error: Exception, image_url: str = "") -> str:
    """
    Convert technical error to user-friendly message (backward compatibility).
    Uses detailed explanation system.
    """
    error_info = _explain_error_detailed(error, image_url)
    return error_info["explanation"]


def _group_errors_by_type(errors_list: list) -> dict:
    """
    Group errors by error_type and sheet_name for summary statistics.
    
    Returns:
        {
            "by_type": {"error_type": count},
            "by_sheet": {"sheet_name": count},
            "total": total_count
        }
    """
    grouped = {
        "by_type": {},
        "by_sheet": {},
        "total": len(errors_list)
    }
    
    for err in errors_list:
        # Group by error type
        error_type = err.get("error_type", "unknown")
        grouped["by_type"][error_type] = grouped["by_type"].get(error_type, 0) + 1
        
        # Group by sheet
        sheet_name = err.get("sheet_name", "unknown")
        grouped["by_sheet"][sheet_name] = grouped["by_sheet"].get(sheet_name, 0) + 1
    
    return grouped


def _extract_drive_file_id(image_url: str) -> Tuple[bool, str, str]:
    """
    Extract file_id from Google Drive link.
    
    Returns:
        (success, file_id, error_message)
    """
    if not image_url or not isinstance(image_url, str):
        return False, "", "Rasm linki mavjud emas"
    
    image_url = image_url.strip()
    
    file_id = None
    if '/file/d/' in image_url:
        try:
            file_id = image_url.split('/file/d/')[1].split('/')[0]
        except Exception:
            return False, "", "Drive linkdan ID ajratib bo'lmadi"
    elif 'id=' in image_url:
        try:
            file_id = image_url.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
        except Exception:
            return False, "", "Drive linkdan ID ajratib bo'lmadi"
    
    if not file_id or len(file_id) < 10:
        return False, "", "Drive link yopiq yoki ID topilmadi"
    
    return True, file_id, ""


@router.callback_query(F.data == "admin_images_fileid")
async def callback_admin_images_fileid(callback_query: CallbackQuery, bot: Bot):
    """Show images file_id conversion menu"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    menu_text = (
        "🖼️ <b>Rasmlarni file_id qilish</b>\n"
        "────────────\n\n"
        "Qaysi sheetdagi rasmlarni file_id ga o'tkazamiz?\n\n"
        "Bu jarayon:\n"
        "• Sheetdagi barcha image_url larni yuboradi\n"
        "• file_id larni oladi\n"
        "• Cache'ga saqlaydi\n"
        "• Test rasmlarni o'chiradi"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Sheets2",
                    callback_data="admin_convert_images:sheets2"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets4",
                    callback_data="admin_convert_images:sheets4"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets5",
                    callback_data="admin_convert_images:sheets5"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets6",
                    callback_data="admin_convert_images:sheets6"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_main"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_convert_images:"))
async def callback_admin_convert_images(callback_query: CallbackQuery, bot: Bot):
    """Convert images from specified sheet to file_id - ENHANCED VERSION"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    data = callback_query.data.split(":", 1)
    if len(data) < 2:
        return
    
    sheet_name = data[1]
    
    await callback_query.answer("⏳ Jarayon boshlandi...", show_alert=False)
    
    chat_id = callback_query.message.chat.id
    
    # Show progress message
    progress_text = (
        f"🖼️ <b>{sheet_name.upper()} rasmlarini file_id ga o'tkazish</b>\n"
        "────────────\n\n"
        "⏳ Jarayon davom etmoqda...\n"
        "Iltimos, kuting."
    )
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=progress_text
        )
    except Exception:
        pass
    
    try:
        from services.google_sheet import CACHE, GoogleSheetService, get_file_id_for_code
        from services.product_utils import normalize_code
        
        # Initialize image_map if not exists
        if "image_map" not in CACHE:
            CACHE["image_map"] = {}
        
        image_map = CACHE["image_map"]
        sheet_service = GoogleSheetService()
        
        # Get records from specified sheet
        records = []
        if sheet_name == "sheets2":
            records = CACHE.get("sheets2_full", [])
        elif sheet_name == "sheets4":
            records = CACHE.get("sheets4", [])
        elif sheet_name == "sheets5":
            records = CACHE.get("sheets5", [])
        elif sheet_name == "sheets6":
            records = CACHE.get("sheets6", [])
        else:
            raise ValueError(f"Unknown sheet: {sheet_name}")
        
        if not records:
            raise ValueError(f"{sheet_name} bo'sh yoki yuklanmagan")
        
        # ========== 1️⃣ SHEETS OLDINDAN TEKSHIRUV ==========
        # Pre-check all records before sending
        errors_list = []  # List of error dicts: {row_index, sheet_name, model_code, image_url, reason}
        valid_records = []  # Records that passed validation
        
        logger.info(f"Starting pre-check for {len(records)} records in {sheet_name}")
        
        for row_index, record in enumerate(records, start=1):
            try:
                # Get code
                code = record.get("code") or record.get("_code_original", "")
                if not code:
                    continue
                
                code_norm = normalize_code(code)
                if not code_norm:
                    continue
                
                # Skip if already has file_id
                existing_file_id = get_file_id_for_code(code)
                if existing_file_id:
                    continue  # Already has file_id, skip
                
                # Get image_url from various possible column names
                image_url = (record.get("image_url") or record.get("imageurl") or 
                           record.get("image url") or record.get("image") or "")
                
                # ========== PRE-CHECK: Validate image_url ==========
                is_valid, validation_error = _validate_image_url(image_url)
                if not is_valid:
                    errors_list.append({
                        "row_index": row_index,
                        "sheet_name": sheet_name,
                        "model_code": code,
                        "image_url": image_url[:100] if image_url else "",  # Limit length
                        "reason": validation_error,
                        "detailed_reason": validation_error,
                        "action": "Rasm linkini tekshiring va to'g'ri Google Drive linkini kiriting",
                        "error_type": "validation"
                    })
                    logger.warning(f"Pre-check failed for row {row_index}, code {code}: {validation_error}")
                    continue
                
                # ========== PRE-CHECK: Extract file_id from Drive link ==========
                success, file_id, extract_error = _extract_drive_file_id(image_url)
                if not success:
                    errors_list.append({
                        "row_index": row_index,
                        "sheet_name": sheet_name,
                        "model_code": code,
                        "image_url": image_url[:100],
                        "reason": extract_error,
                        "detailed_reason": extract_error,
                        "action": "Google Drive linkini 'Har kim ko'rishi mumkin' deb sozlang yoki yangi ochiq link yarating",
                        "error_type": "drive_extraction"
                    })
                    logger.warning(f"Drive file_id extraction failed for row {row_index}, code {code}: {extract_error}")
                    continue
                
                # Convert Google Drive link to direct URL
                converted_url = sheet_service._convert_google_drive_link(image_url.strip())
                if not converted_url:
                    errors_list.append({
                        "row_index": row_index,
                        "sheet_name": sheet_name,
                        "model_code": code,
                        "image_url": image_url[:100],
                        "reason": "Rasm linkini to'g'ri formatga o'tkazib bo'lmadi",
                        "detailed_reason": "Google Drive linkini to'g'ri formatga o'tkazishda muammo",
                        "action": "Linkni 'uc?export=view&id=FILE_ID' formatiga o'tkazing",
                        "error_type": "url_conversion"
                    })
                    continue
                
                # Record passed all checks - add to valid_records
                valid_records.append({
                    "row_index": row_index,
                    "code": code,
                    "code_norm": code_norm,
                    "converted_url": converted_url
                })
                
            except Exception as e:
                logger.error(f"Error in pre-check for row {row_index} in {sheet_name}: {e}")
                error_info = _explain_error_detailed(e, "")
                errors_list.append({
                    "row_index": row_index,
                    "sheet_name": sheet_name,
                    "model_code": record.get("code", "N/A"),
                    "image_url": "",
                    "reason": error_info["explanation"],
                    "detailed_reason": error_info["reason"],
                    "action": error_info["action"],
                    "error_type": error_info["error_type"]
                })
                continue
        
        logger.info(f"Pre-check completed: {len(valid_records)} valid, {len(errors_list)} errors")
        
        # ========== 2️⃣ TELEGRAM YUBORISH BOSQICHI ==========
        processed = 0
        updated = 0
        skipped = 0
        test_message_ids = []
        
        for record_data in valid_records:
            try:
                row_index = record_data["row_index"]
                code = record_data["code"]
                code_norm = record_data["code_norm"]
                converted_url = record_data["converted_url"]
                
                # Send photo to get file_id
                try:
                    sent_msg = await bot.send_photo(
                        chat_id=chat_id,
                        photo=converted_url
                    )
                    
                    if sent_msg and sent_msg.photo:
                        file_id = sent_msg.photo[-1].file_id  # Get highest quality
                        test_message_ids.append(sent_msg.message_id)
                        
                        # Update cache - FAQAT file_id saqlanadi
                        image_map[code_norm] = file_id
                        updated += 1
                    
                    processed += 1
                    
                    # Har 10 rasmda 1-2 soniya kutish
                    if processed % 10 == 0:
                        await asyncio.sleep(1.5)
                        
                except TelegramRetryAfter as e:
                    # FloodWait - faqat shu rasmini skip qil, botni to'xtatma
                    wait_time = e.retry_after
                    logger.warning(f"FloodWait for code {code}: wait {wait_time}s")
                    error_info = _explain_error_detailed(e, converted_url)
                    errors_list.append({
                        "row_index": row_index,
                        "sheet_name": sheet_name,
                        "model_code": code,
                        "image_url": converted_url[:100],
                        "reason": error_info["explanation"],
                        "detailed_reason": error_info["reason"],
                        "action": error_info["action"],
                        "error_type": error_info["error_type"]
                    })
                    # Kichik kutish va davom etish
                    await asyncio.sleep(min(wait_time, 5))
                    continue
                    
                except TelegramAPIError as e:
                    # Boshqa Telegram API xatolari - faqat shu rasmini skip qil
                    error_info = _explain_error_detailed(e, converted_url)
                    logger.warning(f"Telegram API error for code {code}: {e}")
                    errors_list.append({
                        "row_index": row_index,
                        "sheet_name": sheet_name,
                        "model_code": code,
                        "image_url": converted_url[:100],
                        "reason": error_info["explanation"],
                        "detailed_reason": error_info["reason"],
                        "action": error_info["action"],
                        "error_type": error_info["error_type"]
                    })
                    continue
                    
                except Exception as e:
                    # Boshqa xatolar - faqat shu rasmini skip qil
                    error_info = _explain_error_detailed(e, converted_url)
                    logger.warning(f"Error sending photo for code {code}: {e}")
                    errors_list.append({
                        "row_index": row_index,
                        "sheet_name": sheet_name,
                        "model_code": code,
                        "image_url": converted_url[:100],
                        "reason": error_info["explanation"],
                        "detailed_reason": error_info["reason"],
                        "action": error_info["action"],
                        "error_type": error_info["error_type"]
                    })
                    continue
                    
            except Exception as e:
                # Record processing error - faqat shu recordni skip qil
                logger.error(f"Error processing record in {sheet_name}: {e}")
                error_info = _explain_error_detailed(e, "")
                errors_list.append({
                    "row_index": record_data.get("row_index", 0),
                    "sheet_name": sheet_name,
                    "model_code": record_data.get("code", "N/A"),
                    "image_url": "",
                    "reason": error_info["explanation"],
                    "detailed_reason": error_info["reason"],
                    "action": error_info["action"],
                    "error_type": error_info["error_type"]
                })
                continue
        
        # Count skipped (already had file_id)
        for record in records:
            try:
                code = record.get("code") or record.get("_code_original", "")
                if code:
                    code_norm = normalize_code(code)
                    if code_norm and get_file_id_for_code(code):
                        skipped += 1
            except Exception:
                pass
        
        # Delete test messages - darhol o'chirish
        deleted_count = 0
        for msg_id in test_message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                deleted_count += 1
                await asyncio.sleep(0.1)  # Small delay between deletions
            except Exception:
                pass
        
        # ========== 4️⃣ ADMIN PANEL HISOBOTI (ODDIY TILDA) ==========
        # Prepare result message with detailed error explanations
        if errors_list:
            # Group errors for statistics
            error_groups = _group_errors_by_type(errors_list)
            error_count = len(errors_list)
            errors_preview = errors_list[:5]  # Faqat 5 tasini ko'rsat
            
            result_text = (
                f"✅ <b>{sheet_name.upper()}: file_id yangilandi</b>\n"
                "────────────\n\n"
                f"📊 <b>Statistika:</b>\n"
                f"• Qayta ishlangan: {processed}\n"
                f"• Yangilangan: {updated}\n"
                f"• O'tkazib yuborilgan (file_id bor): {skipped}\n"
                f"• O'chirilgan test rasmlar: {deleted_count}\n\n"
                f"❌ <b>{error_count} ta muammo topildi</b>\n\n"
            )
            
            # Show error grouping summary
            if error_groups["by_type"]:
                result_text += "<b>📋 Xatolar guruhlangan:</b>\n"
                # Sort by count (descending)
                sorted_types = sorted(error_groups["by_type"].items(), key=lambda x: x[1], reverse=True)
                for err_type, count in sorted_types[:5]:  # Top 5 error types
                    result_text += f"• {err_type}: {count} ta\n"
                if len(sorted_types) > 5:
                    result_text += f"• ... va yana {len(sorted_types) - 5} ta tur\n"
                result_text += "\n"
            
            # 5 ta xatoni batafsil ko'rsatish (yangi format)
            if errors_preview:
                result_text += "<b>🔍 Muammolar (5 ta):</b>\n\n"
                for i, err in enumerate(errors_preview, 1):
                    reason_default = "Noma'lum"
                    result_text += (
                        f"❌ <b>Xato #{i}</b>\n"
                        f"📄 Sheet: {err.get('sheet_name', 'N/A')}\n"
                        f"🔢 Kod: {err.get('model_code', 'N/A')}\n"
                        f"📌 Sabab: {err.get('reason', reason_default)}\n"
                    )
                    # Show detailed reason if available
                    if err.get('detailed_reason'):
                        result_text += f"💭 Tushuntirish: {err.get('detailed_reason')}\n"
                    # Show action suggestion if available
                    if err.get('action'):
                        result_text += f"💡 Tavsiya: {err.get('action')}\n"
                    result_text += "\n"
            
            if error_count > 5:
                result_text += f"<i>... va yana {error_count - 5} ta muammo</i>\n\n"
            
            result_text += "✅ file_id lar image_map ga saqlandi!"
            
            # Qolgan xatolarni .txt fayl qilib yuborish (batafsil formatda)
            if error_count > 5:
                try:
                    error_file_content = f"Xatolar ro'yxati - {sheet_name.upper()}\n"
                    error_file_content += f"Jami: {error_count} ta muammo\n"
                    error_file_content += "=" * 70 + "\n\n"
                    
                    # Group by error type in file
                    errors_by_type = {}
                    for err in errors_list:
                        err_type = err.get("error_type", "unknown")
                        if err_type not in errors_by_type:
                            errors_by_type[err_type] = []
                        errors_by_type[err_type].append(err)
                    
                    for err_type, type_errors in errors_by_type.items():
                        error_file_content += f"\n{'='*70}\n"
                        error_file_content += f"XATO TURI: {err_type.upper()} ({len(type_errors)} ta)\n"
                        error_file_content += f"{'='*70}\n\n"
                        
                        for err in type_errors:
                            reason_default = "Noma'lum"
                            error_file_content += (
                                f"❌ Xato topildi\n"
                                f"📄 Sheet: {err.get('sheet_name', 'N/A')}\n"
                                f"🔢 Kod: {err.get('model_code', 'N/A')}\n"
                                f"📌 Sabab: {err.get('reason', reason_default)}\n"
                            )
                            if err.get('detailed_reason'):
                                error_file_content += f"💭 Tushuntirish: {err.get('detailed_reason')}\n"
                            if err.get('action'):
                                error_file_content += f"💡 Tavsiya: {err.get('action')}\n"
                            if err.get('image_url'):
                                error_file_content += f"🔗 Link: {err.get('image_url')}\n"
                            error_file_content += "-" * 70 + "\n\n"
                    
                    error_file = BufferedInputFile(
                        error_file_content.encode('utf-8'),
                        filename=f"xatolar_{sheet_name}.txt"
                    )
                    
                    await bot.send_document(
                        chat_id=chat_id,
                        document=error_file,
                        caption=f"📄 Qolgan {error_count - 5} ta muammo (batafsil)"
                    )
                except Exception as e:
                    logger.error(f"Error sending error file: {e}")
        else:
            # Xatolar yo'q
            result_text = (
                f"✅ <b>{sheet_name.upper()}: file_id yangilandi</b>\n"
                "────────────\n\n"
                f"📊 <b>Statistika:</b>\n"
                f"• Qayta ishlangan: {processed}\n"
                f"• Yangilangan: {updated}\n"
                f"• O'tkazib yuborilgan (file_id bor): {skipped}\n"
                f"• O'chirilgan test rasmlar: {deleted_count}\n\n"
                f"✅ file_id lar image_map ga saqlandi!"
            )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_images_fileid"
                    )
                ]
            ]
        )
        
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=callback_query.message.message_id,
                text=result_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
        
        # Log admin action
        record_admin_action(
            admin_id=user_id,
            section="Admin panel",
            action=f"Rasmlarni file_id qilish: {sheet_name}",
            result=f"✅ {updated} ta yangilandi, {len(errors_list)} ta xato",
            username=callback_query.from_user.username,
            first_name=callback_query.from_user.first_name
        )
        
    except Exception as e:
        error_text = (
            f"❌ <b>Xatolik</b>\n"
            "────────────\n\n"
            f"{sheet_name.upper()} rasmlarini file_id ga o'tkazishda xatolik yuz berdi:\n"
            f"{str(e)}"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_images_fileid"
                    )
                ]
            ]
        )
        
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=callback_query.message.message_id,
                text=error_text,
                reply_markup=keyboard
            )
        except Exception:
            pass


# ==================== STATISTICS ====================

async def _show_statistics_logs(callback_query: CallbackQuery, bot: Bot, filter_type: str = "all"):
    """Statistika loglarini ko'rsatish (filtr bo'yicha)."""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    stats = get_stats()
    
    # Format TOP-5 products
    top_products_text = "• Yo'q"
    if stats['top_5_products']:
        top_products_text = "\n".join([
            f"{i+1}. {code} ({count} marta)" 
            for i, (code, count) in enumerate(stats['top_5_products'])
        ])
    
    # Format TOP-5 collections
    top_collections_text = "• Yo'q"
    if stats['top_5_collections']:
        top_collections_text = "\n".join([
            f"{i+1}. {name} ({count} marta)" 
            for i, (name, count) in enumerate(stats['top_5_collections'])
        ])
    
    # Format TOP-5 users
    top_users_text = "• Yo'q"
    if stats['top_5_users']:
        top_users_text = "\n".join([
            f"{i+1}. {name} ({count} marta)" 
            for i, (name, count) in enumerate(stats['top_5_users'])
        ])
    
    # Faolliklar jurnalini filtr bo'yicha olish
    activity_log = get_activity_log(limit=100, filter_type=filter_type)
    
    # Filter label
    filter_labels = {
        "all": "Hammasi",
        "today": "Bugungi loglar",
        "yesterday": "Kechagi loglar",
        "last_7_days": "Oxirgi 7 kun"
    }
    filter_label = filter_labels.get(filter_type, "Hammasi")
    
    activity_log_text = ""
    keyboard_buttons = []
    
    if activity_log:
        activity_log_text = f"\n\n— <b>FAOLLIKLAR JURNALI ({filter_label}) —</b>\n\n"
        for idx, entry in enumerate(activity_log, 1):
            timestamp = entry.get('timestamp', 'N/A')
            if isinstance(timestamp, datetime):
                timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M")
            elif isinstance(timestamp, str):
                try:
                    dt = datetime.fromisoformat(timestamp)
                    timestamp_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    timestamp_str = timestamp
            else:
                timestamp_str = str(timestamp)
            user_name = entry.get('user_name', f"User {entry.get('user_id', 'N/A')}")
            role = entry.get('role', 'User')
            section = entry.get('section', 'N/A')
            action = entry.get('action', 'N/A')
            result = entry.get('result', 'N/A')
            
            # Qisqa ko'rinish (matn uzun bo'lsa)
            full_text = (
                f"[{idx}] {timestamp_str}\n"
                f"👤 Foydalanuvchi: {user_name}\n"
                f"🔑 Roli: {role}\n"
                f"📌 Bo'lim: {section}\n"
                f"📝 Amal: {action}\n"
                f"📊 Natija: {result}"
            )
            
            # Agar matn uzun bo'lsa (300+ belgi), qisqartirilgan versiyani ko'rsatish va "Ko'rish" tugmasi qo'shish
            if len(full_text) > 300:
                short_text = (
                    f"[{idx}] {timestamp_str}\n"
                    f"👤 {user_name} | {role}\n"
                    f"📌 {section} | {action[:50]}{'...' if len(action) > 50 else ''}"
                )
                activity_log_text += short_text + "\n\n"
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"▶️ [{idx}] Ko'rish",
                        callback_data=f"stats_view_log:{filter_type}:{idx-1}"
                    )
                ])
            else:
                activity_log_text += full_text + "\n\n"
    else:
        activity_log_text = f"\n\n— <b>FAOLLIKLAR JURNALI ({filter_label}) —</b>\n\n• Hozircha faolliklar yo'q"
    
    stats_text = (
        "📊 <b>Statistika</b>\n"
        "────────────\n\n"
        "— <b>UMUMIY —</b>\n"
        f"• 👤 Jami foydalanuvchilar soni: {stats['total_unique_users']}\n"
        f"• 🟢 Bugun faol foydalanuvchilar: {stats['today_active_users']}\n"
        f"• 📩 Jami foydalanuvchi so'rovlari: {stats['total_requests']}\n"
        f"• ✅ Muvaffaqiyatli so'rovlar: {stats['today_found']}\n"
        f"• ❌ Muvaffaqiyatsiz so'rovlar: {stats['total_not_found']}\n"
        f"• 💰 Narx bo'yicha so'rovlar: {stats['price_requests']}\n\n"
        "— <b>ENG KO'P —</b>\n"
        f"• 🔝 TOP-5 eng ko'p so'ralgan mahsulot kodlari:\n{top_products_text}\n\n"
        f"• 📂 TOP-5 eng ko'p tanlangan kolleksiyalar:\n{top_collections_text}\n\n"
        f"• 👥 TOP-5 eng faol foydalanuvchilar:\n{top_users_text}\n\n"
        "— <b>OXIRGI —</b>\n"
        f"• ⏰ Oxirgi so'rov vaqti: {stats['last_request_time']}\n"
        f"• 👤 Oxirgi so'rov qilgan foydalanuvchi: {stats['last_user']}\n\n"
        "📅 <b>Bugun:</b>\n"
        f"• So'rovlar soni: {stats['today_requests']}\n"
        f"• Topilgan so'rovlar: {stats['today_found']}\n"
        f"• Topilmagan so'rovlar: {stats['today_not_found']}"
        f"{activity_log_text}"
    )
    
    # Statistika filtrlari tugmalari
    filter_buttons = [
        [
            InlineKeyboardButton(text="🕒 Bugungi loglar", callback_data="stats_filter:today"),
            InlineKeyboardButton(text="📅 Kechagi loglar", callback_data="stats_filter:yesterday")
        ],
        [
            InlineKeyboardButton(text="📆 Oxirgi 7 kun", callback_data="stats_filter:last_7_days"),
            InlineKeyboardButton(text="📦 Hammasi", callback_data="stats_filter:all")
        ]
    ]
    
    # Keshni tozalash tugmalari
    clear_buttons = [
        [InlineKeyboardButton(text="🧹 Keshni tozalash (loglar)", callback_data="stats_clear_cache")],
        [InlineKeyboardButton(text="🗑️ Umumiy statistika tozalash", callback_data="stats_clear_general")]
    ]
    
    # RAM hajmi ma'lumotlari
    memory_info = get_stats_memory_info()
    memory_text = f"\n\n💾 <b>RAM hajmi:</b> {memory_info['current_size_mb']} MB / {memory_info['limit_mb']} MB ({memory_info['percentage']}%)"
    
    # Agar limit oshib ketgan bo'lsa, avtomatik tozalash
    if memory_info['percentage'] >= 100:
        cleared, _ = check_and_auto_clear_stats()
        if cleared:
            memory_text += "\n⚠️ <b>Avtomatik tozalandi!</b>"
    
    stats_text += memory_text
    
    # Orqaga tugmasi
    back_button = [
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_main")]
    ]
    
    # Barcha tugmalarni birlashtirish
    all_buttons = keyboard_buttons + filter_buttons + clear_buttons + back_button
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=all_buttons)
    
    # Telegram xabar limitini tekshirish (4096 belgi)
    MAX_MESSAGE_LENGTH = 4096
    if len(stats_text) > MAX_MESSAGE_LENGTH:
        # Xabar juda uzun, faqat asosiy statistikani ko'rsatish
        stats_text = (
            "📊 <b>Statistika</b>\n"
            "────────────\n\n"
            "— <b>UMUMIY —</b>\n"
            f"• 👤 Jami foydalanuvchilar soni: {stats['total_unique_users']}\n"
            f"• 🟢 Bugun faol foydalanuvchilar: {stats['today_active_users']}\n"
            f"• 📩 Jami foydalanuvchi so'rovlari: {stats['total_requests']}\n\n"
            f"📅 <b>Bugun:</b>\n"
            f"• So'rovlar soni: {stats['today_requests']}\n"
            f"• Topilgan so'rovlar: {stats['today_found']}\n"
            f"• Topilmagan so'rovlar: {stats['today_not_found']}\n\n"
            "⚠️ <i>Loglar juda ko'p, filtrlardan foydalaning</i>"
        )
        # Faqat filter tugmalarini qoldirish
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=filter_buttons + clear_buttons + back_button
        )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=stats_text,
            reply_markup=keyboard
        )
    except Exception as e:
        # "Message is not modified" xatoligini e'tiborsiz qoldirish - bu normal holat
        error_str = str(e).lower()
        if "message is not modified" in error_str or "message_not_modified" in error_str:
            # Bu xatolik normal - xabar o'zgartirilmagan, shunchaki e'tiborsiz qoldiramiz
            pass
        else:
            # Boshqa xatoliklar uchun log qilish
            logger.error(f"Error showing statistics: {e}", exc_info=True)
            try:
                await bot.send_message(
                    chat_id=callback_query.message.chat.id,
                    text=f"❌ Xatolik: {str(e)}"
                )
            except Exception:
                pass
    
    await callback_query.answer()


@router.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback_query: CallbackQuery, bot: Bot):
    """Show statistics"""
    await _show_statistics_logs(callback_query, bot, "all")


@router.callback_query(F.data.startswith("stats_filter:"))
async def callback_stats_filter(callback_query: CallbackQuery, bot: Bot):
    """Statistika filtrlari"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    filter_type = callback_query.data.split(":")[1]
    await _show_statistics_logs(callback_query, bot, filter_type)


@router.callback_query(F.data.startswith("stats_view_log:"))
async def callback_stats_view_log(callback_query: CallbackQuery, bot: Bot):
    """Individual log yozuvini ko'rsatish"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Parse: stats_view_log:filter_type:index
    parts = callback_query.data.split(":")
    if len(parts) != 3:
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    filter_type = parts[1]
    try:
        index = int(parts[2])
    except ValueError:
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Filtrlangan loglarni olish
    activity_log = get_activity_log(limit=100, filter_type=filter_type)
    
    if index < 0 or index >= len(activity_log):
        await callback_query.answer("❌ Yozuv topilmadi", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    entry = activity_log[index]
    
    # To'liq log yozuvini formatlash
    timestamp = entry.get('timestamp', 'N/A')
    if isinstance(timestamp, datetime):
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(timestamp, str):
        try:
            dt = datetime.fromisoformat(timestamp)
            timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            timestamp_str = timestamp
    else:
        timestamp_str = str(timestamp)
    
    user_name = entry.get('user_name', f"User {entry.get('user_id', 'N/A')}")
    role = entry.get('role', 'User')
    section = entry.get('section', 'N/A')
    action = entry.get('action', 'N/A')
    result = entry.get('result', 'N/A')
    
    log_text = (
        f"📋 <b>LOG YOZUV</b>\n"
        f"────────────\n\n"
        f"⏰ <b>Vaqt:</b> {timestamp_str}\n"
        f"👤 <b>Foydalanuvchi:</b> {user_name}\n"
        f"🔑 <b>Roli:</b> {role}\n"
        f"📌 <b>Bo'lim:</b> {section}\n"
        f"📝 <b>Amal:</b> {action}\n"
        f"📊 <b>Natija:</b> {result}"
    )
    
    # Orqaga tugmasi (filtr turi bilan)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data=f"stats_filter:{filter_type}"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=log_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "stats_clear_general")
async def callback_stats_clear_general(callback_query: CallbackQuery, bot: Bot):
    """Umumiy statistika keshi tozalash"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # RAM hajmini tekshirish
    memory_info_before = get_stats_memory_info()
    
    # Umumiy statistika tozalash
    clear_general_stats_cache()
    
    # RAM hajmini qayta tekshirish
    memory_info_after = get_stats_memory_info()
    
    # Xabar ko'rsatish
    freed_mb = memory_info_before['current_size_mb'] - memory_info_after['current_size_mb']
    await callback_query.answer(
        f"✅ Umumiy statistika tozalandi!\n"
        f"Bo'shatildi: {freed_mb:.2f} MB\n"
        f"Joriy hajm: {memory_info_after['current_size_mb']} MB",
        show_alert=True
    )
    
    # Statistika sahifasiga qaytish
    await _show_statistics_logs(callback_query, bot, "all")


@router.callback_query(F.data == "stats_clear_cache")
async def callback_stats_clear_cache(callback_query: CallbackQuery, bot: Bot):
    """Keshni tozalash"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Barcha log fayllarini tozalash
    clear_all_log_files()
    
    # Xabar ko'rsatish
    await callback_query.answer("✅ Kesh tozalandi", show_alert=True)
    
    # Statistika sahifasiga qaytish
    await _show_statistics_logs(callback_query, bot, "all")


# ==================== USERS MANAGEMENT ====================

@router.callback_query(F.data == "admin_users")
async def callback_admin_users(callback_query: CallbackQuery, bot: Bot):
    """Show users list"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # [YANGI] Avtomatik tozalash: foydalanuvchi tarixi juda kattalashib ketmasin
    compact_users_stats_for_admin_users()

    # Get all users
    users = get_all_users()
    
    if not users:
        users_text = "📭 Foydalanuvchilar mavjud emas"
        keyboard = make_admin_back_keyboard()
        
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=users_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
        
        return
    
    # Create keyboard with all users as buttons
    keyboard_buttons = []
    for user_id_item, display_name, total_requests in users:
        button_text = f"👤 {display_name} | {total_requests} so'rov"
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"admin_user_detail:{user_id_item}"
            )
        ])
    
    # [YANGI] Foydalanuvchilar bo'limi uchun QO'LDA keshni tozalash tugmasi
    keyboard_buttons.append([
        InlineKeyboardButton(
            text="🧹 Keshni tozalash (foydalanuvchilar)",
            callback_data="admin_users_clear_cache"
        )
    ])
    
    # Add back button (eski joyi, o'zgarmaydi)
    keyboard_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="admin_main"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    users_text = "👥 <b>Foydalanuvchilar</b>\n\nQuyidagi foydalanuvchilardan birini tanlang:"
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=users_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_users_clear_cache")
async def callback_admin_users_clear_cache(callback_query: CallbackQuery, bot: Bot):
    """Admin foydalanuvchilar bo'limi: RAMdagi user statistikani qo'lda tozalash"""
    user_id = callback_query.from_user.id

    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return

    # RAMdagi foydalanuvchilar statistikasi va tarixini tozalaymiz
    clear_users_cache_for_admin_users()

    await callback_query.answer("✅ Foydalanuvchilar keshi tozalandi", show_alert=True)

    # Tozalangandan keyin yangilangan holatda "Foydalanuvchilar" menyusini qayta ochamiz
    await callback_admin_users(callback_query, bot)


@router.callback_query(F.data == "admin_clear_cache_files")
async def callback_admin_clear_cache_files(callback_query: CallbackQuery, bot: Bot):
    """Clear unnecessary cache files (old code remnants and Python cache)"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    await callback_query.answer()
    
    import os
    import glob
    
    deleted_files = []
    total_size_freed = 0
    
    try:
        # 1. Delete order_notifications.json (old code remnant)
        if os.path.exists("order_notifications.json"):
            file_size = os.path.getsize("order_notifications.json")
            os.remove("order_notifications.json")
            deleted_files.append("order_notifications.json")
            total_size_freed += file_size
            logger.info(f"Deleted order_notifications.json ({file_size} bytes)")
        
        # 2. Delete Python cache files in services/__pycache__/
        pycache_dir = "services/__pycache__"
        if os.path.exists(pycache_dir):
            # Find specific old cache file
            old_cache_file = os.path.join(pycache_dir, "order_notifications.cpython-310.pyc")
            if os.path.exists(old_cache_file):
                file_size = os.path.getsize(old_cache_file)
                os.remove(old_cache_file)
                deleted_files.append("order_notifications.cpython-310.pyc")
                total_size_freed += file_size
                logger.info(f"Deleted {old_cache_file} ({file_size} bytes)")
            
            # Optionally clean all .pyc files (they will be regenerated)
            pyc_files = glob.glob(os.path.join(pycache_dir, "*.pyc"))
            for pyc_file in pyc_files:
                try:
                    file_size = os.path.getsize(pyc_file)
                    os.remove(pyc_file)
                    deleted_files.append(os.path.basename(pyc_file))
                    total_size_freed += file_size
                except Exception as e:
                    logger.error(f"Error deleting {pyc_file}: {e}")
        
        # Format size
        if total_size_freed < 1024:
            size_str = f"{total_size_freed} bytes"
        elif total_size_freed < 1024 * 1024:
            size_str = f"{total_size_freed / 1024:.2f} KB"
        else:
            size_str = f"{total_size_freed / (1024 * 1024):.2f} MB"
        
        # Build report
        if deleted_files:
            files_list = "\n".join([f"• {f}" for f in deleted_files[:10]])
            if len(deleted_files) > 10:
                files_list += f"\n... va yana {len(deleted_files) - 10} ta fayl"
            
            report = f"""🗑️ <b>Cache fayllar tozalandi</b>

<b>O'chirilgan fayllar:</b> {len(deleted_files)} ta
<b>Bo'shatilgan joy:</b> {size_str}

<b>Fayllar ro'yxati:</b>
{files_list}

✅ Barcha keraksiz cache fayllar tozalandi!"""
        else:
            report = """🗑️ <b>Cache fayllar tozalash</b>

✅ Barcha cache fayllar allaqachon tozalangan!

Keraksiz fayllar topilmadi."""
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_main")]
            ]
        )
        
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=report,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        logger.info(f"Cache files cleared by admin {user_id}: {len(deleted_files)} files, {size_str}")
        
    except Exception as e:
        logger.error(f"Error clearing cache files: {e}")
        await callback_query.answer("❌ Xatolik yuz berdi", show_alert=True)


@router.callback_query(F.data.startswith("admin_user_detail:"))
async def callback_admin_user_detail(callback_query: CallbackQuery, bot: Bot):
    """Show user details"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Extract user_id from callback data
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Get user details
    user_details = get_user_details(target_user_id)
    
    if not user_details:
        await callback_query.answer("❌ Foydalanuvchi topilmadi", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Format user details
    username_text = f"@{user_details['username']}" if user_details['username'] else "Yo'q"
    
    user_text = (
        "👤 <b>Foydalanuvchi ma'lumotlari</b>\n"
        "────────────\n\n"
        f"• Ismi: {user_details['first_name']}\n"
        f"• Username: {username_text}\n"
        f"• Jami so'rovlar: {user_details['total_requests']}\n"
        f"• Muvaffaqiyatli so'rovlar: {user_details['successful_requests']}\n"
        f"• Muvaffaqiyatsiz so'rovlar: {user_details['failed_requests']}\n"
        f"• Birinchi faollik: {user_details['first_activity']}\n"
        f"• Oxirgi faollik: {user_details['last_activity']}"
    )
    
    # Format history
    history = user_details.get('history', [])
    if history:
        user_text += "\n\n📄 <b>Foydalanish tarixi:</b>\n\n"
        for idx, entry in enumerate(history, 1):
            timestamp_str = entry['timestamp'].strftime("%Y-%m-%d %H:%M")
            user_text += (
                f"{idx}) [{timestamp_str}]\n"
                f"   • Bo'lim: {entry['section']}\n"
                f"   • So'rov: {entry['request_text']}\n"
                f"   • Natija: {entry['result']}\n\n"
            )
    
    # Check if user is blocked
    blocked = is_user_blocked(target_user_id)
    block_text = "✅ Blokdan chiqarish" if blocked else "🚫 Bloklash"
    
    # Check if user has limit
    user_limit = get_user_limit(target_user_id)
    limit_text = "⏳ Limit qo'yish" if not user_limit else f"⏳ Limit: {user_limit['daily_limit']}/kun"
    
    keyboard_buttons = []
    
    # Block/Unblock button
    keyboard_buttons.append([
        InlineKeyboardButton(
            text=block_text,
            callback_data=f"admin_user_block:{target_user_id}"
        )
    ])
    
    # Limit button
    keyboard_buttons.append([
        InlineKeyboardButton(
            text=limit_text,
            callback_data=f"admin_user_limit:{target_user_id}"
        )
    ])
    
    # Delete button
    keyboard_buttons.append([
        InlineKeyboardButton(
            text="🗑 Foydalanuvchini o'chirish",
            callback_data=f"admin_user_delete:{target_user_id}"
        )
    ])
    
    # Back button
    keyboard_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="admin_users"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=user_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


# ==================== USER MANAGEMENT ====================

@router.callback_query(F.data.startswith("admin_user_block:"))
async def callback_admin_user_block(callback_query: CallbackQuery, bot: Bot):
    """Block or unblock user"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    blocked = is_user_blocked(target_user_id)
    
    if blocked:
        unblock_user(target_user_id)
        action_text = "Blokdan chiqarildi"
    else:
        block_user(target_user_id)
        action_text = "Bloklandi"
    
    # Refresh user details view - create new callback_query with correct data
    from aiogram.types import CallbackQuery as CallbackQueryType
    fake_callback = type('obj', (object,), {
        'message': callback_query.message,
        'from_user': callback_query.from_user,
        'data': f"admin_user_detail:{target_user_id}"
    })()
    
    await callback_admin_user_detail(fake_callback, bot)
    await callback_query.answer(f"✅ Foydalanuvchi {action_text}")


@router.callback_query(F.data.startswith("admin_user_limit:"))
async def callback_admin_user_limit(callback_query: CallbackQuery, bot: Bot):
    """Show user limit options"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    user_limit = get_user_limit(target_user_id)
    current_limit = user_limit['daily_limit'] if user_limit else None
    
    text = (
        "⏳ <b>Limit qo'yish</b>\n"
        "────────────\n\n"
    )
    
    if current_limit:
        text += f"Joriy limit: {current_limit} so'rov/kun\n\n"
    
    text += "Limit variantini tanlang:"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Limit olib tashlash",
                    callback_data=f"admin_user_limit_remove:{target_user_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Kuniga 5 ta so'rov",
                    callback_data=f"admin_user_limit_set:{target_user_id}:5"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Kuniga 10 ta so'rov",
                    callback_data=f"admin_user_limit_set:{target_user_id}:10"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Kuniga 20 ta so'rov",
                    callback_data=f"admin_user_limit_set:{target_user_id}:20"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data=f"admin_user_detail:{target_user_id}"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_user_limit_set:"))
async def callback_admin_user_limit_set(callback_query: CallbackQuery, bot: Bot):
    """Set user limit"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        parts = callback_query.data.split(":")
        target_user_id = int(parts[1])
        limit = int(parts[2])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    set_user_limit(target_user_id, limit)
    
    # Refresh user details view - create new callback_query with correct data
    fake_callback = type('obj', (object,), {
        'message': callback_query.message,
        'from_user': callback_query.from_user,
        'data': f"admin_user_detail:{target_user_id}"
    })()
    
    await callback_admin_user_detail(fake_callback, bot)
    await callback_query.answer(f"✅ Limit qo'yildi: {limit} so'rov/kun")


@router.callback_query(F.data.startswith("admin_user_limit_remove:"))
async def callback_admin_user_limit_remove(callback_query: CallbackQuery, bot: Bot):
    """Remove user limit"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    remove_user_limit(target_user_id)
    
    # Refresh user details view - create new callback_query with correct data
    fake_callback = type('obj', (object,), {
        'message': callback_query.message,
        'from_user': callback_query.from_user,
        'data': f"admin_user_detail:{target_user_id}"
    })()
    
    await callback_admin_user_detail(fake_callback, bot)
    await callback_query.answer("✅ Limit olib tashlandi")


@router.callback_query(F.data.startswith("admin_user_delete:"))
async def callback_admin_user_delete(callback_query: CallbackQuery, bot: Bot):
    """Show delete confirmation"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    text = (
        "🗑 <b>Foydalanuvchini o'chirish</b>\n"
        "────────────\n\n"
        "⚠️ Haqiqatdan ham foydalanuvchini o'chirmoqchimisiz?\n\n"
        "Bu amalni qaytarib bo'lmaydi."
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha",
                    callback_data=f"admin_user_delete_confirm:{target_user_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Yo'q",
                    callback_data=f"admin_user_detail:{target_user_id}"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_user_delete_confirm:"))
async def callback_admin_user_delete_confirm(callback_query: CallbackQuery, bot: Bot):
    """Confirm and delete user"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer("✅ Foydalanuvchi o'chirildi")
    
    delete_user_data(target_user_id)
    
    text = (
        "✅ <b>Foydalanuvchi o'chirildi</b>\n"
        "────────────\n\n"
        "Foydalanuvchi ma'lumotlari o'chirildi."
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_users"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass


# ==================== ADMINS MANAGEMENT ====================

@router.callback_query(F.data == "admin_admins")
async def callback_admin_admins(callback_query: CallbackQuery, bot: Bot):
    """Show admins list"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Format admins list (ism + ID) - admins.json dan
    main_admins_dict = get_main_admins()
    main_admins_list = []
    for admin_id_str, admin_name in main_admins_dict.items():
        admin_id = int(admin_id_str)
        main_admins_list.append(f"• {admin_name} — {admin_id}")
    # config.py dagi adminlarni ham qo'shish (agar admins.json da yo'q bo'lsa)
    for admin_id in ADMINS:
        if str(admin_id) not in main_admins_dict:
            admin_name = get_admin_name(admin_id)  # settings.py dan
            main_admins_list.append(f"• {admin_name} — {admin_id}")
    main_admins_text = "\n".join(main_admins_list) if main_admins_list else "• Yo'q"
    
    helper_admins_dict = get_helper_admins()
    helper_admins_list = []
    for helper_id_str, helper_name in helper_admins_dict.items():
        helper_id = int(helper_id_str)
        helper_admins_list.append(f"• {helper_name} — {helper_id}")
    # config.py dagi helper adminlarni ham qo'shish (agar admins.json da yo'q bo'lsa)
    for helper_id in HELPER_ADMINS:
        if str(helper_id) not in helper_admins_dict:
            helper_name = get_helper_admin_name(helper_id)  # settings.py dan
            helper_admins_list.append(f"• {helper_name} — {helper_id}")
    helper_admins_text = "\n".join(helper_admins_list) if helper_admins_list else "• Yo'q"
    
    admins_text = (
        "👥 <b>Adminlar</b>\n"
        "────────────\n\n"
        "✅ <b>Asosiy adminlar:</b>\n"
        f"{main_admins_text}\n\n"
        "👤 <b>Yordamchi adminlar:</b>\n"
        f"{helper_admins_text}"
    )
    
    # HAR DOIM ko'rinadigan tugmalar
    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text="➕ Admin qo'shish",
                callback_data="admin_add_admin"
            )
        ],
        [
            InlineKeyboardButton(
                text="➖ Adminni olib tashlash",
                callback_data="admin_remove_admin"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="admin_main"
            )
        ]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=admins_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_add_admin")
async def callback_admin_add_admin(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start adding admin process"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    text = (
        "➕ <b>Admin qo'shish</b>\n"
        "────────────\n\n"
        "1-qadam: Admin ismini yuboring:\n\n"
        "⚠️ Eslatma: Admin ismini to'g'ri kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_admin_name)


@router.message(AdminStates.waiting_for_admin_name)
async def process_admin_name(message: Message, state: FSMContext, bot: Bot):
    """Process admin name input"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    admin_name = message.text.strip()
    
    # State ga ismni saqlash
    await state.update_data(admin_name=admin_name)
    
    # Endi ID so'rash
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    text = (
        "➕ <b>Admin qo'shish</b>\n"
        "────────────\n\n"
        f"1-qadam: Ism: <b>{admin_name}</b> ✅\n\n"
        "2-qadam: Admin ID (API) sini yuboring:\n\n"
        "⚠️ Eslatma: Admin ID ni to'g'ri kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_admin_id)


@router.message(AdminStates.waiting_for_admin_id)
async def process_add_admin(message: Message, state: FSMContext, bot: Bot):
    """Process adding admin (ID bilan)"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    try:
        new_admin_id = int(message.text.strip())
        
        # Check if already admin (admins.json + config.py)
        if is_main_admin(new_admin_id) or is_helper_admin_storage(new_admin_id) or new_admin_id in ADMINS or new_admin_id in HELPER_ADMINS:
            state_data = await state.get_data()
            bot_message_id = state_data.get("bot_message_id")
            
            error_text = (
                "⚠️ <b>Xato</b>\n"
                "────────────\n\n"
                "Bu foydalanuvchi allaqachon admin."
            )
            
            keyboard = make_admin_back_keyboard()
            
            try:
                if bot_message_id:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=bot_message_id,
                        text=error_text,
                        reply_markup=keyboard
                    )
            except Exception:
                pass
            
            await state.clear()
            return
        
        # State dan ismni olish
        state_data = await state.get_data()
        admin_name = state_data.get("admin_name", f"User {new_admin_id}")
        bot_message_id = state_data.get("bot_message_id")
        
        # State ga ID ni saqlash
        await state.update_data(admin_id=new_admin_id)
        
        # Endi admin turi so'rash
        text = (
            "➕ <b>Admin qo'shish</b>\n"
            "────────────\n\n"
            f"1-qadam: Ism: <b>{admin_name}</b> ✅\n"
            f"2-qadam: ID: <b>{new_admin_id}</b> ✅\n\n"
            "3-qadam: Admin turini tanlang:\n\n"
            "Quyidagi tugmalardan birini tanlang:"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Asosiy admin",
                        callback_data="admin_type_main"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👤 Yordamchi admin",
                        callback_data="admin_type_helper"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_admins"
                    )
                ]
            ]
        )
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        
        await state.set_state(AdminStates.waiting_for_admin_type)
        
    except ValueError:
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "❌ <b>Xato format</b>\n"
            "────────────\n\n"
            "Iltimos, faqat raqam kiriting."
        )
        
        keyboard = make_admin_back_keyboard()
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        
        await state.set_state(AdminStates.waiting_for_admin_id)


@router.callback_query(F.data.in_(["admin_type_main", "admin_type_helper"]))
async def process_admin_type(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Process admin type selection"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    admin_type = "main" if callback_query.data == "admin_type_main" else "helper"
    
    # State dan ma'lumotlarni olish
    state_data = await state.get_data()
    admin_name = state_data.get("admin_name")
    admin_id = state_data.get("admin_id")
    bot_message_id = state_data.get("bot_message_id")
    
    if not admin_name or not admin_id:
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Admin qo'shish - admins.json ga saqlash
    if admin_type == "main":
        add_main_admin(admin_id, admin_name)
        admin_type_text = "Asosiy admin"
    else:
        add_helper_admin(admin_id, admin_name)
        admin_type_text = "Yordamchi admin"
    
    success_text = (
        "✅ <b>Admin qo'shildi</b>\n"
        "────────────\n\n"
        f"Admin: <b>{admin_name}</b>\n"
        f"ID: {admin_id}\n"
        f"Turi: {admin_type_text}\n\n"
        "Admin darhol ishlaydi."
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_admins"
                )
            ]
        ]
    )
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=bot_message_id,
                text=success_text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.clear()
    await callback_query.answer()


@router.message(AdminStates.waiting_for_user_id)
async def process_remove_admin(message: Message, state: FSMContext, bot: Bot):
    """Process removing admin"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    try:
        target_admin_id = int(message.text.strip())
        
        # Asosiy adminlarni o'chirish mumkin emas (admins.json yoki config.py dan)
        if is_main_admin(target_admin_id) or target_admin_id in ADMINS:
            state_data = await state.get_data()
            bot_message_id = state_data.get("bot_message_id")
            
            # Ismni olish
            if is_main_admin(target_admin_id):
                admin_name = get_main_admin_name(target_admin_id)
            else:
                admin_name = get_admin_name(target_admin_id)
            
            error_text = (
                "❌ <b>Xato</b>\n"
                "────────────\n\n"
                f"<b>{admin_name} — {target_admin_id}</b>\n\n"
                "Asosiy adminlarni o'chirish mumkin emas."
            )
            
            keyboard = make_admin_back_keyboard()
            
            try:
                if bot_message_id:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=bot_message_id,
                        text=error_text,
                        reply_markup=keyboard
                    )
            except Exception:
                pass
            
            await state.clear()
            return
        
        # Yordamchi adminlarni o'chirish
        if is_helper_admin_storage(target_admin_id) or target_admin_id in HELPER_ADMINS:
            # Ismni olish
            if is_helper_admin_storage(target_admin_id):
                helper_name = get_helper_admin_name(target_admin_id)
            else:
                helper_name = get_helper_admin_name(target_admin_id)  # settings.py dan
            
            # admins.json dan o'chirish
            if is_helper_admin_storage(target_admin_id):
                remove_admin(target_admin_id)
            
            state_data = await state.get_data()
            bot_message_id = state_data.get("bot_message_id")
            
            success_text = (
                "✅ <b>Admin olib tashlandi</b>\n"
                "────────────\n\n"
                f"Admin: <b>{helper_name} — {target_admin_id}</b>\n\n"
                "Admin darhol o'chirildi."
            )
            
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Orqaga",
                            callback_data="admin_admins"
                        )
                    ]
                ]
            )
            
            try:
                if bot_message_id:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=bot_message_id,
                        text=success_text,
                        reply_markup=keyboard
                    )
            except Exception:
                pass
            
            await state.clear()
            return
        
        # Admin topilmadi
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "❌ <b>Xato</b>\n"
            "────────────\n\n"
            f"ID: {target_admin_id}\n\n"
            "Bu ID bo'yicha admin topilmadi."
        )
        
        keyboard = make_admin_back_keyboard()
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        
        await state.set_state(AdminStates.waiting_for_user_id)
        
    except ValueError:
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "❌ <b>Xato format</b>\n"
            "────────────\n\n"
            "Iltimos, faqat raqam kiriting."
        )
        
        keyboard = make_admin_back_keyboard()
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        
        await state.set_state(AdminStates.waiting_for_user_id)


@router.callback_query(F.data == "admin_remove_admin")
async def callback_admin_remove_admin(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start removing admin process"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Faqat asosiy adminlar admin olib tashlay oladi", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Adminlar ro'yxatini ko'rsatish (ID + ism bilan) - admins.json dan
    main_admins_dict = get_main_admins()
    main_admins_list = []
    for admin_id_str, admin_name in main_admins_dict.items():
        admin_id = int(admin_id_str)
        main_admins_list.append(f"• {admin_name} — {admin_id}")
    # config.py dagi adminlarni ham qo'shish (agar admins.json da yo'q bo'lsa)
    for admin_id in ADMINS:
        if str(admin_id) not in main_admins_dict:
            admin_name = get_admin_name(admin_id)  # settings.py dan
            main_admins_list.append(f"• {admin_name} — {admin_id}")
    main_admins_text = "\n".join(main_admins_list) if main_admins_list else "• Yo'q"
    
    helper_admins_dict = get_helper_admins()
    helper_admins_list = []
    for helper_id_str, helper_name in helper_admins_dict.items():
        helper_id = int(helper_id_str)
        helper_admins_list.append(f"• {helper_name} — {helper_id}")
    # config.py dagi helper adminlarni ham qo'shish (agar admins.json da yo'q bo'lsa)
    for helper_id in HELPER_ADMINS:
        if str(helper_id) not in helper_admins_dict:
            helper_name = get_helper_admin_name(helper_id)  # settings.py dan
            helper_admins_list.append(f"• {helper_name} — {helper_id}")
    helper_admins_text = "\n".join(helper_admins_list) if helper_admins_list else "• Yo'q"
    
    text = (
        "➖ <b>Admin olib tashlash</b>\n"
        "────────────\n\n"
        "Olib tashlanadigan admin ID sini yuboring:\n\n"
        f"✅ <b>Asosiy adminlar:</b>\n{main_admins_text}\n\n"
        f"👤 <b>Yordamchi adminlar:</b>\n{helper_admins_text}\n\n"
        "⚠️ Eslatma: Asosiy adminlarni o'chirish mumkin emas."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_user_id)


@router.callback_query(F.data == "admin_add_helper_admin")
async def callback_admin_add_helper_admin(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start adding helper admin process"""
    user_id = callback_query.from_user.id
    
    if not is_admin(user_id):
        await callback_query.answer("⛔ Faqat asosiy adminlar yordamchi admin qo'sha oladi", show_alert=True)
        return
    
    text = (
        "➕ <b>Yordamchi admin qo'shish</b>\n"
        "────────────\n\n"
        "Yangi yordamchi admin ismini yuboring:\n\n"
        "⚠️ Eslatma: Yordamchi admin ismini to'g'ri kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_helper_admin_name)
    await callback_query.answer()


@router.message(AdminStates.waiting_for_helper_admin_name)
async def process_helper_admin_name(message: Message, state: FSMContext, bot: Bot):
    """Process helper admin name input"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    helper_admin_name = message.text.strip()
    
    # State ga ismni saqlash
    await state.update_data(helper_admin_name=helper_admin_name)
    
    # Endi ID so'rash
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    text = (
        "➕ <b>Yordamchi admin qo'shish</b>\n"
        "────────────\n\n"
        f"Ism: <b>{helper_admin_name}</b>\n\n"
        "Yangi yordamchi admin ID (API) sini yuboring:\n\n"
        "⚠️ Eslatma: Yordamchi admin ID ni to'g'ri kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_helper_admin_id)


@router.message(AdminStates.waiting_for_helper_admin_id)
async def process_add_helper_admin(message: Message, state: FSMContext, bot: Bot):
    """Process adding helper admin (ID bilan)"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    try:
        new_helper_admin_id = int(message.text.strip())
        
        # Check if already admin
        if new_helper_admin_id in ADMINS or new_helper_admin_id in HELPER_ADMINS:
            state_data = await state.get_data()
            bot_message_id = state_data.get("bot_message_id")
            
            error_text = (
                "⚠️ <b>Xato</b>\n"
                "────────────\n\n"
                "Bu foydalanuvchi allaqachon admin."
            )
            
            keyboard = make_admin_back_keyboard()
            
            try:
                if bot_message_id:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=bot_message_id,
                        text=error_text,
                        reply_markup=keyboard
                    )
            except Exception:
                pass
            
            await state.clear()
            return
        
        # State dan ismni olish
        state_data = await state.get_data()
        helper_admin_name = state_data.get("helper_admin_name", f"User {new_helper_admin_id}")
        bot_message_id = state_data.get("bot_message_id")
        
        # Yordamchi admin qo'shish - admins.json ga saqlash
        add_helper_admin(new_helper_admin_id, helper_admin_name)
        set_helper_admin_name(new_helper_admin_id, helper_admin_name)
        
        success_text = (
            "✅ <b>Yordamchi admin qo'shildi</b>\n"
            "────────────\n\n"
            f"Yordamchi admin: <b>{helper_admin_name}</b>\n"
            f"ID: {new_helper_admin_id}\n\n"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_admins"
                    )
                ]
            ]
        )
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=success_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        
        await state.clear()
        
    except ValueError:
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "❌ <b>Xato format</b>\n"
            "────────────\n\n"
            "Iltimos, faqat raqam kiriting."
        )
        
        keyboard = make_admin_back_keyboard()
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        
        await state.set_state(AdminStates.waiting_for_helper_admin_id)


@router.callback_query(F.data == "admin_reload_cache")
async def callback_admin_reload_cache(callback_query: CallbackQuery, bot: Bot):
    """Show cache reload submenu"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    menu_text = (
        "🔄 <b>Qaysi bazani yangilaymiz?</b>\n"
        "────────────\n\n"
        "Quyidagi bazalardan birini tanlang:"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Sheets1",
                    callback_data="admin_reload_sheet:sheets1"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets2",
                    callback_data="admin_reload_sheet:sheets2"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets3",
                    callback_data="admin_reload_sheet:sheets3"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets4",
                    callback_data="admin_reload_sheet:sheets4"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets5",
                    callback_data="admin_reload_sheet:sheets5"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Sheets6",
                    callback_data="admin_reload_sheet:sheets6"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔥 Hammasini yangilash",
                    callback_data="admin_reload_sheet:all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_main"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_reload_sheet:"))
async def callback_admin_reload_sheet(callback_query: CallbackQuery, bot: Bot):
    """Handle individual sheet reload or reload all"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    data = callback_query.data.split(":", 1)
    if len(data) < 2:
        return
    
    sheet_name = data[1]
    
    await callback_query.answer("⏳ Yangilanmoqda...", show_alert=False)
    
    try:
        if sheet_name == "all":
            # Reload all sheets
            await load_sheets1_to_cache()
            await load_sheets2_to_cache()
            await load_sheets3_to_cache()
            await load_sheets4_to_cache()
            await load_sheets5_to_cache()
            await load_sheets6_to_cache()
            
            confirmation_text = (
                "🔥 <b>Barcha bazalar yangilandi</b>\n"
                "────────────\n\n"
                "Sheets1 ✅\n"
                "Sheets2 ✅\n"
                "Sheets3 ✅\n"
                "Sheets4 ✅\n"
                "Sheets5 ✅\n"
                "Sheets6 ✅"
            )
        elif sheet_name == "sheets1":
            await load_sheets1_to_cache()
            confirmation_text = "✅ Sheets1 muvaffaqiyatli yangilandi"
        elif sheet_name == "sheets2":
            await load_sheets2_to_cache()
            confirmation_text = "✅ Sheets2 muvaffaqiyatli yangilandi"
        elif sheet_name == "sheets3":
            await load_sheets3_to_cache()
            confirmation_text = "✅ Sheets3 muvaffaqiyatli yangilandi"
        elif sheet_name == "sheets4":
            await load_sheets4_to_cache()
            confirmation_text = "✅ Sheets4 muvaffaqiyatli yangilandi"
        elif sheet_name == "sheets5":
            await load_sheets5_to_cache()
            confirmation_text = "✅ Sheets5 muvaffaqiyatli yangilandi"
        elif sheet_name == "sheets6":
            await load_sheets6_to_cache()
            confirmation_text = "✅ Sheets6 muvaffaqiyatli yangilandi"
        else:
            confirmation_text = "❌ Noto'g'ri sheet nomi"
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_reload_cache"
                    )
                ]
            ]
        )
        
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=confirmation_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
        
    except Exception as e:
        error_text = (
            f"❌ <b>Xatolik</b>\n"
            "────────────\n\n"
            f"{sheet_name.upper()} ni yangilashda xatolik yuz berdi:\n"
            f"{str(e)}"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_reload_cache"
                    )
                ]
            ]
        )
        
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=error_text,
                reply_markup=keyboard
            )
        except Exception:
            pass


# ==================== BROADCAST ====================

@router.callback_query(F.data == "admin_broadcast")
async def callback_admin_broadcast(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start broadcast process"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    text = (
        "📢 <b>Xabar yuborish</b>\n"
        "────────────\n\n"
        "Barcha foydalanuvchilarga yuboriladigan xabar matnini yuboring:"
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback_query.answer()


@router.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast_text(message: Message, state: FSMContext, bot: Bot):
    """Process broadcast text and show preview"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    broadcast_text = message.text
    
    preview_text = (
        "📢 <b>Xabar ko'rinishi:</b>\n"
        "────────────\n\n"
        f"{broadcast_text}\n\n"
        "⚠️ Bu xabar barcha foydalanuvchilarga yuboriladi."
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Yuborish",
                    callback_data=f"admin_broadcast_confirm:{message.message_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="admin_broadcast_cancel"
                )
            ]
        ]
    )
    
    await message.answer(preview_text, reply_markup=keyboard)
    await state.update_data(broadcast_text=broadcast_text, broadcast_message_id=message.message_id)
    await state.set_state(AdminStates.waiting_for_broadcast_text)


@router.callback_query(F.data.startswith("admin_broadcast_confirm:"))
async def callback_broadcast_confirm(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Confirm and send broadcast"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    
    if not broadcast_text:
        await callback_query.answer("❌ Xabar topilmadi", show_alert=True)
        return
    
    await callback_query.answer("⏳ Xabar yuborilmoqda...", show_alert=False)
    
    # Get all users from statistics
    logger.info("[Broadcast] Started")
    users = get_all_users()
    user_ids = [user[0] for user in users]  # Extract user IDs
    
    success_count = 0
    failed_count = 0
    
    # Send message to each user
    for target_user_id in user_ids:
        try:
            await bot.send_message(target_user_id, broadcast_text)
            success_count += 1
            logger.info(f"[Broadcast] Sent to: {target_user_id}")
        except Exception as e:
            failed_count += 1
            logger.warning(f"[Broadcast] Failed: {target_user_id} - {str(e)}")
            continue
    
    logger.info(f"[Broadcast] Finished. Success: {success_count}, Failed: {failed_count}")
    
    success_text = (
        "✅ <b>Xabar yuborildi</b>\n"
        "────────────\n\n"
        f"Muvaffaqiyatli: {success_count}\n"
        f"Muvaffaqiyatsiz: {failed_count}\n"
        f"Jami: {len(user_ids)}"
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=success_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await state.clear()


@router.callback_query(F.data == "admin_broadcast_cancel")
async def callback_broadcast_cancel(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Cancel broadcast"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    text = (
        "📢 <b>Xabar yuborish</b>\n"
        "────────────\n\n"
        "Xabar yuborish bekor qilindi."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await state.clear()
    await callback_query.answer()


# ==================== SETTINGS ====================

@router.callback_query(F.data == "admin_settings")
async def callback_admin_settings(callback_query: CallbackQuery, bot: Bot):
    """Show settings"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    contact_phone = get_contact_phone()
    error_text = get_error_message()
    
    settings_text = (
        "⚙️ <b>Sozlamalar</b>\n"
        "────────────\n\n"
        f"📞 Kontakt raqami: {contact_phone}\n"
        f"❌ Xato matni: {error_text}\n\n"
        "Quyidagi sozlamalarni o'zgartirishingiz mumkin:"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📞 Kontakt raqamini o'zgartirish",
                    callback_data="admin_set_contact"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Xato matnini o'zgartirish",
                    callback_data="admin_set_error_text"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📣 Broadcast sozlamalari",
                    callback_data="admin_settings_broadcast"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Statistika sozlamalari",
                    callback_data="admin_settings_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧾 Loglar va tarix",
                    callback_data="admin_settings_logs"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_main"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=settings_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data == "admin_set_contact")
async def callback_admin_set_contact(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start setting contact number"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    contact_phone = get_contact_phone()
    
    text = (
        "📞 <b>Kontakt raqami o'zgartirish</b>\n"
        "────────────\n\n"
        f"Joriy raqam: {contact_phone}\n\n"
        "Yangi kontakt raqamini yuboring:"
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        # Bot xabarining message_id ni state ga saqlash
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_contact_number)
    await callback_query.answer()


@router.message(AdminStates.waiting_for_contact_number)
async def process_contact_number(message: Message, state: FSMContext, bot: Bot):
    """Process new contact number"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    new_contact = message.text.strip()
    set_contact_phone(new_contact)
    
    # Bot xabarining message_id ni state dan olish
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    # Yakuniy xabar matni
    success_text = "✅ Hammasi tayyor"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_settings"
                )
            ]
        ]
    )
    
    # Bot xabarini edit qilish
    if bot_message_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=success_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
    else:
        # Agar bot_message_id topilmasa, yangi xabar yuborish
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=success_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
    
    await state.clear()


@router.callback_query(F.data == "admin_set_error_text")
async def callback_admin_set_error_text(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start setting error text"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    text = (
        "📝 <b>Xato matni o'zgartirish</b>\n"
        "────────────\n\n"
        "Yangi xato matnini yuboring:"
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        # Bot xabarining message_id ni state ga saqlash
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_error_text)
    await callback_query.answer()


@router.message(AdminStates.waiting_for_error_text)
async def process_error_text(message: Message, state: FSMContext, bot: Bot):
    """Process new error text"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    new_error_text = message.text.strip()
    set_error_message(new_error_text)
    
    # Bot xabarining message_id ni state dan olish
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    # Yakuniy xabar matni
    success_text = "✅ Hammasi tayyor"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_settings"
                )
            ]
        ]
    )
    
    # Bot xabarini edit qilish
    if bot_message_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=success_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
    else:
        # Agar bot_message_id topilmasa, yangi xabar yuborish
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=success_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
    
    await state.clear()


# ==================== SETTINGS SUBMENUS ====================

@router.callback_query(F.data == "admin_settings_broadcast")
async def callback_admin_settings_broadcast(callback_query: CallbackQuery, bot: Bot):
    """Show broadcast settings"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    broadcast_enabled = get_broadcast_enabled()
    active_users_only = get_broadcast_active_users_only()
    skip_blocked = get_broadcast_skip_blocked()
    
    enabled_icon = "✅" if broadcast_enabled else "❌"
    active_icon = "✅" if active_users_only else "❌"
    skip_icon = "✅" if skip_blocked else "❌"
    
    yes_no = lambda x: "Ha" if x else "Yo'q"
    
    settings_text = (
        "📣 <b>Broadcast sozlamalari</b>\n"
        "────────────\n\n"
        f"{enabled_icon} Broadcast yoqilgan: {yes_no(broadcast_enabled)}\n"
        f"{active_icon} Faqat faol foydalanuvchilar (7 kun): {yes_no(active_users_only)}\n"
        f"{skip_icon} Blok qilgan userlarni tashlab o'tish: {yes_no(skip_blocked)}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{enabled_icon} Broadcast yoqilgan / o'chiq",
                    callback_data="admin_toggle_broadcast_enabled"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{active_icon} Faqat faol foydalanuvchilar (7 kun)",
                    callback_data="admin_toggle_broadcast_active"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{skip_icon} Blok qilgan userlarni tashlab o'tish",
                    callback_data="admin_toggle_broadcast_skip"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_settings"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=settings_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data == "admin_toggle_broadcast_enabled")
async def callback_toggle_broadcast_enabled(callback_query: CallbackQuery, bot: Bot):
    """Toggle broadcast enabled"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_broadcast_enabled()
    await callback_admin_settings_broadcast(callback_query, bot)


@router.callback_query(F.data == "admin_toggle_broadcast_active")
async def callback_toggle_broadcast_active(callback_query: CallbackQuery, bot: Bot):
    """Toggle broadcast active users only"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_broadcast_active_users_only()
    await callback_admin_settings_broadcast(callback_query, bot)


@router.callback_query(F.data == "admin_toggle_broadcast_skip")
async def callback_toggle_broadcast_skip(callback_query: CallbackQuery, bot: Bot):
    """Toggle broadcast skip blocked"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_broadcast_skip_blocked()
    await callback_admin_settings_broadcast(callback_query, bot)


@router.callback_query(F.data == "admin_settings_stats")
async def callback_admin_settings_stats(callback_query: CallbackQuery, bot: Bot):
    """Show statistics settings"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    history_logging = get_history_logging_enabled()
    auto_reset = get_daily_stats_auto_reset()
    max_logs = get_max_logs_per_user()
    
    history_icon = "✅" if history_logging else "❌"
    reset_icon = "✅" if auto_reset else "❌"
    
    yes_no = lambda x: "Ha" if x else "Yo'q"
    
    settings_text = (
        "📊 <b>Statistika sozlamalari</b>\n"
        "────────────\n\n"
        f"{history_icon} Foydalanish tarixini yozib borish: {yes_no(history_logging)}\n"
        f"{reset_icon} Kunlik statistika auto-reset: {yes_no(auto_reset)}\n"
        f"📦 Bitta foydalanuvchi uchun maksimal log soni: {max_logs}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{history_icon} Foydalanish tarixini yozib borish",
                    callback_data="admin_toggle_history_logging"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{reset_icon} Kunlik statistika auto-reset",
                    callback_data="admin_toggle_auto_reset"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📦 Maksimal log soni ({max_logs})",
                    callback_data="admin_set_max_logs"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_settings"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=settings_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data == "admin_toggle_history_logging")
async def callback_toggle_history_logging(callback_query: CallbackQuery, bot: Bot):
    """Toggle history logging"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_history_logging_enabled()
    await callback_admin_settings_stats(callback_query, bot)


@router.callback_query(F.data == "admin_toggle_auto_reset")
async def callback_toggle_auto_reset(callback_query: CallbackQuery, bot: Bot):
    """Toggle auto reset"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_daily_stats_auto_reset()
    await callback_admin_settings_stats(callback_query, bot)


@router.callback_query(F.data == "admin_set_max_logs")
async def callback_admin_set_max_logs(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start setting max logs"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    text = (
        "📦 <b>Maksimal log soni</b>\n"
        "────────────\n\n"
        "Yangi maksimal log sonini yuboring (masalan: 100):"
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_max_logs)
    await callback_query.answer()


@router.message(AdminStates.waiting_for_max_logs)
async def process_max_logs(message: Message, state: FSMContext, bot: Bot):
    """Process max logs"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    try:
        max_logs = int(message.text.strip())
        set_max_logs_per_user(max_logs)
        
        # Show stats settings
        history_logging = get_history_logging_enabled()
        auto_reset = get_daily_stats_auto_reset()
        
        history_icon = "✅" if history_logging else "❌"
        reset_icon = "✅" if auto_reset else "❌"
        yes_no = lambda x: "Ha" if x else "Yo'q"
        
        settings_text = (
            "📊 <b>Statistika sozlamalari</b>\n"
            "────────────\n\n"
            f"{history_icon} Foydalanish tarixini yozib borish: {yes_no(history_logging)}\n"
            f"{reset_icon} Kunlik statistika auto-reset: {yes_no(auto_reset)}\n"
            f"📦 Bitta foydalanuvchi uchun maksimal log soni: {max_logs}\n\n"
            "✅ Maksimal log soni yangilandi!"
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{history_icon} Foydalanish tarixini yozib borish",
                        callback_data="admin_toggle_history_logging"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"{reset_icon} Kunlik statistika auto-reset",
                        callback_data="admin_toggle_auto_reset"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"📦 Maksimal log soni ({max_logs})",
                        callback_data="admin_set_max_logs"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_settings"
                    )
                ]
            ]
        )
        
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id - 1,
                text=settings_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
        
        await message.answer(f"✅ Maksimal log soni yangilandi: {max_logs}")
    except ValueError:
        await message.answer("❌ Noto'g'ri format. Faqat raqam kiriting.")
    
    await state.clear()


@router.callback_query(F.data == "admin_settings_logs")
async def callback_admin_settings_logs(callback_query: CallbackQuery, bot: Bot):
    """Show logs and history settings"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    request_history = get_user_request_history_enabled()
    show_first_name = get_show_first_name()
    show_username = get_show_username()
    
    history_icon = "✅" if request_history else "❌"
    name_icon = "✅" if show_first_name else "❌"
    username_icon = "✅" if show_username else "❌"
    
    yes_no = lambda x: "Ha" if x else "Yo'q"
    
    settings_text = (
        "🧾 <b>Loglar va tarix</b>\n"
        "────────────\n\n"
        f"{history_icon} Foydalanuvchi so'rov tarixini saqlash: {yes_no(request_history)}\n"
        f"❌ User ID ko'rsatish: Yo'q (hech qachon yoqilmaydi)\n"
        f"{name_icon} Ism ko'rsatish: {yes_no(show_first_name)}\n"
        f"{username_icon} Username ko'rsatish: {yes_no(show_username)}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{history_icon} Foydalanuvchi so'rov tarixini saqlash",
                    callback_data="admin_toggle_request_history"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{name_icon} Ism ko'rsatish",
                    callback_data="admin_toggle_show_first_name"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{username_icon} Username ko'rsatish",
                    callback_data="admin_toggle_show_username"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_settings"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=settings_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data == "admin_toggle_request_history")
async def callback_toggle_request_history(callback_query: CallbackQuery, bot: Bot):
    """Toggle request history"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_user_request_history_enabled()
    await callback_admin_settings_logs(callback_query, bot)


@router.callback_query(F.data == "admin_toggle_show_first_name")
async def callback_toggle_show_first_name(callback_query: CallbackQuery, bot: Bot):
    """Toggle show first name"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_show_first_name()
    await callback_admin_settings_logs(callback_query, bot)


@router.callback_query(F.data == "admin_toggle_show_username")
async def callback_toggle_show_username(callback_query: CallbackQuery, bot: Bot):
    """Toggle show username"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    toggle_show_username()
    await callback_admin_settings_logs(callback_query, bot)


# ==================== EXIT ====================

@router.callback_query(F.data == "admin_exit")
async def callback_admin_exit(callback_query: CallbackQuery, bot: Bot):
    """Exit admin panel and return to main menu"""
    user_id = callback_query.from_user.id
    
    menu_text = (
        "👋 Assalomu alaykum! Botga xush kelibsiz.\n\n"
        "Quyidagi menyulardan birini tanlang:"
    )
    
    # FAQAT make_main_menu_keyboard funksiyasini ishlatish
    from handlers.start import make_main_menu_keyboard
    keyboard = make_main_menu_keyboard(user_id)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        store_main_menu_message(callback_query.message.chat.id, callback_query.message.message_id)
    except Exception:
        pass
    
    await callback_query.answer()


# ==================== SERVER MONITORING PRO ====================

def get_server_monitoring_data():
    """
    Server monitoring ma'lumotlarini olish.
    
    Returns:
        Dictionary with server monitoring data
    """
    try:
        import psutil
        
        # CPU yuklama
        cpu_percent = psutil.cpu_percent(interval=0.1)
        
        # RAM ishlatilishi
        memory = psutil.virtual_memory()
        ram_percent = memory.percent
        ram_used_mb = memory.used / (1024 * 1024)
        ram_total_mb = memory.total / (1024 * 1024)
        
        # Disk holati
        try:
            # Windows uchun C:\, Linux/Mac uchun /
            if os.name == 'nt':  # Windows
                disk_path = 'C:\\'
            else:
                disk_path = '/'
            disk = psutil.disk_usage(disk_path)
            disk_free_gb = disk.free / (1024 * 1024 * 1024)
            disk_total_gb = disk.total / (1024 * 1024 * 1024)
        except Exception:
            disk_free_gb = 0
            disk_total_gb = 0
        
        # Bot jarayoni holati
        try:
            process = psutil.Process(os.getpid())
            bot_process_status = "faol" if process.is_running() else "to'xtagan"
        except Exception:
            bot_process_status = "noma'lum"
        
        # Server uptime
        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.now() - boot_time
            uptime_hours = int(uptime.total_seconds() // 3600)
            uptime_minutes = int((uptime.total_seconds() % 3600) // 60)
            server_uptime = f"{uptime_hours} soat {uptime_minutes} daqiqa"
        except Exception:
            server_uptime = "noma'lum"
        
        # Response time statistikasi
        response_stats = get_response_time_stats()
        
        avg_response_time = response_stats.get("avg_response_time_ms")
        slowest_response = response_stats.get("slowest_response_ms")
        requests_last_5_min = response_stats.get("requests_last_5_min", 0)
        last_slowdown_time = response_stats.get("last_slowdown_time")
        
        # Oxirgi sekinlashuv vaqti
        if last_slowdown_time:
            last_slowdown_str = last_slowdown_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_slowdown_str = "yo'q"
        
        # Xavf tahlili
        risks = []
        risk_level = "BARQAROR"
        risk_emoji = "🟢"
        
        if cpu_percent > 80:
            risks.append("CPU > 80%")
            risk_level = "YUQORI YUKLAMA"
            risk_emoji = "🔴"
        
        if ram_percent > 85:
            risks.append("RAM > 85%")
            risk_level = "XOTIRA XAVFI"
            risk_emoji = "🔴"
        
        if disk_free_gb < 2:
            risks.append("Disk < 2 GB")
            risk_level = "DISK TO'LIB QOLMOQDA"
            risk_emoji = "🔴"
        
        # Tavsiyalar
        recommendations = []
        if cpu_percent > 80:
            recommendations.append("CPU yuklama yuqori, jarayonlarni tekshiring")
        if ram_percent > 85:
            recommendations.append("RAM to'lib boryapti, cache hajmini tekshiring")
        if disk_free_gb < 2:
            recommendations.append("Disk bo'sh joyi kam, log fayllarni tozalang")
        
        return {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_mb": ram_used_mb,
            "ram_total_mb": ram_total_mb,
            "disk_free_gb": disk_free_gb,
            "disk_total_gb": disk_total_gb,
            "bot_process_status": bot_process_status,
            "server_uptime": server_uptime,
            "avg_response_time_ms": avg_response_time,
            "slowest_response_ms": slowest_response,
            "requests_last_5_min": requests_last_5_min,
            "last_slowdown_time": last_slowdown_str,
            "risks": risks,
            "risk_level": risk_level,
            "risk_emoji": risk_emoji,
            "recommendations": recommendations,
            "success": True
        }
    except Exception as e:
        logger.error(f"Error getting server monitoring data: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


# ==================== BOT STATUS CHECK ====================

@router.callback_query(F.data == "admin_bot_status")
async def callback_admin_bot_status(callback_query: CallbackQuery, bot: Bot):
    """Check bot status and show diagnostics"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    await callback_query.answer("⏳ Tekshirilmoqda...", show_alert=False)
    
    try:
        # 1. Bot ishlayaptimi
        bot_status = "ishlayapti"
        
        # 2. Google Sheets ulanganmi
        sheets_connected = False
        sheets_error = None
        try:
            sheet_service = GoogleSheetService()
            # Check if service account file exists and sheet_id is set
            if sheet_service.sheet_id and sheet_service.service_account_file.exists():
                # Try to check if client exists (already connected)
                if sheet_service.client is not None:
                    sheets_connected = True
                else:
                    # Client not initialized yet, but config is OK
                    sheets_connected = True  # Will connect on first use
            else:
                sheets_connected = False
                if not sheet_service.sheet_id:
                    sheets_error = "GOOGLE_SHEET_ID sozlanmagan"
                elif not sheet_service.service_account_file.exists():
                    sheets_error = f"Service account fayl topilmadi: {sheet_service.service_account_file}"
        except Exception as e:
            sheets_connected = False
            sheets_error = str(e)[:100]  # Limit error message length
        
        # 3. Cache yuklanganmi
        cache_loaded = bool(CACHE.get("sheets1") or CACHE.get("sheets2") or 
                           CACHE.get("sheets2_full") or CACHE.get("sheets3") or CACHE.get("sheets4"))
        
        # 4. Har bir sheet bo'yicha yozuvlar soni
        sheets1_count = len(CACHE.get("sheets1", []))
        sheets2_count = len(CACHE.get("sheets2_full", []))
        sheets3_count = len(CACHE.get("sheets3", []))
        sheets4_count = len(CACHE.get("sheets4", []))
        sheets5_count = len(CACHE.get("sheets5", []))
        
        # 5. Sheets2 image map mavjudmi
        image_map_ready = bool(CACHE.get("sheets2", {}))
        image_map_count = len(CACHE.get("sheets2", {}))
        
        # 6. Oxirgi yangilanish vaqti (cache timestamp yo'q, shuning uchun faqat cache mavjudligini ko'rsatamiz)
        last_update = "Noma'lum"  # Cache timestamp saqlanmayapti
        
        # 7. Oxirgi xato - logging dan olish qiyin, shuning uchun faqat sheets connection xatosini ko'rsatamiz
        last_error = None
        error_module = None
        error_reason = None
        
        if sheets_error:
            last_error = sheets_error
            error_module = "Google Sheets"
            error_reason = "Ulanish xatosi"
        
        # 8. Ishlash statistikasi
        try:
            stats = get_stats()
            # So'nggi 1 daqiqada so'rovlar - faqat bugungi so'rovlardan taxmin qilamiz
            # Real-time tracking yo'q, shuning uchun bugungi so'rovlardan taxmin qilamiz
            today_requests = stats.get("today_requests", 0)
            # Taxminiy: 1 daqiqada so'rovlar = bugungi so'rovlar / (bugungi soatlar * 60)
            # Lekin bu aniq emas, shuning uchun "noma'lum" yozamiz
            last_minute_requests = "noma'lum"  # Real-time tracking yo'q
            avg_response_time = "noma'lum"  # Response time tracking yo'q
            slowest_response = "noma'lum"  # Response time tracking yo'q
        except Exception:
            last_minute_requests = "noma'lum"
            avg_response_time = "noma'lum"
            slowest_response = "noma'lum"
        
        # 9. Ish vaqti
        try:
            # Process yaratilgan vaqtini olish
            import psutil
            process = psutil.Process(os.getpid())
            create_time = datetime.fromtimestamp(process.create_time())
            uptime = datetime.now() - create_time
            uptime_hours = int(uptime.total_seconds() // 3600)
            uptime_minutes = int((uptime.total_seconds() % 3600) // 60)
            uptime_str = f"{uptime_hours} soat {uptime_minutes} daqiqa"
            last_restart = create_time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            uptime_str = "noma'lum"
            last_restart = "noma'lum"
        
        # 10. Xotira holati
        try:
            import psutil
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            ram_mb = memory_info.rss / (1024 * 1024)  # Convert to MB
            ram_str = f"{ram_mb:.0f} MB"
            
            # Cache hajmi
            total_cache_records = sheets1_count + sheets2_count + sheets3_count + sheets4_count
            cache_size_str = f"{total_cache_records} ta"
            
            # Image map xotirada
            image_map_in_memory = "ha" if image_map_ready else "yo'q"
        except Exception:
            ram_str = "noma'lum"
            cache_size_str = "noma'lum"
            image_map_in_memory = "noma'lum"
        
        # 11. Oxirgi xatolar (oxirgi 5 daqiqada)
        try:
            activity_log = get_activity_log(limit=50)
            recent_errors = []
            five_minutes_ago = datetime.now() - timedelta(minutes=5)
            
            for entry in activity_log:
                try:
                    timestamp_str = entry.get("timestamp", "")
                    if timestamp_str:
                        entry_time = datetime.fromisoformat(timestamp_str)
                        if entry_time >= five_minutes_ago:
                            result = entry.get("result", "").lower()
                            if "xato" in result or "error" in result or "fail" in result:
                                recent_errors.append({
                                    "time": entry_time.strftime("%H:%M:%S"),
                                    "module": entry.get("section", "Noma'lum"),
                                    "error": entry.get("result", "Noma'lum")[:50]
                                })
                except Exception:
                    # Skip invalid entries
                    continue
            
            if recent_errors:
                latest_error = recent_errors[0]
                last_error_time = latest_error["time"]
                last_error_module = latest_error["module"]
                last_error_type = latest_error["error"]
                last_error_reason = latest_error["error"][:50]
            else:
                last_error_time = None
                last_error_module = None
                last_error_type = None
                last_error_reason = None
        except Exception:
            last_error_time = None
            last_error_module = None
            last_error_type = None
            last_error_reason = None
        
        # 12. Cache sog'ligi
        sheets1_ok = sheets1_count > 0
        sheets2_ok = sheets2_count > 0
        image_map_ok = image_map_ready
        
        sheets1_health = "OK" if sheets1_ok else "MUAMMO"
        sheets2_health = "OK" if sheets2_ok else "MUAMMO"
        image_map_health = "OK" if image_map_ok else "MUAMMO"
        
        # Statusni aniqlash
        has_issues = False
        issues = []
        
        if not sheets_connected:
            has_issues = True
            issues.append("Google Sheets ulanishi")
        
        if not cache_loaded:
            has_issues = True
            issues.append("Cache yuklanmagan")
        
        if sheets1_count == 0 and sheets2_count == 0 and sheets3_count == 0 and sheets4_count == 0:
            has_issues = True
            issues.append("Barcha sheetlar bo'sh")
        
        # Xabar formatlash
        if has_issues:
            status_emoji = "🔴"
            status_text = "MUAMMO BOR"
        else:
            status_emoji = "🟢"
            status_text = "YAXSHI"
        
        status_message = f"{status_emoji} <b>Bot holati: {status_text}</b>\n\n"
        
        status_message += f"🔹 Bot: {bot_status}\n"
        status_message += f"🔹 Google Sheets: {'ulangan' if sheets_connected else 'ulanishda xato'}\n"
        status_message += f"🔹 Cache: {'yuklangan' if cache_loaded else 'yuklanmagan'}\n\n"
        
        status_message += "<b>📦 Ma'lumotlar:</b>\n"
        status_message += f"🔹 sheets1: {sheets1_count} ta yozuv\n"
        status_message += f"🔹 sheets2: {sheets2_count} ta yozuv\n"
        status_message += f"🔹 sheets3: {sheets3_count} ta yozuv\n"
        status_message += f"🔹 sheets4: {sheets4_count} ta yozuv\n"
        status_message += f"🔹 sheets5: {sheets5_count} ta yozuv\n"
        
        if image_map_ready:
            status_message += f"🔹 image_map: {image_map_count} ta rasm\n"
        else:
            status_message += f"🔹 Image map: tayyor emas\n"
        
        status_message += "\n<b>📊 Ishlash:</b>\n"
        status_message += f"🔹 So'nggi 1 daqiqa: {last_minute_requests} ta so'rov\n"
        status_message += f"🔹 O'rtacha javob: {avg_response_time} ms\n"
        status_message += f"🔹 Eng sekin javob: {slowest_response} ms\n"
        
        status_message += "\n<b>🕒 Ish vaqti:</b>\n"
        status_message += f"🔹 Ishlayapti: {uptime_str}\n"
        status_message += f"🔹 Oxirgi restart: {last_restart}\n"
        
        status_message += "\n<b>💾 Xotira:</b>\n"
        status_message += f"🔹 RAM: {ram_str}\n"
        status_message += f"🔹 Cache yozuvlari: {cache_size_str}\n"
        
        # Server Monitoring PRO
        try:
            server_data = get_server_monitoring_data()
            
            if server_data.get("success"):
                status_message += "\n" + "=" * 40 + "\n"
                status_message += "<b>🖥️ Server holati:</b>\n"
                status_message += "=" * 40 + "\n\n"
                
                # CPU
                cpu_percent = server_data.get("cpu_percent", 0)
                status_message += f"🔹 CPU yuklama: {cpu_percent:.1f} %\n"
                
                # RAM
                ram_percent = server_data.get("ram_percent", 0)
                ram_used_mb = server_data.get("ram_used_mb", 0)
                status_message += f"🔹 RAM ishlatilishi: {ram_percent:.1f} % ({ram_used_mb:.0f} MB)\n"
                
                # Disk
                disk_free_gb = server_data.get("disk_free_gb", 0)
                status_message += f"🔹 Disk holati: {disk_free_gb:.2f} GB bo'sh\n"
                
                # Bot jarayoni
                bot_process_status = server_data.get("bot_process_status", "noma'lum")
                status_message += f"🔹 Bot jarayoni: {bot_process_status}\n"
                
                # Server uptime
                server_uptime = server_data.get("server_uptime", "noma'lum")
                status_message += f"🔹 Server uptime: {server_uptime}\n"
                
                # Oxirgi sekinlashuv
                last_slowdown = server_data.get("last_slowdown_time", "yo'q")
                status_message += f"🔹 Oxirgi sekinlashuv: {last_slowdown}\n"
                
                # Xavf tahlili
                status_message += "\n" + "-" * 40 + "\n"
                status_message += "<b>⚠️ SERVER XAVF TAHLILI</b>\n"
                status_message += "-" * 40 + "\n\n"
                
                risks = server_data.get("risks", [])
                risk_level = server_data.get("risk_level", "BARQAROR")
                risk_emoji = server_data.get("risk_emoji", "🟢")
                
                if risks:
                    for risk in risks:
                        status_message += f"🔴 {risk}\n"
                    status_message += f"\n{risk_emoji} <b>Server holati: {risk_level}</b>\n"
                else:
                    status_message += f"{risk_emoji} <b>Server holati: {risk_level}</b>\n"
                
                # Tavsiyalar
                recommendations = server_data.get("recommendations", [])
                if recommendations:
                    status_message += "\n<b>💡 Tavsiyalar:</b>\n"
                    for rec in recommendations:
                        status_message += f"• {rec}\n"
                
                # Ishlash tezligi nazorati
                status_message += "\n" + "-" * 40 + "\n"
                status_message += "<b>📉 ISHLASH TEZLIGI NAZORATI</b>\n"
                status_message += "-" * 40 + "\n\n"
                
                avg_response = server_data.get("avg_response_time_ms")
                slowest_response = server_data.get("slowest_response_ms")
                requests_last_5_min = server_data.get("requests_last_5_min", 0)
                
                if avg_response is not None:
                    status_message += f"🔹 O'rtacha javob vaqti: {avg_response:.0f} ms\n"
                else:
                    status_message += f"🔹 O'rtacha javob vaqti: noma'lum\n"
                
                if slowest_response is not None:
                    status_message += f"🔹 Eng sekin javob: {slowest_response:.0f} ms\n"
                else:
                    status_message += f"🔹 Eng sekin javob: noma'lum\n"
                
                status_message += f"🔹 So'nggi 5 daqiqadagi so'rovlar: {requests_last_5_min}\n"
                
                # Avtomatik ogohlantirish
                if risks:
                    status_message += "\n" + "-" * 40 + "\n"
                    status_message += "<b>🚨 AVTOMATIK OGOHLANTIRISH</b>\n"
                    status_message += "-" * 40 + "\n\n"
                    status_message += f"{risk_emoji} <b>Xavf holati aniqlandi!</b>\n\n"
                    
                    if "CPU" in str(risks):
                        status_message += "🔴 <b>CPU yuklama yuqori</b>\n"
                        status_message += "Sabab: CPU > 80%\n"
                        status_message += "Tavsiya: Jarayonlarni tekshiring, kerak bo'lsa restart qiling\n\n"
                    
                    if "RAM" in str(risks):
                        status_message += "🔴 <b>RAM xavfi</b>\n"
                        status_message += "Sabab: RAM > 85%\n"
                        status_message += "Tavsiya: RAM to'lib boryapti, cache hajmini tekshiring\n\n"
                    
                    if "Disk" in str(risks):
                        status_message += "🔴 <b>Disk to'lib qolmoqda</b>\n"
                        status_message += "Sabab: Disk < 2 GB bo'sh\n"
                        status_message += "Tavsiya: Log fayllarni tozalang, kerak bo'lsa eski fayllarni o'chiring\n\n"
            else:
                status_message += "\n" + "=" * 40 + "\n"
                status_message += "<b>🖥️ Server holati:</b>\n"
                status_message += "=" * 40 + "\n\n"
                status_message += "❌ Server ma'lumotlarini olishda xatolik yuz berdi\n"
                error_msg = server_data.get("error", "Noma'lum xato")
                status_message += f"Xato: {error_msg[:100]}\n"
        except Exception as e:
            logger.error(f"Error adding server monitoring: {e}", exc_info=True)
            status_message += "\n" + "=" * 40 + "\n"
            status_message += "<b>🖥️ Server holati:</b>\n"
            status_message += "=" * 40 + "\n\n"
            status_message += "❌ Server monitoring xatolik: noma'lum\n"
        
        status_message += "\n<b>⚠ Xatolar:</b>\n"
        if last_error_time:
            status_message += f"🔴 So'nggi xato vaqti: {last_error_time}\n"
            status_message += f"🔴 Modul: {last_error_module}\n"
            status_message += f"🔴 Xato turi: {last_error_type}\n"
            status_message += f"🔴 Sabab: {last_error_reason}\n"
        else:
            status_message += f"🔹 So'nggi xatolar: yo'q\n"
        
        status_message += "\n<b>🧠 Cache sog'ligi:</b>\n"
        status_message += f"🔹 sheets1 moslik: {sheets1_health}\n"
        status_message += f"🔹 sheets2 moslik: {sheets2_health}\n"
        status_message += f"🔹 image_map moslik: {image_map_health}\n"
        
        # Ogohlantirishlar
        if not image_map_ok:
            status_message += "\n❌ <b>Ogohlantirish:</b> sheets2 cache yo'q"
        
        # 13. AI holati
        try:
            ai_stats = get_ai_stats()
            
            # AI ulangan
            ai_connected = False
            if OPENAI_API_KEY:
                ai_connected = ai_stats.get("api_key_valid", None) is not False
            else:
                ai_connected = False
            
            # Model nomi
            model_name = OPENAI_MODEL or "gpt-4o-mini"
            
            # API kalit holati
            api_key_status = "faol"
            if not OPENAI_API_KEY:
                api_key_status = "xato (yo'q)"
            elif ai_stats.get("api_key_valid") is False:
                api_key_status = "xato"
            elif ai_stats.get("api_key_valid") is True:
                api_key_status = "faol"
            else:
                api_key_status = "noma'lum"
            
            # Bugungi so'rovlar va sarf
            today_requests = ai_stats.get("today_ai_requests", 0)
            today_cost = ai_stats.get("today_ai_cost", 0.0)
            
            # Oylik so'rovlar va sarf
            month_requests = ai_stats.get("month_ai_requests", 0)
            month_cost = ai_stats.get("month_ai_cost", 0.0)
            
            # Balans ma'lumotlari
            initial_balance = get_initial_balance()
            remaining_balance = initial_balance - month_cost
            if remaining_balance < 0:
                remaining_balance = 0.0
            
            # Oxirgi AI so'rov vaqti
            last_request_time = ai_stats.get("last_ai_request_time")
            if last_request_time:
                last_request_time_str = last_request_time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                last_request_time_str = "noma'lum"
            
            # Oxirgi AI javob vaqti
            last_response_time = ai_stats.get("last_response_time_ms")
            if last_response_time is not None:
                last_response_time_str = f"{int(last_response_time)} ms"
            else:
                last_response_time_str = "noma'lum"
            
            # Oxirgi AI xato (oxirgi 10 daqiqada)
            last_ai_error_time = None
            last_ai_error_type = None
            last_ai_error_reason = None
            
            error_time = ai_stats.get("last_error_time")
            if error_time:
                ten_minutes_ago = datetime.now() - timedelta(minutes=10)
                if error_time >= ten_minutes_ago:
                    last_ai_error_time = error_time.strftime("%H:%M:%S")
                    last_ai_error_type = ai_stats.get("last_error_type", "Noma'lum")
                    last_ai_error_reason = (ai_stats.get("last_error", "Noma'lum") or "Noma'lum")[:50]
            
            # Xavf darajasi baholash
            risk_level = "XAVF YO'Q"
            risk_emoji = "🟢"
            
            if remaining_balance < 0.5:
                risk_level = "XAVF"
                risk_emoji = "🔴"
            elif remaining_balance < 2.0:
                risk_level = "OGOHLANTIRISH"
                risk_emoji = "🟡"
            
            # AI sog'ligi baholash
            ai_health_emoji = "🟢"
            ai_health_text = "YAXSHI"
            
            if not ai_connected or api_key_status == "xato":
                ai_health_emoji = "🔴"
                ai_health_text = "MUAMMO (AI ishlamayapti)"
            elif remaining_balance < 0.5:
                ai_health_emoji = "🔴"
                ai_health_text = "MUAMMO (balans tugagan)"
            elif remaining_balance < 2.0:
                ai_health_emoji = "🟡"
                ai_health_text = "OGOHLANTIRISH (balans tugayapti)"
            
            status_message += "\n<b>🤖 AI holati:</b>\n"
            status_message += f"{ai_health_emoji} AI holati: {ai_health_text}\n"
            ai_connected_text = "ha" if ai_connected else "yo'q"
            status_message += f"🔹 AI ulangan: {ai_connected_text}\n"
            status_message += f"🔹 Model: {model_name}\n"
            status_message += f"🔹 API kalit: {api_key_status}\n"
            status_message += f"🔹 Bugungi so'rovlar: {today_requests} ta\n"
            status_message += f"🔹 Bugungi sarf: ~{today_cost:.4f} $\n"
            status_message += f"🔹 Oylik so'rovlar: {month_requests} ta\n"
            status_message += f"🔹 Oylik sarf: ~{month_cost:.4f} $\n"
            status_message += f"🔹 Oxirgi AI so'rov vaqti: {last_request_time_str}\n"
            
            status_message += "\n<b>💰 Balans:</b>\n"
            status_message += f"🔹 Boshlang'ich balans: {initial_balance:.2f} $\n"
            status_message += f"🔹 Taxminiy ishlatilgan: {month_cost:.4f} $\n"
            status_message += f"🔹 Taxminiy qolgan balans: {remaining_balance:.4f} $\n"
            status_message += f"{risk_emoji} Limit holati: {risk_level}\n"
            
            status_message += "\n<b>⚠ AI Xatolar:</b>\n"
            if last_ai_error_time:
                status_message += f"🔴 Oxirgi AI xato vaqti: {last_ai_error_time}\n"
                status_message += f"🔴 Xato turi: {last_ai_error_type}\n"
                status_message += f"🔴 Sabab: {last_ai_error_reason}\n"
            else:
                status_message += f"🔹 Oxirgi AI xato: yo'q\n"
                
        except Exception as e:
            logger.error(f"Error getting AI stats: {e}")
            status_message += "\n<b>🤖 AI holati:</b>\n"
            status_message += "🔹 AI ma'lumotlari: noma'lum\n"
        
        keyboard = make_admin_back_keyboard()
        
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=status_message,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error editing bot status message: {e}")
            # Fallback: send new message
            await bot.send_message(
                chat_id=callback_query.message.chat.id,
                text=status_message,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"Error checking bot status: {e}", exc_info=True)
        error_text = (
            "❌ <b>Bot holatini tekshirishda xatolik yuz berdi</b>\n\n"
            f"Xato: {str(e)}"
        )
        keyboard = make_admin_back_keyboard()
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=error_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            await bot.send_message(
                chat_id=callback_query.message.chat.id,
                text=error_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )


# ==================== API ACCESS MANAGEMENT ====================

@router.callback_query(F.data == "admin_api_access")
async def callback_admin_api_access(callback_query: CallbackQuery, bot: Bot):
    """Show API access management menu"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Get dict of API users
    api_users = get_api_users()
    
    api_users_text = "• Yo'q"
    if api_users:
        # Format: "Ism — ID"
        api_users_list = []
        for user_id in sorted(api_users.keys()):
            user_info = api_users[user_id]
            user_name = user_info.get("name", f"User {user_id}")
            api_users_list.append(f"• {user_name} — {user_id}")
        api_users_text = "\n".join(api_users_list)
    
    menu_text = (
        "🛠 <b>Narxlar bo'limi uchun API ruxsat</b>\n"
        "────────────\n\n"
        "Bu bo'lim orqali foydalanuvchilarga 'Modellar narxini bilish' bo'limiga kirish ruxsati beriladi.\n\n"
        f"<b>API ruxsati bor foydalanuvchilar:</b>\n{api_users_text}\n\n"
        "Quyidagi amallardan birini tanlang:"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ API ruxsat berish",
                    callback_data="admin_api_grant"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➖ API ruxsatni olib tashlash",
                    callback_data="admin_api_revoke"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_main"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data == "admin_api_grant")
async def callback_admin_api_grant(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start granting API access process"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    text = (
        "➕ <b>API ruxsat berish</b>\n"
        "────────────\n\n"
        "1️⃣ Foydalanuvchi ismini kiriting:\n\n"
        "⚠️ Eslatma: Foydalanuvchi ismini kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard
        )
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_api_user_name)
    await callback_query.answer()


@router.message(AdminStates.waiting_for_api_user_name)
async def process_api_grant_name(message: Message, state: FSMContext, bot: Bot):
    """Process API access name input"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    user_name = message.text.strip() if message.text else f"User"
    
    # State ga ismni saqlash
    await state.update_data(api_user_name=user_name)
    
    # Endi ID so'rash
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    text = (
        "➕ <b>API ruxsat berish</b>\n"
        "────────────\n\n"
        f"1️⃣ Ism: <b>{user_name}</b> ✅\n\n"
        "2️⃣ Foydalanuvchi ID sini kiriting:\n\n"
        "⚠️ Eslatma: Foydalanuvchi ID ni to'g'ri kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=text,
                reply_markup=keyboard
            )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_api_user_id)


@router.message(AdminStates.waiting_for_api_user_id)
async def process_api_grant_id(message: Message, state: FSMContext, bot: Bot):
    """Process API access ID input"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    try:
        target_user_id = int(message.text.strip())
        
        # State dan ismni olish
        state_data = await state.get_data()
        user_name = state_data.get("api_user_name", f"User {target_user_id}")
        bot_message_id = state_data.get("bot_message_id")
        
        # API ruxsat berish - admins.json ga saqlash
        grant_api_access(target_user_id, user_name)
        
        # Faolliklar jurnaliga yozish
        record_admin_action(
            admin_id=user_id,
            section="API ruxsat",
            action="API ruxsat berildi",
            result=f"✅ User {target_user_id}",
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        
        success_text = (
            "✅ <b>API ruxsat berildi</b>\n"
            "────────────\n\n"
            f"Foydalanuvchi: <b>{user_name}</b>\n"
            f"ID: {target_user_id}\n\n"
            "Ruxsat darhol kuchga kirdi."
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_api_access"
                    )
                ]
            ]
        )
        
        # Bot xabarini edit qilish
        if bot_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=success_text,
                    reply_markup=keyboard
                )
            except Exception:
                pass
        else:
            # Agar bot_message_id topilmasa, yangi xabar yuborish
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=success_text,
                    reply_markup=keyboard
                )
            except Exception:
                pass
        
        await state.clear()
        
    except ValueError:
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "❌ <b>Xato format</b>\n"
            "────────────\n\n"
            "Iltimos, faqat raqam kiriting."
        )
        
        keyboard = make_admin_back_keyboard()
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
        except Exception:
            pass
        
        await state.set_state(AdminStates.waiting_for_api_user_id)


@router.callback_query(F.data == "admin_api_revoke")
async def callback_admin_api_revoke(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start revoking API access process"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Get dict of API users
    api_users = get_api_users()
    
    if not api_users:
        await callback_query.answer("❌ API ruxsati bor foydalanuvchilar mavjud emas", show_alert=True)
        return
    
    # Create keyboard with API users (ID + ism bilan)
    buttons = []
    for api_user_id in sorted(api_users.keys()):
        user_info = api_users[api_user_id]
        user_name = user_info.get("name", f"User {api_user_id}")
        buttons.append([
            InlineKeyboardButton(
                text=f"➖ {user_name} — {api_user_id}",
                callback_data=f"admin_api_revoke_confirm:{api_user_id}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="admin_api_access"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    menu_text = (
        "➖ <b>API ruxsatni olib tashlash</b>\n"
        "────────────\n\n"
        "API ruxsatini olib tashlash uchun foydalanuvchini tanlang:"
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data.startswith("admin_api_revoke_confirm:"))
async def callback_admin_api_revoke_confirm(callback_query: CallbackQuery, bot: Bot):
    """Confirm and revoke API access"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Foydalanuvchi ismini olish (olib tashlashdan oldin)
    api_users = get_api_users()
    user_name = "Noma'lum"
    if target_user_id in api_users:
        user_name = api_users[target_user_id].get("name", f"User {target_user_id}")
    
    # API ruxsatni olib tashlash
    revoke_api_access(target_user_id)
    
    # Faolliklar jurnaliga yozish
    record_admin_action(
        admin_id=user_id,
        section="API ruxsat",
        action="API ruxsat olib tashlandi",
        result=f"❌ User {target_user_id}",
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
    )
    
    success_text = (
        "✅ <b>API ruxsat olib tashlandi</b>\n"
        "────────────\n\n"
        f"Foydalanuvchi: <b>{user_name}</b>\n"
        f"ID: {target_user_id}\n\n"
        "Ruxsat darhol olib tashlandi."
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_api_access"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=success_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer("✅ API ruxsat olib tashlandi")


# ==================== STORE ACCESS MANAGEMENT ====================

@router.callback_query(F.data == "admin_store_access")
async def callback_admin_store_access(callback_query: CallbackQuery, bot: Bot):
    """Show store access management menu"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    # Get dict of store access users
    from services.admin_storage import get_ready_sizes_store_access
    store_users = get_ready_sizes_store_access()
    
    store_users_text = "• Yo'q"
    if store_users:
        # Format: "Ism — ID"
        store_users_list = []
        for user_id_str in sorted(store_users.keys()):
            user_name = store_users[user_id_str]
            store_users_list.append(f"• {user_name} — {user_id_str}")
        store_users_text = "\n".join(store_users_list)
    
    menu_text = (
        "🏬 <b>Magazindagi tayyor razmerlar ruxsati</b>\n"
        "────────────\n\n"
        "Bu bo'lim orqali foydalanuvchilarga 'Magazindagi tayyor razmerlar' bo'limiga kirish ruxsati beriladi.\n\n"
        f"<b>Ruxsati bor foydalanuvchilar:</b>\n{store_users_text}\n\n"
        "Quyidagi amallardan birini tanlang:"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Ruxsat berish",
                    callback_data="admin_store_grant"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➖ Ruxsatni olib tashlash",
                    callback_data="admin_store_revoke"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_main"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data == "admin_store_grant")
async def callback_admin_store_grant(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start granting store access process"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    text = (
        "➕ <b>Magazin ruxsati berish</b>\n"
        "────────────\n\n"
        "1️⃣ Foydalanuvchi ismini kiriting:\n\n"
        "⚠️ Eslatma: Foydalanuvchi ismini kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await state.update_data(bot_message_id=callback_query.message.message_id)
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_store_access_user_name)
    await callback_query.answer()


@router.message(AdminStates.waiting_for_store_access_user_name)
async def process_store_grant_name(message: Message, state: FSMContext, bot: Bot):
    """Process store access name input"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    user_name = message.text.strip() if message.text else f"User"
    
    # State ga ismni saqlash
    await state.update_data(store_access_user_name=user_name)
    
    # Endi ID so'rash
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")
    
    text = (
        "➕ <b>Magazin ruxsati berish</b>\n"
        "────────────\n\n"
        f"1️⃣ Ism: <b>{user_name}</b> ✅\n\n"
        "2️⃣ Foydalanuvchi ID sini kiriting:\n\n"
        "⚠️ Eslatma: Foydalanuvchi ID ni to'g'ri kiriting."
    )
    
    keyboard = make_admin_back_keyboard()
    
    try:
        if bot_message_id:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=bot_message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    except Exception:
        pass
    
    await state.set_state(AdminStates.waiting_for_store_access_user_id)


@router.message(AdminStates.waiting_for_store_access_user_id)
async def process_store_grant_id(message: Message, state: FSMContext, bot: Bot):
    """Process store access ID input"""
    user_id = message.from_user.id
    
    if not is_any_admin(user_id):
        await message.answer("⛔ Sizda admin huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await message.delete()
    except Exception:
        pass
    
    try:
        target_user_id = int(message.text.strip())
        
        # State dan ismni olish
        state_data = await state.get_data()
        user_name = state_data.get("store_access_user_name", f"User {target_user_id}")
        bot_message_id = state_data.get("bot_message_id")
        
        # Store ruxsat berish - admins.json ga saqlash
        from services.admin_storage import add_ready_sizes_store_access
        add_ready_sizes_store_access(target_user_id, user_name)
        
        # Faolliklar jurnaliga yozish
        record_admin_action(
            admin_id=user_id,
            section="Magazin ruxsati",
            action="Magazin ruxsati berildi",
            result=f"✅ User {target_user_id}",
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        
        success_text = (
            "✅ <b>Magazin ruxsati berildi</b>\n"
            "────────────\n\n"
            f"Foydalanuvchi: <b>{user_name}</b>\n"
            f"ID: {target_user_id}\n\n"
            "Ruxsat darhol kuchga kirdi."
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="admin_store_access"
                    )
                ]
            ]
        )
        
        # Bot xabarini edit qilish
        if bot_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=success_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            # Agar bot_message_id topilmasa, yangi xabar yuborish
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=success_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception:
                pass
        
        await state.clear()
        
    except ValueError:
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        
        error_text = (
            "❌ <b>Xato format</b>\n"
            "────────────\n\n"
            "Iltimos, faqat raqam kiriting."
        )
        
        keyboard = make_admin_back_keyboard()
        
        try:
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
        except Exception:
            pass


@router.callback_query(F.data == "admin_store_revoke")
async def callback_admin_store_revoke(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Start revoking store access process"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    from services.admin_storage import get_ready_sizes_store_access
    store_users = get_ready_sizes_store_access()
    
    if not store_users:
        await callback_query.answer("❌ Ruxsati bor foydalanuvchilar yo'q", show_alert=True)
        return
    
    # Create buttons for each user
    buttons = []
    for user_id_str in sorted(store_users.keys()):
        user_name = store_users[user_id_str]
        buttons.append([
            InlineKeyboardButton(
                text=f"{user_name} — {user_id_str}",
                callback_data=f"admin_store_revoke_confirm:{user_id_str}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="admin_store_access"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    text = (
        "➖ <b>Magazin ruxsatini olib tashlash</b>\n"
        "────────────\n\n"
        "Qaysi foydalanuvchidan ruxsatni olib tashlashni xohlaysiz?"
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data.startswith("admin_store_revoke_confirm:"))
async def callback_admin_store_revoke_confirm(callback_query: CallbackQuery, bot: Bot):
    """Confirm and revoke store access"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔ Sizda admin huquqi yo'q", show_alert=True)
        return
    
    try:
        target_user_id = int(callback_query.data.split(":")[1])
    except (ValueError, IndexError):
        await callback_query.answer("❌ Xatolik", show_alert=True)
        return
    
    # Foydalanuvchi ismini olish (olib tashlashdan oldin)
    from services.admin_storage import get_ready_sizes_store_access, remove_ready_sizes_store_access, get_ready_sizes_store_access_name
    user_name = get_ready_sizes_store_access_name(target_user_id)
    
    # Store ruxsatni olib tashlash
    remove_ready_sizes_store_access(target_user_id)
    
    # Faolliklar jurnaliga yozish
    record_admin_action(
        admin_id=user_id,
        section="Magazin ruxsati",
        action="Magazin ruxsati olib tashlandi",
        result=f"❌ User {target_user_id}",
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
    )
    
    success_text = (
        "✅ <b>Magazin ruxsati olib tashlandi</b>\n"
        "────────────\n\n"
        f"Foydalanuvchi: <b>{user_name}</b>\n"
        f"ID: {target_user_id}\n\n"
        "Ruxsat darhol olib tashlandi."
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="admin_store_access"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=success_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass
    
    await callback_query.answer("✅ Magazin ruxsati olib tashlandi")


# ==================== RESTART BOT ====================

@router.callback_query(F.data == "admin_restart_bot")
async def callback_admin_restart_bot(callback_query: CallbackQuery, bot: Bot):
    """Show restart bot submenu"""
    user_id = callback_query.from_user.id
    
    # Faqat adminlar kirishi mumkin
    if not is_any_admin(user_id):
        await callback_query.answer("❌ Sizda ruxsat yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    menu_text = (
        "🔄 <b>Botni restart qilish</b>\n"
        "────────────────\n\n"
        "Quyidagi amallardan birini tanlang:"
    )
    
    keyboard = make_restart_bot_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_restart_start")
async def callback_admin_restart_start(callback_query: CallbackQuery, bot: Bot):
    """Start the bot service"""
    user_id = callback_query.from_user.id
    
    # Faqat adminlar kirishi mumkin
    if not is_any_admin(user_id):
        await callback_query.answer("❌ Sizda ruxsat yo'q", show_alert=True)
        return
    
    # Answer immediately
    await callback_query.answer("▶️ Bot ishga tushirilmoqda...")
    
    # Import system service
    from services.system_service import start_bot_service
    
    # Show processing message
    processing_text = "⏳ Bot ishga tushirilmoqda...\n\nIltimos, kuting..."
    keyboard = make_restart_bot_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=processing_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    # Execute start command
    result = start_bot_service()
    
    # Record admin action
    record_admin_action(
        admin_id=user_id,
        section="Restart bot",
        action="Start bot",
        result="✅ Muvaffaqiyatli" if result["success"] else "❌ Xatolik",
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
    )
    
    # Show result
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result["message"],
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_restart_stop")
async def callback_admin_restart_stop(callback_query: CallbackQuery, bot: Bot):
    """Stop the bot service"""
    user_id = callback_query.from_user.id
    
    # Faqat adminlar kirishi mumkin
    if not is_any_admin(user_id):
        await callback_query.answer("❌ Sizda ruxsat yo'q", show_alert=True)
        return
    
    # Answer immediately
    await callback_query.answer("⏹ Bot to'xtatilmoqda...")
    
    # Import system service
    from services.system_service import stop_bot_service
    
    # Show processing message
    processing_text = "⏳ Bot to'xtatilmoqda...\n\nIltimos, kuting..."
    keyboard = make_restart_bot_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=processing_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    # Execute stop command
    result = stop_bot_service()
    
    # Record admin action
    record_admin_action(
        admin_id=user_id,
        section="Restart bot",
        action="Stop bot",
        result="✅ Muvaffaqiyatli" if result["success"] else "❌ Xatolik",
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
    )
    
    # Show result
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result["message"],
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_restart_restart")
async def callback_admin_restart_restart(callback_query: CallbackQuery, bot: Bot):
    """Restart the bot service"""
    user_id = callback_query.from_user.id
    
    # Faqat adminlar kirishi mumkin
    if not is_any_admin(user_id):
        await callback_query.answer("❌ Sizda ruxsat yo'q", show_alert=True)
        return
    
    # Answer immediately
    await callback_query.answer("🔄 Bot restart qilinyapti...")
    
    # Import system service
    from services.system_service import restart_bot_service
    
    # Show processing message
    processing_text = "⏳ Bot restart qilinyapti...\n\nIltimos, kuting..."
    keyboard = make_restart_bot_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=processing_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    # Wait 1 second
    await asyncio.sleep(1)
    
    # Execute restart command
    result = restart_bot_service()
    
    # Record admin action
    record_admin_action(
        admin_id=user_id,
        section="Restart bot",
        action="Restart bot",
        result="✅ Muvaffaqiyatli" if result["success"] else "❌ Xatolik",
        username=callback_query.from_user.username,
        first_name=callback_query.from_user.first_name,
    )
    
    # Show result
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result["message"],
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_restart_status")
async def callback_admin_restart_status(callback_query: CallbackQuery, bot: Bot):
    """Get bot service status"""
    user_id = callback_query.from_user.id
    
    # Faqat adminlar kirishi mumkin
    if not is_any_admin(user_id):
        await callback_query.answer("❌ Sizda ruxsat yo'q", show_alert=True)
        return
    
    # Answer immediately
    await callback_query.answer("📊 Status tekshirilmoqda...")
    
    # Import system service
    from services.system_service import get_bot_service_status
    
    # Show processing message
    processing_text = "⏳ Status tekshirilmoqda...\n\nIltimos, kuting..."
    keyboard = make_restart_bot_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=processing_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    # Get status
    result = get_bot_service_status()
    
    # Show result
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result["message"],
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "admin_restart_log")
async def callback_admin_restart_log(callback_query: CallbackQuery, bot: Bot):
    """Get bot service logs"""
    user_id = callback_query.from_user.id
    
    # Faqat adminlar kirishi mumkin
    if not is_any_admin(user_id):
        await callback_query.answer("❌ Sizda ruxsat yo'q", show_alert=True)
        return
    
    # Answer immediately
    await callback_query.answer("📜 Loglar yuklanmoqda...")
    
    # Import system service
    from services.system_service import get_bot_service_logs
    
    # Show processing message
    processing_text = "⏳ Loglar yuklanmoqda...\n\nIltimos, kuting..."
    keyboard = make_restart_bot_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=processing_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    # Get logs
    result = get_bot_service_logs(30)
    
    # Show result
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result["message"],
            reply_markup=keyboard
        )
    except Exception:
        pass
