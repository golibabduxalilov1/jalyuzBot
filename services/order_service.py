"""
Ready sizes order service.

MUHIM:
- Mavjud admin panel, sotuvchilar, hamkorlar va boshqa logikalarga tegilmaydi.
- Faqat tayyor razmerlar (sheets6) uchun buyurtma funksiyasi.
- Karzinka servisi bilan 100% bir xil struktura.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import uuid4

from services.google_sheet import CACHE
from services.product_utils import normalize_code, normalize_razmer
from services.admin_storage import (
    get_seller_name, 
    get_partners, 
    get_sellers,
    get_admin_name_storage,
    get_super_admin_name,
)


@dataclass
class OrderItem:
    code: str
    razmer: str
    kolleksiya: str
    model_nomi: str
    qty: int
    image_url: str
    added_at: datetime
    user_id: int
    user_name: str
    is_partner: bool
    seller_id: Optional[int]
    seller_name: Optional[str]
    order_id: str = ""

    def to_dict(self) -> Dict:
        data = asdict(self)
        # datetime ni string formatga o'tkazamiz (storage uchun)
        data["added_at"] = self.added_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "OrderItem":
        d = dict(data)
        added_at_raw = d.get("added_at")
        if isinstance(added_at_raw, str):
            try:
                d["added_at"] = datetime.fromisoformat(added_at_raw)
            except Exception:
                d["added_at"] = datetime.utcnow()
        elif isinstance(added_at_raw, datetime):
            d["added_at"] = added_at_raw
        else:
            d["added_at"] = datetime.utcnow()
        
        # MUHIM: qty ni to'g'ri parse qilish - har doim int bo'lishi kerak
        # String, float yoki boshqa tipdan int ga o'tkazish
        qty_raw = d.get("qty")
        if qty_raw is not None:
            try:
                if isinstance(qty_raw, int):
                    d["qty"] = qty_raw
                elif isinstance(qty_raw, str):
                    # To'liq stringni tozalash va int ga o'tkazish (6, 7, 10, 15 ham ishlashi kerak)
                    cleaned = qty_raw.replace(",", ".").replace(" ", "").strip()
                    d["qty"] = int(float(cleaned))
                else:
                    # Float yoki boshqa tipdan int ga
                    cleaned = str(qty_raw).replace(",", ".").replace(" ", "").strip()
                    d["qty"] = int(float(cleaned))
            except (ValueError, TypeError, AttributeError):
                # Xatolik bo'lsa, default 0 (lekin bu noto'g'ri buyurtma bo'ladi)
                d["qty"] = 0
        
        return cls(**d)


# RAM ichida saqlanadigan buyurtmalar:
# key: order_id (har bir buyurtma alohida element)
_READY_SIZES_ORDERS: Dict[str, OrderItem] = {}

# 24 soatlik TTL
_ORDER_TTL = timedelta(hours=24)


def _make_order_key(user_id: int, code: str, razmer: str) -> str:
    return f"{user_id}:{normalize_code(code)}:{(razmer or '').strip()}"


def _generate_order_id() -> str:
    """Har bir buyurtma uchun qisqa unique ID yaratadi."""
    while True:
        order_id = uuid4().hex[:16]
        if order_id not in _READY_SIZES_ORDERS:
            return order_id


def _cleanup_expired_items() -> None:
    """24 soatlik muddatdan oshgan buyurtma elementlarini avtomatik tozalash."""
    if not _READY_SIZES_ORDERS:
        return
    now = datetime.utcnow()
    keys_to_delete: List[str] = []
    for key, item in _READY_SIZES_ORDERS.items():
        if now - item.added_at > _ORDER_TTL:
            keys_to_delete.append(key)
    for key in keys_to_delete:
        _READY_SIZES_ORDERS.pop(key, None)


def find_sheets6_record_by_code(code: str) -> Optional[Dict]:
    """Sheets6 (tayyor razmerlar) dan code bo'yicha bitta yozuvni topish."""
    sheets6 = CACHE.get("sheets6", [])
    if not sheets6:
        return None
    code_norm = normalize_code(code)
    for rec in sheets6:
        rec_code = rec.get("code", "").strip()
        if not rec_code:
            continue
        if normalize_code(rec_code) == code_norm:
            return rec
    return None


def find_sheets6_record_by_code_and_razmer(code: str, razmer: str) -> Optional[Dict]:
    """Sheets6 dan code+razmer bo'yicha aniq yozuvni topish."""
    sheets6 = CACHE.get("sheets6", [])
    if not sheets6:
        return None
    code_norm = normalize_code(code)
    razmer_norm = normalize_razmer(razmer or "")
    for rec in sheets6:
        rec_code = rec.get("code", "").strip()
        rec_razmer = (rec.get("razmer", "") or "").strip()
        if not rec_code:
            continue
        if (
            normalize_code(rec_code) == code_norm
            and normalize_razmer(rec_razmer) == razmer_norm
        ):
            return rec
    return None


def get_available_quantity_from_sheets6(code: str) -> int:
    """Sheets6 dagi 'shtuk'/'soni' ustunidan mavjud miqdorni int ko'rinishida qaytarish."""
    rec = find_sheets6_record_by_code(code)
    if not rec:
        return 0
    raw = (
        rec.get("shtuk")
        or rec.get("Shtuk")
        or rec.get("soni")
        or rec.get("Soni")
        or rec.get("miqdor")
        or ""
    )
    raw_str = str(raw).strip()
    if not raw_str:
        return 0
    try:
        return int(float(raw_str.replace(",", ".").replace(" ", "")))
    except Exception:
        return 0


def get_or_create_order_item(
    user_id: int,
    code: str,
    qty: int,
    user_name: str,
    is_partner: bool,
    seller_id: Optional[int],
    seller_name: Optional[str],
    razmer: str = "",
) -> Optional[OrderItem]:
    """
    Buyurtmalarga bitta mahsulotni qo'shish.
    Har bir bosilgan buyurtma alohida element sifatida saqlanadi.
    Mavjud admin logikalariga tegmaydi, faqat RAM ichida saqlaydi.
    """
    _cleanup_expired_items()

    if razmer:
        record = find_sheets6_record_by_code_and_razmer(code, razmer)
    else:
        record = find_sheets6_record_by_code(code)
    if not record:
        return None

    code_val = record.get("code", "").strip()
    razmer_val = record.get("razmer", "").strip() or ""
    model_nomi = record.get("model_nomi", "").strip() or ""
    kolleksiya = record.get("kolleksiya", "").strip() or ""
    image_url = record.get("image_url", "").strip() or ""

    order_id = _generate_order_id()

    item = OrderItem(
        code=code_val,
        razmer=razmer_val,
        kolleksiya=kolleksiya,
        model_nomi=model_nomi,
        qty=qty,
        image_url=image_url,
        added_at=datetime.utcnow(),
        user_id=user_id,
        user_name=user_name,
        is_partner=is_partner,
        seller_id=seller_id,
        seller_name=seller_name,
        order_id=order_id,
    )
    _READY_SIZES_ORDERS[order_id] = item
    return item


def remove_order_item(
    user_id: int,
    code: str,
    razmer: str,
    order_id: Optional[str] = None,
) -> None:
    """Buyurtmalardan bitta elementni olib tashlash."""
    item = None
    key_to_remove = None

    if order_id:
        item = _READY_SIZES_ORDERS.get(order_id)
        if item:
            key_to_remove = order_id
    else:
        code_norm = normalize_code(code)
        razmer_norm = normalize_razmer(razmer or "")
        for key, existing_item in _READY_SIZES_ORDERS.items():
            if existing_item.user_id != user_id:
                continue
            if (
                normalize_code(existing_item.code) == code_norm
                and normalize_razmer(existing_item.razmer or "") == razmer_norm
            ):
                item = existing_item
                key_to_remove = key
                break
    
    # LOG DELETED EVENT: Order item removed/canceled
    if item:
        from services.ready_sizes_events import log_deleted_event
        try:
            log_deleted_event(
                model_nomi=item.model_nomi,
                code=item.code,
                razmer=item.razmer,
                qty=item.qty,
                kolleksiya=item.kolleksiya,
                user_id=item.user_id,
                user_name=item.user_name,
                is_partner=item.is_partner,
                seller_name=item.seller_name,
                order_id=item.order_id or None,
            )
        except Exception:
            # Event logging should not break the main flow
            pass
    
    if key_to_remove:
        _READY_SIZES_ORDERS.pop(key_to_remove, None)


def get_order_items_for_admin_view() -> List[OrderItem]:
    """
    Admin panel → Tayyor razmerlar → Buyurtmalar uchun barcha elementlarni qaytarish.
    Har chaqirilganda 24 soatlik muddat tugaganlarini avtomatik tozalaydi.
    """
    _cleanup_expired_items()
    return list(_READY_SIZES_ORDERS.values())


def get_order_items_for_user(user_id: int) -> List[OrderItem]:
    """
    Faqat o'sha foydalanuvchining buyurtmalaridagi elementlarni qaytarish.
    Har chaqirilganda 24 soatlik muddat tugaganlarini avtomatik tozalaydi.
    """
    _cleanup_expired_items()
    user_items = []
    for item in _READY_SIZES_ORDERS.values():
        if item.user_id == user_id:
            user_items.append(item)
    return user_items


def get_order_items_by_role(user_id: int) -> List[OrderItem]:
    """
    Foydalanuvchi roliga qarab buyurtmalarni filtrlash.
    
    Role-based filtering:
    1) Oddiy user: faqat o'zi bergan buyurtmalar
    2) Hamkor (partner): faqat o'zi bergan buyurtmalar
    3) Sotuvchi: o'zi bergan + o'ziga biriktirilgan hamkorlar bergan buyurtmalar
    4) Admin: barcha buyurtmalar
    
    Returns:
        Filtered list of OrderItem based on user role
    """
    from services.admin_storage import is_seller, get_partners
    from services.admin_utils import is_admin, is_super_admin
    
    _cleanup_expired_items()
    
    # Admin: barcha buyurtmalar
    if is_admin(user_id) or is_super_admin(user_id):
        return list(_READY_SIZES_ORDERS.values())
    
    # Sotuvchi: o'zi bergan + o'ziga biriktirilgan hamkorlar bergan buyurtmalar
    if is_seller(user_id):
        seller_partners = get_partners(seller_id=user_id)
        partner_ids = set()
        for p in seller_partners:
            partner_id_str = p.get("partner_id")
            if partner_id_str:
                try:
                    partner_ids.add(int(partner_id_str))
                except (ValueError, TypeError):
                    continue
        
        result = []
        for item in _READY_SIZES_ORDERS.values():
            # O'zi bergan buyurtmalar
            if item.user_id == user_id:
                result.append(item)
            # O'ziga biriktirilgan hamkorlar bergan buyurtmalar
            elif item.user_id in partner_ids:
                result.append(item)
        return result
    
    # Hamkor va oddiy user: faqat o'zi bergan buyurtmalar
    user_items = []
    for item in _READY_SIZES_ORDERS.values():
        if item.user_id == user_id:
            user_items.append(item)
    return user_items


def is_in_order_any_user(code: str, razmer: str) -> bool:
    """
    Berilgan code+razmer hozirda istalgan foydalanuvchi buyurtmalarida bormi yoki yo'qmi.
    Tayyor razmerlar natijalari ostida status ko'rsatish uchun ishlatiladi.
    """
    _cleanup_expired_items()
    code_norm = normalize_code(code)
    razmer_norm = normalize_razmer(razmer or "")
    for item in _READY_SIZES_ORDERS.values():
        item_razmer_norm = normalize_razmer(item.razmer or "")
        if (
            normalize_code(item.code) == code_norm
            and item_razmer_norm == razmer_norm
        ):
            return True
    return False


def get_order_quantity_by_code(code: str, razmer: str) -> int:
    """
    Berilgan code+razmer bo'yicha buyurtmalarda nechta olinganini qaytaradi.
    Barcha foydalanuvchilarning buyurtmalarini hisoblaydi.
    
    MUHIM: Faqat aniq code+razmer juftligi uchun qaytaradi.
    Bir xil code bo'lgan, lekin turli razmer bo'lgan itemlar qo'shilmaydi.
    
    Returns:
        Buyurtmalarga olingan donalar soni (0 yoki ko'p)
    """
    _cleanup_expired_items()
    code_norm = normalize_code(code)
    razmer_norm = normalize_razmer(razmer or "")
    total_qty = 0
    
    for item in _READY_SIZES_ORDERS.values():
        item_razmer_norm = normalize_razmer(item.razmer or "")
        if (
            normalize_code(item.code) == code_norm
            and item_razmer_norm == razmer_norm
        ):
            total_qty += item.qty
    
    return total_qty


def build_order_owner_text(
    item: OrderItem,
) -> str:
    """
    Buyurtma elementi qaysi foydalanuvchi va qaysi sotuvchi/hamkor bilan bog'liqligini text ko'rinishida qaytarish.
    Foydalanuvchi rolini aniq ko'rsatadi.
    """
    from services.admin_storage import is_seller
    from services.admin_utils import is_admin, is_super_admin
    
    lines: List[str] = []
    
    # Foydalanuvchi identifikatori va roli
    # Agar yordamchi admin bo'lsa
    if is_admin(item.user_id) and not is_super_admin(item.user_id):
        admin_name = get_admin_name_storage(item.user_id)
        if admin_name and not admin_name.startswith("User"):
            lines.append(f"👤 Buyurtmaga olgan: {admin_name} (ID: {item.user_id})")
        else:
            lines.append(f"👤 Buyurtmaga olgan: {item.user_name} (ID: {item.user_id})")
        lines.append("🏷 Rol: Yordamchi admin")
    # Agar super admin bo'lsa
    elif is_super_admin(item.user_id):
        admin_name = get_super_admin_name(item.user_id)
        if admin_name and not admin_name.startswith("User"):
            lines.append(f"👤 Buyurtmaga olgan: {admin_name} (ID: {item.user_id})")
        else:
            lines.append(f"👤 Buyurtmaga olgan: {item.user_name} (ID: {item.user_id})")
        lines.append("🏷 Rol: Super admin")
    # Agar sotuvchi bo'lsa
    elif is_seller(item.user_id):
        seller_display = get_seller_name(item.user_id)
        lines.append(f"👤 Buyurtmaga olgan: {seller_display} (ID: {item.user_id})")
        lines.append("🏷 Rol: Sotuvchi")
    # Agar hamkor bo'lsa
    elif item.is_partner:
        user_display = item.user_name
        lines.append(f"👤 Buyurtmaga olgan: {user_display} (ID: {item.user_id})")
        lines.append("🏷 Rol: Hamkor")
        # Biriktirilgan sotuvchi
        if item.seller_id:
            seller_display = item.seller_name or get_seller_name(item.seller_id)
            lines.append(f"🤝 Biriktirilgan sotuvchi: {seller_display}")
    # Oddiy foydalanuvchi
    else:
        user_display = item.user_name
        lines.append(f"👤 Buyurtmaga olgan: {user_display} (ID: {item.user_id})")
        lines.append("🏷 Rol: Oddiy foydalanuvchi")

    return "\n".join(lines)


def detect_partner_and_seller(user_id: int, first_name: str = "", username: str = ""):
    """
    Foydalanuvchi hamkor, sotuvchi yoki oddiy user ekanligini aniqlash.
    - Agar admins.json → partners ichida 'partner_id' bo'lsa → hamkor.
    - Agar admins.json → sellers ichida bo'lsa → sotuvchi.
    - Aks holda oddiy foydalanuvchi.

    Returns:
        (is_partner: bool, seller_id: Optional[int], seller_name: Optional[str], user_display_name: str)
    """
    from services.admin_storage import is_seller
    
    # Default user display
    display_name = first_name or (f"@{username}" if username else f"User {user_id}")
    
    # Avval hamkor ekanligini tekshirish
    all_partners = get_partners()
    for p in all_partners:
        partner_id = p.get("partner_id")
        if str(partner_id) == str(user_id):
            seller_id_str = p.get("seller_id") or ""
            try:
                seller_id = int(seller_id_str)
            except Exception:
                seller_id = None
            seller_name = p.get("seller_name") or (get_seller_name(seller_id) if seller_id else None)
            # Hamkor bo'lsa, display_name ni ham yangilash
            partner_name = p.get("partner_name", "")
            if partner_name:
                display_name = partner_name
            return True, seller_id, seller_name, display_name

    # Hamkor emas, lekin sotuvchi bo'lishi mumkin
    if is_seller(user_id):
        seller_display = get_seller_name(user_id)
        if seller_display and not seller_display.startswith("User"):
            display_name = seller_display
        # Sotuvchi o'zi bo'lsa, seller_id = user_id
        return False, user_id, seller_display, display_name

    # Oddiy foydalanuvchi
    return False, None, None, display_name


def log_order_confirmed(item: OrderItem, confirmer_id: int = None, confirmer_name: str = None) -> None:
    """
    Log a confirmed event when an order is confirmed/sold.
    This should be called when admin confirms the order.
    
    Args:
        item: The OrderItem that was confirmed (buyer info)
        confirmer_id: User ID of admin who confirmed the order
        confirmer_name: Display name of admin who confirmed
    """
    from services.ready_sizes_events import log_confirmed_event
    
    try:
        log_confirmed_event(
            model_nomi=item.model_nomi,
            code=item.code,
            razmer=item.razmer,
            qty=item.qty,
            kolleksiya=item.kolleksiya,
            user_id=item.user_id,
            user_name=item.user_name,
            is_partner=item.is_partner,
            seller_name=item.seller_name,
            order_id=item.order_id or None,
            confirmer_id=confirmer_id,
            confirmer_name=confirmer_name,
        )
    except Exception:
        # Event logging should not break the main flow
        pass

