"""
Model prices handler - shows prices from sheets3.
Only accessible to users with API access.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services.settings import has_api_access
from services.admin_utils import is_super_admin, is_admin, is_any_admin
from services.google_sheet import GoogleSheetService, CACHE
from services.product_utils import normalize_code, generate_fuzzy_code_variants
from services.stats import record_price_request, record_price_history, record_discount_section_action
import logging
import re
from typing import Any, Dict, AnyStr

logger = logging.getLogger(__name__)

router = Router()


# ==================== STATES ====================

class ModelPricesStates(StatesGroup):
    waiting_for_code = State()


# ==================== COLLECTION MAPPINGS ====================

# Katta kolleksiyalar
COLLECTION_MAIN = {
    "xitoy_kombo": "Xitoy kombo",
    "kombo_turk": "Kombo turk",
    "rollo_kitay": "Rollo kitay",
    "rollo_turk": "Rollo turk",
    "dabl_turk": "ROLLO SHTOR",
    "dikey_turk": "Дикий",
    "plise": "Plise"
}

# Ichki qatorlar (aniq belgilangan)
COLLECTION_SUB = {
    "xitoy_kombo": [
        "0-start",
        "1-stage",
        "2-middle",
        "3-optimal",
        "4-top",
        "5-perfect",
        "6-exclusive"
    ]
}


# ==================== HELPER FUNCTIONS ====================

def make_prices_menu_keyboard() -> InlineKeyboardMarkup:
    """Create prices menu keyboard"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Umumiy narxdan bilish",
                    callback_data="prices_general"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗂 Kolleksiya bo'yicha",
                    callback_data="prices_collection"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="menu_main"
                )
            ]
        ]
    )


def make_back_keyboard() -> InlineKeyboardMarkup:
    """Create back keyboard for prices section"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="price_back"
                ),
                InlineKeyboardButton(
                    text="🏠 Asosiy menyu",
                    callback_data="price_home"
                )
            ]
        ]
    )


def make_collection_result_back_keyboard() -> InlineKeyboardMarkup:
    """Create back keyboard for collection results - FAQAT 1 bosqich orqaga qaytadi"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="prices_collection_back"
                ),
                InlineKeyboardButton(
                    text="🏠 Asosiy menyu",
                    callback_data="price_home"
                )
            ]
        ]
    )


def make_collection_main_keyboard() -> InlineKeyboardMarkup:
    """Create main collections keyboard"""
    buttons = []
    for key, name in COLLECTION_MAIN.items():
        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"prices_collection_main:{key}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="prices_menu"
        ),
        InlineKeyboardButton(
            text="🏠 Asosiy menyu",
            callback_data="menu_main"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def make_collection_sub_keyboard(collection_key: str) -> InlineKeyboardMarkup:
    """Create sub-collections keyboard"""
    if collection_key not in COLLECTION_SUB:
        return make_back_keyboard()
    
    buttons = []
    for sub_name in COLLECTION_SUB[collection_key]:
        buttons.append([
            InlineKeyboardButton(
                text=sub_name,
                callback_data=f"prices_collection_sub:{collection_key}:{sub_name}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="prices_collection"
        ),
        InlineKeyboardButton(
            text="🏠 Asosiy menyu",
            callback_data="menu_main"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def make_discount_menu_keyboard() -> InlineKeyboardMarkup:
    """Create discount models main category keyboard (sheets4)"""
    buttons = [
        [
            InlineKeyboardButton(
                text="Turk kombo",
                callback_data="discount_category:turk_kombo"
            )
        ],
        [
            InlineKeyboardButton(
                text="Xitoy kombo",
                callback_data="discount_category:xitoy_kombo"
            )
        ],
        [
            InlineKeyboardButton(
                text="Dikey",
                callback_data="discount_category:dikey"
            )
        ],
        [
            InlineKeyboardButton(
                text="Plise",
                callback_data="discount_category:plise"
            )
        ],
        [
            InlineKeyboardButton(
                text="Rollo (zashitka)",
                callback_data="discount_category:rollo_zashitka"
            )
        ],
        [
            InlineKeyboardButton(
                text="Rollo shtor",
                callback_data="discount_category:rollo_shtor"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="discount_back"
            ),
            InlineKeyboardButton(
                text="🏠 Asosiy menyu",
                callback_data="discount_home"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def make_discount_back_keyboard() -> InlineKeyboardMarkup:
    """Create back keyboard specifically for discount section"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="discount_categories_back"
                ),
                InlineKeyboardButton(
                    text="🏠 Asosiy menyu",
                    callback_data="discount_home"
                )
            ]
        ]
    )


def normalize_collection(collection: str) -> str:
    """Normalize collection name for comparison.

    Qoidalar:
    - lower()
    - strip()
    - bo'sh joylarni olib tashlash
    - "_" va "-" (hamda "—") belgilarini olib tashlash

    Masalan, quyidagilarning barchasi bir xil bo'ladi:
        "0-start", "0 start", "0_start" -> "0start"
    """
    if not collection:
        return ""
    value = str(collection).strip().lower()
    # Bo'sh joylarni olib tashlash
    value = value.replace(" ", "")
    # "_" va turli chiziqchalarni olib tashlash
    for ch in ["_", "-", "—"]:
        value = value.replace(ch, "")
    return value


def fuzzy_normalize_model_name(model_name: str) -> str:
    """Fuzzy normalization for model names - handles Cyrillic/Latin, case, spaces, punctuation.
    
    Bu funksiya "Дикий" tugmasi uchun maxsus fuzzy qidiruv uchun ishlatiladi.
    
    Qoidalar:
    - Case insensitive (katta/kichik harf farqi yo'q)
    - Cyrillic/Latin conversion (Дикий <-> Dikey)
    - Extra spaces, dashes, dots, commas olib tashlanadi
    - Faqat harf va raqam qoladi
    
    Masalan:
        "Дикий" -> "dikey"
        "Dikey" -> "dikey"
        "д икий" -> "dikey"
        "Dikey-" -> "dikey"
    """
    if not model_name:
        return ""
    
    # Convert to lowercase
    value = str(model_name).strip().lower()
    
    # Cyrillic to Latin mapping
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
    
    # Remove all whitespace (spaces, tabs, newlines)
    value = re.sub(r'\s+', '', value)
    
    # Remove punctuation: dashes, dots, commas, underscores
    for ch in ["-", "_", ".", ",", "—", "–"]:
        value = value.replace(ch, "")
    
    # Keep only letters and numbers
    value = re.sub(r'[^a-z0-9]', '', value)
    
    return value


def get_special_model_display_labels(model_name: str):
    """Maxsus modellar uchun sarlavha va ustun ko'rsatish qoidalarini aniqlash.
    
    Args:
        model_name: Model nomi (sheets3 dagi "Madel nomi" ustuni)
    
    Returns:
        Tuple (header, show_kasetniy):
        - header: Jadval sarlavhasi (None bo'lsa, standart sarlavha ishlatiladi)
        - show_kasetniy: Kasetniy ustunini ko'rsatish kerakmi (True/False)
    """
    if not model_name:
        return None, True
    
    model_name_upper = model_name.upper().strip()
    model_name_lower = model_name.lower().strip()
    model_name_original = model_name.strip()
    
    # ROLLO SHTOR tekshiruvi
    is_rollo_shtor = (
        "ROLLO SHTOR" in model_name_upper or 
        "rollo shtor" in model_name_lower or 
        "ролло штор" in model_name_lower or
        "ROLLO" in model_name_upper and "SHTOR" in model_name_upper
    )
    
    # Дикий tekshiruvi (kirill va lotin)
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
    
    # PLISE tekshiruvi
    is_plise = (
        "PLISE" in model_name_upper or 
        "plise" in model_name_lower or 
        "плисе" in model_name_lower or
        "плисе" in model_name_original
    )
    
    if is_rollo_shtor:
        return "Code | 50% lik | 100% lik", False
    elif is_dikey:
        return "Code | To'ldi o'zi bo'lsa | Yoniga porter bo'lsa", False
    elif is_plise:
        return "Code | 0,50 kv | 1,00 kv", False
    
    return None, True


def fuzzy_match_model_name(search_term: str, model_name: str) -> bool:
    """Check if model_name matches search_term using fuzzy matching.
    
    Args:
        search_term: Qidirilayotgan model nomi (masalan: "Дикий")
        model_name: sheets3 dagi "Madel nomi" ustunidagi qiymat
    
    Returns:
        True agar mos kelsa, False aks holda
    
    Qidiruv qoidalari:
    - Case insensitive
    - Cyrillic/Latin insensitive
    - Extra spaces/punctuation ignored
    - Partial match (qisman moslik yetarli) - IKKI TOMONLAMA:
      * normalized_button in normalized_model_name
      * normalized_model_name in normalized_button
    """
    if not search_term or not model_name:
        return False
    
    # Fuzzy normalization
    search_normalized = fuzzy_normalize_model_name(search_term)
    model_normalized = fuzzy_normalize_model_name(model_name)
    
    if not search_normalized or not model_normalized:
        return False
    
    # "Дикий" uchun maxsus variant: "Дикий" -> "dikiy", lekin "Дикей" -> "dikey"
    # Ikkalasini ham qidirish uchun "dikey" variantini ham qo'shamiz
    search_variants = [search_normalized]
    if search_normalized == "dikiy":  # "Дикий" -> "dikiy"
        search_variants.append("dikey")  # "Дикей" variantini ham qidirish
    elif search_normalized == "dikey":  # "Дикей" -> "dikey"
        search_variants.append("dikiy")  # "Дикий" variantini ham qidirish
    
    # IKKI TOMONLAMA qisman moslik tekshiruvi:
    # 1) normalized_button normalized_model_name ichida bo'lsa
    # 2) normalized_model_name normalized_button ichida bo'lsa
    # Masalan: "Дикий" -> "dikey" va "Дикий турк" -> "dikeyturk" -> "dikey" in "dikeyturk" = True
    # Yoki: "Дикий турк" -> "dikeyturk" va "Дикий" -> "dikey" -> "dikeyturk" in "dikey" = False, lekin "dikey" in "dikeyturk" = True
    for search_var in search_variants:
        if search_var in model_normalized or model_normalized in search_var:
            return True
    return False


def make_code_input_keyboard() -> InlineKeyboardMarkup:
    """Create inline keyboard for code input with back button"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="prices_menu"
                )
            ]
        ]
    )


# ==================== ENTRY POINT ====================

@router.callback_query(F.data == "menu_model_prices")
async def callback_model_prices_menu(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle model prices menu entry - INLINE BUTTON"""
    try:
        await callback_query.answer()
        
        user_id = callback_query.from_user.id
        
        # FSM STATE TOZALASH
        try:
            await state.clear()
        except Exception:
            pass
        
        # Narx bo'limi tekshiruvi
        is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
        if not (is_any_admin_user or has_api_access(user_id)):
            return

        # Log yozish
        try:
            record_price_request(
                user_id=user_id,
                username=callback_query.from_user.username,
                first_name=callback_query.from_user.first_name,
            )
        except Exception:
            pass
        
        menu_text = (
            "💰 <b>Modellar narxini bilish</b>\n\n"
            "Quyidagi variantlardan birini tanlang:"
        )
        
        keyboard = make_prices_menu_keyboard()
        
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.message(F.text == "💰 Modellar narxini bilish")
async def handle_model_prices_text(message: Message, state: FSMContext, bot: Bot):
    """Handle model prices menu entry - REPLY KEYBOARD TEXT"""
    try:
        # FSM STATE TOZALASH - ENG BOSHIDA
        await state.clear()
    except Exception:
        pass
    
    try:
        user_id = message.from_user.id
        
        # Narx bo'limi tekshiruvi (log uchun, lekin javobni bloklamaydi)
        is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
        has_access = is_any_admin_user or has_api_access(user_id)

        # Log yozish
        if has_access:
            try:
                record_price_request(
                    user_id=user_id,
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                )
            except Exception:
                pass
        
        menu_text = (
            "💰 <b>Modellar narxini bilish</b>\n\n"
            "Quyidagi variantlardan birini tanlang:"
        )
        
        keyboard = make_prices_menu_keyboard()
        
        # message.answer() - majburiy (har doim javob qaytaradi)
        await message.answer(
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass


# ==================== SKIDKA MODELLAR (sheets4) ====================

DISCOUNT_CATEGORY_LABELS = {
    "turk_kombo": "Turk kombo",
    "xitoy_kombo": "Xitoy kombo",
    "dikey": "Dikey",
    "plise": "Plise",
    "rollo_zashitka": "Rollo (zashitka)",
    "rollo_shtor": "Rollo shtor",
}


def _build_sheets1_quantity_lookup() -> Dict[str, str]:
    """
    Build a fast lookup dictionary from Sheets1: normalized_code -> quantity.
    This is used for discount models to get quantities from Sheets1 instead of Sheets4.
    
    Returns:
        Dictionary mapping normalized code to quantity string
    """
    lookup = {}
    sheets1_data = CACHE.get("sheets1", [])
    
    for row in sheets1_data:
        code = row.get("code", "")
        if not code:
            continue
        
        # Normalize the code for matching
        code_normalized = normalize_code(code)
        if not code_normalized:
            continue
        
        # Get quantity (column B in Sheets1)
        quantity = row.get("quantity", "")
        if quantity is None:
            quantity = ""
        else:
            quantity = str(quantity).strip()
        
        # Store in lookup dict
        lookup[code_normalized] = quantity
    
    logger.debug(f"Built Sheets1 quantity lookup with {len(lookup)} entries")
    return lookup


def _get_quantity_from_sheets1(code: str, lookup: Dict[str, str]) -> str:
    """
    Get quantity for a code from Sheets1 lookup dictionary.
    
    Args:
        code: Original code from Sheets4
        lookup: Lookup dictionary from _build_sheets1_quantity_lookup()
    
    Returns:
        Quantity string or empty string if not found
    """
    if not code:
        return ""
    
    # Normalize the code
    code_normalized = normalize_code(code)
    if not code_normalized:
        return ""
    
    # Lookup in dictionary
    quantity = lookup.get(code_normalized, "")
    return quantity


def _normalize_discount_model_name(name: str) -> str:
    """
    Chegirma bo'limi uchun model nomini normalizatsiya qilish.
    Sodda va moslashuvchan: katta/kichik harf, bo'sh joylar, maxsus belgilar olib tashlanadi.
    
    Args:
        name: Model nomi (masalan: "Turk kombo", "Xitoy-kombo", "Dikey (yangi)")
    
    Returns:
        Normalizatsiyalangan nom (masalan: "turkkombo", "xitoykombo", "dikeyyangi")
    """
    if not name:
        return ""
    
    # 1. Kichik harfga o'tkazish
    normalized = name.lower().strip()
    
    # 2. Maxsus belgilarni olib tashlash: - _ . , ( ) [ ] { } / \ "
    import re
    normalized = re.sub(r'[-_.,()[\]{}/\\\"]', ' ', normalized)
    
    # 3. Ketma-ket bo'sh joylarni bitta bo'sh joyga aylantirish
    normalized = re.sub(r'\s+', '', normalized)
    
    # 4. Final strip
    normalized = normalized.strip()
    
    return normalized


def shorten_collection_name(collection: str) -> str:
    """
    Kolleksiya nomini qisqartirish.
    Misollar:
    - "3 OPTIMAL" -> "OPT3"
    - "1-stage" -> "ST1"
    - "4-TOP" -> "TOP4"
    - "2 MIDDLE" -> "MID2"
    """
    if not collection:
        return ""
    
    collection = collection.strip()
    
    # Raqamlarni va harflarni ajratish
    import re
    
    # Raqam + harflar (masalan: "3 OPTIMAL" -> "OPT3")
    match = re.match(r'(\d+)\s*([A-Za-z]+)', collection, re.IGNORECASE)
    if match:
        num = match.group(1)
        letters = match.group(2).upper()
        # Harflarni qisqartirish (maksimal 3-4 ta harf)
        if len(letters) > 4:
            letters = letters[:4]
        # Format: harflar + raqam
        return f"{letters}{num}"
    
    # Harflar + raqam (masalan: "1-stage" -> "ST1", "stage-1" -> "STA1")
    match = re.match(r'([A-Za-z]+)[\s\-]*(\d+)', collection, re.IGNORECASE)
    if match:
        letters = match.group(1).upper()
        num = match.group(2)
        # Harflarni qisqartirish (maksimal 3 ta harf)
        if len(letters) > 3:
            letters = letters[:3]
        # Format: harflar + raqam
        return f"{letters}{num}"
    
    # Agar hech qanday pattern mos kelmasa, faqat harflarni olish (maksimal 4 ta)
    letters_only = re.sub(r'[^A-Za-z]', '', collection).upper()
    if letters_only:
        return letters_only[:4]
    
    return collection[:4] if len(collection) > 4 else collection


@router.callback_query(F.data == "prices_discount")
async def callback_prices_discount(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle entry to discount models section - INLINE BUTTON"""
    try:
        await callback_query.answer()

        user_id = callback_query.from_user.id

        # FSM STATE TOZALASH
        try:
            await state.clear()
        except Exception:
            pass

        # Skidka bo'limi - barcha userlarga ochiq (ruxsat tekshiruvi olib tashlandi)

        # Log yozish
        try:
            record_discount_section_action(
                user_id=user_id,
                section="Skidka",
                action="Bo'limga kirish",
                username=callback_query.from_user.username,
                first_name=callback_query.from_user.first_name,
            )
        except Exception:
            pass

        menu_text = (
            "🔥 <b>Skidkaga tushgan modellar</b>\n\n"
            "Quyidagi kategoriyalardan birini tanlang:"
        )

        keyboard = make_discount_menu_keyboard()

        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.message(F.text == "🔥 Skidkaga tushgan modellar")
async def handle_prices_discount_text(message: Message, state: FSMContext, bot: Bot):
    """Handle entry to discount models section - REPLY KEYBOARD TEXT"""
    try:
        # FSM STATE TOZALASH - ENG BOSHIDA
        await state.clear()
    except Exception:
        pass
    
    try:
        user_id = message.from_user.id

        # Skidka bo'limi tekshiruvi (log uchun, lekin javobni bloklamaydi)
        # 1. Avval admin tekshiriladi
        is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
        # 2. Keyin price_access tekshiriladi
        # 3. Keyin discount_access tekshiriladi
        from services.settings import has_discount_access
        has_discount = has_discount_access(user_id)
        has_access = is_any_admin_user or has_api_access(user_id) or has_discount

        # Log yozish
        if has_access:
            try:
                record_discount_section_action(
                    user_id=user_id,
                    section="Skidka",
                    action="Bo'limga kirish",
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                )
            except Exception:
                pass

        menu_text = (
            "🔥 <b>Skidkaga tushgan modellar</b>\n\n"
            "Quyidagi kategoriyalardan birini tanlang:"
        )

        keyboard = make_discount_menu_keyboard()

        # message.answer() - majburiy (har doim javob qaytaradi)
        await message.answer(
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("discount_category:"))
async def callback_discount_category(callback_query: CallbackQuery, bot: Bot):
    """Handle discount category selection and show models from sheets4 in LIST VIEW format"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()

    user_id = callback_query.from_user.id

    # Skidka bo'limi - barcha userlarga ochiq (ruxsat tekshiruvi olib tashlandi)

    data = callback_query.data.split(":", 1)
    if len(data) < 2:
        return

    category_key = data[1]
    category_label = DISCOUNT_CATEGORY_LABELS.get(category_key)
    if not category_label:
        return

    # Statistika - kategoriya tanlandi
    try:
        record_discount_section_action(
            user_id=user_id,
            section="Skidka",
            action=category_label,
            username=callback_query.from_user.username,
            first_name=callback_query.from_user.first_name,
        )
    except Exception:
        pass

    # sheets4 dan ma'lumot olish (code va photo uchun)
    sheet_service = GoogleSheetService()
    try:
        discounts = await sheet_service.read_discount_prices_from_sheets4()
    except Exception:
        discounts = []

    # Build Sheets1 quantity lookup dictionary (YANGI: quantity Sheets1 dan olinadi)
    sheets1_quantity_lookup = _build_sheets1_quantity_lookup()

    # User tanlagan model nomini normalizatsiya qilish (sodda va moslashuvchan)
    user_model_normalized = _normalize_discount_model_name(category_label)
    logger.info(f"[discount_category] Searching for: '{category_label}' (normalized: '{user_model_normalized}')")

    # Model nomi bo'yicha filtrlash - Sheets4 "Madel nomi" ustunidan
    matched = []
    for row in discounts:
        row_model_name = row.get("model_name", "")
        if not row_model_name:
            continue

        row_model_normalized = _normalize_discount_model_name(row_model_name)

        # To'liq tenglik: normalize(button_text) == normalize(sheets4_madel_nomi)
        if row_model_normalized == user_model_normalized:
            matched.append(row)
            logger.info(f"[discount_category] Matched: '{row_model_name}' (normalized: '{row_model_normalized}')")

    # Debug log: filtrlash natijalari soni
    logger.info(f"[discount_category] Total matched rows: {len(matched)}")

    if not matched:
        keyboard = make_discount_back_keyboard()
        error_text = (
            f"❌ <b>{category_label}</b> bo'yicha skidkaga tushgan modellardan topilmadi."
        )
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=error_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # LIST VIEW: bitta matn xabarda jadval ko'rinishida chiqarish
    # Unique kombinatsiya = (code + collection)
    unique_models = {}
    for item in matched:
        code = (item.get("code") or "").strip()
        collection = (item.get("collection") or "").strip()
        if not code:
            continue

        unique_key = f"{code}|{collection}"
        if unique_key not in unique_models:
            # YANGI: quantity ni Sheets1 dan olish
            quantity_from_sheets1 = _get_quantity_from_sheets1(code, sheets1_quantity_lookup)
            
            unique_models[unique_key] = {
                "code": code,
                "collection": collection,
                "quantity": quantity_from_sheets1,  # Sheets1 dan olingan quantity
                "image_url": (item.get("image_url") or "").strip(),
                "model_name": (item.get("model_name") or "").strip(),
            }

    if not unique_models:
        keyboard = make_discount_back_keyboard()
        error_text = (
            f"❌ <b>{category_label}</b> bo'yicha skidkaga tushgan modellardan topilmadi."
        )
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=error_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    # Header ma'lumotlari - kategoriya nomi
    lines = []
    lines.append(f"🔥 {category_label}")
    lines.append("")

    # Har bir model uchun yangi format: faqat CODE, QOLDIQ va qisqartirilgan kolleksiya
    model_codes = []
    for item in unique_models.values():
        code = item["code"]
        collection = item["collection"] or ""
        quantity = item["quantity"] or "-"

        # Kolleksiyani qisqartirish
        collection_short = shorten_collection_name(collection) if collection else ""
        
        # Format: 🔹 {CODE} - {QOLDIQ} ({KOLLEKSIYA_QISQA})
        if collection_short:
            line = f"🔹 {code} - {quantity} ({collection_short})"
        else:
            line = f"🔹 {code} - {quantity}"
        lines.append(line)
        
        model_codes.append(code)

    result_text = "\n".join(lines)

    # Inline keyboard: har bir model CODE uchun tugma, 3 tadan qatorda
    inline_buttons = []
    for i, code in enumerate(model_codes):
        if i % 3 == 0:
            # Yangi qator boshlash
            inline_buttons.append([])
        # Tugma matni = model CODE
        inline_buttons[-1].append(
            InlineKeyboardButton(
                text=code,
                callback_data=f"discount_model:{code}"
            )
        )

    # Pastki qatorga: ⬅️ Orqaga, 🏠 Asosiy menyu
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="discount_categories_back"
        ),
        InlineKeyboardButton(
            text="🏠 Asosiy menyu",
            callback_data="discount_home"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)

    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("discount_model:"))
async def callback_discount_model(callback_query: CallbackQuery, bot: Bot):
    """Handle discount model CODE button - send photo with minimal info"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()

    user_id = callback_query.from_user.id

    # Skidka bo'limi - barcha userlarga ochiq

    # callback_data format: discount_model:CODE
    data = callback_query.data.split(":", 1)
    if len(data) < 2:
        await callback_query.answer("❌ Noto'g'ri ma'lumot!", show_alert=True)
        return

    code = data[1]

    # 2-bosqich xabari matnidan kategoriya nomini olish (🔥 <Model nomi>)
    header_label = ""
    msg_text = (callback_query.message.text or callback_query.message.caption or "") if callback_query.message else ""
    if msg_text:
        for line in msg_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("🔥"):
                # "🔥 " dan keyingi qismni olish
                header_label = stripped.lstrip("🔥").strip()
                break

    # sheets4 dan ma'lumot olish (code va photo uchun)
    sheet_service = GoogleSheetService()
    try:
        discounts = await sheet_service.read_discount_prices_from_sheets4()
    except Exception:
        discounts = []

    # Build Sheets1 lookup dictionary (quantity va date Sheets1 dan olinadi)
    sheets1_data = CACHE.get("sheets1", [])
    sheets1_lookup = {}  # normalized_code -> {quantity, date, collection}
    for row in sheets1_data:
        row_code = row.get("code", "")
        if not row_code:
            continue
        code_normalized = normalize_code(row_code)
        if not code_normalized:
            continue
        sheets1_lookup[code_normalized] = {
            "quantity": str(row.get("quantity", "")).strip(),
            "date": str(row.get("date", "")).strip(),
            "collection": str(row.get("collection", "")).strip()
        }

    # Code bo'yicha topish (normalize bilan) - birinchi topilgan qatorni olish
    code_norm = normalize_code(code)
    matched_sheets4 = None
    for row in discounts:
        row_code_norm = row.get("code_normalized", "")
        if row_code_norm == code_norm:
            matched_sheets4 = row
            break

    if not matched_sheets4:
        await callback_query.answer("❌ Mahsulot topilmadi yoki ma'lumotlar yuklanmagan!", show_alert=True)
        return

    # Ma'lumotlarni olish
    code_display = matched_sheets4.get("code", "").strip()
    image_url = (matched_sheets4.get("image_url") or "").strip()
    
    # Sheets1 dan ma'lumot olish
    sheets1_info = sheets1_lookup.get(code_norm, {})
    quantity = sheets1_info.get("quantity", "")
    date_value = sheets1_info.get("date", "")
    collection = sheets1_info.get("collection", "")

    # Minimal format: faqat Code, Kolleksiya, Sana, Qoldiq
    result_text = f"📦 Code: <b>{code_display}</b>\n"
    if collection:
        result_text += f"📁 Kolleksiya: {collection}\n"
    if date_value:
        result_text += f"📅 Sana: {date_value}\n"
    if quantity:
        result_text += f"📊 Qoldiq: {quantity}"

    # Orqaga tugmalari
    back_label = header_label if header_label else ""

    detail_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data=f"discount_list_back:{back_label}"
                ),
                InlineKeyboardButton(
                    text="🏠 Asosiy menyu",
                    callback_data="discount_home"
                ),
            ]
        ]
    )

    # Eski xabarni o'chirish
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

    # YANGI xabar yuborish: rasm bilan yoki rasmsiz
    if image_url:
        try:
            converted_url = sheet_service._convert_google_drive_link(image_url)
            await bot.send_photo(
                chat_id=callback_query.message.chat.id,
                photo=converted_url,
                caption=result_text,
                reply_markup=detail_keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error sending photo: {e}")
            # Fallback: rasm yuborib bo'lmasa, faqat matn yuborish
            try:
                await bot.send_message(
                    chat_id=callback_query.message.chat.id,
                    text=result_text,
                    reply_markup=detail_keyboard,
                    parse_mode="HTML"
                )
            except Exception:
                pass
    else:
        # Rasm yo'q bo'lsa, faqat matn yuborish
        try:
            await bot.send_message(
                chat_id=callback_query.message.chat.id,
                text=result_text,
                reply_markup=detail_keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("discount_list_back:"))
async def callback_discount_list_back(callback_query: CallbackQuery, bot: Bot):
    """Handle 'Orqaga' from image detail view - return to LIST VIEW (DELETE + SEND)"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()

    user_id = callback_query.from_user.id

    # Skidka bo'limi - barcha userlarga ochiq

    # Kategoriya label ni olish
    data = callback_query.data.split(":", 1)
    if len(data) < 2:
        logger.error(f"discount_list_back: Invalid callback_data: {callback_query.data}")
        await callback_query.answer("❌ Noto'g'ri ma'lumot!", show_alert=True)
        return

    category_label = data[1]
    logger.info(f"discount_list_back: category_label={category_label}")

    # sheets4 dan ma'lumot olish (code va photo uchun)
    sheet_service = GoogleSheetService()
    try:
        discounts = await sheet_service.read_discount_prices_from_sheets4()
    except Exception:
        discounts = []

    # Build Sheets1 quantity lookup dictionary (YANGI: quantity Sheets1 dan olinadi)
    sheets1_quantity_lookup = _build_sheets1_quantity_lookup()

    # Model nomi bo'yicha filtrlash - barcha modellarni topish (sodda normalizatsiya)
    user_model_normalized = _normalize_discount_model_name(category_label)
    matched = []
    for row in discounts:
        row_model_name = row.get("model_name", "")
        if not row_model_name:
            continue
        row_model_normalized = _normalize_discount_model_name(row_model_name)
        if row_model_normalized == user_model_normalized:
            matched.append(row)

    if not matched:
        await callback_query.answer("❌ Modellar ro'yxati topilmadi!", show_alert=True)
        return

    # Joriy (3-bosqich) rasm xabarini o'chirish
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except Exception as e:
        logger.error(f"discount_list_back: error deleting message: {e}")

    # LIST VIEW formatlash - xuddi kategoriya tanlangandagi kabi (2-bosqich)
    unique_models = {}
    for item in matched:
        code_item = (item.get("code") or "").strip()
        collection_item = (item.get("collection") or "").strip()
        if not code_item:
            continue

        unique_key = f"{code_item}|{collection_item}"
        if unique_key not in unique_models:
            # YANGI: quantity ni Sheets1 dan olish
            quantity_from_sheets1 = _get_quantity_from_sheets1(code_item, sheets1_quantity_lookup)
            
            unique_models[unique_key] = {
                "code": code_item,
                "collection": collection_item,
                "quantity": quantity_from_sheets1,  # Sheets1 dan olingan quantity
                "model_name": (item.get("model_name") or "").strip(),
            }

    # Header ma'lumotlari - kategoriya nomi
    lines = []
    lines.append(f"🔥 {category_label}")
    lines.append("")

    # Har bir model uchun yangi format: faqat CODE, QOLDIQ va qisqartirilgan kolleksiya
    model_codes = []
    for item in unique_models.values():
        code_item = item["code"]
        collection_item = item["collection"] or ""
        quantity = item["quantity"] or "-"

        # Kolleksiyani qisqartirish
        collection_short = shorten_collection_name(collection_item) if collection_item else ""
        
        # Format: 🔹 {CODE} - {QOLDIQ} ({KOLLEKSIYA_QISQA})
        if collection_short:
            line = f"🔹 {code_item} - {quantity} ({collection_short})"
        else:
            line = f"🔹 {code_item} - {quantity}"
        lines.append(line)
        
        model_codes.append(code_item)

    result_text = "\n".join(lines)

    # Inline keyboard: har bir model CODE uchun tugma, 3 tadan qatorda
    inline_buttons = []
    for i, code in enumerate(model_codes):
        if i % 3 == 0:
            # Yangi qator boshlash
            inline_buttons.append([])
        # Tugma matni = model CODE
        inline_buttons[-1].append(
            InlineKeyboardButton(
                text=code,
                callback_data=f"discount_model:{code}"
            )
        )

    # Pastki qatorga: ⬅️ Orqaga, 🏠 Asosiy menyu
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="discount_categories_back"
        ),
        InlineKeyboardButton(
            text="🏠 Asosiy menyu",
            callback_data="discount_home"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)

    # 2-bosqich natijasini YANGI xabar sifatida yuborish
    try:
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=result_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in discount_list_back send_message: {e}")


@router.callback_query(F.data == "discount_categories_back")
async def callback_discount_categories_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle 'Orqaga' from discount results - return to discount categories menu"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()

    user_id = callback_query.from_user.id

    # FSM STATE TOZALASH - kategoriyalar oynasiga qaytishda ham (try/except ichida)
    try:
        await state.clear()
    except Exception:
        pass

    # Skidka bo'limi - barcha userlarga ochiq

    menu_text = (
        "🔥 <b>Skidkaga tushgan modellar</b>\n\n"
        "Quyidagi kategoriyalardan birini tanlang:"
    )

    keyboard = make_discount_menu_keyboard()

    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Edit error in discount_categories_back: {e}")
        # Agar edit ishlamasa (masalan, xabar rasm bo'lsa), delete va send
        try:
            await bot.delete_message(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id
            )
        except Exception:
            pass
        
        try:
            await bot.send_message(
                chat_id=callback_query.message.chat.id,
                text=menu_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e2:
            logger.error(f"Send error in discount_categories_back: {e2}")


@router.callback_query(F.data == "discount_back")
async def callback_discount_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle 'Orqaga' from discount section - return to main menu"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()

    user_id = callback_query.from_user.id

    # FSM STATE TOZALASH (try/except ichida)
    try:
        await state.clear()
    except Exception:
        pass

    # Skidka bo'limi - barcha userlarga ochiq

    chat_id = callback_query.message.chat.id

    from handlers.start import make_main_menu_keyboard
    menu_text = (
        "👋 Assalomu alaykum! Botga xush kelibsiz.\n\n"
        "Quyidagi menyulardan birini tanlang:"
    )

    keyboard = make_main_menu_keyboard(user_id)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        from services.message_utils import store_main_menu_message
        store_main_menu_message(chat_id, callback_query.message.message_id)
    except Exception as e:
        logger.error(f"Edit error in discount_back: {e}")
        # Agar edit ishlamasa, delete va send
        try:
            await bot.delete_message(chat_id, callback_query.message.message_id)
        except Exception:
            pass
        
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            from services.message_utils import store_main_menu_message
            store_main_menu_message(chat_id, sent.message_id)
        except Exception as e2:
            logger.error(f"Send error in discount_back: {e2}")


@router.callback_query(F.data == "discount_home")
async def callback_discount_home(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Handle '🏠 Asosiy menyu' button from discount section.
    Works from:
    1. Category selection screen (make_discount_menu_keyboard)
    2. Model buttons screen (after category selection)
    
    Tries to edit message first, if fails - deletes and sends new main menu.
    """
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    # FSM STATE TOZALASH
    try:
        await state.clear()
    except Exception:
        pass
    
    # Asosiy menyu keyboard va matn
    from handlers.start import make_main_menu_keyboard
    keyboard = make_main_menu_keyboard(user_id)
    menu_text = (
        "Assalomu alaykum! TIZIMGA  xush kelibsiz.\n\n"
        "Quyidagi menyulardan birini tanlang:"
    )
    
    # Avval edit qilishga urinish (afzal usul)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
        # Edit muvaffaqiyatli bo'lsa, asosiy menyu xabarini track qilish
        from services.message_utils import store_main_menu_message
        store_main_menu_message(chat_id, callback_query.message.message_id)
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa (masalan, xabar CONTENT bo'lsa yoki edit error bo'lsa)
        # Eski xabarni delete qilib, yangi asosiy menyuni yubor
        try:
            await bot.delete_message(chat_id, callback_query.message.message_id)
        except Exception:
            pass
        
        # Yangi asosiy menyu xabarini yuborish
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


@router.callback_query(F.data == "prices_menu")
async def callback_prices_menu(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Return to prices menu - STATE YOPILADI"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    # State yopish - "⬅️ Orqaga" bosilganda state yopiladi (try/except ichida)
    try:
        await state.clear()
    except Exception:
        pass
    
    menu_text = (
        "💰 <b>Modellar narxini bilish</b>\n\n"
        "Quyidagi variantlardan birini tanlang:"
    )
    
    keyboard = make_prices_menu_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


# ==================== NATIJA TUGMALARI (CLEANUP) ====================

@router.callback_query(F.data == "prices_collection_back")
async def callback_prices_collection_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle 'Orqaga' from collection price results - FAQAT 1 bosqich orqaga qaytadi (kolleksiya tanlash oynasiga)"""
    # ENG BIRINCHI QATOR: callback.answer() - MAJBURIY
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    chat_id = callback_query.message.chat.id
    
    # State dan qator tanlash ma'lumotlarini tozalash (kolleksiya tanlash oynasiga qaytishda)
    await state.update_data(
        collection_row_select=False,
        collection_key=None,
        collection_name=None
    )
    
    menu_text = (
        "🗂 <b>Kolleksiya bo'yicha</b>\n\n"
        "Quyidagi kolleksiyalardan birini tanlang:"
    )
    
    keyboard = make_collection_main_keyboard()
    
    # FAQAT EDIT orqali - yangi xabar yuborilmaydi
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data == "price_back")
async def callback_price_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle 'Orqaga' button from price results - OLDINGI BO'LIMGA QAYTISH"""
    # ENG BIRINCHI QATOR: callback.answer() - MAJBURIY
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    chat_id = callback_query.message.chat.id
    
    # State dan ma'lumotlarni olish
    state_data = await state.get_data()
    source_section = state_data.get("source_section")
    collection_row_select = state_data.get("collection_row_select", False)
    collection_key = state_data.get("collection_key")
    collection_name = state_data.get("collection_name")
    
    # Debug log
    logger.info(f"[price_back] State data: source_section={source_section}, collection_row_select={collection_row_select}, collection_key={collection_key}, collection_name={collection_name}")
    
    # Agar qator tanlash oynasidan kelgan bo'lsa, qator tanlash oynasiga qaytish
    if collection_row_select and collection_key and collection_name:
        # Qator tanlash oynasiga qaytish
        menu_text = (
            f"📌 <b>{collection_name}</b>\n\n"
            "Quyidagi qatorlardan birini tanlang:"
        )
        keyboard = make_collection_sub_keyboard(collection_key)
        
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=callback_query.message.message_id,
                text=menu_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass
        return
    
    # Agar "prices_general" (Umumiy narxdan bilish) dan kelgan bo'lsa, prices_menu ga qaytish
    if source_section == "prices_general":
        menu_text = (
            "💰 <b>Modellar narxini bilish</b>\n\n"
            "Quyidagi variantlardan birini tanlang:"
        )
        keyboard = make_prices_menu_keyboard()
        
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=callback_query.message.message_id,
                text=menu_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass
        return
    
    # Agar qator tanlash oynasi emas bo'lsa, kolleksiya tanlash oynasiga qaytish
    # State ni tozalash emas, faqat oynani o'zgartirish
    menu_text = (
        "🗂 <b>Kolleksiya bo'yicha</b>\n\n"
        "Quyidagi kolleksiyalardan birini tanlang:"
    )
    
    keyboard = make_collection_main_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        pass


# callback_price_home funksiyasi O'CHIRILDI - endi start.py dagi umumiy menu_main handler ishlatiladi


# ==================== UMUMIY NARXDAN BILISH ====================

@router.callback_query(F.data == "prices_general")
async def callback_prices_general(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle general price search"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    menu_text = "Mahsulot kodini yuboring"
    
    # State ga bot message_id va source_section ni saqlash
    await state.update_data(
        bot_message_id=callback_query.message.message_id,
        source_section="prices_general"
    )
    
    # Inline Keyboard yaratish (faqat "⬅️ Orqaga" tugmasi)
    keyboard = make_code_input_keyboard()
    
    try:
        # FAQAT edit_message orqali - send_message ISHLATILMASIN
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await state.set_state(ModelPricesStates.waiting_for_code)


@router.message(ModelPricesStates.waiting_for_code)
async def handle_code_input(message: Message, state: FSMContext, bot: Bot):
    """Handle product code input"""
    user_id = message.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        await message.answer("⛔ Sizda API huquqi yo'q")
        return
    
    # Foydalanuvchi xabarini o'chirish
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass
    
    # Kodni normalizatsiya qilish
    user_code = message.text.strip()
    
    # Qidiruv uchun kengaytirilgan normalizatsiya funksiyasi
    def normalize_for_search(code_str):
        """Qidiruv uchun normalizatsiya: M, m, -, _, bo'shliq olib tashlanadi, faqat harf+raqam qoladi"""
        if not code_str:
            return ""
        # Upper, strip, bo'sh joylar olib tashlash
        normalized = str(code_str).strip().upper()
        # Barcha bo'sh joylar olib tashlash
        normalized = re.sub(r'\s+', '', normalized)
        # M, m, -, _, bo'shliq olib tashlash (lekin boshqa harflar qoladi)
        # Faqat harf+raqam qoldirish (normalize_code kabi)
        normalized = re.sub(r'[^A-Z0-9]', '', normalized)
        return normalized
    
    normalized_code = normalize_for_search(user_code)
    
    if not normalized_code:
        # Xato xabari matni (talab qilingan format)
        error_text = (
            f"❌ Bunday kod topilmadi: {user_code}\n\n"
            "Sabablar:\n"
            "— Kod noto'g'ri yozilgan bo'lishi mumkin\n"
            "— Yoki bu kod bazada mavjud emas\n\n"
            "Davom etish uchun:\n"
            "👉 To'g'ri kodni qayta yuboring — bot ishlashda davom etadi."
        )
        keyboard = make_back_keyboard()
        
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        source_section = state_data.get("source_section")
        
        # Faqat "prices_general" bo'limida mavjud xabarni EDIT qilish
        if source_section == "prices_general" and bot_message_id:
            try:
                # Mavjud xabarni EDIT qilish
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
            except Exception:
                pass
        else:
            # Agar bot_message_id yo'q bo'lsa yoki boshqa bo'lim bo'lsa, eski logika
            error_message_id = state_data.get("error_message_id")
            try:
                if error_message_id:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=error_message_id,
                        text=error_text,
                        reply_markup=keyboard
                    )
                else:
                    sent_message = await bot.send_message(
                        chat_id=message.chat.id,
                        text=error_text,
                        reply_markup=keyboard
                    )
                    await state.update_data(error_message_id=sent_message.message_id)
            except Exception:
                pass
        
        # State ochiq qoladi - foydalanuvchi yana kod yubora oladi
        await state.set_state(ModelPricesStates.waiting_for_code)
        return
    
    # sheets3 dan qidirish
    sheet_service = GoogleSheetService()
    prices = await sheet_service.read_prices_from_sheets3()

    # Kod bo'yicha qidirish - FUZZY MATCHING (1340-1 va 1340-01 bir xil)
    matched_row = None
    
    # Generate fuzzy variants for user code
    user_code_variants = generate_fuzzy_code_variants(normalized_code)
    
    for price in prices:
        sheet_code = price.get("code", "")
        # Sheet kodini ham qidiruv uchun normalizatsiya qilish
        sheet_code_normalized = normalize_for_search(sheet_code)
        
        if not sheet_code_normalized:
            continue
        
        # Generate fuzzy variants for sheet code
        sheet_code_variants = generate_fuzzy_code_variants(sheet_code_normalized)
        
        # Fuzzy matching: 5 ta usul
        is_match = False
        
        # 1. Exact match
        if normalized_code == sheet_code_normalized:
            is_match = True
        # 2. Startswith match
        elif sheet_code_normalized.startswith(normalized_code) or normalized_code.startswith(sheet_code_normalized):
            is_match = True
        # 3. Contains match
        elif normalized_code in sheet_code_normalized or sheet_code_normalized in normalized_code:
            is_match = True
        # 4. Fuzzy variants match (13401 <-> 134001)
        elif (sheet_code_normalized in user_code_variants or 
              normalized_code in sheet_code_variants or
              any(v in sheet_code_variants for v in user_code_variants)):
            is_match = True
        
        if is_match:
            matched_row = price
            break  # Birinchi topilgan qatorni olish va to'xtash
    
    state_data = await state.get_data()
    bot_message_id = state_data.get("bot_message_id")

    # Narx tarixini yozish (try/except ichida)
    try:
        if not matched_row:
            # Narx topilmadi
            record_price_history(
                user_id=user_id,
                product_code=user_code,
                found=False,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
        else:
            # Narx topildi - birinchi narxni olish (asosiy_price yoki mini_price yoki kasetniy_price)
            asosiy_price = matched_row.get("asosiy_price", "").strip()
            mini_price = matched_row.get("mini_price", "").strip()
            kasetniy_price = matched_row.get("kasetniy_price", "").strip()
            
            # Birinchi mavjud narxni olish
            price_value = ""
            if asosiy_price:
                price_value = asosiy_price
            elif mini_price:
                price_value = mini_price
            elif kasetniy_price:
                price_value = kasetniy_price
            
            # Narx matnini formatlash (faqat raqam, $ belgisi o'chiriladi)
            price_text = price_value.replace("$", "").strip() if price_value else ""
            
            record_price_history(
                user_id=user_id,
                product_code=user_code,
                found=True,
                price_text=price_text if price_text else None,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )
    except Exception:
        pass

    if not matched_row:
        # Xato xabari matni (talab qilingan format)
        error_text = (
            f"❌ Bunday kod topilmadi: {user_code}\n\n"
            "Sabablar:\n"
            "— Kod noto'g'ri yozilgan bo'lishi mumkin\n"
            "— Yoki bu kod bazada mavjud emas\n\n"
            "Davom etish uchun:\n"
            "👉 To'g'ri kodni qayta yuboring — bot ishlashda davom etadi."
        )
        keyboard = make_back_keyboard()
        
        state_data = await state.get_data()
        bot_message_id = state_data.get("bot_message_id")
        source_section = state_data.get("source_section")
        
        # Faqat "prices_general" bo'limida mavjud xabarni EDIT qilish
        if source_section == "prices_general" and bot_message_id:
            try:
                # Mavjud xabarni EDIT qilish
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=error_text,
                    reply_markup=keyboard
                )
            except Exception:
                pass
        else:
            # Agar bot_message_id yo'q bo'lsa yoki boshqa bo'lim bo'lsa, eski logika
            error_message_id = state_data.get("error_message_id")
            try:
                if error_message_id:
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=error_message_id,
                        text=error_text,
                        reply_markup=keyboard
                    )
                else:
                    sent_message = await bot.send_message(
                        chat_id=message.chat.id,
                        text=error_text,
                        reply_markup=keyboard
                    )
                    await state.update_data(error_message_id=sent_message.message_id)
            except Exception:
                pass
        
        # State ochiq qoladi - foydalanuvchi yana kod yubora oladi
        await state.set_state(ModelPricesStates.waiting_for_code)
        return
    
    keyboard = make_back_keyboard()
    
    # PREMIUM DIZAYN - FAQAT BITTA QATOR
    # Qiymatlarni olish
    code = matched_row.get("code", "").strip()
    model_name = matched_row.get("model_name", "").strip()
    collection = matched_row.get("collection", "").strip()
    asosiy_price = matched_row.get("asosiy_price", "").strip()
    mini_price = matched_row.get("mini_price", "").strip()
    kasetniy_price = matched_row.get("kasetniy_price", "").strip()
    asosiy_qimmat = matched_row.get("asosiy_qimmat", "").strip()
    mini_qimmat = matched_row.get("mini_qimmat", "").strip()
    kasetniy_qimmat = matched_row.get("kasetniy_qimmat", "").strip()
    izoh = matched_row.get("izoh", "").strip()
    
    # Narxlarni formatlash - faqat raqamni olish
    def get_price_value(price_str):
        """Narxni olish - faqat raqam"""
        if not price_str or price_str.strip() == "" or price_str.lower() == "yo'q":
            return None
        price_clean = price_str.strip().replace("$", "").strip()
        if not price_clean:
            return None
        return price_clean
    
    # Narxlarni olish
    asosiy_arzon = get_price_value(asosiy_price)
    asosiy_qimmat_val = get_price_value(asosiy_qimmat)
    mini_arzon = get_price_value(mini_price)
    mini_qimmat_val = get_price_value(mini_qimmat)
    kasetniy_arzon = get_price_value(kasetniy_price)
    kasetniy_qimmat_val = get_price_value(kasetniy_qimmat)
    
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
    
    # Asosiy bo'limi mavjudligini tekshirish
    has_asosiy = asosiy_arzon or asosiy_qimmat_val
    # Mini bo'limi mavjudligini tekshirish
    has_mini = mini_arzon or mini_qimmat_val
    # Kasetniy bo'limi mavjudligini tekshirish
    has_kasetniy = kasetniy_arzon or kasetniy_qimmat_val
    
    # Yangi format yaratish
    result_text = f"🔹 Model nomi: {model_name}\n\n"
    result_text += f"🔸 Model kodi: {code}\n"
    
    # Maxsus modellar uchun kolleksiya ko'rsatilmaydi (faqat Дикий uchun)
    if not is_dikey:
        result_text += f"📂 Kolleksiya: {collection}\n\n"
    else:
        result_text += "\n"
    
    result_text += "Narxlar:\n"
    
    # ROLLO SHTOR uchun maxsus format
    if is_rollo_shtor:
        if has_asosiy:
            asosiy_display = ""
            if asosiy_arzon and asosiy_qimmat_val:
                asosiy_display = f"{asosiy_arzon} $ ({asosiy_qimmat_val} $)"
            elif asosiy_arzon:
                asosiy_display = f"{asosiy_arzon} $"
            elif asosiy_qimmat_val:
                asosiy_display = f"({asosiy_qimmat_val} $)"
            if asosiy_display:
                result_text += f"• 50%lik: {asosiy_display}\n"
        
        if has_mini:
            mini_display = ""
            if mini_arzon and mini_qimmat_val:
                mini_display = f"{mini_arzon} $ ({mini_qimmat_val} $)"
            elif mini_arzon:
                mini_display = f"{mini_arzon} $"
            elif mini_qimmat_val:
                mini_display = f"({mini_qimmat_val} $)"
            if mini_display:
                result_text += f"• 100%lik: {mini_display}\n"
    
    # Дикий uchun maxsus format
    elif is_dikey:
        if has_asosiy:
            asosiy_display = ""
            if asosiy_arzon and asosiy_qimmat_val:
                asosiy_display = f"{asosiy_arzon} $ ({asosiy_qimmat_val} $)"
            elif asosiy_arzon:
                asosiy_display = f"{asosiy_arzon} $"
            elif asosiy_qimmat_val:
                asosiy_display = f"({asosiy_qimmat_val} $)"
            if asosiy_display:
                result_text += f"• To'ldi uzi bo'lsa: {asosiy_display}\n"
        
        if has_mini:
            mini_display = ""
            if mini_arzon and mini_qimmat_val:
                mini_display = f"{mini_arzon} $ ({mini_qimmat_val} $)"
            elif mini_arzon:
                mini_display = f"{mini_arzon} $"
            elif mini_qimmat_val:
                mini_display = f"({mini_qimmat_val} $)"
            if mini_display:
                result_text += f"• Yoniga porter bo'lsa: {mini_display}\n"
    
    # PLISE uchun maxsus format
    elif is_plise:
        if has_asosiy:
            asosiy_display = ""
            if asosiy_arzon and asosiy_qimmat_val:
                asosiy_display = f"{asosiy_arzon} $ ({asosiy_qimmat_val} $)"
            elif asosiy_arzon:
                asosiy_display = f"{asosiy_arzon} $"
            elif asosiy_qimmat_val:
                asosiy_display = f"({asosiy_qimmat_val} $)"
            if asosiy_display:
                result_text += f"• 0,50 kv: {asosiy_display}\n"
        
        if has_mini:
            mini_display = ""
            if mini_arzon and mini_qimmat_val:
                mini_display = f"{mini_arzon} $ ({mini_qimmat_val} $)"
            elif mini_arzon:
                mini_display = f"{mini_arzon} $"
            elif mini_qimmat_val:
                mini_display = f"({mini_qimmat_val} $)"
            if mini_display:
                result_text += f"• 1,00 kv: {mini_display}\n"
    
    # Boshqa modellar uchun standart format
    else:
        # Asosiy bo'limi
        if has_asosiy:
            result_text += "• Asosiy:\n"
            if asosiy_arzon:
                result_text += f"  - Oddiy: {asosiy_arzon} $\n"
            if asosiy_qimmat_val:
                result_text += f"  - Qimmat: {asosiy_qimmat_val} $\n"
        
        # Mini bo'limi
        if has_mini:
            result_text += "• Mini:\n"
            if mini_arzon:
                result_text += f"  - Oddiy: {mini_arzon} $\n"
            if mini_qimmat_val:
                result_text += f"  - Qimmat: {mini_qimmat_val} $\n"
        
        # Kasetniy bo'limi
        if has_kasetniy:
            result_text += "• Kasetniy:\n"
            if kasetniy_arzon:
                result_text += f"  - Oddiy: {kasetniy_arzon} $\n"
            if kasetniy_qimmat_val:
                result_text += f"  - Qimmat: {kasetniy_qimmat_val} $\n"
    
    # Izoh - faqat mavjud bo'lsa
    if izoh and izoh.lower() != "yo'q" and izoh.strip():
        result_text += f"\nIzoh:\n{izoh.strip()}"
    
    # Agar mahsulot topilsa, oldingi xato xabarini o'chirish
    state_data = await state.get_data()
    error_message_id = state_data.get("error_message_id")
    
    if error_message_id:
        try:
            await bot.delete_message(
                chat_id=message.chat.id,
                message_id=error_message_id
            )
            # Xato xabarining message_id ni state dan olib tashlash
            await state.update_data(error_message_id=None)
        except Exception:
            pass
    
    # Natijani EDIT qilish (yangi xabar emas)
    result_message_id = state_data.get("result_message_id")
    
    try:
        if result_message_id:
            # Agar natija xabari mavjud bo'lsa, uni edit qilish
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=result_message_id,
                text=result_text,
                reply_markup=keyboard
            )
        else:
            # Agar natija xabari yo'q bo'lsa, "Mahsulot kodini yuboring" xabarini edit qilish
            if bot_message_id:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=bot_message_id,
                    text=result_text,
                    reply_markup=keyboard
                )
                # Natija xabarining message_id ni state ga saqlash
                await state.update_data(result_message_id=bot_message_id)
            else:
                # Agar bot_message_id ham yo'q bo'lsa, yangi xabar yuborish (faqat birinchi marta)
                sent_message = await bot.send_message(
                    chat_id=message.chat.id,
                    text=result_text,
                    reply_markup=keyboard
                )
                await state.update_data(result_message_id=sent_message.message_id)
    except Exception:
        # Agar edit qilishda xatolik bo'lsa, yangi xabar yuborish
        try:
            sent_message = await bot.send_message(
                chat_id=message.chat.id,
                text=result_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await state.update_data(result_message_id=sent_message.message_id)
        except Exception:
            pass
    
    # State ochiq qoladi - foydalanuvchi ketma-ket cheksiz kod yubora oladi
    # State faqat "Orqaga" yoki "Asosiy menyu" tugmalarida yopiladi
    # State ni qayta set qilish - foydalanuvchi yana kod yubora olishi uchun
    await state.set_state(ModelPricesStates.waiting_for_code)


# ==================== KOLLEKSIYA BO'YICHA BILISH ====================

@router.callback_query(F.data == "prices_collection")
async def callback_prices_collection(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle collection-based price search"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    # State dan qator tanlash ma'lumotlarini tozalash (kolleksiya tanlash oynasiga qaytishda)
    await state.update_data(
        collection_row_select=False,
        collection_key=None,
        collection_name=None
    )
    
    menu_text = (
        "🗂 <b>Kolleksiya bo'yicha</b>\n\n"
        "Quyidagi kolleksiyalardan birini tanlang:"
    )
    
    keyboard = make_collection_main_keyboard()
    
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


@router.callback_query(F.data.startswith("prices_collection_main:"))
async def callback_collection_main_selected(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle main collection selection"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    collection_key = callback_query.data.split(":")[1]
    collection_name = COLLECTION_MAIN.get(collection_key, collection_key)
    
    # Rollo kitay uchun sheets3 dan barcha variantlarni olish
    if collection_key == "rollo_kitay":
        await show_rollo_kitay_variants(callback_query, bot, collection_name, state)
        return
    
    # Kombo turk uchun bevosita natija ko'rsatish (ichki tugmalar yo'q)
    # model_name bo'yicha qidirish (collection emas)
    if collection_key == "kombo_turk":
        await show_collection_prices(callback_query, bot, collection_name, state, search_by_model_name=True)
        return
    
    # Agar ichki qatorlar bo'lsa, ularni ko'rsatish
    if collection_key in COLLECTION_SUB:
        # State ga qator tanlash oynasi ma'lumotlarini saqlash
        await state.update_data(
            collection_row_select=True,
            collection_key=collection_key,
            collection_name=collection_name
        )
        
        menu_text = (
            f"📌 <b>{collection_name}</b>\n\n"
            "Quyidagi qatorlardan birini tanlang:"
        )
        keyboard = make_collection_sub_keyboard(collection_key)
        
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=menu_text,
                reply_markup=keyboard
            )
        except Exception:
            pass
    else:
        # Agar ichki qatorlar bo'lmasa, to'g'ridan-to'g'ri natija ko'rsatish
        # Xitoy kombo emas bo'lsa, model_name bo'yicha qidirish
        if collection_key == "xitoy_kombo":
            # Xitoy kombo uchun collection bo'yicha qidirish (o'zgartirilmaydi)
            await show_collection_prices(callback_query, bot, collection_name, state, search_by_model_name=False)
        else:
            # Qolgan barcha tugmalar uchun model_name bo'yicha qidirish
            await show_collection_prices(callback_query, bot, collection_name, state, search_by_model_name=True)


async def show_rollo_kitay_variants(callback_query: CallbackQuery, bot: Bot, collection_name: str, state: FSMContext = None):
    """Show all Rollo kitay variants from sheets3"""
    user_id = callback_query.from_user.id
    
    # sheets3 dan ma'lumot olish
    sheet_service = GoogleSheetService()
    prices = await sheet_service.read_prices_from_sheets3()
    
    # Rollo kitay kolleksiyasini topish
    collection_normalized = normalize_collection(collection_name)
    matched = []
    
    for price in prices:
        price_collection = price.get("collection", "")
        if normalize_collection(price_collection) == collection_normalized or \
           normalize_collection(price_collection).startswith("ROLLOKITAY"):
            matched.append(price)
    
    if not matched:
        # Agar variantlar topilmasa, to'g'ridan-to'g'ri barcha modellarni ko'rsatish
        # Rollo kitay uchun model_name bo'yicha qidirish
        await show_collection_prices(callback_query, bot, collection_name, state, search_by_model_name=True)
        return
    
    # Barcha unique collection nomlarini olish (sheets3 dagi barcha rollo kitay variantlari)
    variants = set()
    for price in matched:
        price_collection = price.get("collection", "")
        if price_collection:
            variants.add(price_collection)
    
    # Agar variantlar bo'lmasa yoki faqat bitta variant bo'lsa, to'g'ridan-to'g'ri natija ko'rsatish
    # Rollo kitay uchun model_name bo'yicha qidirish
    if not variants or len(variants) == 1:
        await show_collection_prices(callback_query, bot, collection_name, state, search_by_model_name=True)
        return
    
    # Variantlarni ko'rsatish
    menu_text = (
        f"📌 <b>{collection_name}</b>\n\n"
        "Quyidagi variantlardan birini tanlang:"
    )
    
    buttons = []
    for variant in sorted(variants):
        buttons.append([
            InlineKeyboardButton(
                text=variant,
                callback_data=f"prices_rollo_kitay_variant:{variant}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="prices_collection"
        ),
        InlineKeyboardButton(
            text="🏠 Asosiy menyu",
            callback_data="menu_main"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("prices_rollo_kitay_variant:"))
async def callback_rollo_kitay_variant(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle Rollo kitay variant selection"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    variant_name = callback_query.data.split(":", 1)[1]
    
    # Rollo kitay variantlari uchun ham model_name bo'yicha qidirish
    await show_collection_prices(callback_query, bot, variant_name, state, search_by_model_name=True)


@router.callback_query(F.data.startswith("prices_collection_sub:"))
async def callback_collection_sub_selected(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle sub-collection selection"""
    # ENG BIRINCHI QATOR: callback.answer()
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Narx bo'limi: main_admin YOKI helper_admin YOKI price_access
    # 1. Avval admin tekshiriladi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    # 2. Keyin price_access tekshiriladi
    if not (is_any_admin_user or has_api_access(user_id)):
        return
    
    parts = callback_query.data.split(":")
    if len(parts) < 3:
        return
    
    collection_key = parts[1]
    sub_name = parts[2]
    collection_name = COLLECTION_MAIN.get(collection_key, collection_key)
    
    # To'liq kolleksiya nomi
    full_collection_name = f"{collection_name} - {sub_name}"
    
    # State ga natija ko'rsatish ma'lumotlarini saqlash (qator tanlash oynasiga qaytish uchun)
    await state.update_data(
        collection_row_select=True,
        collection_key=collection_key,
        collection_name=collection_name
    )
    
    # Debug log
    logger.info(f"[prices_collection_sub] Saved state: collection_key={collection_key}, collection_name={collection_name}")
    
    await show_collection_prices(callback_query, bot, full_collection_name, state)


async def show_collection_prices(callback_query: CallbackQuery, bot: Bot, collection_name: str, state: FSMContext = None, search_by_model_name: bool = False):
    """Show all models for a collection - NATIJA EDIT QILINADI
    
    Args:
        search_by_model_name: True bo'lsa, model_name ustunidan qidiriladi (case-insensitive partial match)
                             False bo'lsa, collection ustunidan qidiriladi (to'liq tenglik)
    """
    user_id = callback_query.from_user.id
    
    # sheets3 dan ma'lumot olish - BARCHA qatorlar 1 marta yuklanadi
    sheet_service = GoogleSheetService()
    prices = await sheet_service.read_prices_from_sheets3()
    
    # Debug: yuklangan qatorlar soni
    logger.info(f"[prices_collection] Loaded {len(prices)} price records from sheets3")
    
    matched = []
    
    # FAQAT "ROLLO SHTOR", "Дикий", "PLISE" tugmalari uchun fuzzy qidiruv
    # Bu tugmalar "Madel nomi" ustunidan qidiriladi (kolleksiya emas)
    is_fuzzy_search = (collection_name == "ROLLO SHTOR" or collection_name == "Дикий" or collection_name == "Plise" or collection_name == "PLISE")
    
    if search_by_model_name:
        if is_fuzzy_search:
            # Fuzzy qidiruv - Cyrillic/Latin insensitive, case insensitive, punctuation insensitive
            logger.info(f"[prices_collection] Fuzzy search for '{collection_name}' in model_name column")
            
            # Debug: search term ni normalize qilish
            search_normalized = fuzzy_normalize_model_name(collection_name)
            logger.info(f"[prices_collection] Search term '{collection_name}' normalized to: '{search_normalized}'")
            
            # Debug: bir nechta model nomlarini ko'rsatish
            sample_count = 0
            for price in prices:
                price_model_name = price.get("model_name", "")  # Model nomi ustuni (Madel nomi)
                if price_model_name and sample_count < 5:
                    model_normalized = fuzzy_normalize_model_name(price_model_name)
                    logger.info(f"[prices_collection] Sample model_name: '{price_model_name}' -> normalized: '{model_normalized}'")
                    sample_count += 1
            
            # Debug: "Дикий" so'zini o'z ichiga olgan barcha model nomlarini topish
            if collection_name == "Дикий":
                dikey_candidates = []
                for price in prices:
                    price_model_name = price.get("model_name", "")
                    if price_model_name and ("дик" in price_model_name.lower() or "dike" in price_model_name.lower()):
                        model_normalized = fuzzy_normalize_model_name(price_model_name)
                        dikey_candidates.append(f"'{price_model_name}' -> '{model_normalized}'")
                if dikey_candidates:
                    logger.info(f"[prices_collection] Found {len(dikey_candidates)} potential Дикий matches: {', '.join(dikey_candidates[:10])}")
            
            for price in prices:
                price_model_name = price.get("model_name", "")  # Model nomi ustuni (Madel nomi)
                if not price_model_name:
                    continue
                
                # Fuzzy match: Cyrillic/Latin insensitive, case insensitive, punctuation insensitive
                if fuzzy_match_model_name(collection_name, price_model_name):
                    matched.append(price)
                    # Debug: birinchi topilgan natijani ko'rsatish
                    if len(matched) == 1:
                        model_normalized = fuzzy_normalize_model_name(price_model_name)
                        logger.info(f"[prices_collection] First match found: '{price_model_name}' -> normalized: '{model_normalized}'")
        else:
            # Boshqa modellar uchun oddiy qidirish - case-insensitive partial match (contains)
            # User tanlagan model nomini normalizatsiya qilish
            user_model_normalized = normalize_collection(collection_name)
            logger.info(f"[prices_collection] Searching by model_name: '{collection_name}' (normalized: '{user_model_normalized}')")
            
            for price in prices:
                price_model_name = price.get("model_name", "")  # Model nomi ustuni
                if not price_model_name:
                    continue
                
                price_model_normalized = normalize_collection(price_model_name)
                
                # Partial match: user_model_normalized price_model_normalized ichida yoki aksincha
                if user_model_normalized in price_model_normalized or price_model_normalized in user_model_normalized:
                    matched.append(price)
    else:
        # Collection bo'yicha qidirish - FAQAT to'liq tenglik (Xitoy kombo uchun)
        # User tanlagan kolleksiyani normalizatsiya qilish
        # Agar collection_name ichida " - " bo'lsa (masalan: "Xitoy kombo - 0-start")
        # Variant nomini ajratib olish va faqat variant nomini normalizatsiya qilish
        if " - " in collection_name:
            main_collection, variant = collection_name.split(" - ", 1)
            user_collection_normalized = normalize_collection(variant)
            logger.info(f"[prices_collection] Collection name contains ' - ', using variant: '{variant}' (normalized: '{user_collection_normalized}')")
        else:
            user_collection_normalized = normalize_collection(collection_name)
            logger.info(f"[prices_collection] Searching for collection: '{collection_name}' (normalized: '{user_collection_normalized}')")
        
        # Kolleksiya bo'yicha filtrlash - FAQAT to'liq tenglik
        for price in prices:
            price_collection = price.get("collection", "")  # B ustun
            if not price_collection:
                continue
            
            price_collection_normalized = normalize_collection(price_collection)
            
            # FAQAT to'liq tenglik: normalize(row.collection) == normalize(user_collection)
            if price_collection_normalized == user_collection_normalized:
                matched.append(price)
    
    # Debug log: filtrlash natijalari soni
    logger.info(f"[prices_collection] Collection filter matched rows: {len(matched)}")
    
    # Agar filter natijasi 0 bo'lsa
    if not matched:
        error_text = "Bu kolleksiya bo'yicha narxlar topilmadi"
        keyboard = make_collection_result_back_keyboard()
        
        try:
            # Natijani edit qilish
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=error_text,
                reply_markup=keyboard
            )
            # result_message_id ni state ga saqlash
            if state:
                await state.update_data(result_message_id=callback_query.message.message_id)
        except Exception as e:
            logger.error(f"[prices_collection] Error sending 'not found' message: {e}")
            # Agar edit qilishda xatolik bo'lsa, yangi xabar yuborish
            try:
                sent_message = await bot.send_message(
                    chat_id=callback_query.message.chat.id,
                    text=error_text,
                    reply_markup=keyboard
                )
                if state:
                    await state.update_data(result_message_id=sent_message.message_id)
            except Exception as e2:
                logger.error(f"[prices_collection] Error sending 'not found' message (fallback): {e2}")
        return
    
    # Natija formatlash - JADVAL FORMATI
    # Narxlarni formatlash funksiyasi
    def get_price_value(price_str):
        """Narxni olish - faqat raqam"""
        if not price_str or price_str.strip() == "" or price_str.lower() == "yo'q":
            return None
        price_clean = price_str.strip().replace("$", "").strip()
        if not price_clean:
            return None
        return price_clean
    
    # Jadval sarlavhasi
    result_lines = []
    
    # Kolleksiya nomi - ENG BOSHIDA
    result_lines.append(f"📁 Kolleksiya: {collection_name}")
    result_lines.append("")
    
    # Maxsus modellar uchun sarlavhani aniqlash (birinchi model nomidan)
    special_header = None
    show_kasetniy = True
    if matched:
        first_model_name = matched[0].get("model_name", "").strip()
        special_header, show_kasetniy = get_special_model_display_labels(first_model_name)
    
    # Jadval sarlavhasi
    if special_header:
        result_lines.append(special_header)
        # Separator chizig'i - sarlavha uzunligiga mos
        separator_length = max(len(special_header), 40)
        result_lines.append("-" * separator_length)
    else:
        result_lines.append("Kod | Asosiy | Mini | Kasetniy")
        result_lines.append("--------------------------------")
    
    # Har bir model uchun jadval qatori
    izoh_text = None
    for price in matched:
        code = price.get("code", "").strip()  # A ustun - model kodi
        asosiy_price = price.get("asosiy_price", "").strip()  # D ustun - Asosiy
        mini_price = price.get("mini_price", "").strip()  # E ustun - Mini
        kasetniy_price = price.get("kasetniy_price", "").strip()  # F ustun - kasetniy
        asosiy_qimmat = price.get("asosiy_qimmat", "").strip()  # H ustun - Asosiy qimmat
        mini_qimmat = price.get("mini_qimmat", "").strip()  # I ustun - Mini qimmat
        kasetniy_qimmat = price.get("kasetniy_qimmat", "").strip()  # J ustun - Kasetniy qimmat
        izoh = price.get("izoh", "").strip()  # Izoh ustuni
        
        # Izoh ni saqlash (birinchisini olish)
        if izoh and izoh.lower() != "yo'q" and izoh.strip() and not izoh_text:
            izoh_text = izoh.strip()
        
        # Narxlarni olish
        asosiy_arzon = get_price_value(asosiy_price)
        asosiy_qimmat_val = get_price_value(asosiy_qimmat)
        mini_arzon = get_price_value(mini_price)
        mini_qimmat_val = get_price_value(mini_qimmat)
        kasetniy_arzon = get_price_value(kasetniy_price)
        kasetniy_qimmat_val = get_price_value(kasetniy_qimmat)
        
        # Asosiy ustun - oddiy_narx (qimmat_narx)
        asosiy_str = ""
        if asosiy_arzon and asosiy_qimmat_val:
            asosiy_str = f"{asosiy_arzon} ({asosiy_qimmat_val})"
        elif asosiy_arzon:
            asosiy_str = asosiy_arzon
        elif asosiy_qimmat_val:
            asosiy_str = f"({asosiy_qimmat_val})"
        
        # Mini ustun - oddiy_narx (qimmat_narx)
        mini_str = ""
        if mini_arzon and mini_qimmat_val:
            mini_str = f"{mini_arzon} ({mini_qimmat_val})"
        elif mini_arzon:
            mini_str = mini_arzon
        elif mini_qimmat_val:
            mini_str = f"({mini_qimmat_val})"
        
        # Kasetniy ustun - oddiy_narx (qimmat_narx)
        kasetniy_str = ""
        if kasetniy_arzon and kasetniy_qimmat_val:
            kasetniy_str = f"{kasetniy_arzon} ({kasetniy_qimmat_val})"
        elif kasetniy_arzon:
            kasetniy_str = kasetniy_arzon
        elif kasetniy_qimmat_val:
            kasetniy_str = f"({kasetniy_qimmat_val})"
        
        # Jadval qatori
        code_padded = code[:7].ljust(7)
        if show_kasetniy:
            result_lines.append(f"{code_padded} | {asosiy_str} | {mini_str} | {kasetniy_str}")
        else:
            # Kasetniy ko'rsatilmaydi
            result_lines.append(f"{code_padded} | {asosiy_str} | {mini_str}")
    
    # Jadval matnini yaratish
    result_text = "\n".join(result_lines)
    
    # Izoh - ENG OXIRIDA (faqat mavjud bo'lsa)
    if izoh_text:
        result_text += f"\n\nIzoh: {izoh_text}"
    
    # Kolleksiya natijalari uchun alohida keyboard - FAQAT 1 bosqich orqaga qaytadi
    keyboard = make_collection_result_back_keyboard()
    
    # USERGA XABAR YUBORISH - ALBATTA
    try:
        # NATIJA EDIT QILINADI - yangi xabar emas
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result_text,
            reply_markup=keyboard
        )
        # result_message_id ni state ga saqlash (collection_row_select, collection_key, collection_name o'zgartirmasdan)
        if state:
            # State dan mavjud ma'lumotlarni olish va saqlash
            current_state = await state.get_data()
            await state.update_data(
                result_message_id=callback_query.message.message_id,
                collection_row_select=current_state.get("collection_row_select", False),
                collection_key=current_state.get("collection_key"),
                collection_name=current_state.get("collection_name")
            )
        logger.info(f"[prices_collection] Successfully sent {len(matched)} results to user")
    except Exception as e:
        logger.error(f"[prices_collection] Error editing message: {e}")
        # Agar edit qilishda xatolik bo'lsa, yangi xabar yuborish
        try:
            sent_message = await bot.send_message(
                chat_id=callback_query.message.chat.id,
                text=result_text,
                reply_markup=keyboard
            )
            if state:
                # State dan mavjud ma'lumotlarni olish va saqlash
                current_state = await state.get_data()
                await state.update_data(
                    result_message_id=sent_message.message_id,
                    collection_row_select=current_state.get("collection_row_select", False),
                    collection_key=current_state.get("collection_key"),
                    collection_name=current_state.get("collection_name")
                )
            logger.info(f"[prices_collection] Successfully sent {len(matched)} results to user (fallback)")
        except Exception as e2:
            logger.error(f"[prices_collection] Error sending message (fallback): {e2}")


# ==================== TAYYOR RAZMERLAR (SHEETS5) ====================

# Foydalanuvchi uchun oxirgi natijalar ro'yxati konteksti
READY_SIZES_CONTEXT: Dict[int, Dict[str, Any]] = {}


def normalize_text_for_search(text: str) -> str:
    """
    Matnni qidiruv uchun normalizatsiya qilish.
    - Katta-kichik harfga bog'liq bo'lmasin
    - Ortiqcha bo'sh joylarni olib tashlash
    - Til farqi muammo qilmasin
    """
    if not text:
        return ""
    
    # Kichik harfga o'tkazish
    normalized = str(text).strip().lower()
    
    # Ortiqcha bo'sh joylarni bitta bo'sh joyga almashtirish
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized


def get_value_ci(record: Dict[str, Any], *field_names: str, default: str = "") -> str:
    """
    Dictionary ichidan ustunni case-insensitive tarzda olish.
    Masalan: 'Magazin', 'MAGAZIN', 'magazin' — hammasi bir xil hisoblanadi.
    """
    if not isinstance(record, dict):
        return default

    lowered = {str(k).strip().lower(): v for k, v in record.items()}

    for name in field_names:
        key = str(name).strip().lower()
        if key in lowered:
            value = lowered[key]
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                return value_str

    return default


def make_store_selection_keyboard() -> InlineKeyboardMarkup:
    """Magazin tanlash keyboard"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Gloria",
                    callback_data="ready_sizes_store:Gloria"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Farhod bozor 151",
                    callback_data="ready_sizes_store:Farhod bozor 151"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Farhod bozor 121",
                    callback_data="ready_sizes_store:Farhod bozor 121"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Asosiy menyu",
                    callback_data="menu_main"
                )
            ]
        ]
    )


def make_product_type_keyboard(store: str) -> InlineKeyboardMarkup:
    """Mahsulot turi tanlash keyboard"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Xitoy kombo",
                    callback_data=f"ready_sizes_product:{store}:Xitoy kombo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Turk kombo",
                    callback_data=f"ready_sizes_product:{store}:Turk kombo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Plise",
                    callback_data=f"ready_sizes_product:{store}:Plise"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Rollo shtor",
                    callback_data=f"ready_sizes_product:{store}:Rollo shtor"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Dikey Rollo",
                    callback_data=f"ready_sizes_product:{store}:Dikey Rollo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_sizes_menu"
                )
            ]
        ]
    )


def make_ready_sizes_result_keyboard(models: list) -> InlineKeyboardMarkup:
    """1-natija: har bir CODE uchun alohida tugma + umumiy Orqaga"""
    buttons = []

    # Har bir model uchun CODE tugmasi (2 tadan yonma-yon)
    row: list[InlineKeyboardButton] = []
    for model in models:
        code = (model.get("code") or "").strip()
        if not code:
            continue

        button = InlineKeyboardButton(
            text=f"👉 {code}",
            callback_data=f"code:{code}"
        )

        row.append(button)

        # 2 tadan keyin yangi qatordan boshlaymiz
        if len(row) == 2:
            buttons.append(row)
            row = []

    # Agar oxirgi qator to'lib qolmagan bo'lsa ham, uni qo'shamiz
    if row:
        buttons.append(row)

    # Pastiga har doim Orqaga tugmasi
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="back:list"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def make_ready_sizes_back_keyboard() -> InlineKeyboardMarkup:
    """Bitta model ko'rsatilganda orqaga tugmasi (2-natija)"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="back:detail"
                )
            ]
        ]
    )


@router.callback_query(F.data == "ready_sizes_menu")
async def callback_ready_sizes_menu(callback_query: CallbackQuery, bot: Bot):
    """Tayyor razmerlar bo'limiga kirish - magazin tanlash"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Ruxsat tekshiruvi - faqat adminlar va maxsus ruxsat berilgan userlar
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    from services.admin_storage import has_ready_sizes_store_access
    has_store_access = has_ready_sizes_store_access(user_id)
    
    if not (is_any_admin_user or has_store_access):
        # Ruxsat yo'q xabari
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text="❌ Sizda bu bo'limga kirish ruxsati yo'q.\n\nAsosiy menyuga qaytilmoqda...",
                reply_markup=None
            )
        except Exception:
            pass
        
        # 2 soniya kutib asosiy menyuga qaytarish
        import asyncio
        await asyncio.sleep(2)
        
        from handlers.start import make_main_menu_keyboard
        keyboard = make_main_menu_keyboard(user_id)
        menu_text = (
            "Assalomu alaykum! TIZIMGA  xush kelibsiz.\n\n"
            "Quyidagi menyulardan birini tanlang:"
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
        return
    
    menu_text = (
        "📐 <b>Tayyor razmerlar</b>\n\n"
        "Quyidagi magazindan birini tanlang:"
    )
    
    keyboard = make_store_selection_keyboard()
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in ready_sizes_menu: {e}")


@router.callback_query(F.data == "my_ready_sizes_report")
async def callback_my_ready_sizes_report(callback_query: CallbackQuery, bot: Bot):
    """Show personalized ready sizes report for current user"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Check permissions
    from services.admin_utils import is_admin, is_super_admin
    from services.admin_storage import is_seller, get_seller_name
    
    is_admin_user = is_super_admin(user_id) or is_admin(user_id)
    is_seller_user = is_seller(user_id)
    seller_name = get_seller_name(user_id) if is_seller_user else None
    
    # Get filtered events
    from services.ready_sizes_events import get_user_events
    from datetime import datetime, timedelta
    
    events = get_user_events(
        user_id=user_id,
        is_admin=is_admin_user,
        is_seller=is_seller_user,
        seller_name=seller_name
    )
    
    # Count events
    cart_count = len(events["cart"])
    confirmed_count = len(events["confirmed"])
    deleted_count = len(events["deleted"])
    
    # Count today's events
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    cart_today = sum(1 for e in events["cart"] if e.timestamp >= today_start)
    confirmed_today = sum(1 for e in events["confirmed"] if e.timestamp >= today_start)
    deleted_today = sum(1 for e in events["deleted"] if e.timestamp >= today_start)
    
    # Build report title
    if is_admin_user:
        title = "📊 <b>Xisobot - Admin (Barcha foydalanuvchilar)</b>"
    elif is_seller_user and seller_name:
        title = f"📊 <b>Xisobot - Sotuvchi: {seller_name}</b>\n<i>(Siz va sizga biriktirilgan hamkorlar)</i>"
    else:
        title = "📊 <b>Mening xisobotim</b>"
    
    report = f"""{title}

<b>📈 Umumiy statistika:</b>
🛒 Karzinkaga qo'shilgan: {cart_count} ta
✅ Tasdiqlangan (sotilgan): {confirmed_count} ta
🗑 O'chirilgan/qaytarilgan: {deleted_count} ta

<b>📊 Bugungi faollik:</b>
🛒 Karzinkaga: {cart_today} ta
✅ Tasdiqlangan: {confirmed_today} ta
🗑 O'chirilgan: {deleted_today} ta
"""
    
    # Add last 5 confirmed events
    if confirmed_count > 0:
        recent_confirmed = events["confirmed"][:5]
        report += "\n<b>📌 Oxirgi tasdiqlangan buyurtmalar (5 ta):</b>\n"
        
        for idx, event in enumerate(recent_confirmed, 1):
            uz_time = event.timestamp + timedelta(hours=5)
            time_str = uz_time.strftime("%d.%m.%Y %H:%M")
            
            # Display: CODE | Razmer | Model name (if available)
            display_parts = [event.code]
            if event.razmer:
                display_parts.append(event.razmer)
            if event.model_nomi and event.model_nomi != event.code:
                display_parts.append(event.model_nomi)
            
            report += f"\n{idx}. {' | '.join(display_parts)}\n"
            
            # Show buyer info with clear role designation
            if event.role == "Hamkor" and event.seller_name:
                report += f"   Sotuvchi {event.seller_name}ning hamkori {event.user_name}\n"
            elif event.role == "Sotuvchi":
                report += f"   Sotuvchi {event.user_name}\n"
            else:
                report += f"   {event.role}: {event.user_name}\n"
            
            report += f"   Vaqt: {time_str}\n"
    
    # Buttons
    keyboard_buttons = [
        [InlineKeyboardButton(text="🛒 Karzinkadagi buyurtmalarim", callback_data="my_cart_events:0")],
        [InlineKeyboardButton(text="✅ Tasdiqlangan buyurtmalarim", callback_data="my_confirmed_events:0")],
        [InlineKeyboardButton(text="🗑 O'chirilgan buyurtmalarim", callback_data="my_deleted_events:0")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="ready_sizes")]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=report,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in my_ready_sizes_report: {e}")


@router.callback_query(F.data.startswith("my_cart_events:"))
async def callback_my_cart_events(callback_query: CallbackQuery, bot: Bot):
    """Show user's cart events with pagination"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Parse page number
    try:
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    # Check permissions
    from services.admin_utils import is_admin, is_super_admin
    from services.admin_storage import is_seller, get_seller_name
    
    is_admin_user = is_super_admin(user_id) or is_admin(user_id)
    is_seller_user = is_seller(user_id)
    seller_name = get_seller_name(user_id) if is_seller_user else None
    
    # Get filtered events
    from services.ready_sizes_events import get_user_events
    from datetime import timedelta
    
    events = get_user_events(
        user_id=user_id,
        is_admin=is_admin_user,
        is_seller=is_seller_user,
        seller_name=seller_name
    )
    
    cart_events = events["cart"]
    total_events = len(cart_events)
    
    # Pagination settings
    items_per_page = 10
    total_pages = (total_events + items_per_page - 1) // items_per_page if total_events > 0 else 1
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, total_events)
    page_items = cart_events[start_idx:end_idx]
    
    # Build report
    if total_events == 0:
        report = "🛒 <b>Karzinkadagi buyurtmalarim</b>\n\nHozircha karzinkaga qo'shilgan buyurtmalar yo'q."
    else:
        lines = ["🛒 <b>Karzinkadagi buyurtmalarim</b>\n"]
        lines.append(f"Jami: {total_events} ta")
        lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
        
        for i, event in enumerate(page_items, start=start_idx + 1):
            uz_time = event.timestamp + timedelta(hours=5)
            time_str = uz_time.strftime("%d.%m.%Y %H:%M")
            
            # Display: CODE | Razmer | Model name (if different)
            display_parts = [event.code]
            if event.razmer:
                display_parts.append(event.razmer)
            if event.model_nomi and event.model_nomi != event.code:
                display_parts.append(event.model_nomi)
            qty_display = f" | {event.qty} ta" if event.qty > 1 else ""
            
            lines.append(f"{i}. <b>Kod:</b> {' | '.join(display_parts)}{qty_display}")
            
            # Show buyer info with clear role designation
            if event.role == "Hamkor" and event.seller_name:
                lines.append(f"   <b>Sotuvchi {event.seller_name}ning hamkori:</b> {event.user_name}")
            elif event.role == "Sotuvchi":
                lines.append(f"   <b>Sotuvchi:</b> {event.user_name}")
            else:
                lines.append(f"   <b>{event.role}:</b> {event.user_name}")
            
            lines.append(f"   <b>Vaqt:</b> {time_str}\n")
        
        report = "\n".join(lines)
    
    # Build keyboard with pagination
    keyboard_buttons = []
    
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"my_cart_events:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"my_cart_events:{page + 1}"))
        if nav_row:
            keyboard_buttons.append(nav_row)
    
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="my_ready_sizes_report")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=report,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in my_cart_events: {e}")


@router.callback_query(F.data.startswith("my_confirmed_events:"))
async def callback_my_confirmed_events(callback_query: CallbackQuery, bot: Bot):
    """Show user's confirmed events with pagination"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Parse page number
    try:
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    # Check permissions
    from services.admin_utils import is_admin, is_super_admin
    from services.admin_storage import is_seller, get_seller_name
    
    is_admin_user = is_super_admin(user_id) or is_admin(user_id)
    is_seller_user = is_seller(user_id)
    seller_name = get_seller_name(user_id) if is_seller_user else None
    
    # Get filtered events
    from services.ready_sizes_events import get_user_events
    from datetime import timedelta
    
    events = get_user_events(
        user_id=user_id,
        is_admin=is_admin_user,
        is_seller=is_seller_user,
        seller_name=seller_name
    )
    
    confirmed_events = events["confirmed"]
    total_events = len(confirmed_events)
    
    # Pagination settings
    items_per_page = 10
    total_pages = (total_events + items_per_page - 1) // items_per_page if total_events > 0 else 1
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, total_events)
    page_items = confirmed_events[start_idx:end_idx]
    
    # Build report
    if total_events == 0:
        report = "✅ <b>Tasdiqlangan buyurtmalarim</b>\n\nHozircha tasdiqlangan buyurtmalar yo'q."
    else:
        lines = ["✅ <b>Tasdiqlangan buyurtmalarim</b>\n"]
        lines.append(f"Jami: {total_events} ta")
        lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
        
        for i, event in enumerate(page_items, start=start_idx + 1):
            uz_time = event.timestamp + timedelta(hours=5)
            time_str = uz_time.strftime("%d.%m.%Y %H:%M")
            
            # Display code, razmer, model name
            lines.append(f"\n{i}. 📌 <b>Kod:</b> {event.code}")
            lines.append(f"📌 <b>Razmer:</b> {event.razmer if event.razmer else '-'}")
            if event.model_nomi and event.model_nomi != event.code:
                lines.append(f"📌 <b>Model:</b> {event.model_nomi}")
            lines.append(f"📌 <b>Miqdor:</b> {event.qty} ta")
            
            # Show buyer info with clear role designation
            if event.role == "Hamkor" and event.seller_name:
                lines.append(f"👤 <b>Sotuvchi {event.seller_name}ning hamkori:</b> {event.user_name}")
            elif event.role == "Sotuvchi":
                lines.append(f"👤 <b>Sotuvchi:</b> {event.user_name}")
            else:
                lines.append(f"👤 <b>{event.role}:</b> {event.user_name}")
            
            # Show confirmer
            if event.confirmer_id and event.confirmer_name:
                confirmer_role = event.confirmer_role if event.confirmer_role else "Admin"
                lines.append(f"🛡 <b>Tasdiqlagan ({confirmer_role}):</b> {event.confirmer_name}")
            
            lines.append(f"🕒 <b>Vaqt:</b> {time_str}\n")
        
        report = "\n".join(lines)
    
    # Build keyboard with pagination
    keyboard_buttons = []
    
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"my_confirmed_events:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"my_confirmed_events:{page + 1}"))
        if nav_row:
            keyboard_buttons.append(nav_row)
    
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="my_ready_sizes_report")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=report,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in my_confirmed_events: {e}")


@router.callback_query(F.data.startswith("my_deleted_events:"))
async def callback_my_deleted_events(callback_query: CallbackQuery, bot: Bot):
    """Show user's deleted events with pagination"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Parse page number
    try:
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    # Check permissions
    from services.admin_utils import is_admin, is_super_admin
    from services.admin_storage import is_seller, get_seller_name
    
    is_admin_user = is_super_admin(user_id) or is_admin(user_id)
    is_seller_user = is_seller(user_id)
    seller_name = get_seller_name(user_id) if is_seller_user else None
    
    # Get filtered events
    from services.ready_sizes_events import get_user_events
    from datetime import timedelta
    
    events = get_user_events(
        user_id=user_id,
        is_admin=is_admin_user,
        is_seller=is_seller_user,
        seller_name=seller_name
    )
    
    deleted_events = events["deleted"]
    total_events = len(deleted_events)
    
    # Pagination settings
    items_per_page = 10
    total_pages = (total_events + items_per_page - 1) // items_per_page if total_events > 0 else 1
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, total_events)
    page_items = deleted_events[start_idx:end_idx]
    
    # Build report
    if total_events == 0:
        report = "🗑 <b>O'chirilgan buyurtmalarim</b>\n\nHozircha o'chirilgan buyurtmalar yo'q."
    else:
        lines = ["🗑 <b>O'chirilgan buyurtmalarim</b>\n"]
        lines.append(f"Jami: {total_events} ta")
        lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
        
        for i, event in enumerate(page_items, start=start_idx + 1):
            uz_time = event.timestamp + timedelta(hours=5)
            time_str = uz_time.strftime("%d.%m.%Y %H:%M")
            
            # Display: CODE | Razmer | Model name (if different)
            display_parts = [event.code]
            if event.razmer:
                display_parts.append(event.razmer)
            if event.model_nomi and event.model_nomi != event.code:
                display_parts.append(event.model_nomi)
            qty_display = f" | {event.qty} ta" if event.qty > 1 else ""
            
            lines.append(f"{i}. <b>Kod:</b> {' | '.join(display_parts)}{qty_display}")
            
            # Show who deleted with clear role designation
            if event.role == "Hamkor" and event.seller_name:
                lines.append(f"   <b>Sotuvchi {event.seller_name}ning hamkori:</b> {event.user_name}")
            elif event.role == "Sotuvchi":
                lines.append(f"   <b>Sotuvchi:</b> {event.user_name}")
            else:
                lines.append(f"   <b>{event.role}:</b> {event.user_name}")
            
            lines.append(f"   <b>Vaqt:</b> {time_str}\n")
        
        report = "\n".join(lines)
    
    # Build keyboard with pagination
    keyboard_buttons = []
    
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"my_deleted_events:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"my_deleted_events:{page + 1}"))
        if nav_row:
            keyboard_buttons.append(nav_row)
    
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="my_ready_sizes_report")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=report,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in my_deleted_events: {e}")


@router.callback_query(F.data.startswith("ready_sizes_store:"))
async def callback_ready_sizes_store(callback_query: CallbackQuery, bot: Bot):
    """Magazin tanlangandan keyin - mahsulot turi tanlash"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Ruxsat tekshiruvi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    from services.settings import has_discount_access, has_api_access
    has_discount = has_discount_access(user_id)
    if not (is_any_admin_user or has_api_access(user_id) or has_discount):
        return
    
    # Magazin nomini olish
    data = callback_query.data.split(":", 1)
    if len(data) < 2:
        return
    
    store = data[1]
    
    menu_text = (
        f"📐 <b>Tayyor razmerlar</b>\n\n"
        f"Magazin: <b>{store}</b>\n\n"
        "Quyidagi mahsulot turidan birini tanlang:"
    )
    
    keyboard = make_product_type_keyboard(store)
    
    try:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in ready_sizes_store: {e}")


@router.callback_query(F.data.startswith("ready_sizes_product:"))
async def callback_ready_sizes_product(callback_query: CallbackQuery, bot: Bot):
    """Tayyor razmerlar natijasini ko'rsatish (sheets5)"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Ruxsat tekshiruvi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    from services.settings import has_discount_access, has_api_access
    has_discount = has_discount_access(user_id)
    if not (is_any_admin_user or has_api_access(user_id) or has_discount):
        return
    
    # Store va product_type ni olish
    data = callback_query.data.split(":", 2)
    if len(data) < 3:
        return
    
    store = data[1]
    product_type = data[2]
    
    # Sheets5 dan ma'lumot olish (cache dan)
    sheets5_data = CACHE.get("sheets5", [])
    
    # Filtrlash: magazin == store va mahsulot_turi == product_type (ustun nomlari case-insensitive)
    matched_models = []
    store_normalized = normalize_text_for_search(store)
    product_type_normalized = normalize_text_for_search(product_type)
    
    for record in sheets5_data:
        record_magazin = normalize_text_for_search(
            get_value_ci(record, "magazin", "Magazin")
        )
        record_mahsulot_turi = normalize_text_for_search(
            get_value_ci(record, "mahsulot_turi", "Mahsulot turi")
        )
        
        if record_magazin == store_normalized and record_mahsulot_turi == product_type_normalized:
            matched_models.append(record)

    # Natija ko'rsatish (BITTA xabar ichida, faqat edit_message_text)
    if not matched_models:
        # Hech qanday natija topilmasa - faqat qisqa xabar
        result_text = "❌ Hech qanday natija topilmadi."
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="back:list"
                    )
                ]
            ]
        )
    else:
        # Birinchi modeldan umumiy sarlavha ma'lumotlari
        first = matched_models[0]
        header_magazin = get_value_ci(
            first,
            "magazin",
            "Magazin",
            default="noma'lum"
        ) or "noma'lum"
        header_model = get_value_ci(
            first,
            "nomi",
            "model_nomi",
            "Model nomi",
            "model",
            default="noma'lum"
        ) or "noma'lum"

        header_lines = [
            f"🏬 Magazin: {header_magazin}",
            f"📌 Model: {header_model}",
            "— — — — —",
        ]

        # Pastda ro'yxat: har bir yozuv uchun Code / Razmer / Kolleksiya
        item_blocks: list[str] = []
        for model in matched_models:
            code_value = get_value_ci(
                model,
                "code",
                "Code",
                default="noma'lum"
            ) or "noma'lum"
            razmer_value = get_value_ci(
                model,
                "razmer",
                "Razmer",
                default="noma'lum"
            ) or "noma'lum"
            kolleksiya_value = get_value_ci(
                model,
                "kolleksiya",
                "Kolleksiya",
                default="noma'lum"
            ) or "noma'lum"

            block_lines = [
                f"🔢 Code: {code_value}",
                f"📐 Razmer: {razmer_value}",
                f"🎨 Kolleksiya: {kolleksiya_value}",
            ]
            item_blocks.append("\n".join(block_lines))

        result_text = "\n".join(header_lines)
        if item_blocks:
            result_text += "\n\n" + "\n\n".join(item_blocks)

        keyboard = make_ready_sizes_result_keyboard(matched_models)
    
    # Foydalanuvchi kontekstini saqlab qo'yamiz (1-natija TEXT va REPLY_MARKUP bilan)
    READY_SIZES_CONTEXT[callback_query.from_user.id] = {
        "store": store,
        "product_type": product_type,
        "models": matched_models,
        "result_text": result_text,  # 1-natija matni
        "result_keyboard": keyboard,  # 1-natija tugmalari
    }
    
    try:
        # Yangi xabar yubormaymiz, faqat mavjudini tahrir qilamiz
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=result_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error in ready_sizes_product: {e}")


@router.callback_query(F.data.startswith("code:"))
async def callback_view_ready_model(callback_query: CallbackQuery, bot: Bot):
    """Model bosilganda - bitta model ma'lumotlari"""
    await callback_query.answer()
    
    user_id = callback_query.from_user.id
    
    # Ruxsat tekshiruvi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    from services.settings import has_discount_access, has_api_access
    has_discount = has_discount_access(user_id)
    if not (is_any_admin_user or has_api_access(user_id) or has_discount):
        return
    
    # Code ni olish
    code = callback_query.data.split(":", 1)[1] if ":" in callback_query.data else ""
    
    # Sheets5 dan model topish (code ustuni case-insensitive)
    sheets5_data = CACHE.get("sheets5", [])
    model = None
    
    for record in sheets5_data:
        record_code = get_value_ci(record, "code", "Code")
        if record_code.strip() == code.strip():
            model = record
            break
    
    if not model:
        await callback_query.answer("❌ Model topilmadi", show_alert=True)
        return
    
    # Store va product_type ni modeldan olish (case-insensitive)
    store = get_value_ci(model, "magazin", "Magazin", default="noma'lum")
    product_type = get_value_ci(model, "mahsulot_turi", "Mahsulot turi", default="noma'lum")
    
    # Model ma'lumotlarini ko'rsatish (2-natija) - barcha kerakli ustunlar
    code_value = get_value_ci(model, "code", "Code", default="noma'lum") or "noma'lum"
    razmer = get_value_ci(model, "razmer", "Razmer", default="noma'lum") or "noma'lum"
    # Model nomi turli nomlar bilan kelishi mumkin
    model_nomi = get_value_ci(
        model,
        "nomi",
        "model_nomi",
        "Model nomi",
        "model",
        default="noma'lum",
    ) or "noma'lum"
    kolleksiya = get_value_ci(
        model,
        "kolleksiya",
        "Kolleksiya",
        default="noma'lum",
    ) or "noma'lum"

    # Linklar faqat ichki logika uchun, userga ko'rsatilmaydi
    image_url = get_value_ci(model, "image_url", "Image URL", "image", default="")
    
    # Bo'sh qiymatlar uchun oldindan "noma'lum" ni hisoblab qo'yamiz
    safe_store = store or "noma'lum"
    safe_product_type = product_type or "noma'lum"
    safe_model_nomi = model_nomi or "noma'lum"
    safe_code_value = code_value or "noma'lum"
    safe_razmer = razmer or "noma'lum"
    safe_kolleksiya = kolleksiya or "noma'lum"

    # 2-natija caption (skidka 2-natijadagi kabi faqat asosiy ma'lumotlar)
    caption_lines = []
    if safe_model_nomi and safe_model_nomi != "noma'lum":
        caption_lines.append(f"📌 Model nomi: {safe_model_nomi}")
    caption_lines.append(f"🔢 Code: {safe_code_value}")
    caption_lines.append(f"📐 Razmer: {safe_razmer}")
    caption_lines.append(f"🎨 Kolleksiya: {safe_kolleksiya}")
    caption = "\n".join(caption_lines)

    # 2-natija uchun orqaga tugmasi (rasm ostida)
    keyboard = make_ready_sizes_back_keyboard()
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id
    
    try:
        # image_url faqat sheets5 dan olinadi, agar Google Drive bo'lsa, konvertatsiya qilinadi
        if image_url:
            # Rasmli natija: avval edit qilishga urinish
            sheet_service = GoogleSheetService()
            converted_url = sheet_service._convert_google_drive_link(image_url)
            
            try:
                # Avval edit_message_caption bilan urinish
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=caption,
                    reply_markup=keyboard
                )
            except Exception:
                # Agar edit ishlamasa, eski xabarni o'chirib yangi yuborish
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=converted_url,
                    caption=caption,
                    reply_markup=keyboard
                )
        else:
            # Rasm bo'lmagan holatda: avval edit qilishga urinish
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=caption,
                    reply_markup=keyboard
                )
            except Exception:
                # Agar edit ishlamasa, eski xabarni o'chirib yangi yuborish
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass
                await bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
    except Exception as e:
        logger.error(f"Error in view_ready_model: {e}")


@router.callback_query(F.data == "back:detail")
async def callback_back_ready_detail(callback_query: CallbackQuery, bot: Bot):
    """2-natija orqaga: rasmli xabarni o'chirib, 1-natija ro'yxatini qayta yuborish"""
    await callback_query.answer()

    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id

    # Rasmli xabarni o'chirish
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=callback_query.message.message_id
        )
    except Exception:
        pass

    # Context'dan 1-natija TEXT va REPLY_MARKUP ni olish
    ctx = READY_SIZES_CONTEXT.get(user_id) or {}
    result_text = ctx.get("result_text")
    result_keyboard = ctx.get("result_keyboard")

    if result_text and result_keyboard:
        # Context'dan saqlangan 1-natija TEXT va REPLY_MARKUP ni qayta chiqarish
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=result_text,
                reply_markup=result_keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in back:detail: {e}")
    else:
        # Fallback: agar context'da TEXT/KEYBOARD bo'lmasa, qayta yaratish
        models = ctx.get("models") or []
        if not models:
            text = "❌ Hech qanday natija topilmadi."
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Orqaga",
                            callback_data="back:list"
                        )
                    ]
                ]
            )
        else:
            first = models[0]
            header_magazin = get_value_ci(
                first,
                "magazin",
                "Magazin",
                default="noma'lum"
            ) or "noma'lum"
            header_model = get_value_ci(
                first,
                "nomi",
                "model_nomi",
                "Model nomi",
                "model",
                default="noma'lum"
            ) or "noma'lum"

            header_lines = [
                f"🏬 Magazin: {header_magazin}",
                f"📌 Model: {header_model}",
                "— — — — —",
            ]

            item_blocks: list[str] = []
            for model in models:
                code_value = get_value_ci(
                    model,
                    "code",
                    "Code",
                    default="noma'lum"
                ) or "noma'lum"
                razmer_value = get_value_ci(
                    model,
                    "razmer",
                    "Razmer",
                    default="noma'lum"
                ) or "noma'lum"
                kolleksiya_value = get_value_ci(
                    model,
                    "kolleksiya",
                    "Kolleksiya",
                    default="noma'lum"
                ) or "noma'lum"

                block_lines = [
                    f"🔢 Code: {code_value}",
                    f"📐 Razmer: {razmer_value}",
                    f"🎨 Kolleksiya: {kolleksiya_value}",
                ]
                item_blocks.append("\n".join(block_lines))

            text = "\n".join(header_lines)
            if item_blocks:
                text += "\n\n" + "\n\n".join(item_blocks)

            keyboard = make_ready_sizes_result_keyboard(models)

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error in back:detail fallback: {e}")


@router.callback_query(F.data == "back:list")
async def callback_back_ready_list(callback_query: CallbackQuery, bot: Bot):
    """1-natija ostidagi Orqaga: bitta qadam orqaga EDIT bo'lib qaytadi (mahsulot turi tanlash sahifasi)"""
    await callback_query.answer()

    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id

    # Ruxsat tekshiruvi
    is_any_admin_user = is_super_admin(user_id) or is_admin(user_id)
    from services.settings import has_discount_access, has_api_access
    has_discount = has_discount_access(user_id)
    if not (is_any_admin_user or has_api_access(user_id) or has_discount):
        return

    # Foydalanuvchi kontekstidan magazin nomini olish
    ctx = READY_SIZES_CONTEXT.get(user_id) or {}
    store = ctx.get("store")

    # Agar kontekst yo'q bo'lsa, eski xulq-atvor: asosiy menyuga qaytish
    if not store:
        chat_id = callback_query.message.chat.id

        # Joriy xabarni o'chirish (agar bo'lsa)
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=callback_query.message.message_id
            )
        except Exception:
            pass

        from handlers.start import make_main_menu_keyboard
        from services.message_utils import store_main_menu_message

        menu_text = (
            "👋 Assalomu alaykum! Botga xush kelibsiz.\n\n"
            "Quyidagi menyulardan birini tanlang:"
        )

        keyboard = make_main_menu_keyboard(user_id)

        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            store_main_menu_message(chat_id, sent.message_id)
        except Exception:
            pass
        return

    # Kolleksiya turi tanlash sahifasiga EDIT orqali qaytish
    menu_text = (
        "📐 <b>Tayyor razmerlar</b>\n\n"
        f"Magazin: <b>{store}</b>\n\n"
        "Quyidagi mahsulot turidan birini tanlang:"
    )

    keyboard = make_product_type_keyboard(store)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=menu_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        # Agar EDIT muvaffaqiyatsiz bo'lsa, xabarni o'chirib, yangi menyu yuboramiz
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=callback_query.message.message_id
            )
        except Exception:
            pass

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass
