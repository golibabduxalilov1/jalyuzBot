"""
Admin storage management - admins.json fayl bilan ishlash
"""
import json
from pathlib import Path
from typing import Dict

# admins.json fayl yo'li
ADMINS_FILE = Path(__file__).parent.parent / "admins.json"

# RAM da saqlanadigan adminlar va ruxsatlar (admins.json dan yuklanadi)
_super_admins: Dict[str, str] = {}  # user_id -> name
_admins: Dict[str, str] = {}  # user_id -> name
_price_access: Dict[str, str] = {}  # user_id -> name
_discount_access: Dict[str, str] = {}  # user_id -> name
_ready_sizes_store_access: Dict[str, str] = {}  # user_id -> name (Magazindagi tayyor razmerlar uchun)
_sellers: Dict[str, str] = {}  # user_id -> name
_partners: list = []  # [{"sotuvchi": "seller_id", "hamkor": "name", "id": "partner_id"}]


def load_admins():
    """Bot start da admins.json dan yuklash"""
    global _super_admins, _admins, _price_access, _discount_access, _ready_sizes_store_access, _sellers, _partners
    
    if ADMINS_FILE.exists():
        try:
            with open(ADMINS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Yangi formatni qo'llab-quvvatlash
                _super_admins = data.get("super_admins", data.get("main_admins", {}))
                _admins = data.get("admins", data.get("helper_admins", data.get("helpers", {})))
                _price_access = data.get("price_access", {})
                _discount_access = data.get("discount_access", {})
                _ready_sizes_store_access = data.get("ready_sizes_store_access", {})
                sellers_raw = data.get("sellers", {})
                # Backward compatibility: yangi format (dict) ni eski formatga (string) o'tkazish
                _sellers = {}
                for user_id, value in sellers_raw.items():
                    if isinstance(value, dict):
                        # Yangi formatdan eski formatga
                        _sellers[user_id] = value.get("name", f"User {user_id}")
                    elif isinstance(value, str):
                        # Eski format
                        _sellers[user_id] = value
                partners_raw = data.get("partners", [])
                # Backward compatibility: eski formatni yangi formatga o'tkazish
                _partners = []
                for partner in partners_raw:
                    if isinstance(partner, dict):
                        # Eski format: {"sotuvchi": "...", "hamkor": "...", "id": "..."}
                        if "sotuvchi" in partner:
                            seller_id = partner.get("sotuvchi", "")
                            seller_name = get_seller_name(int(seller_id)) if seller_id.isdigit() else ""
                            if not seller_name or seller_name.startswith("User"):
                                seller_name = _sellers.get(seller_id, f"User {seller_id}")
                            _partners.append({
                                "seller_id": seller_id,
                                "seller_name": seller_name,
                                "partner_name": partner.get("hamkor", ""),
                                "partner_id": partner.get("id", "")
                            })
                        # Yangi format: {"seller_id": "...", "seller_name": "...", "partner_name": "...", "partner_id": "..."}
                        elif "seller_id" in partner:
                            _partners.append(partner)
        except Exception as e:
            print(f"Error loading admins.json: {e}")
            _super_admins = {}
            _admins = {}
            _price_access = {}
            _discount_access = {}
            _ready_sizes_store_access = {}
            _sellers = {}
            _partners = []
    else:
        # Fayl mavjud emas, bo'sh yaratish
        _super_admins = {}
        _admins = {}
        _price_access = {}
        _discount_access = {}
        _ready_sizes_store_access = {}
        _sellers = {}
        _partners = []
        save_admins()


def save_admins():
    """admins.json faylga saqlash"""
    try:
        data = {
            "main_admins": _super_admins,
            "helper_admins": _admins,
            "price_access": _price_access,
            "discount_access": _discount_access,
            "ready_sizes_store_access": _ready_sizes_store_access,
            "sellers": _sellers,
            "partners": _partners
        }
        with open(ADMINS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving admins.json: {e}")


def get_super_admins() -> Dict[str, str]:
    """
    Super adminlar ro'yxatini olish.
    admins.json dan + config.py dagi ADMINS dan.
    """
    from config import ADMINS
    result = _super_admins.copy()
    # config.py dagi ADMINS ni qo'shish (agar admins.json da yo'q bo'lsa)
    for admin_id in ADMINS:
        admin_id_str = str(admin_id)
        if admin_id_str not in result:
            # Agar ism yo'q bo'lsa, default ism qo'yamiz
            result[admin_id_str] = f"Super Admin {admin_id}"
    return result


def get_admins() -> Dict[str, str]:
    """
    Yordamchi adminlar ro'yxatini olish.
    admins.json dan + config.py dagi HELPER_ADMINS dan.
    """
    from config import HELPER_ADMINS
    result = _admins.copy()
    # config.py dagi HELPER_ADMINS ni qo'shish (agar admins.json da yo'q bo'lsa)
    for admin_id in HELPER_ADMINS:
        admin_id_str = str(admin_id)
        if admin_id_str not in result:
            # Agar ism yo'q bo'lsa, default ism qo'yamiz
            result[admin_id_str] = f"Helper Admin {admin_id}"
    return result


def add_super_admin(user_id: int, name: str):
    """Super admin qo'shish"""
    global _super_admins
    _super_admins[str(user_id)] = name.strip()
    save_admins()


def add_admin(user_id: int, name: str):
    """Admin qo'shish"""
    global _admins
    _admins[str(user_id)] = name.strip()
    save_admins()


def remove_super_admin(user_id: int):
    """Super adminni o'chirish"""
    global _super_admins
    _super_admins.pop(str(user_id), None)
    save_admins()


def remove_admin(user_id: int):
    """Adminni o'chirish"""
    global _admins
    _admins.pop(str(user_id), None)
    save_admins()


def get_super_admin_name(user_id: int) -> str:
    """Super admin ismini olish"""
    return _super_admins.get(str(user_id), f"User {user_id}")


def get_admin_name_storage(user_id: int) -> str:
    """Admin ismini olish"""
    return _admins.get(str(user_id), f"User {user_id}")


def is_super_admin(user_id: int) -> bool:
    """Super admin ekanligini tekshirish"""
    return str(user_id) in _super_admins


def is_admin_storage(user_id: int) -> bool:
    """Admin ekanligini tekshirish"""
    return str(user_id) in _admins


def is_any_admin_storage(user_id: int) -> bool:
    """Har qanday admin ekanligini tekshirish (super yoki oddiy)"""
    return is_super_admin(user_id) or is_admin_storage(user_id)


# Eski funksiyalar (backward compatibility)
def get_main_admins() -> Dict[str, str]:
    """Asosiy adminlar ro'yxatini olish (backward compatibility)"""
    return get_super_admins()


def get_helper_admins() -> Dict[str, str]:
    """Yordamchi adminlar ro'yxatini olish (backward compatibility)"""
    return get_admins()


def add_main_admin(user_id: int, name: str):
    """Asosiy admin qo'shish (backward compatibility)"""
    add_super_admin(user_id, name)


def add_helper_admin(user_id: int, name: str):
    """Yordamchi admin qo'shish (backward compatibility)"""
    add_admin(user_id, name)


def remove_main_admin(user_id: int):
    """Asosiy adminni o'chirish (backward compatibility)"""
    remove_super_admin(user_id)


def remove_helper_admin(user_id: int):
    """Yordamchi adminni o'chirish (backward compatibility)"""
    remove_admin(user_id)


def get_main_admin_name(user_id: int) -> str:
    """Asosiy admin ismini olish (backward compatibility)"""
    return get_super_admin_name(user_id)


def get_helper_admin_name(user_id: int) -> str:
    """Yordamchi admin ismini olish (backward compatibility)"""
    return get_admin_name_storage(user_id)


def is_main_admin(user_id: int) -> bool:
    """Asosiy admin ekanligini tekshirish (backward compatibility)"""
    return is_super_admin(user_id)


def is_helper_admin_storage(user_id: int) -> bool:
    """Yordamchi admin ekanligini tekshirish (backward compatibility)"""
    return is_admin_storage(user_id)


# ==================== PRICE ACCESS ====================

def get_price_access() -> Dict[str, str]:
    """Price access ro'yxatini olish"""
    return _price_access.copy()


def add_price_access(user_id: int, name: str):
    """Price access qo'shish"""
    global _price_access
    _price_access[str(user_id)] = name.strip()
    save_admins()


def remove_price_access(user_id: int):
    """Price access ni o'chirish"""
    global _price_access
    _price_access.pop(str(user_id), None)
    save_admins()


def get_price_access_name(user_id: int) -> str:
    """Price access ismini olish"""
    return _price_access.get(str(user_id), f"User {user_id}")


def has_price_access(user_id: int) -> bool:
    """
    Price access borligini tekshirish - FAQAT admins.json dan o'qiladi.
    - Agar user_id main_admins da bo'lsa → TRUE
    - Agar user_id helper_admins da bo'lsa → TRUE
    - Agar user_id price_access da bo'lsa → TRUE
    - Aks holda → FALSE
    """
    user_id_str = str(user_id)
    if user_id_str in _super_admins:  # main_admins
        return True
    if user_id_str in _admins:  # helper_admins
        return True
    if user_id_str in _price_access:  # price_access
        return True
    return False


# ==================== DISCOUNT ACCESS ====================

def get_discount_access() -> Dict[str, str]:
    """Discount access ro'yxatini olish"""
    return _discount_access.copy()


def add_discount_access(user_id: int, name: str):
    """Discount access qo'shish"""
    global _discount_access
    _discount_access[str(user_id)] = name.strip()
    save_admins()


def remove_discount_access(user_id: int):
    """Discount access ni o'chirish"""
    global _discount_access
    _discount_access.pop(str(user_id), None)
    save_admins()


def get_discount_access_name(user_id: int) -> str:
    """Discount access ismini olish"""
    return _discount_access.get(str(user_id), f"User {user_id}")


def has_discount_access(user_id: int) -> bool:
    """
    Discount access borligini tekshirish - FAQAT admins.json dan o'qiladi.
    - Agar user_id main_admins da bo'lsa → TRUE
    - Agar user_id helper_admins da bo'lsa → TRUE
    - Agar user_id price_access da bo'lsa → TRUE
    - Agar user_id discount_access da bo'lsa → TRUE
    - Aks holda → FALSE
    """
    user_id_str = str(user_id)
    if user_id_str in _super_admins:  # main_admins
        return True
    if user_id_str in _admins:  # helper_admins
        return True
    if user_id_str in _price_access:  # price_access
        return True
    if user_id_str in _discount_access:  # discount_access
        return True
    return False


def has_price_access_only(user_id: int) -> bool:
    """
    FAQAT price_access borligini tekshirish (admin emas).
    - Agar user_id admin bo'lsa → FALSE
    - Agar user_id price_access da bo'lsa → TRUE
    - Aks holda → FALSE
    """
    user_id_str = str(user_id)
    # Admin bo'lsa, False qaytaradi
    if user_id_str in _super_admins or user_id_str in _admins:
        return False
    # FAQAT price_access tekshiriladi
    return user_id_str in _price_access


def has_discount_access_only(user_id: int) -> bool:
    """
    FAQAT discount_access borligini tekshirish (admin emas, price_access emas).
    - Agar user_id admin bo'lsa → FALSE
    - Agar user_id price_access da bo'lsa → FALSE
    - Agar user_id discount_access da bo'lsa → TRUE
    - Aks holda → FALSE
    """
    user_id_str = str(user_id)
    # Admin yoki price_access bo'lsa, False qaytaradi
    if user_id_str in _super_admins or user_id_str in _admins:
        return False
    if user_id_str in _price_access:
        return False
    # FAQAT discount_access tekshiriladi
    return user_id_str in _discount_access


# ==================== READY SIZES STORE ACCESS ====================

def get_ready_sizes_store_access() -> Dict[str, str]:
    """Magazindagi tayyor razmerlar ruxsati ro'yxatini olish"""
    return _ready_sizes_store_access.copy()


def add_ready_sizes_store_access(user_id: int, name: str):
    """Magazindagi tayyor razmerlar ruxsati qo'shish"""
    global _ready_sizes_store_access
    _ready_sizes_store_access[str(user_id)] = name.strip()
    save_admins()


def remove_ready_sizes_store_access(user_id: int):
    """Magazindagi tayyor razmerlar ruxsatini o'chirish"""
    global _ready_sizes_store_access
    _ready_sizes_store_access.pop(str(user_id), None)
    save_admins()


def get_ready_sizes_store_access_name(user_id: int) -> str:
    """Magazindagi tayyor razmerlar ruxsati ismini olish"""
    return _ready_sizes_store_access.get(str(user_id), f"User {user_id}")


def has_ready_sizes_store_access(user_id: int) -> bool:
    """
    Magazindagi tayyor razmerlar ruxsati borligini tekshirish.
    - Agar user_id main_admins da bo'lsa → TRUE
    - Agar user_id helper_admins da bo'lsa → TRUE
    - Agar user_id ready_sizes_store_access da bo'lsa → TRUE
    - Aks holda → FALSE
    """
    user_id_str = str(user_id)
    if user_id_str in _super_admins:  # main_admins
        return True
    if user_id_str in _admins:  # helper_admins
        return True
    if user_id_str in _ready_sizes_store_access:  # ready_sizes_store_access
        return True
    return False


# ==================== SELLERS ====================

def get_sellers() -> Dict[str, str]:
    """Sotuvchilar ro'yxatini olish"""
    return _sellers.copy()


def add_seller(user_id: int, name: str):
    """Sotuvchi qo'shish - avtomatik skidka ruxsati beriladi"""
    global _sellers, _discount_access
    _sellers[str(user_id)] = name.strip()
    # Sotuvchi qo'shilganda avtomatik skidka ruxsati beriladi
    if str(user_id) not in _discount_access:
        _discount_access[str(user_id)] = name.strip()
    save_admins()


def remove_seller(user_id: int):
    """Sotuvchini o'chirish"""
    global _sellers
    _sellers.pop(str(user_id), None)
    save_admins()


def get_seller_name(user_id: int) -> str:
    """Sotuvchi ismini olish"""
    return _sellers.get(str(user_id), f"User {user_id}")


def is_seller(user_id: int) -> bool:
    """Sotuvchi ekanligini tekshirish"""
    return str(user_id) in _sellers


# ==================== PARTNERS ====================

def get_partners(seller_id: int = None) -> list:
    """Hamkorlar ro'yxatini olish"""
    if seller_id is None:
        return _partners.copy()
    # Agar seller_id berilgan bo'lsa, faqat shu sotuvchining hamkorlarini qaytaradi
    return [p for p in _partners if p.get("seller_id") == str(seller_id)]


def add_partner(seller_id: int, partner_name: str, partner_id: str):
    """Hamkor qo'shish"""
    global _partners
    seller_name = get_seller_name(seller_id)
    partner = {
        "seller_id": str(seller_id),
        "seller_name": seller_name,
        "partner_name": partner_name.strip(),
        "partner_id": partner_id.strip()
    }
    _partners.append(partner)
    save_admins()


def remove_partner(seller_id: int, partner_id: str):
    """Hamkorni o'chirish"""
    global _partners
    _partners = [p for p in _partners if not (p.get("seller_id") == str(seller_id) and p.get("partner_id") == partner_id)]
    save_admins()


def has_partner(seller_id: int, partner_id: str) -> bool:
    """Hamkor mavjudligini tekshirish"""
    return any(p.get("seller_id") == str(seller_id) and p.get("partner_id") == partner_id for p in _partners)
