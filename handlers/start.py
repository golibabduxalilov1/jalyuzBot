# handlers/start.py

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from services.google_sheet import CACHE, GoogleSheetService
from services.product_utils import normalize_code, normalize_razmer
from services.cart_service import (
    get_or_create_cart_item,
    remove_cart_item,
    get_cart_items_for_admin_view,
    get_cart_items_for_user,
    detect_partner_and_seller,
    get_cart_quantity_by_code,
    get_cart_item_by_id,
)
from services.order_service import (
    get_or_create_order_item,
    remove_order_item,
    get_order_items_for_admin_view,
    get_order_items_for_user,
    get_order_items_by_role,
    detect_partner_and_seller as detect_partner_and_seller_order,
    get_order_quantity_by_code,
)
from services.admin_utils import is_admin, is_super_admin
from services.message_utils import cleanup_all_bot_messages, store_main_menu_message, track_bot_message
from services.admin_storage import is_seller, get_super_admins, get_admins, get_admins

router = Router()


class SizeSearchStates(StatesGroup):
    waiting_for_size = State()


def _format_razmer_for_barcha(razmer: str) -> str:
    """
    Tayyor razmerlar bo'limidagi "Barcha razmerlar" ro'yxati uchun
    razmer matnini yagona formatga keltiradi: 1.40×2.00
    """
    text = (razmer or "").strip()
    if not text:
        return "Noma'lum"

    # Vergulni nuqtaga almashtirish
    text = text.replace(",", ".")

    # Ajratuvchilarni tekshirish: x, X, *, ×
    for sep in ("×", "x", "X", "*"):
        if sep in text:
            parts = text.split(sep)
            if len(parts) == 2:
                left = parts[0].strip()
                right = parts[1].strip()
                if left and right:
                    return f"{left}×{right}"

    # Agar hech biri topilmasa, faqat vergul → nuqta almashtirilgan matn qaytariladi
    return text


def _decrement_ready_size_stock(code: str, razmer: str, qty_to_subtract: int) -> bool:
    """
    Tayyor razmerlar ro'yxatidan (CACHE['sheets6']) berilgan code+razmer bo'yicha
    BUYURTMA MIQDORIGA teng miqdorda ayrish.
    
    Asosiy qoida:
        QOLDIQ_YANGI = QOLDIQ_ESKI − BUYURTMA_MIqdORI
    
    MUHIM:
    - ESKI_QOLDIQ faqat CACHE['sheets6'] dagi real DB stock qiymatidan olinadi
    - UI'da ko'rsatilgan Q qiymati umuman ishlatilmaydi
    - BUYURTMA_MIQDORI faqat qty_to_subtract parametridan olinadi
    
    - Agar QOLDIQ_YANGI > 0 bo'lsa → yangilangan qoldiq yoziladi
    - Agar QOLDIQ_YANGI <= 0 bo'lsa → element ro'yxatdan butunlay o'chiriladi
    
    Returns:
        True agar muvaffaqiyatli o'zgartirilgan bo'lsa, aks holda False.
    """
    from services.product_utils import normalize_razmer
    
    # Noto'g'ri yoki 0 bo'lgan buyurtma miqdori bilan ishlamaymiz
    try:
        qty_int = int(qty_to_subtract)
    except (ValueError, TypeError):
        print(f"[DELETE_DEBUG] step=_decrement_ready_size_stock_INVALID_QTY, code={code}, size={razmer}, qty={qty_to_subtract}")
        return False
    if qty_int <= 0:
        print(f"[DELETE_DEBUG] step=_decrement_ready_size_stock_INVALID_QTY, code={code}, size={razmer}, qty={qty_to_subtract}")
        return False

    # MUHIM: CACHE dan to'g'ridan-to'g'ri reference olish kerak
    # CACHE.get() yangi list qaytarmasligi uchun tekshiramiz
    if "sheets6" not in CACHE:
        print(f"[DELETE_DEBUG] step=_decrement_ready_size_stock_NO_CACHE, code={code}, size={razmer}, qty={qty_int}")
        return False
    
    sheets6 = CACHE["sheets6"]
    if not sheets6:
        print(f"[DELETE_DEBUG] step=_decrement_ready_size_stock_EMPTY_CACHE, code={code}, size={razmer}, qty={qty_int}")
        return False
    
    code_norm = normalize_code(code)
    razmer_norm = normalize_razmer(razmer or "")
    
    # Teskari tartibda o'qish, chunki o'chirishda indekslar o'zgaradi
    for idx in range(len(sheets6) - 1, -1, -1):
        rec = sheets6[idx]
        rec_code = (rec.get("code") or "").strip()
        rec_razmer = (rec.get("razmer") or "").strip()
        if (
            normalize_code(rec_code) != code_norm
            or normalize_razmer(rec_razmer) != razmer_norm
        ):
            continue
        
        # ESKI_QOLDIQ ni faqat CACHE['sheets6'] dagi real DB stock qiymatidan olish
        # UI'da ko'rsatilgan Q qiymati umuman ishlatilmaydi
        # MUHIM: Barcha mumkin bo'lgan maydonlarni tekshirib, birinchi mavjud va to'ldirilgan qiymatni olish
        raw = None
        raw_str = ""
        # Barcha mumkin bo'lgan maydonlarni ketma-ket tekshirish
        for field_name in ["shtuk", "Shtuk", "soni", "Soni", "miqdor"]:
            field_value = rec.get(field_name)
            if field_value is not None and str(field_value).strip():
                raw = field_value
                raw_str = str(field_value).strip()
                break
        
        if not raw_str:
            raw_str = ""
        
        try:
            # Real DB stock qiymatini olish - faqat CACHE['sheets6'] dagi qiymatdan
            current_qty = int(float(raw_str.replace(",", ".").replace(" ", ""))) if raw_str else 0
        except (ValueError, TypeError):
            current_qty = 0

        # Yangi qoldiqni hisoblash: QOLDIQ_YANGI = QOLDIQ_ESKI - BUYURTMA_MIqdORI
        # BUYURTMA_MIQDORI = qty_int (qty_to_subtract parametridan)
        # ESKI_QOLDIQ = current_qty (CACHE['sheets6'] dagi real DB stock)
        new_qty = current_qty - qty_int
        
        print(f"[DELETE_DEBUG] step=_decrement_ready_size_stock_UPDATE, code={code}, size={razmer}, qty={qty_int}, oldQ={current_qty}, newQ={new_qty}")

        if new_qty <= 0:
            # BUYURTMA_MIQDORI >= ESKI_QOLDIQ bo'lsa → mahsulot tayyor razmerlar ro'yxatidan BUTUNLAY O'CHIRILSIN
            try:
                sheets6.pop(idx)
            except (IndexError, ValueError):
                pass
        else:
            # BUYURTMA_MIQDORI < ESKI_QOLDIQ bo'lsa → qoldiq to'g'ri kamaytirilib yangilansin
            # MUHIM: Barcha mavjud stock maydonlarini yangilash (faqat bittasini emas)
            # Bu qoldiqni barcha joylarda bir xil saqlaydi
            if "shtuk" in rec:
                rec["shtuk"] = str(new_qty)
            if "Shtuk" in rec:
                rec["Shtuk"] = str(new_qty)
            if "soni" in rec:
                rec["soni"] = str(new_qty)
            if "Soni" in rec:
                rec["Soni"] = str(new_qty)
            if "miqdor" in rec:
                rec["miqdor"] = str(new_qty)
        # CACHE dagi ro'yxat in-place o'zgartirilgan bo'ladi
        return True
    
    print(f"[DELETE_DEBUG] step=_decrement_ready_size_stock_NOT_FOUND, code={code}, size={razmer}, qty={qty_int}")
    return False


def _increment_ready_size_stock(code: str, razmer: str, qty_to_add: int) -> bool:
    """
    Tayyor razmerlar ro'yxatidagi (CACHE['sheets6']) berilgan code+razmer uchun
    QOLDIQ ga qty_to_add miqdorda QO'SHISH.

    Asosiy formula:
        Q_YANGI = Q_ESKI + qty_to_add

    MUHIM:
    - qty_to_add har doim int bo'lishi kerak va > 0 bo'lishi kerak
    - Agar mahsulot topilmasa → hech qanday xato chiqarilmaydi (False qaytadi)
    - Agar natijada Q_YANGI > 0 bo'lsa → stock yangilanadi
    """
    from services.product_utils import normalize_razmer
    
    # Noto'g'ri yoki 0 bo'lgan qty bilan ishlamaymiz
    try:
        qty_int = int(qty_to_add)
    except (ValueError, TypeError):
        print(f"[DELETE_DEBUG] step=_increment_ready_size_stock_INVALID_QTY, code={code}, size={razmer}, qty={qty_to_add}")
        return False
    if qty_int <= 0:
        print(f"[DELETE_DEBUG] step=_increment_ready_size_stock_INVALID_QTY, code={code}, size={razmer}, qty={qty_to_add}")
        return False

    if "sheets6" not in CACHE:
        print(f"[DELETE_DEBUG] step=_increment_ready_size_stock_NO_CACHE, code={code}, size={razmer}, qty={qty_int}")
        return False

    sheets6 = CACHE["sheets6"]
    if not sheets6:
        print(f"[DELETE_DEBUG] step=_increment_ready_size_stock_EMPTY_CACHE, code={code}, size={razmer}, qty={qty_int}")
        return False

    code_norm = normalize_code(code)
    razmer_norm = normalize_razmer(razmer or "")

    # Teskari tartibda o'qish, chunki kelajakda o'chirish bo'lishi mumkin
    for idx in range(len(sheets6) - 1, -1, -1):
        rec = sheets6[idx]
        rec_code = (rec.get("code") or "").strip()
        rec_razmer = (rec.get("razmer") or "").strip()
        if (
            normalize_code(rec_code) != code_norm
            or normalize_razmer(rec_razmer) != razmer_norm
        ):
            continue

        raw = (
            rec.get("shtuk")
            or rec.get("Shtuk")
            or rec.get("soni")
            or rec.get("Soni")
            or rec.get("miqdor")
            or ""
        )
        raw_str = str(raw).strip()
        try:
            current_qty = int(float(raw_str.replace(",", ".").replace(" ", ""))) if raw_str else 0
        except (ValueError, TypeError):
            current_qty = 0

        # Q_YANGI = Q_ESKI + qty_int
        new_qty = current_qty + qty_int
        
        print(f"[DELETE_DEBUG] step=_increment_ready_size_stock_UPDATE, code={code}, size={razmer}, qty={qty_int}, oldQ={current_qty}, newQ={new_qty}")

        if new_qty <= 0:
            # Teoretik holat, amalda bo'lmasligi kerak, lekin xavfsizlik uchun
            try:
                sheets6.pop(idx)
            except (IndexError, ValueError):
                pass
        else:
            # MUHIM: Barcha mavjud stock maydonlarini yangilash (faqat bittasini emas)
            # Bu qoldiqni barcha joylarda bir xil saqlaydi va double increment bug ni oldini oladi
            if "shtuk" in rec:
                rec["shtuk"] = str(new_qty)
            if "Shtuk" in rec:
                rec["Shtuk"] = str(new_qty)
            if "soni" in rec:
                rec["soni"] = str(new_qty)
            elif "Soni" in rec:
                rec["Soni"] = str(new_qty)
            elif "miqdor" in rec:
                rec["miqdor"] = str(new_qty)

        # CACHE dagi ro'yxat in-place o'zgartirilgan bo'ladi
        return True

    # Mahsulot topilmasa ham xatolik chiqarilmaydi
    print(f"[DELETE_DEBUG] step=_increment_ready_size_stock_NOT_FOUND, code={code}, size={razmer}, qty={qty_int}")
    return False


def _remove_single_order_any_user(code: str, razmer: str, confirmer_id: int = None, confirmer_name: str = None) -> bool:
    """
    Berilgan code+razmer bo'yicha RAM ichidagi buyurtmalardan BIR DONA elementni topadi,
    shu elementning BUYURTMA MIQDORI ga teng miqdorda tayyor razmer stokidan ayradi
    va buyurtmani o'chiradi.
    
    Args:
        code: Model code
        razmer: Size
        confirmer_id: User ID of admin who confirmed the order
        confirmer_name: Display name of admin who confirmed
    
    ASOSIY QOIDA:
        QOLDIQ_YANGI = QOLDIQ_ESKI - BUYURTMA_MIqdORI
    
    Returns:
        True - agar biror buyurtma topilib, stok va buyurtma muvaffaqiyatli yangilangan bo'lsa,
        aks holda False.
    """
    from services.order_service import get_order_items_for_admin_view, remove_order_item
    from services.product_utils import normalize_razmer

    print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_START, code={code}, size={razmer}")
    
    all_items = get_order_items_for_admin_view()
    if not all_items:
        print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_NOT_FOUND, code={code}, size={razmer}")
        return False

    code_norm = normalize_code(code)
    razmer_norm = normalize_razmer(razmer or "")

    for item in all_items:
        if (
            normalize_code(item.code) == code_norm
            and normalize_razmer(item.razmer or "") == razmer_norm
        ):
            # 1) Buyurtma miqdorini TO'G'RI olish va tekshirish
            # item.qty int tipida bo'lishi kerak (OrderItem dataclassida qty: int)
            # MUHIM: qty HAR DOIM real buyurtma obyektidan, integer ko'rinishida, to'liq qiymati bilan olinadi
            # Hech qachon string slice ([-1]), limit (1-5), yoki default 1 ishlatilmaydi
            # 6, 7, 10, 15 va boshqa barcha qiymatlar to'liq olinishi shart
            order_qty = 0
            try:
                # Avval to'g'ridan-to'g'ri int bo'lsa (eng keng tarqalgan holat)
                if isinstance(item.qty, int):
                    order_qty = item.qty
                # Agar str bo'lsa, to'liq stringni tozalash va int ga o'tkazish (6, 7, 10, 15 ham ishlashi kerak)
                elif isinstance(item.qty, str):
                    # MUHIM: To'liq stringni olish - hech qachon slice yoki limit ishlatilmaydi
                    # Masalan: "6" → 6, "10" → 10, "15" → 15
                    cleaned = item.qty.replace(",", ".").replace(" ", "").strip()
                    if cleaned:
                        order_qty = int(float(cleaned))
                    else:
                        order_qty = 0
                # Boshqa holatda (float, None, va h.k.) str ga o'tkazib, keyin int ga
                else:
                    if item.qty is not None:
                        cleaned = str(item.qty).replace(",", ".").replace(" ", "").strip()
                        if cleaned:
                            order_qty = int(float(cleaned))
                        else:
                            order_qty = 0
                    else:
                        order_qty = 0
            except (ValueError, TypeError, AttributeError):
                # Xatolik bo'lsa, 0 ga teng qilib qo'yamiz
                order_qty = 0

            # 2) Agar miqdor 0 yoki manfiy bo'lsa, bu noto'g'ri buyurtma
            # Buyurtmani o'chirish kerak, lekin stokdan hech narsa ayirmaymiz
            if order_qty <= 0:
                # Noto'g'ri miqdor, buyurtmani o'chirish
                print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_INVALID_QTY, code={code}, size={razmer}, qty={order_qty}")
                try:
                    remove_order_item(item.user_id, item.code, item.razmer)
                except Exception:
                    pass
                return False

            # 3) Stokdan BUYURTMA MIQDORI ga teng miqdorda ayrish
            # QOIDA: QOLDIQ_YANGI = QOLDIQ_ESKI - BUYURTMA_MIqdORI
            # Bu yerda order_qty har doim > 0 bo'ladi va BUYURTMA MIQDORI ni ifodalaydi
            # MUHIM: order_qty buyurtma miqdorini ifodalaydi, hech qachon 1 ga teng qilib qo'yilmaydi
            print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_BEFORE_decrement, code={code}, size={razmer}, qty={order_qty}")
            stock_decremented = _decrement_ready_size_stock(item.code, item.razmer, order_qty)
            print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_AFTER_decrement, code={code}, size={razmer}, qty={order_qty}, result={stock_decremented}")
            
            if not stock_decremented:
                # Stok yangilanmadi, lekin buyurtmani o'chirish kerak
                pass

            # 4) LOG CONFIRMED EVENT: Buyurtma tasdiqlandi
            print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_LOG_EVENT, code={code}, size={razmer}, qty={order_qty}")
            try:
                from services.order_service import log_order_confirmed
                log_order_confirmed(item, confirmer_id=confirmer_id, confirmer_name=confirmer_name)
            except Exception as e:
                print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_LOG_ERROR: {e}")
                pass
            
            # 5) Shu buyurtmani RAM dan o'chirish
            print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_BEFORE_remove, code={code}, size={razmer}, qty={order_qty}")
            try:
                remove_order_item(item.user_id, item.code, item.razmer)
            except Exception:
                pass
            print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_AFTER_remove, code={code}, size={razmer}, qty={order_qty}")

            print(f"[DELETE_DEBUG] step=_remove_single_order_any_user_OK, code={code}, size={razmer}, qty={order_qty}")
            return True

    return False


def _cancel_single_order_any_user(code: str, razmer: str) -> str:
    """
    Berilgan code+razmer bo'yicha RAM ichidagi buyurtmalardan BIR DONA elementni topadi
    va buyurtmani o'chiradi.

    DIQQAT:
    - Stok (sheets6 / CACHE['sheets6']) bu yerda UMUMAN o'zgartirilmaydi.
    - Ekrandagi Q allaqachon `total_qty - cart_qty - order_qty` formulasi bilan hisoblanadi.
      Shuning uchun buyurtmani o'chirganimizda faqat order ro'yxatidan o'chirsak kifoya.
    - Double increment bug ni oldini olish uchun:
      1) Faqat birinchi mos elementni qaytaradi (return "ok" dan keyin loop to'xtaydi)
      2) Item o'chirilganini tekshiradi; agar callback ikki marta bosilsa, stok baribir o'zgarmaydi.
    """
    from services.order_service import get_order_items_for_admin_view, remove_order_item as _remove_order_item_any
    print(f"[DELETE_DEBUG] step=_cancel_single_order_any_user_START, code={code}, size={razmer}")

    all_items = get_order_items_for_admin_view()
    if not all_items:
        print(f"[DELETE_DEBUG] step=_cancel_single_order_any_user_NOT_FOUND, code={code}, size={razmer}")
        return "not_found"
    from services.product_utils import normalize_razmer

    code_norm = normalize_code(code)
    razmer_norm = normalize_razmer(razmer or "")
    # MUHIM: Faqat BIRINCHI mos elementni qaytaradi
    for item in all_items:
        if (
            normalize_code(item.code) == code_norm
            and normalize_razmer(item.razmer or "") == razmer_norm
        ):
            # qty ni to'g'ri olish (faqat log uchun, stokni o'zgartirishga ishlatmaymiz)
            order_qty = 0
            try:
                if isinstance(item.qty, int):
                    order_qty = item.qty
                elif isinstance(item.qty, str):
                    cleaned = item.qty.replace(",", ".").replace(" ", "").strip()
                    if cleaned:
                        order_qty = int(float(cleaned))
                    else:
                        order_qty = 0
                else:
                    if item.qty is not None:
                        cleaned = str(item.qty).replace(",", ".").replace(" ", "").strip()
                        if cleaned:
                            order_qty = int(float(cleaned))
                        else:
                            order_qty = 0
                    else:
                        order_qty = 0
            except (ValueError, TypeError, AttributeError):
                order_qty = 0
            if order_qty <= 0:
                print(f"[DELETE_DEBUG] step=_cancel_single_order_any_user_INVALID_QTY, code={code}, size={razmer}, qty={order_qty}")
                return "invalid_qty"
            # MUHIM: Item ni o'chirishdan OLDIN uni saqlaymiz
            item_user_id = item.user_id
            item_code = item.code
            item_razmer = item.razmer

            print(f"[DELETE_DEBUG] step=_cancel_single_order_any_user_BEFORE_remove, code={code}, size={razmer}, qty={order_qty}")

            # Buyurtmani RAM dan o'chiramiz
            _remove_order_item_any(item_user_id, item_code, item_razmer)

            print(f"[DELETE_DEBUG] step=_cancel_single_order_any_user_AFTER_remove, code={code}, size={razmer}, qty={order_qty}")

            # Double-call tekshiruvi (stok baribir o'zgarmaydi, faqat diagnostika uchun)
            all_items_after = get_order_items_for_admin_view()
            item_still_exists = False
            for check_item in all_items_after:
                if (
                    normalize_code(check_item.code) == code_norm
                    and normalize_razmer(check_item.razmer or "") == razmer_norm
                    and check_item.user_id == item_user_id
                ):
                    item_still_exists = True
                    break

            if item_still_exists:
                print(f"[DELETE_DEBUG] step=_cancel_single_order_any_user_ITEM_STILL_EXISTS, code={code}, size={razmer}, qty={order_qty}, SKIPPING_STOCK_CHANGE")
                return "ok"

            # STOKNI O'ZGARTIRMAYMIZ – faqat buyurtma o'chirildi
            print(f"[DELETE_DEBUG] step=_cancel_single_order_any_user_OK_NO_STOCK_CHANGE, code={code}, size={razmer}, qty={order_qty}")
            return "ok"
    return "not_found"


def _cancel_single_cart_any_user(code: str, razmer: str, cart_id: str = "") -> str:
    """
    Berilgan code+razmer bo'yicha RAM ichidagi karzinka elementlaridan BIR DONA elementni topadi
    va karzinka elementini o'chiradi.

    DIQQAT:
    - Stok (sheets6 / CACHE['sheets6']) bu yerda UMUMAN o'zgartirilmaydi.
    - Ekrandagi Q `total_qty - cart_qty - order_qty` asosida qayta hisoblanadi.
    - Double increment bug ni oldini olish uchun:
      1) Faqat birinchi mos elementni qaytaradi
      2) Item o'chirilganini tekshiradi; callback ikki marta bosilsa ham stok o'zgarmaydi.
    """
    from services.cart_service import get_cart_items_for_admin_view, remove_cart_item as _remove_cart_item_any
    print(f"[DELETE_DEBUG] step=_cancel_single_cart_any_user_START, code={code}, size={razmer}, cart_id={cart_id}")

    all_items = get_cart_items_for_admin_view()
    if not all_items:
        print(f"[DELETE_DEBUG] step=_cancel_single_cart_any_user_NOT_FOUND, code={code}, size={razmer}")
        return "not_found"
    from services.product_utils import normalize_razmer
    code_norm = normalize_code(code) if code else ""
    razmer_norm = normalize_razmer(razmer or "") if razmer else ""
    # MUHIM: Faqat BIRINCHI mos elementni qaytaradi
    for item in all_items:
        item_cart_id = getattr(item, "cart_id", "") or ""
        matches = False
        if cart_id:
            matches = item_cart_id == cart_id
        else:
            matches = (
                normalize_code(item.code) == code_norm
                and normalize_razmer(item.razmer or "") == razmer_norm
            )

        if matches:
            cart_qty = 0
            try:
                if isinstance(item.qty, int):
                    cart_qty = item.qty
                elif isinstance(item.qty, str):
                    cleaned = item.qty.replace(",", ".").replace(" ", "").strip()
                    if cleaned:
                        cart_qty = int(float(cleaned))
                    else:
                        cart_qty = 0
                else:
                    if item.qty is not None:
                        cleaned = str(item.qty).replace(",", ".").replace(" ", "").strip()
                        if cleaned:
                            cart_qty = int(float(cleaned))
                        else:
                            cart_qty = 0
                    else:
                        cart_qty = 0
            except (ValueError, TypeError, AttributeError):
                cart_qty = 0
            if cart_qty <= 0:
                print(f"[DELETE_DEBUG] step=_cancel_single_cart_any_user_INVALID_QTY, code={code}, size={razmer}, qty={cart_qty}")
                return "invalid_qty"
            item_user_id = item.user_id
            item_code = item.code
            item_razmer = item.razmer

            print(f"[DELETE_DEBUG] step=_cancel_single_cart_any_user_BEFORE_remove, code={code}, size={razmer}, qty={cart_qty}")

            # Karzinka elementini RAM dan o'chiramiz
            _remove_cart_item_any(item_user_id, item_code, item_razmer, cart_id=item_cart_id or None)

            print(f"[DELETE_DEBUG] step=_cancel_single_cart_any_user_AFTER_remove, code={code}, size={razmer}, qty={cart_qty}")

            # Double-call tekshiruvi (diagnostika)
            all_items_after = get_cart_items_for_admin_view()
            item_still_exists = False
            for check_item in all_items_after:
                if cart_id:
                    if (getattr(check_item, "cart_id", "") or "") == cart_id:
                        item_still_exists = True
                        break
                else:
                    if (
                        normalize_code(check_item.code) == code_norm
                        and normalize_razmer(check_item.razmer or "") == razmer_norm
                        and check_item.user_id == item_user_id
                    ):
                        item_still_exists = True
                        break

            if item_still_exists:
                print(f"[DELETE_DEBUG] step=_cancel_single_cart_any_user_ITEM_STILL_EXISTS, code={code}, size={razmer}, qty={cart_qty}, SKIPPING_STOCK_CHANGE")
                return "ok"

            # STOKNI O'ZGARTIRMAYMIZ – faqat karzinka elementi o'chirildi
            print(f"[DELETE_DEBUG] step=_cancel_single_cart_any_user_OK_NO_STOCK_CHANGE, code={code}, size={razmer}, qty={cart_qty}")
            return "ok"
    return "not_found"

def make_main_menu_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    """Create main menu keyboard"""
    from services.admin_storage import is_seller, has_price_access, has_discount_access, has_ready_sizes_store_access
    from services.admin_utils import is_admin, is_super_admin
    
    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text="🔹Astatka",
                callback_data="menu_astatka"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔹AI generatsiya",
                callback_data="menu_ai_generate"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔹Yordam",
                callback_data="menu_questions"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔹Modellar katalogi",
                callback_data="menu_model_images"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔹tayyor razmer ",
                callback_data="ready_sizes"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔹ID olish",
                callback_data="id_get"
            )
        ]
    ]
    
    # Admin va ruxsatli foydalanuvchilar uchun qo'shimcha tugmalar
    if user_id is not None:
        is_super = is_super_admin(user_id)
        is_adm = is_admin(user_id)
        is_any_admin = is_super or is_adm
        is_seller_user = is_seller(user_id)
        has_price = has_price_access(user_id)
        has_discount = has_discount_access(user_id)
        has_store_access = has_ready_sizes_store_access(user_id)
        
        # Magazindagi tayyor razmerlar - faqat ruxsatli userlar uchun
        if is_any_admin or has_store_access:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="🔹Magazindagi tayyor razmerlar",
                    callback_data="ready_sizes_menu"
                )
            ])
        
        # Skidkaga tushgan modellar - barcha userlarga ochiq
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="🔹Chegirmadagi modellar",
                callback_data="prices_discount"
            )
        ])
        
        # Modellar narxini bilish
        if is_any_admin or has_price:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="🔹Narxlar",
                    callback_data="menu_model_prices"
                )
            ])
        
        # Admin panel - adminlar va sotuvchilar uchun
        if is_any_admin or is_seller_user:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="🔹Admin panel",
                    callback_data="admin_panel"
                )
            ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


def make_ready_sizes_menu_keyboard() -> InlineKeyboardMarkup:
    """Create ready sizes menu keyboard"""
    keyboard_buttons = [
        # VAQTINCHA YASHIRILGAN: "Razmer bo'yicha qidirish" tugmasi
        # Keyinchalik qayta yoqish uchun comment ni olib tashlash kifoya
        # [
        #     InlineKeyboardButton(
        #         text="🔍 Razmer bo'yicha qidirish",
        #         callback_data="ready_size_search"
        #     )
        # ],
        [
            InlineKeyboardButton(
                text="📋 Barcha razmerlar",
                callback_data="ready_size_all"
            )
        ],
        [
            InlineKeyboardButton(
                text="🧺 Karzinka",
                callback_data="open_ready_sizes_cart"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Xisobot",
                callback_data="my_ready_sizes_report"
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡ Buyurtmalar",
                callback_data="ready_sizes_orders_soon"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="back_to_main_menu"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


def make_admin_ready_sizes_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Create ready sizes menu keyboard for admin users.
    Mavjud tugmalar o'zgarmaydi, faqat qo'shimcha:
    - ⚡ Buyurtmalar (barcha foydalanuvchilar uchun, endi make_ready_sizes_menu_keyboard da mavjud)
    """
    # Endi "⚡ Buyurtmalar" tugmasi barcha foydalanuvchilar uchun make_ready_sizes_menu_keyboard da mavjud
    # Admin uchun alohida qo'shish shart emas
    keyboard = make_ready_sizes_menu_keyboard()
    return keyboard

@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
    """
    /start command handler
    Shows the main menu
    """
    chat_id = message.chat.id

    # 1) Foydalanuvchining /start xabarini darhol o'chirish (agar imkon bo'lsa)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception:
        # Xatolar e'tiborsiz qoldiriladi (message not found, va hokazo)
        pass

    # 2) Shu chat uchun bot yuborgan avvalgi xabarlarni tozalash
    await cleanup_all_bot_messages(bot, chat_id)

    # 3) Yangi asosiy menyuni toza xabar sifatida yuborish
    user_id = message.from_user.id
    keyboard = make_main_menu_keyboard(user_id)
    sent = await message.answer(
        "Assalomu alaykum.\n"
        "Tizimga xush kelibsiz.\n\n"
        "Quyidagi bo‘limlardan birini tanlang:",
        reply_markup=keyboard
    )

    # 4) Yangi asosiy menyu xabarini keyingi /start lar uchun track qilish
    store_main_menu_message(chat_id, sent.message_id)


@router.callback_query(F.data == "back_to_main_menu")
async def callback_back_to_main_menu(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Back to main menu button - TAYYOR RAZMERLAR bo'limi uchun.
    Bu handler o'zgartirilmaydi, chunki tayyor razmerlar bo'limi uchun maxsus.
    """
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # Clear any active state
    await state.clear()
    
    user_id = callback_query.from_user.id
    keyboard = make_main_menu_keyboard(user_id)
    try:
        await callback_query.message.edit_text(
            "Assalomu alaykum.\n"
            "Tizimga xush kelibsiz.\n\n"
            "Quyidagi bo'limlardan birini tanlang:",
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        # Agar edit ishlamasa (masalan, xabar o'zgarmagan bo'lsa), yangi xabar yuborish
        await callback_query.message.answer(
            "Assalomu alaykum.\n"
            "Tizimga xush kelibsiz.\n\n"
            "Quyidagi bo'limlardan birini tanlang:",
            reply_markup=keyboard
        )


# YANGI: Umumiy menu_main handler - barcha joylar uchun (tayyor razmerlar tashqari)
@router.callback_query(F.data == "menu_main")
async def callback_menu_main(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Umumiy asosiy menyu handler - barcha joylardan asosiy menyuga qaytish uchun.
    Tayyor razmerlar bo'limidan tashqari barcha joylar uchun ishlatiladi.
    DELETE ishlatib yangi asosiy menyu xabarini yuboradi.
    """
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    # State ni tozalash
    try:
        await state.clear()
    except Exception:
        pass
    
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    # Asosiy menyu keyboard va matn (yangi matn)
    keyboard = make_main_menu_keyboard(user_id)
    menu_text = (
        "Assalomu alaykum! TIZIMGA  xush kelibsiz.\n\n"
        "Quyidagi menyulardan birini tanlang:"
    )
    
    # Joriy xabarni o'chirish va yangi asosiy menyu xabarini yuborish
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=callback_query.message.message_id
        )
    except Exception:
        pass
    
    # Yangi asosiy menyu xabarini yuborish
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        store_main_menu_message(chat_id, sent.message_id)
    except Exception:
        pass


@router.callback_query(F.data == "ready_sizes")
async def callback_ready_sizes(callback_query: CallbackQuery, bot: Bot):
    """Tayyor razmerlar section entry point"""
    user_id = callback_query.from_user.id
    
    # Admin uchun buyurtmalar tugmasi ko'rsatiladi
    if is_admin(user_id) or is_super_admin(user_id):
        keyboard = make_admin_ready_sizes_menu_keyboard()
    else:
        keyboard = make_ready_sizes_menu_keyboard()
    
    try:
        await callback_query.message.edit_text(
            "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:",
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        await callback_query.message.answer(
            "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:",
            reply_markup=keyboard
        )
    await callback_query.answer()


@router.callback_query(F.data == "ready_size_all")
async def callback_ready_size_all(callback_query: CallbackQuery, state: FSMContext):
    """
    Show all ready sizes grouped by model/collection
    Uses inline keyboard for code selection
    """
    from services.google_sheet import CACHE
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    sheets6_data = CACHE.get("sheets6", [])
    if not sheets6_data:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=True)
        return
    
    # Group by (model_nomi, kolleksiya)
    grouped = {}
    for item in sheets6_data:
        model = item.get("model_nomi", "").strip() or "Noma'lum"
        kolleksiya = item.get("kolleksiya", "").strip() or ""
        key = (model, kolleksiya)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)
    
    # Build text list
    lines = ["📋 Barcha razmerlar:\n"]
    for (model, kolleksiya), items in grouped.items():
        display_model = (model or "").strip() or "Noma'lum"
        if display_model.lower() == "xitoy kombo":
            display_model = "X.Kombo"
        header = f"🪟 {display_model}"
        if kolleksiya:
            header += f" / {kolleksiya}"
        lines.append(header)
        
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or "Noma'lum"
            shtuk_raw = item.get("shtuk", "").strip() or ""
            
            # Jami sonni int ga o'tkazish
            try:
                total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
            except Exception:
                total_qty = 0
            
            # Karzinkaga olingan sonni olish
            cart_qty = get_cart_quantity_by_code(code, razmer)
            
            # Buyurtmaga olingan sonni olish
            order_qty = get_order_quantity_by_code(code, razmer)
            
            # Formatlash: KOD → RAZMER (SON)
            size_text = _format_razmer_for_barcha(razmer)
            line = f"{code} \u2192 {size_text} ({total_qty})"
            
            # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
            remaining_count = total_qty - cart_qty - order_qty
            remaining_count = max(remaining_count, 0)
            
            # Agar karzinkaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if cart_qty > 0:
                line += f" 🧺K:{cart_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if order_qty > 0:
                line += f" ⚡B:{order_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            lines.append(line)
        
        lines.append("")  # Bo'sh qator har bir guruhdan keyin
    
    result_text = "\n".join(lines).strip()
    
    # Inline keyboard for code selection - TEXT ro'yxat tartibida yaratish
    inline_buttons = []
    row = []
    seen = set()  # Unique kodlarni tekshirish uchun
    # grouped.items() tartibida kodlarni yig'ish (text ro'yxat bilan bir xil tartib)
    for (model, kolleksiya), items in grouped.items():
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or ""
            if not code:
                continue
            # Unique kodlarni tekshirish (tartibni saqlab)
            code_key = f"{code}|{razmer}"
            if code_key not in seen:
                seen.add(code_key)
                # 3 ta kod bir qatorda
                row.append(
                    InlineKeyboardButton(
                        text=code,
                        callback_data=f"ready_size_code:{code}|{razmer}"
                    )
                )
                if len(row) == 3:
                    inline_buttons.append(row)
                    row = []
    
    if row:
        inline_buttons.append(row)
    
    # "Barchasini ko'rish" va "Orqaga" tugmalari alohida qatorlarda
    inline_buttons.append([
        InlineKeyboardButton(
            text="🖼 Barchasini ko'rish",
            callback_data="ready_size_view_all"
        )
    ])
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="ready_sizes"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)
    
    # Send or edit message
    try:
        await callback_query.message.edit_text(
            result_text,
            reply_markup=keyboard
        )
    except TelegramBadRequest:
        # Agar edit ishlamasa, yangi xabar yuborish
        await callback_query.message.answer(
            result_text,
            reply_markup=keyboard
        )
    
    # Save message ID for potential future cleanup
    await state.update_data(ready_size_list_message_id=callback_query.message.message_id)
    await callback_query.answer()


@router.callback_query(F.data == "ready_size_view_all")
async def callback_ready_size_view_all(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Show all ready sizes with images (each as a separate message)
    """
    from services.google_sheet import CACHE, GoogleSheetService
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    sheets6_data = CACHE.get("sheets6", [])
    if not sheets6_data:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=True)
        return
    
    # 1) OLDINGI RO'YXAT XABARINI DELETE qilish
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except Exception:
        pass
    
    chat_id = callback_query.message.chat.id
    sheet_service = GoogleSheetService()
    
    # Track all message IDs sent in this "view all" page
    view_all_message_ids = []
    
    # 2) BITTA-BITTA xabar yuborish (har bir mahsulot uchun)
    for item in sheets6_data:
        code = item.get("code", "").strip()
        if not code:
            continue
        
        razmer = item.get("razmer", "").strip() or "Noma'lum"
        kolleksiya = item.get("kolleksiya", "").strip() or "Noma'lum"
        model_nomi = item.get("model_nomi", "").strip() or "Noma'lum"
        image_url = item.get("image_url", "").strip()
        shtuk_raw = item.get("shtuk", "").strip() or ""
        
        caption_text = f"📌 Kod: {code}"
        if kolleksiya:
            caption_text += f" ({kolleksiya})"
        caption_text += f"\n📐 Razmer: {razmer}\n"
        caption_text += f"📦 Kolleksiya: {kolleksiya}"
        
        # Jami sonni int ga o'tkazish
        try:
            total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
        except Exception:
            total_qty = 0
        
        # Karzinkaga olingan sonni olish
        cart_qty = get_cart_quantity_by_code(code, razmer)
        
        # Buyurtmaga olingan sonni olish
        from services.order_service import get_order_quantity_by_code
        order_qty = get_order_quantity_by_code(code, razmer)
        
        # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
        remaining_count = total_qty - cart_qty - order_qty
        remaining_count = max(remaining_count, 0)
        
        # Formatlash (qisqartirilgan)
        if cart_qty > 0:
            caption_text += f" 🧺K:{cart_qty}"
            if total_qty > 0:
                caption_text += f" 📦Q:{remaining_count}"
        elif total_qty > 0:
            # Karzinkaga olingan yo'q, lekin soni bor
            caption_text += f"\n📊 Soni: {total_qty} dona"
        
        # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
        if order_qty > 0:
            caption_text += f" ⚡B:{order_qty}"
            if total_qty > 0:
                caption_text += f" 📦Q:{remaining_count}"
        
        # Tugmalar: 1 qatorda 3 ta (mavjud tuzilma o'zgarmaydi, faqat callback data boyitiladi)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_size_view_all_back"
                ),
                InlineKeyboardButton(
                    text="🧺 Karzinkaga qo'shish",
                    callback_data=f"ready_size_cart_add:{code}|{razmer}"
                ),
                InlineKeyboardButton(
                    text="⚡ Buyurtma berish",
                    callback_data=f"ready_size_order:{code}|{razmer}"
                )
            ]
        ])
        
        # BIRTA RASM XABARI (caption va tugmalar bilan)
        if image_url:
            converted_url = sheet_service._convert_google_drive_link(image_url)
            try:
                photo_msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=converted_url,
                    caption=caption_text,
                    reply_markup=keyboard
                )
                view_all_message_ids.append(photo_msg.message_id)
            except Exception:
                # Rasm yuborishda xato bo'lsa, text xabar sifatida yuborish (fallback)
                try:
                    text_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=keyboard
                    )
                    view_all_message_ids.append(text_msg.message_id)
                except Exception:
                    pass
        else:
            # Rasm yo'q bo'lsa, faqat text xabar yuborish
            try:
                text_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=caption_text,
                    reply_markup=keyboard
                )
                view_all_message_ids.append(text_msg.message_id)
            except Exception:
                pass
    
    # State ga barcha message_id larni saqlash
    await state.update_data(ready_size_view_all_ids=view_all_message_ids, ready_size_is_view_all_page=True)
    await callback_query.answer()


@router.callback_query(F.data == "ready_size_view_all_back")
async def callback_ready_size_view_all_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    "Barchasini ko'rish" sahifasidan orqaga qaytish.
    BARCHA mahsulot xabarlarini o'chirish va "Barcha razmerlar" ro'yxatini qayta yuborish.
    """
    from services.google_sheet import CACHE
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id
    
    # 1) State dan barcha message_id larni olish
    state_data = await state.get_data()
    view_all_message_ids = state_data.get("ready_size_view_all_ids", [])
    
    # Hozirgi xabarning ID sini ham qo'shish
    if message_id not in view_all_message_ids:
        view_all_message_ids.append(message_id)
    
    # 2) BARCHA xabarlarni o'chirish
    for msg_id in view_all_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    
    # 3) State ni tozalash
    await state.update_data(ready_size_view_all_ids=[])
    
    # 4) "Barcha razmerlar" ro'yxatini QAYTA yuborish
    sheets6_data = CACHE.get("sheets6", [])
    if not sheets6_data:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=True)
        return
    
    # Group by (model_nomi, kolleksiya)
    grouped = {}
    for item in sheets6_data:
        model = item.get("model_nomi", "").strip() or "Noma'lum"
        kolleksiya = item.get("kolleksiya", "").strip() or ""
        key = (model, kolleksiya)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)
    
    # Build text list
    lines = ["📋 Barcha razmerlar:\n"]
    for (model, kolleksiya), items in grouped.items():
        display_model = (model or "").strip() or "Noma'lum"
        if display_model.lower() == "xitoy kombo":
            display_model = "X.Kombo"
        header = f"🪟 {display_model}"
        if kolleksiya:
            header += f" / {kolleksiya}"
        lines.append(header)
        
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or "Noma'lum"
            shtuk_raw = item.get("shtuk", "").strip() or ""
            
            # Jami sonni int ga o'tkazish
            try:
                total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
            except Exception:
                total_qty = 0
            
            # Karzinkaga olingan sonni olish
            cart_qty = get_cart_quantity_by_code(code, razmer)
            
            # Buyurtmaga olingan sonni olish
            order_qty = get_order_quantity_by_code(code, razmer)
            
            # Formatlash: KOD → RAZMER (SON)
            size_text = _format_razmer_for_barcha(razmer)
            line = f"{code} \u2192 {size_text} ({total_qty})"
            
            # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
            remaining_count = total_qty - cart_qty - order_qty
            remaining_count = max(remaining_count, 0)
            
            # Agar karzinkaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if cart_qty > 0:
                line += f" 🧺K:{cart_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if order_qty > 0:
                line += f" ⚡B:{order_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            lines.append(line)
        
        lines.append("")  # Bo'sh qator har bir guruhdan keyin
    
    result_text = "\n".join(lines).strip()
    
    # Inline keyboard for code selection - TEXT ro'yxat tartibida yaratish
    inline_buttons = []
    row = []
    seen = set()  # Unique kodlarni tekshirish uchun
    # grouped.items() tartibida kodlarni yig'ish (text ro'yxat bilan bir xil tartib)
    for (model, kolleksiya), items in grouped.items():
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or ""
            if not code:
                continue
            # Unique kodlarni tekshirish (tartibni saqlab)
            code_key = f"{code}|{razmer}"
            if code_key not in seen:
                seen.add(code_key)
                # 3 ta kod bir qatorda
                row.append(
                    InlineKeyboardButton(
                        text=code,
                        callback_data=f"ready_size_code:{code}|{razmer}"
                    )
                )
                if len(row) == 3:
                    inline_buttons.append(row)
                    row = []
    
    if row:
        inline_buttons.append(row)
    
    # "Barchasini ko'rish" va "Orqaga" tugmalari alohida qatorlarda
    inline_buttons.append([
        InlineKeyboardButton(
            text="🖼 Barchasini ko'rish",
            callback_data="ready_size_view_all"
        )
    ])
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="ready_sizes"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)
    
    # YANGI xabar yuborish
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data.startswith("ready_size_search_code:"))
async def callback_ready_size_search_code(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Show code details from closest matches list"""
    from services.google_sheet import CACHE, GoogleSheetService
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    chat_id = callback_query.message.chat.id
    message = callback_query.message
    
    # Code va razmer ni olish (format: "ready_size_search_code:code|razmer")
    data_part = callback_query.data.split(":", 1)[1] if ":" in callback_query.data else ""
    
    if not data_part:
        await callback_query.answer("Kod topilmadi", show_alert=False)
        return
    
    # Parse code|razmer
    if "|" in data_part:
        code, razmer = data_part.split("|", 1)
        code = code.strip()
        razmer = razmer.strip()
    else:
        # Backward compatibility: faqat code bo'lsa
        code = data_part.strip()
        razmer = ""
    
    if not code:
        await callback_query.answer("Kod topilmadi", show_alert=False)
        return
    
    # Sheets6 dan code+razmer bo'yicha ma'lumotni topish
    sheets6_data = CACHE.get("sheets6", [])
    record = None
    for item in sheets6_data:
        item_code = item.get("code", "").strip()
        item_razmer = (item.get("razmer", "").strip() or "")
        # Agar razmer bo'sh bo'lsa, faqat code bo'yicha qidirish
        if razmer:
            # Razmer mavjud bo'lsa, code+razmer bo'yicha qidirish
            if item_code == code and item_razmer == razmer:
                record = item
                break
        else:
            # Razmer bo'sh bo'lsa, faqat code bo'yicha qidirish (birinchi topilgan)
            if item_code == code:
                record = item
                break
    
    if not record:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=False)
        return
    
    # OLDINGI XABARNI DELETE qilish (closest matches list)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception:
        pass
    
    # Oldingi natijalarni o'chirish
    state_data = await state.get_data()
    search_result_message_ids = state_data.get("search_result_message_ids", [])
    for msg_id in search_result_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    
    # State ni tozalash
    await state.update_data(search_result_message_ids=[])
    
    # Ma'lumotlarni olish
    code_val = record.get("code", "").strip()
    if not razmer:
        razmer = record.get("razmer", "").strip() or "Noma'lum"
    kolleksiya = record.get("kolleksiya", "").strip() or "Noma'lum"
    shtuk_raw = record.get("shtuk", "").strip() or ""
    image_url = record.get("image_url", "").strip()
    
    # Karzinkaga olingan sonni olish
    from services.cart_service import get_cart_quantity_by_code
    
    caption_text = f"📌 Kod: {code}"
    if kolleksiya and kolleksiya != "Noma'lum":
        caption_text += f" ({kolleksiya})"
    caption_text += f"\n📐 Razmer: {razmer}\n📦 Kolleksiya: {kolleksiya}"
    
    # Jami sonni int ga o'tkazish
    try:
        total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
    except Exception:
        total_qty = 0
    
    # Karzinkaga olingan sonni olish
    cart_qty = get_cart_quantity_by_code(code, razmer)
    
    # Buyurtmaga olingan sonni olish
    from services.order_service import get_order_quantity_by_code
    order_qty = get_order_quantity_by_code(code, razmer)
    
    # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
    remaining_count = total_qty - cart_qty - order_qty
    remaining_count = max(remaining_count, 0)
    
    # Formatlash (qisqartirilgan)
    if cart_qty > 0:
        caption_text += f" 🧺K:{cart_qty}"
        if total_qty > 0:
            caption_text += f" 📦Q:{remaining_count}"
    elif total_qty > 0:
        # Karzinkaga olingan yo'q, lekin soni bor
        caption_text += f"\n📊 Soni: {total_qty} dona"
    
    # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
    if order_qty > 0:
        caption_text += f" ⚡B:{order_qty}"
        if total_qty > 0:
            caption_text += f" 📦Q:{remaining_count}"
    
    # Tugmalar
    detail_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="ready_size_search_result_back"
            ),
            InlineKeyboardButton(
                text="🧺 Karzinkaga",
                callback_data=f"ready_size_cart_add:{code}|{razmer}"
            ),
            InlineKeyboardButton(
                text="⚡ Buyurtma",
                callback_data=f"ready_size_order:{code}|{razmer}"
            )
        ]
    ])
    
    result_message_ids = []
    
    # BITTA XABAR: Rasm + caption
    if image_url:
        sheet_service = GoogleSheetService()
        converted_url = sheet_service._convert_google_drive_link(image_url)
        
        try:
            photo_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=converted_url,
                caption=caption_text,
                reply_markup=detail_keyboard
            )
            result_message_ids.append(photo_msg.message_id)
        except Exception:
            # Rasm yuborishda xato bo'lsa, text xabar sifatida yuborish
            try:
                text_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=caption_text,
                    reply_markup=detail_keyboard
                )
                result_message_ids.append(text_msg.message_id)
            except Exception:
                pass
    else:
        # Rasm yo'q bo'lsa, faqat text
        try:
            text_msg = await bot.send_message(
                chat_id=chat_id,
                text=caption_text,
                reply_markup=detail_keyboard
            )
            result_message_ids.append(text_msg.message_id)
        except Exception:
            pass
    
    # State ga yangi message_id larni saqlash
    await state.update_data(
        search_result_message_ids=result_message_ids,
        last_result_type="code_detail"
    )
    
    await callback_query.answer()


@router.message(SizeSearchStates.waiting_for_size)
async def process_size_input(message: Message, state: FSMContext, bot: Bot):
    """
    Process user's size input and show closest matches or exact match
    """
    from services.google_sheet import CACHE, GoogleSheetService
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    user_input = message.text.strip()
    chat_id = message.chat.id
    
    # Delete user's input message
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception:
        pass
    
    sheets6_data = CACHE.get("sheets6", [])
    if not sheets6_data:
        await message.answer("Ma'lumot topilmadi. /start bilan qayta boshlang.")
        await state.clear()
        return
    
    # Check for exact match (case-insensitive)
    exact_match = None
    for item in sheets6_data:
        razmer = item.get("razmer", "").strip()
        if razmer.lower() == user_input.lower():
            exact_match = item
            break
    
    # If exact match found, show it
    if exact_match:
        code = exact_match.get("code", "").strip()
        razmer = exact_match.get("razmer", "").strip() or "Noma'lum"
        kolleksiya = exact_match.get("kolleksiya", "").strip() or "Noma'lum"
        shtuk_raw = exact_match.get("shtuk", "").strip() or ""
        image_url = exact_match.get("image_url", "").strip()
        
        # Karzinkaga olingan sonni olish
        from services.cart_service import get_cart_quantity_by_code
        
        caption_text = f"📌 Kod: {code}"
        if kolleksiya and kolleksiya != "Noma'lum":
            caption_text += f" ({kolleksiya})"
        caption_text += f"\n📐 Razmer: {razmer}\n📦 Kolleksiya: {kolleksiya}"
        
        # Jami sonni int ga o'tkazish
        try:
            total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
        except Exception:
            total_qty = 0
        
        # Karzinkaga olingan sonni olish
        cart_qty = get_cart_quantity_by_code(code, razmer)
        
        # Buyurtmaga olingan sonni olish
        from services.order_service import get_order_quantity_by_code
        order_qty = get_order_quantity_by_code(code, razmer)
        
        # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
        remaining_count = total_qty - cart_qty - order_qty
        remaining_count = max(remaining_count, 0)
        
        # Formatlash (qisqartirilgan)
        if cart_qty > 0:
            caption_text += f" 🧺K:{cart_qty}"
            if total_qty > 0:
                caption_text += f" 📦Q:{remaining_count}"
        elif total_qty > 0:
            # Karzinkaga olingan yo'q, lekin soni bor
            caption_text += f"\n📊 Soni: {total_qty} dona"
        
        # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
        if order_qty > 0:
            caption_text += f" ⚡B:{order_qty}"
            if total_qty > 0:
                caption_text += f" 📦Q:{remaining_count}"
        
        # Tugmalar
        detail_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_size_search_result_back"
                ),
                InlineKeyboardButton(
                    text="🧺 Karzinkaga",
                    callback_data=f"ready_size_cart_add:{code}|{razmer}"
                ),
                InlineKeyboardButton(
                    text="⚡ Buyurtma",
                    callback_data=f"ready_size_order:{code}|{razmer}"
                )
            ]
        ])
        
        result_message_ids = []
        
        # Rasm yuborish
        if image_url:
            sheet_service = GoogleSheetService()
            converted_url = sheet_service._convert_google_drive_link(image_url)
            
            try:
                photo_msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=converted_url,
                    caption=caption_text,
                    reply_markup=detail_keyboard
                )
                result_message_ids.append(photo_msg.message_id)
            except Exception:
                # Rasm yuborishda xato bo'lsa, text xabar sifatida yuborish
                try:
                    text_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=detail_keyboard
                    )
                    result_message_ids.append(text_msg.message_id)
                except Exception:
                    pass
        else:
            # Rasm yo'q bo'lsa, faqat text
            try:
                text_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=caption_text,
                    reply_markup=detail_keyboard
                )
                result_message_ids.append(text_msg.message_id)
            except Exception:
                pass
        
        # State ga natija message_id larni saqlash
        await state.update_data(
            search_result_message_ids=result_message_ids,
            last_result_type="exact_match"
        )
        return
    
    # If no exact match, show closest matches (top 5)
    # Simple fuzzy matching: contains substring
    matches = []
    for item in sheets6_data:
        razmer = item.get("razmer", "").strip()
        if user_input.lower() in razmer.lower():
            matches.append(item)
    
    if not matches:
        result_text = f"❌ Razmer '{user_input}' topilmadi.\n\nIltimos, boshqa razmer kiriting yoki /start bilan qayta boshlang."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_size_search_result_back"
                )
            ]
        ])
        try:
            text_msg = await bot.send_message(
                chat_id=chat_id,
                text=result_text,
                reply_markup=keyboard
            )
            await state.update_data(
                search_result_message_ids=[text_msg.message_id],
                last_result_type="no_match"
            )
        except Exception:
            pass
        return
    
    # Show top 5 closest matches
    top_matches = matches[:5]
    result_text = f"🔍 Qidiruv natijalari: '{user_input}'\n\n"
    for item in top_matches:
        code = item.get("code", "").strip()
        razmer = item.get("razmer", "").strip() or "Noma'lum"
        shtuk_raw = item.get("shtuk", "").strip() or ""
        
        # Jami sonni int ga o'tkazish
        try:
            total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
        except Exception:
            total_qty = 0
        
        # Karzinkaga olingan sonni olish
        cart_qty = get_cart_quantity_by_code(code, razmer)
        
        # Buyurtmaga olingan sonni olish
        order_qty = get_order_quantity_by_code(code, razmer)
        
        # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
        remaining_count = total_qty - cart_qty - order_qty
        remaining_count = max(remaining_count, 0)
        
        # Formatlash
        if cart_qty > 0:
            result_text += f"{code} — {razmer}\n🧺K:{cart_qty}"
            if total_qty > 0:
                result_text += f" 📦Q:{remaining_count}\n"
            else:
                result_text += "\n"
        elif total_qty > 0:
            result_text += f"{code} — {razmer} | Mavjud: {total_qty} ta\n"
        else:
            result_text += f"{code} — {razmer}\n"
        
        # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
        if order_qty > 0:
            result_text += f"⚡B:{order_qty}"
            if total_qty > 0:
                result_text += f" 📦Q:{remaining_count}\n"
            else:
                result_text += "\n"
    
    result_text += "\nKodni tanlang:"
    
    # Inline keyboard with code buttons
    inline_buttons = []
    row = []
    for item in top_matches:
        code = item.get("code", "").strip()
        razmer = item.get("razmer", "").strip() or ""
        row.append(
            InlineKeyboardButton(
                text=code,
                callback_data=f"ready_size_search_code:{code}|{razmer}"
            )
        )
        if len(row) == 2:  # 2 ta kod bir qatorda
            inline_buttons.append(row)
            row = []
    if row:
        inline_buttons.append(row)
    
    # Orqaga tugmasi
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="ready_size_search_result_back"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)
    
    result_message_ids = []
    try:
        text_msg = await bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=keyboard
        )
        result_message_ids.append(text_msg.message_id)
    except Exception:
        pass
    
    # State ga natija message_id larni va message type ni saqlash
    await state.update_data(
        search_result_message_ids=result_message_ids,
        last_result_type="closest_matches"  # exact_match yoki closest_matches
    )


@router.callback_query(F.data.startswith("ready_size_search_code:"))
async def callback_ready_size_search_code(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Show code details from closest matches list"""
    from services.google_sheet import CACHE, GoogleSheetService
    
    chat_id = callback_query.message.chat.id
    message = callback_query.message
    
    # Code va razmer ni olish (format: "ready_size_search_code:code|razmer")
    data_part = callback_query.data.split(":", 1)[1] if ":" in callback_query.data else ""
    
    if not data_part:
        await callback_query.answer("Kod topilmadi", show_alert=False)
        return
    
    # Parse code|razmer
    if "|" in data_part:
        code, razmer = data_part.split("|", 1)
        code = code.strip()
        razmer = razmer.strip()
    else:
        # Backward compatibility: faqat code bo'lsa
        code = data_part.strip()
        razmer = ""
    
    if not code:
        await callback_query.answer("Kod topilmadi", show_alert=False)
        return
    
    # Sheets6 dan code+razmer bo'yicha ma'lumotni topish
    sheets6_data = CACHE.get("sheets6", [])
    record = None
    for item in sheets6_data:
        item_code = item.get("code", "").strip()
        item_razmer = (item.get("razmer", "").strip() or "")
        # Agar razmer bo'sh bo'lsa, faqat code bo'yicha qidirish
        if razmer:
            # Razmer mavjud bo'lsa, code+razmer bo'yicha qidirish
            if item_code == code and item_razmer == razmer:
                record = item
                break
        else:
            # Razmer bo'sh bo'lsa, faqat code bo'yicha qidirish (birinchi topilgan)
            if item_code == code:
                record = item
                break
    
    if not record:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=False)
        return
    
    # OLDINGI XABARNI DELETE qilish (closest matches list)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
    except Exception:
        pass
    
    # Oldingi natijalarni o'chirish
    state_data = await state.get_data()
    search_result_message_ids = state_data.get("search_result_message_ids", [])
    for msg_id in search_result_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    
    # State ni tozalash
    await state.update_data(search_result_message_ids=[])
    
    # Ma'lumotlarni olish
    code_val = record.get("code", "").strip()
    razmer = record.get("razmer", "").strip() or "Noma'lum"
    kolleksiya = record.get("kolleksiya", "").strip() or "Noma'lum"
    shtuk_raw = record.get("shtuk", "").strip() or ""
    image_url = record.get("image_url", "").strip()
    
    # Karzinkaga olingan sonni olish
    from services.cart_service import get_cart_quantity_by_code
    cart_qty = get_cart_quantity_by_code(code_val, razmer)
    
    # Buyurtmaga olingan sonni olish
    from services.order_service import get_order_quantity_by_code
    order_qty = get_order_quantity_by_code(code_val, razmer)
    
    caption_text = f"📌 Kod: {code_val}"
    if kolleksiya and kolleksiya != "Noma'lum":
        caption_text += f" ({kolleksiya})"
    caption_text += f"\n📐 Razmer: {razmer}\n📦 Kolleksiya: {kolleksiya}"
    
    # Jami sonni int ga o'tkazish
    try:
        total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
    except Exception:
        total_qty = 0
    
    # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
    remaining_count = total_qty - cart_qty - order_qty
    remaining_count = max(remaining_count, 0)
    
    # Formatlash (qisqartirilgan)
    if cart_qty > 0:
        caption_text += f" 🧺K:{cart_qty}"
        if total_qty > 0:
            caption_text += f" 📦Q:{remaining_count}"
    elif total_qty > 0:
        # Karzinkaga olingan yo'q, lekin soni bor
        caption_text += f"\n📊 Soni: {total_qty} dona"
    
    # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
    if order_qty > 0:
        caption_text += f" ⚡B:{order_qty}"
        if total_qty > 0:
            caption_text += f" 📦Q:{remaining_count}"
    
    # Tugmalar
    detail_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="ready_size_search_result_back"
            ),
            InlineKeyboardButton(
                text="🧺 Karzinkaga",
                callback_data=f"ready_size_cart_add:{code_val}|{razmer}"
            ),
            InlineKeyboardButton(
                text="⚡ Buyurtma",
                callback_data=f"ready_size_order:{code_val}|{razmer}"
            )
        ]
    ])
    
    result_message_ids = []
    
    # BITTA XABAR: Rasm + caption
    if image_url:
        sheet_service = GoogleSheetService()
        converted_url = sheet_service._convert_google_drive_link(image_url)
        
        try:
            photo_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=converted_url,
                caption=caption_text,
                reply_markup=detail_keyboard
            )
            result_message_ids.append(photo_msg.message_id)
        except Exception:
            # Rasm yuborishda xato bo'lsa, text xabar sifatida yuborish
            try:
                text_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=caption_text,
                    reply_markup=detail_keyboard
                )
                result_message_ids.append(text_msg.message_id)
            except Exception:
                pass
    else:
        # Rasm yo'q bo'lsa, faqat text
        try:
            text_msg = await bot.send_message(
                chat_id=chat_id,
                text=caption_text,
                reply_markup=detail_keyboard
            )
            result_message_ids.append(text_msg.message_id)
        except Exception:
            pass
    
    # State ga yangi message_id larni saqlash
    await state.update_data(
        search_result_message_ids=result_message_ids,
        last_result_type="code_detail"
    )
    
    await callback_query.answer()


@router.callback_query(F.data == "ready_size_search_result_back")
async def callback_ready_size_search_result_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Go back from search result to search prompt
    """
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id
    
    # 1) HOZIRGI XABARNI DELETE qilish
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
    
    # 2) State dan barcha search result message_id larni olish va o'chirish
    state_data = await state.get_data()
    search_result_message_ids = state_data.get("search_result_message_ids", [])
    for msg_id in search_result_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    
    # 3) State ni tozalash
    await state.update_data(search_result_message_ids=[])
    await state.set_state(SizeSearchStates.waiting_for_size)
    
    # 4) Qidiruv promptini qayta yuborish
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="ready_sizes"
            )
        ]
    ])
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="🔍 Razmer bo'yicha qidirish\n\nRazmer ni kiriting (masalan: 39, 40, 41...):",
            reply_markup=keyboard
        )
    except Exception:
        pass
    
    await callback_query.answer()


# MUHIM: Aniqroq handlerlar oldinroq qo'yilishi kerak!
# ready_size_cart_back va ready_size_cart_success_back handlerlari ready_size_cart_add dan oldinroq bo'lishi kerak
# chunki ready_size_cart_add handler ready_size_cart_back ni ham ushlaydi

@router.callback_query(F.data.startswith("ready_size_cart_back:"))
async def callback_ready_size_cart_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    "Orqaga" tugmasi bosilganda - qty tanlashdan oldingi holatga qaytish.
    BARCHA xabarlarni o'chirib, Tayyor razmerlar ro'yxatiga qaytish.
    """
    # DARHOL javob qaytarish
    await callback_query.answer()
    
    chat_id = callback_query.message.chat.id
    current_message_id = callback_query.message.message_id
    
    # State dan BARCHA message ID larni olish (o'chirishdan OLDIN)
    state_data = await state.get_data()
    
    # Barcha message ID larni to'plab olish
    all_message_ids = set()
    
    # 1) Joriy xabar (miqdor tanlash ekrani - bu mahsulot detail xabari)
    all_message_ids.add(current_message_id)
    
    # 2) "Barchasini ko'rish" sahifasidagi barcha xabarlar
    view_all_message_ids = state_data.get("ready_size_view_all_ids", [])
    for msg_id in view_all_message_ids:
        all_message_ids.add(msg_id)
    
    # 3) Mahsulot detail sahifasidagi rasm xabar
    image_message_id = state_data.get("ready_size_image_id")
    if image_message_id:
        all_message_ids.add(image_message_id)
    
    # 4) Ro'yxat xabari (agar mavjud bo'lsa)
    list_message_id = state_data.get("ready_size_list_message_id")
    if list_message_id:
        all_message_ids.add(list_message_id)
    
    # BARCHA xabarlarni o'chirish
    for msg_id in all_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    
    # State ni tozalash (barcha ready_size bilan bog'liq ma'lumotlar)
    await state.update_data(
        ready_size_view_all_ids=[],
        ready_size_is_view_all_page=False,
        ready_size_image_id=None,
        ready_size_list_message_id=None
    )
    
    # "Barcha razmerlar" ro'yxatini yuborish
    from services.google_sheet import CACHE
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    sheets6_data = CACHE.get("sheets6", [])
    if not sheets6_data:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=True)
        return
    
    # Group by (model_nomi, kolleksiya)
    grouped = {}
    for item in sheets6_data:
        model = item.get("model_nomi", "").strip() or "Noma'lum"
        kolleksiya = item.get("kolleksiya", "").strip() or ""
        key = (model, kolleksiya)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)
    
    # Build text list
    lines = ["📋 Barcha razmerlar:\n"]
    for (model, kolleksiya), items in grouped.items():
        display_model = (model or "").strip() or "Noma'lum"
        if display_model.lower() == "xitoy kombo":
            display_model = "X.Kombo"
        header = f"🪟 {display_model}"
        if kolleksiya:
            header += f" / {kolleksiya}"
        lines.append(header)
        
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or "Noma'lum"
            shtuk_raw = item.get("shtuk", "").strip() or ""
            
            # Jami sonni int ga o'tkazish
            try:
                total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
            except Exception:
                total_qty = 0
            
            # Karzinkaga olingan sonni olish
            cart_qty = get_cart_quantity_by_code(code, razmer)
            
            # Buyurtmaga olingan sonni olish
            order_qty = get_order_quantity_by_code(code, razmer)
            
            # Formatlash: KOD → RAZMER (SON)
            size_text = _format_razmer_for_barcha(razmer)
            line = f"{code} \u2192 {size_text} ({total_qty})"
            
            # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
            remaining_count = total_qty - cart_qty - order_qty
            remaining_count = max(remaining_count, 0)
            
            # Agar karzinkaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if cart_qty > 0:
                line += f" 🧺K:{cart_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if order_qty > 0:
                line += f" ⚡B:{order_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            lines.append(line)
        
        lines.append("")  # Bo'sh qator har bir guruhdan keyin
    
    result_text = "\n".join(lines).strip()
    
    # Inline keyboard for code selection - TEXT ro'yxat tartibida yaratish
    inline_buttons = []
    row = []
    seen = set()  # Unique kodlarni tekshirish uchun
    # grouped.items() tartibida kodlarni yig'ish (text ro'yxat bilan bir xil tartib)
    for (model, kolleksiya), items in grouped.items():
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or ""
            if not code:
                continue
            # Unique kodlarni tekshirish (tartibni saqlab)
            code_key = f"{code}|{razmer}"
            if code_key not in seen:
                seen.add(code_key)
                # 3 ta kod bir qatorda
                row.append(
                    InlineKeyboardButton(
                        text=code,
                        callback_data=f"ready_size_code:{code}|{razmer}"
                    )
                )
                if len(row) == 3:
                    inline_buttons.append(row)
                    row = []
    
    if row:
        inline_buttons.append(row)
    
    # "Barchasini ko'rish" va "Orqaga" tugmalari alohida qatorlarda
    inline_buttons.append([
        InlineKeyboardButton(
            text="🖼 Barchasini ko'rish",
            callback_data="ready_size_view_all"
        )
    ])
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="ready_sizes"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)
    
    # YANGI ro'yxat xabarini yuborish
    try:
        list_msg = await bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=keyboard
        )
        # Yangi list message ID ni saqlash
        await state.update_data(ready_size_list_message_id=list_msg.message_id)
    except Exception:
        pass


@router.callback_query(F.data.startswith("ready_size_cart_success_back:"))
async def callback_ready_size_cart_success_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Karzinkaga qo'shilgandan keyin "Orqaga" tugmasi - oldingi natijalar ro'yxatiga qaytish.
    """
    # Code ni olish
    code = callback_query.data.split(":", 1)[1] if ":" in callback_query.data else ""
    
    # Hozirgi xabarni (tasdiq xabarini) o'chirish
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
    
    # State dan is_view_all_page ni olish
    state_data = await state.get_data()
    is_view_all_page = state_data.get("ready_size_is_view_all_page", False)
    
    # State ni tozalash (view_all ro'yxatini tozalaymiz, lekin list/image ID larni qoldiramiz)
    await state.update_data(ready_size_view_all_ids=[], ready_size_is_view_all_page=False)
    
    # Oldingi sahifaga qaytish:
    # - Agar "Barchasini ko'rish" sahifasidan kelgan bo'lsa, shu sahifani tiklaymiz
    # - Aks holda, "Barcha razmerlar" ro'yxatiga qaytamiz
    if is_view_all_page:
        await _return_to_previous_ready_sizes_page(bot, state, chat_id, code, is_view_all_page=True)
        await callback_query.answer()
    else:
        # Barcha razmerlar ro'yxatiga qaytish
        await callback_ready_size_back_to_list(callback_query, state, bot)


@router.callback_query(F.data.startswith("ready_size_cart_add"))
async def callback_ready_size_cart_add(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Tayyor razmerlar mahsulotini karzinkaga qo'shish.
    - Agar soni > 1 bo'lsa: inline tugmalar orqali miqdor tanlash.
    - Agar soni == 1 yoki noma'lum bo'lsa: bevosita 1 dona qo'shiladi.
    """
    user = callback_query.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""

    # Callback data formatlari:
    # 1) "ready_size_cart_add:<code>"
    # 2) "ready_size_cart_add_qty:<code>:<qty>"
    data = callback_query.data or ""

    # 2-variant: qty tanlangan holat
    if data.startswith("ready_size_cart_add_qty:"):
        try:
            _, data_part, qty_str = data.split(":", 2)
        except ValueError:
            await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
            return
        try:
            qty = int(qty_str)
        except ValueError:
            await callback_query.answer("❌ Noto'g'ri son", show_alert=False)
            return

        # Parse code|razmer
        if "|" in data_part:
            code, razmer = data_part.split("|", 1)
            code = code.strip()
            razmer = razmer.strip()
        else:
            # Backward compatibility: faqat code bo'lsa
            code = data_part.strip()
            razmer = ""

        # Qolgan sonni tekshirish (qo'shishdan OLDIN)
        from services.google_sheet import CACHE
        from services.cart_service import get_cart_quantity_by_code
        from services.order_service import get_order_quantity_by_code
        
        sheets6_data = CACHE.get("sheets6", [])
        record = None
        for item in sheets6_data:
            item_code = item.get("code", "").strip()
            item_razmer = (item.get("razmer", "").strip() or "")
            if item_code == code and item_razmer == razmer:
                record = item
                break
        
        if not record:
            await callback_query.answer("❌ Mahsulot topilmadi", show_alert=False)
            return
        
        if not razmer:
            razmer = record.get("razmer", "").strip() or ""
        shtuk_raw = record.get("shtuk", "").strip() or ""
        
        try:
            total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
        except Exception:
            total_qty = 0
        
        cart_qty = get_cart_quantity_by_code(code, razmer)
        order_qty = get_order_quantity_by_code(code, razmer)
        remaining_count = total_qty - cart_qty - order_qty
        remaining_count = max(remaining_count, 0)  # Hech qachon manfiy bo'lmasin
        
        # Agar qolgan son 0 yoki kam bo'lsa, qo'shishga ruxsat berilmaydi
        if remaining_count <= 0:
            await callback_query.answer("❌ Qolmadi", show_alert=True)
            return
        
        # Agar tanlangan son qolgan sondan ko'p bo'lsa, qo'shishga ruxsat berilmaydi
        if qty > remaining_count:
            await callback_query.answer(f"❌ Faqat {remaining_count} dona qoldi", show_alert=True)
            return

        # Hamkor/sotuvchi kontekstini aniqlash
        is_partner, seller_id, seller_name, user_display = detect_partner_and_seller(
            user_id=user_id,
            first_name=first_name,
            username=username,
        )

        item = get_or_create_cart_item(
            user_id=user_id,
            code=code,
            qty=qty,
            user_name=user_display,
            is_partner=is_partner,
            seller_id=seller_id,
            seller_name=seller_name,
            razmer=razmer,
        )
        if not item:
            await callback_query.answer("❌ Ma'lumot topilmadi", show_alert=False)
            return

        # State dan ready_size_is_view_all_page flag ni olish
        state_data = await state.get_data()
        is_view_all_page = state_data.get("ready_size_is_view_all_page", False)
        
        # Agar "Barchasini ko'rish" sahifasida bo'lsa VA qty == 1 bo'lsa - faqat popup ko'rsatish
        if is_view_all_page and qty == 1:
            await callback_query.answer(f"🧺 Siz {qty} dona mahsulotni karzinkaga qo'shdingiz", show_alert=True)
            return

        # BARCHA eski xabarlarni o'chirish (rasm + matn + sonli tugmalar + orqaga)
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.message_id
        
        # State dan barcha message_id larni olish va o'chirish (barcha mahsulot natijalari)
        view_all_message_ids = state_data.get("ready_size_view_all_ids", [])
        
        # Hozirgi xabarning ID sini ham qo'shish
        if message_id not in view_all_message_ids:
            view_all_message_ids.append(message_id)
        
        # Barcha xabarlarni o'chirish
        for msg_id in view_all_message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        
        # State ni tozalash (barcha natijalar o'chirilgani uchun)
        await state.update_data(ready_size_view_all_ids=[], ready_size_is_view_all_page=is_view_all_page)
        
        # Yangi tasdiq xabarini yuborish: "🧺 Siz {N} ta mahsulotni karzinkaga oldingiz"
        success_text = f"🧺 Siz {qty} ta mahsulotni karzinkaga oldingiz"
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data=f"ready_size_cart_success_back:{code}|{razmer}",
                )
            ]
        ])
        
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=success_text,
                reply_markup=back_keyboard
            )
        except Exception:
            pass
        
        await callback_query.answer("🧺 Karzinkaga qo'shildi", show_alert=False)
        return

    # 1-variant: "ready_size_cart_add:<code>|razmer" → miqdor tanlash yoki bevosita 1 dona
    # Code va razmer ni ajratib olish
    try:
        _, data_part = data.split(":", 1)
    except ValueError:
        await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
        return

    # Parse code|razmer
    if "|" in data_part:
        code, razmer = data_part.split("|", 1)
        code = code.strip()
        razmer = razmer.strip()
    else:
        # Backward compatibility: faqat code bo'lsa
        code = data_part.strip()
        razmer = ""

    # Sheets6 dan code+razmer bo'yicha ma'lumotni topish
    from services.google_sheet import CACHE
    sheets6_data = CACHE.get("sheets6", [])
    record = None
    for item in sheets6_data:
        item_code = item.get("code", "").strip()
        item_razmer = (item.get("razmer", "").strip() or "")
        # Agar razmer bo'sh bo'lsa, faqat code bo'yicha qidirish
        if razmer:
            # Razmer mavjud bo'lsa, code+razmer bo'yicha qidirish
            if item_code == code and item_razmer == razmer:
                record = item
                break
        else:
            # Razmer bo'sh bo'lsa, faqat code bo'yicha qidirish (birinchi topilgan)
            if item_code == code:
                record = item
                break

    if not record:
        await callback_query.answer("❌ Mahsulot topilmadi", show_alert=False)
        return

    # Razmer va soni (shtuk) ma'lumotini olish
    if not razmer:
        razmer = record.get("razmer", "").strip() or ""
    shtuk_raw = record.get("shtuk", "").strip() or ""
    
    # Soni int ga o'tkazish
    try:
        total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
    except Exception:
        total_qty = 0

    # Karzinkaga allaqachon olingan sonni hisoblash
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    cart_qty = get_cart_quantity_by_code(code, razmer)
    order_qty = get_order_quantity_by_code(code, razmer)

    # Qolgan soni (karzinka + buyurtma hisobga olinadi)
    remaining_count = total_qty - cart_qty - order_qty if total_qty > 0 else 0
    remaining_count = max(remaining_count, 0)

    # Qolgan sonni tekshirish (qo'shishdan OLDIN)
    if remaining_count <= 0:
        await callback_query.answer("❌ Qolmadi", show_alert=True)
        return

    # Qolgan soni (eski format - backward compatibility)
    remaining = remaining_count
    available = remaining_count

    # Agar soni 0 yoki 1 bo'lsa, bevosita 1 dona qo'shish
    if available <= 1:
        # Hamkor/sotuvchi kontekstini aniqlash
        is_partner, seller_id, seller_name, user_display = detect_partner_and_seller(
            user_id=user_id,
            first_name=first_name,
            username=username,
        )

        item = get_or_create_cart_item(
            user_id=user_id,
            code=code,
            qty=1,
            user_name=user_display,
            is_partner=is_partner,
            seller_id=seller_id,
            seller_name=seller_name,
            razmer=razmer,
        )
        if not item:
            await callback_query.answer("❌ Ma'lumot topilmadi", show_alert=False)
            return
        
        # State dan ready_size_is_view_all_page flag ni olish
        state_data = await state.get_data()
        is_view_all_page = state_data.get("ready_size_is_view_all_page", False)
        
        # Agar "Barchasini ko'rish" sahifasida bo'lsa - faqat popup ko'rsatish
        if is_view_all_page:
            await callback_query.answer("🧺 Siz 1 dona mahsulotni karzinkaga qo'shdingiz", show_alert=True)
            return
        
        # BARCHA eski xabarlarni o'chirish (rasm + matn + tugmalar)
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.message_id
        
        # State dan barcha message_id larni olish va o'chirish (barcha mahsulot natijalari)
        view_all_message_ids = state_data.get("ready_size_view_all_ids", [])
        
        # Hozirgi xabarning ID sini ham qo'shish
        if message_id not in view_all_message_ids:
            view_all_message_ids.append(message_id)
        
        # Barcha xabarlarni o'chirish
        for msg_id in view_all_message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:

                pass
        
        # State ni tozalash (barcha natijalar o'chirilgani uchun)
        await state.update_data(ready_size_view_all_ids=[], ready_size_is_view_all_page=is_view_all_page)
        
        # Yangi tasdiq xabarini yuborish: "🧺 Siz {N} ta mahsulotni karzinkaga oldingiz"
        success_text = f"🧺 Siz 1 ta mahsulotni karzinkaga oldingiz"
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data=f"ready_size_cart_success_back:{code}|{razmer}",
                )
            ]
        ])
        
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=success_text,
                reply_markup=back_keyboard
            )
        except Exception:
            pass
        
        await callback_query.answer("🧺 Karzinkaga qo'shildi", show_alert=False)
        return

    # Aks holda: tanlash uchun inline tugmalar (1..remaining)
    # Juda katta bo'lsa, 20 donagacha cheklaymiz
    max_qty = min(remaining, 20)
    buttons = []
    row = []
    for i in range(1, max_qty + 1):
        row.append(
            InlineKeyboardButton(
                text=str(i),
                callback_data=f"ready_size_cart_add_qty:{code}|{razmer}:{i}",
            )
        )
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # ⬅️ "Orqaga" tugmasi raqamli tugmalar ostiga qo'shiladi
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data=f"ready_size_cart_back:{code}|{razmer}",
            )
        ]
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await bot.edit_message_reply_markup(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        # Agar edit_markup ishlamasa, shunchaki javob qaytaramiz
        pass

    await callback_query.answer("Miqdorini tanlang", show_alert=False)


async def _return_to_previous_ready_sizes_page(bot: Bot, state: FSMContext, chat_id: int, code: str, is_view_all_page: bool = None):
    """
    Helper function to return to the previous ready sizes page.
    - If is_view_all_page is True, restore "Barchasini ko'rish" page
    - Otherwise, restore code detail page
    """
    from services.google_sheet import CACHE, GoogleSheetService
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    state_data = await state.get_data()
    
    # Agar is_view_all_page parametr berilmagan bo'lsa, state dan olish
    if is_view_all_page is None:
        is_view_all_page = state_data.get("ready_size_is_view_all_page", False)
    
    # 1) Agar "Barchasini ko'rish" sahifasidan kelgan bo'lsa - barcha mahsulotlarni qayta yuborish
    if is_view_all_page:
        # Barchasini ko'rish sahifasini restore qilish
        sheets6_data = CACHE.get("sheets6", [])
        if not sheets6_data:
            return
        
        from services.google_sheet import GoogleSheetService
        sheet_service = GoogleSheetService()
        
        # Yangi natijalar uchun bo'sh ro'yxat (eski natijalar allaqachon o'chirilgan)
        view_all_message_ids = []
        for record_item in sheets6_data:
            record_code = record_item.get("code", "").strip()
            if not record_code:
                continue
            
            record_razmer = record_item.get("razmer", "").strip() or "Noma'lum"
            record_kolleksiya = record_item.get("kolleksiya", "").strip() or "Noma'lum"
            record_image_url = record_item.get("image_url", "").strip()
            
            shtuk_raw = record_item.get("shtuk", "").strip() or ""
            caption_text = f"📌 Kod: {record_code}"
            if record_kolleksiya:
                caption_text += f" ({record_kolleksiya})"
            caption_text += f"\n📐 Razmer: {record_razmer}\n"
            caption_text += f"📦 Kolleksiya: {record_kolleksiya}"
            
            try:
                total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
            except Exception:
                total_qty = 0
            
            cart_qty = get_cart_quantity_by_code(record_code, record_razmer)
            
            # Buyurtmaga olingan sonni olish
            from services.order_service import get_order_quantity_by_code
            order_qty = get_order_quantity_by_code(record_code, record_razmer)
            
            # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
            remaining_count = total_qty - cart_qty - order_qty
            remaining_count = max(remaining_count, 0)
            
            # Formatlash (qisqartirilgan)
            if cart_qty > 0:
                caption_text += f"\n🧺 K:{cart_qty}"
                if total_qty > 0:
                    caption_text += f"\n📦 Q:{remaining_count}"
            elif total_qty > 0:
                caption_text += f"\n📊 Soni: {total_qty} dona"
            
            # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if order_qty > 0:
                caption_text += f"\n⚡ B:{order_qty}"
                if total_qty > 0:
                    caption_text += f"\n📦 Q:{remaining_count}"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="ready_size_view_all_back"
                    ),
                    InlineKeyboardButton(
                        text="🧺 Karzinkaga qo'shish",
                        callback_data=f"ready_size_cart_add:{record_code}|{record_razmer}"
                    ),
                    InlineKeyboardButton(
                        text="⚡ Buyurtma berish",
                        callback_data=f"ready_size_order:{record_code}|{record_razmer}"
                    )
                ]
            ])
            
            if record_image_url:
                converted_url = sheet_service._convert_google_drive_link(record_image_url)
                try:
                    photo_msg = await bot.send_photo(
                        chat_id=chat_id,
                        photo=converted_url,
                        caption=caption_text,
                        reply_markup=keyboard
                    )
                    view_all_message_ids.append(photo_msg.message_id)
                except Exception:
                    try:
                        text_msg = await bot.send_message(
                            chat_id=chat_id,
                            text=caption_text,
                            reply_markup=keyboard
                        )
                        view_all_message_ids.append(text_msg.message_id)
                    except Exception:
                        pass
            else:
                try:
                    text_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=keyboard
                    )
                    view_all_message_ids.append(text_msg.message_id)
                except Exception:
                    pass
        
        await state.update_data(ready_size_view_all_ids=view_all_message_ids)
        return
    
    # 2) Agar ready_size_image_id va ready_size_list_message_id bo'lsa - code detail sahifasiga qaytarish
    image_id = state_data.get("ready_size_image_id")
    list_message_id = state_data.get("ready_size_list_message_id")
    
    if image_id or list_message_id:
        # Code detail sahifasini restore qilish
        sheets6_data = CACHE.get("sheets6", [])
        record = None
        for item in sheets6_data:
            if item.get("code", "").strip() == code:
                record = item
                break
        
        if record:
            razmer = record.get("razmer", "").strip() or "Noma'lum"
            kolleksiya = record.get("kolleksiya", "").strip() or "Noma'lum"
            model_nomi = record.get("model_nomi", "").strip() or "Noma'lum"
            razmer = record.get("razmer", "").strip() or "Noma'lum"
            shtuk_raw = record.get("shtuk", "").strip() or ""
            image_url = record.get("image_url", "").strip()
            
            caption_text = (
                f"📦 Kolleksiya: {kolleksiya}\n"
                f"🧩 Model: {model_nomi}\n\n"
                f"📌 Kod: {code}"
            )
            if kolleksiya and kolleksiya != "Noma'lum":
                caption_text += f" ({kolleksiya})"
            caption_text += f"\n📐 Razmer: {razmer}"
            
            try:
                total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
            except Exception:
                total_qty = 0
            
            cart_qty = get_cart_quantity_by_code(code, razmer)
            
            # Buyurtmaga olingan sonni olish
            from services.order_service import get_order_quantity_by_code
            order_qty = get_order_quantity_by_code(code, razmer)
            
            # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
            remaining_count = total_qty - cart_qty - order_qty
            remaining_count = max(remaining_count, 0)
            
            # Formatlash (qisqartirilgan)
            if cart_qty > 0:
                caption_text += f"\n🧺 K:{cart_qty}"
                if total_qty > 0:
                    caption_text += f"\n📦 Q:{remaining_count}"
            elif total_qty > 0:
                caption_text += f"\n📊 Soni: {total_qty} dona"
            
            # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if order_qty > 0:
                caption_text += f"\n⚡ B:{order_qty}"
                if total_qty > 0:
                    caption_text += f"\n📦 Q:{remaining_count}"
            
            detail_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="ready_size_back_to_list"
                    ),
                    InlineKeyboardButton(
                        text="🧺 Karzinkaga",
                        callback_data=f"ready_size_cart_add:{code}|{razmer}"
                    ),
                    InlineKeyboardButton(
                        text="⚡ Buyurtma berish",
                        callback_data=f"ready_size_order:{code}|{razmer}"
                    )
                ]
            ])
            
            sheet_service = GoogleSheetService()
            if image_url:
                converted_url = sheet_service._convert_google_drive_link(image_url)
                try:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=converted_url,
                        caption=caption_text,
                        reply_markup=detail_keyboard
                    )
                except Exception:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=caption_text,
                            reply_markup=detail_keyboard
                        )
                    except Exception:
                        pass
            else:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=detail_keyboard
                    )
                except Exception:
                    pass


@router.callback_query(F.data == "ready_size_back_to_list")
async def callback_ready_size_back_to_list(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Return to the list from code detail page
    """
    from services.google_sheet import CACHE
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id
    
    # 1) OLDINGI RO'YXAT XABARINI DELETE qilish
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
    
    # 2) State dan list_message_id ni olish va o'chirish (agar mavjud bo'lsa)
    state_data = await state.get_data()
    list_message_id = state_data.get("ready_size_list_message_id")
    if list_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=list_message_id)
        except Exception:
            pass
    
    # 3) State ni tozalash
    await state.update_data(ready_size_image_id=None, ready_size_list_message_id=None)
    
    # 4) "Barcha razmerlar" ro'yxatini qayta yuborish
    sheets6_data = CACHE.get("sheets6", [])
    if not sheets6_data:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=True)
        return
    
    # Group by (model_nomi, kolleksiya)
    grouped = {}
    for item in sheets6_data:
        model = item.get("model_nomi", "").strip() or "Noma'lum"
        kolleksiya = item.get("kolleksiya", "").strip() or ""
        key = (model, kolleksiya)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)
    
    # Build text list
    lines = ["📋 Barcha razmerlar:\n"]
    for (model, kolleksiya), items in grouped.items():
        display_model = (model or "").strip() or "Noma'lum"
        if display_model.lower() == "xitoy kombo":
            display_model = "X.Kombo"
        header = f"🪟 {display_model}"
        if kolleksiya:
            header += f" / {kolleksiya}"
        lines.append(header)
        
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or "Noma'lum"
            shtuk_raw = item.get("shtuk", "").strip() or ""
            
            # Jami sonni int ga o'tkazish
            try:
                total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
            except Exception:
                total_qty = 0
            
            # Karzinkaga olingan sonni olish
            cart_qty = get_cart_quantity_by_code(code, razmer)
            
            # Buyurtmaga olingan sonni olish
            order_qty = get_order_quantity_by_code(code, razmer)
            
            # Formatlash: KOD → RAZMER (SON)
            size_text = _format_razmer_for_barcha(razmer)
            line = f"{code} \u2192 {size_text} ({total_qty})"
            
            # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
            remaining_count = total_qty - cart_qty - order_qty
            remaining_count = max(remaining_count, 0)
            
            # Agar karzinkaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if cart_qty > 0:
                line += f" 🧺K:{cart_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if order_qty > 0:
                line += f" ⚡B:{order_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            lines.append(line)
        
        lines.append("")  # Bo'sh qator har bir guruhdan keyin
    
    result_text = "\n".join(lines).strip()
    
    # Inline keyboard for code selection - TEXT ro'yxat tartibida yaratish
    inline_buttons = []
    row = []
    seen = set()  # Unique kodlarni tekshirish uchun
    # grouped.items() tartibida kodlarni yig'ish (text ro'yxat bilan bir xil tartib)
    for (model, kolleksiya), items in grouped.items():
        for item in items:
            code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or ""
            if not code:
                continue
            # Unique kodlarni tekshirish (tartibni saqlab)
            code_key = f"{code}|{razmer}"
            if code_key not in seen:
                seen.add(code_key)
                # 3 ta kod bir qatorda
                row.append(
                    InlineKeyboardButton(
                        text=code,
                        callback_data=f"ready_size_code:{code}|{razmer}"
                    )
                )
                if len(row) == 3:
                    inline_buttons.append(row)
                    row = []
    
    if row:
        inline_buttons.append(row)
    
    # "Barchasini ko'rish" va "Orqaga" tugmalari alohida qatorlarda
    inline_buttons.append([
        InlineKeyboardButton(
            text="🖼 Barchasini ko'rish",
            callback_data="ready_size_view_all"
        )
    ])
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="ready_sizes"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)
    
    # YANGI xabar yuborish
    try:
        list_msg = await bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=keyboard
        )
        # Yangi list message ID ni saqlash
        await state.update_data(ready_size_list_message_id=list_msg.message_id)
    except Exception:
        pass
    
    await callback_query.answer()


@router.callback_query(F.data.startswith("ready_size_code:"))
async def callback_ready_size_code(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Show code details with image (from the list)
    """
    from services.google_sheet import CACHE, GoogleSheetService
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    chat_id = callback_query.message.chat.id
    message = callback_query.message
    
    # 1) OLDINGI RO'YXAT XABARINI EDIT yoki DELETE qilish
    list_message_id = message.message_id
    try:
        # EDIT ga urinish (bo'sh text bilan, keyin o'chirish uchun)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=list_message_id,
            text="..."
        )
    except Exception:
        # Edit muvaffaqiyatsiz bo'lsa, DELETE qilish
        try:
            await bot.delete_message(chat_id=chat_id, message_id=list_message_id)
        except Exception:
            pass
    
    # Code va razmer ni olish (format: "ready_size_code:code|razmer")
    data_part = callback_query.data.split(":", 1)[1] if ":" in callback_query.data else ""
    
    if not data_part:
        await callback_query.answer("Kod topilmadi", show_alert=False)
        return
    
    # Parse code|razmer
    if "|" in data_part:
        code, razmer = data_part.split("|", 1)
        code = code.strip()
        razmer = razmer.strip()
    else:
        # Backward compatibility: faqat code bo'lsa
        code = data_part.strip()
        razmer = ""
    
    if not code:
        await callback_query.answer("Kod topilmadi", show_alert=False)
        return
    
    # Sheets6 dan code+razmer bo'yicha ma'lumotni topish
    sheets6_data = CACHE.get("sheets6", [])
    record = None
    for item in sheets6_data:
        item_code = item.get("code", "").strip()
        item_razmer = (item.get("razmer", "").strip() or "")
        # Agar razmer bo'sh bo'lsa, faqat code bo'yicha qidirish
        if razmer:
            # Razmer mavjud bo'lsa, code+razmer bo'yicha qidirish
            if item_code == code and item_razmer == razmer:
                record = item
                break
        else:
            # Razmer bo'sh bo'lsa, faqat code bo'yicha qidirish (birinchi topilgan)
            if item_code == code:
                record = item
                break
    
    if not record:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=False)
        return
    
    # Ma'lumotlarni olish
    if not razmer:
        razmer = record.get("razmer", "").strip() or "Noma'lum"
    kolleksiya = record.get("kolleksiya", "").strip() or "Noma'lum"
    model_nomi = record.get("model_nomi", "").strip() or "Noma'lum"
    shtuk_raw = record.get("shtuk", "").strip() or ""
    image_url = record.get("image_url", "").strip()
    
    from services.cart_service import get_cart_quantity_by_code
    
    # 2) BIRTA RASM XABARI (caption bilan va tugmalar bilan)
    caption_text = (
        f"📦 Kolleksiya: {kolleksiya}\n"
        f"🧩 Model: {model_nomi}\n\n"
        f"📌 Kod: {code}"
    )
    if kolleksiya and kolleksiya != "Noma'lum":
        caption_text += f" ({kolleksiya})"
    caption_text += f"\n📐 Razmer: {razmer}"
    
    # Jami sonni int ga o'tkazish
    try:
        total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
    except Exception:
        total_qty = 0
    
    # Karzinkaga olingan sonni olish
    cart_qty = get_cart_quantity_by_code(code, razmer)
    
    # Buyurtmaga olingan sonni olish
    from services.order_service import get_order_quantity_by_code
    order_qty = get_order_quantity_by_code(code, razmer)
    
    # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
    remaining_count = total_qty - cart_qty - order_qty
    remaining_count = max(remaining_count, 0)
    
    # Formatlash (qisqartirilgan)
    if cart_qty > 0:
        caption_text += f" 🧺K:{cart_qty}"
        if total_qty > 0:
            caption_text += f" 📦Q:{remaining_count}"
    elif total_qty > 0:
        # Karzinkaga olingan yo'q, lekin soni bor
        caption_text += f"\n📊 Soni: {total_qty} dona"
    
    # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
    if order_qty > 0:
        caption_text += f" ⚡B:{order_qty}"
        if total_qty > 0:
            caption_text += f" 📦Q:{remaining_count}"
    
    # Tugmalar: 1 qatorda 3 ta
    detail_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data="ready_size_back_to_list"
            ),
            InlineKeyboardButton(
                text="🧺 Karzinkaga",
                callback_data=f"ready_size_cart_add:{code}|{razmer}"
            ),
            InlineKeyboardButton(
                text="⚡ Buyurtma berish",
                callback_data=f"ready_size_order:{code}|{razmer}"
            )
        ]
    ])
    
    photo_msg_id = None
    sheet_service = GoogleSheetService()
    
    if image_url:
        converted_url = sheet_service._convert_google_drive_link(image_url)
        
        try:
            photo_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=converted_url,
                caption=caption_text,
                reply_markup=detail_keyboard
            )
            photo_msg_id = photo_msg.message_id
        except Exception:
            # Rasm yuborishda xato bo'lsa, text xabar sifatida yuborish
            try:
                photo_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=caption_text,
                    reply_markup=detail_keyboard
                )
                photo_msg_id = photo_msg.message_id
            except Exception:
                pass
    else:
        # Rasm yo'q bo'lsa, faqat text xabar yuborish
        try:
            photo_msg = await bot.send_message(
                chat_id=chat_id,
                text=caption_text,
                reply_markup=detail_keyboard
            )
            photo_msg_id = photo_msg.message_id
        except Exception:
            pass
    
    # Save message IDs for potential "Orqaga" action
    await state.update_data(
        ready_size_image_id=photo_msg_id,
        ready_size_list_message_id=list_message_id
    )
    
    await callback_query.answer()


# MUHIM: Aniqroq handlerlar oldinroq qo'yilishi kerak!
# ready_size_order_back, ready_size_order_success_back va ready_size_orders_back handlerlari 
# ready_size_order dan oldinroq bo'lishi kerak
# chunki ready_size_order handler ready_size_order_back ni ham ushlaydi

@router.callback_query(F.data == "ready_size_orders_back")
async def callback_ready_size_orders_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Buyurtmalar bo'limidan "Orqaga" tugmasi bosilganda:
    - Barcha buyurtma xabarlarini o'chirish (GLOBAL)
    - Tayyor razmerlar bo'limiga qaytish
    
    Qaysi buyurtma tagidagi "Orqaga" bosilishidan qat'iy nazar,
    barcha buyurtma xabarlari o'chiriladi.
    """
    await callback_query.answer()
    
    chat_id = callback_query.message.chat.id
    
    # State dan barcha buyurtma xabarlarining ID larini olish
    state_data = await state.get_data()
    order_message_ids = state_data.get("ready_size_order_message_ids", [])
    
    # Joriy xabarni ham qo'shish (agar state da bo'lmasa)
    current_message_id = callback_query.message.message_id
    all_message_ids = set(order_message_ids) if order_message_ids else set()
    all_message_ids.add(current_message_id)
    
    # BARCHA buyurtma xabarlarini o'chirish (GLOBAL o'chirish)
    # Qaysi buyurtma tagidagi "Orqaga" bosilishidan qat'iy nazar,
    # barcha xabarlar o'chiriladi
    for msg_id in all_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            # Xabar allaqachon o'chirilgan yoki topilmagan bo'lishi mumkin
            pass
    
    # State ni tozalash (barcha buyurtma xabarlari o'chirilgani uchun)
    await state.update_data(ready_size_order_message_ids=[])
    
    # Tayyor razmerlar menyusini yuborish
    user_id = callback_query.from_user.id
    
    # Admin uchun buyurtmalar tugmasi ko'rsatiladi
    if is_admin(user_id) or is_super_admin(user_id):
        keyboard = make_admin_ready_sizes_menu_keyboard()
    else:
        keyboard = make_ready_sizes_menu_keyboard()
    
    menu_text = "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:"
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("ready_size_order_back:"))
async def callback_ready_size_order_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    "Orqaga" tugmasi bosilganda - qty tanlashdan oldingi holatga qaytish.
    TO'LIQ RESET: barcha eski xabarlar o'chiriladi, state tozalanadi, ro'yxat qayta chiqariladi.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # DARHOL javob qaytarish (handler ishga tushganini ta'minlash uchun)
    await callback_query.answer()
    
    # Code ni olish
    code = callback_query.data.split(":", 1)[1] if ":" in callback_query.data else ""
    logger.info(f"=== ready_size_order_back handler ishga tushdi, code: {code} ===")
    print(f"=== ready_size_order_back handler ishga tushdi, code: {code} ===")
    
    from services.google_sheet import CACHE
    from services.cart_service import get_cart_quantity_by_code
    from services.order_service import get_order_quantity_by_code
    
    chat_id = callback_query.message.chat.id
    current_message_id = callback_query.message.message_id
    
    # State dan BARCHA message ID larni olish (o'chirishdan OLDIN)
    state_data = await state.get_data()
    
    # Barcha message ID larni to'plab olish
    all_message_ids = set()
    
    # 1) Joriy xabar (son tanlash ekrani - bu mahsulot detail xabari bo'lishi mumkin)
    all_message_ids.add(current_message_id)
    
    # 2) "Barchasini ko'rish" sahifasidagi barcha xabarlar
    view_all_message_ids = state_data.get("ready_size_view_all_ids", [])
    for msg_id in view_all_message_ids:
        all_message_ids.add(msg_id)
    
    # 3) Mahsulot detail sahifasidagi rasm xabar
    image_message_id = state_data.get("ready_size_image_id")
    if image_message_id:
        all_message_ids.add(image_message_id)
    
    # 4) Ro'yxat xabar
    list_message_id = state_data.get("ready_size_list_message_id")
    if list_message_id:
        all_message_ids.add(list_message_id)
    
    # BARCHA xabarlarni o'chirish (xatolarni e'tiborsiz qoldirish)
    for msg_id in all_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            logger.info(f"Xabar o'chirildi: {msg_id}")
        except Exception as e:
            logger.debug(f"Xabarni o'chirishda xatolik (ehtimol allaqachon o'chirilgan): {msg_id}, {e}")
            pass

    # State ni TO'LIQ tozalash (quantity state reset) - BARCHA ready_size related state
    await state.update_data(
        ready_size_view_all_ids=[],
        ready_size_is_view_all_page=False,
        ready_size_image_id=None,
        ready_size_list_message_id=None
    )

    # 5) To'g'ridan-to'g'ri "Barcha razmerlar" ro'yxatini qayta yuborish
    sheets6_data = CACHE.get("sheets6", [])
    if not sheets6_data:
        await callback_query.answer("Ma'lumot topilmadi", show_alert=True)
        return
    
    # Group by (model_nomi, kolleksiya)
    grouped = {}
    for item in sheets6_data:
        model = item.get("model_nomi", "").strip() or "Noma'lum"
        kolleksiya = item.get("kolleksiya", "").strip() or ""
        key = (model, kolleksiya)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)
    
    # Build text list
    lines = ["📋 Barcha razmerlar:\n"]
    for (model, kolleksiya), items in grouped.items():
        if kolleksiya:
            lines.append(f"🧩 Model: {model} ({kolleksiya})")
        else:
            lines.append(f"🧩 Model: {model}")
        
        for item in items:
            item_code = item.get("code", "").strip()
            razmer = item.get("razmer", "").strip() or "Noma'lum"
            shtuk_raw = item.get("shtuk", "").strip() or ""
            
            # Jami sonni int ga o'tkazish
            try:
                total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
            except Exception:
                total_qty = 0
            
            # Karzinkaga olingan sonni olish
            cart_qty = get_cart_quantity_by_code(item_code, razmer)
            
            # Buyurtmaga olingan sonni olish
            order_qty = get_order_quantity_by_code(item_code, razmer)
            
            # Formatlash
            if kolleksiya:
                line = f"{item_code} ({kolleksiya}) — {razmer}"
            else:
                line = f"{item_code} — {razmer}"
            
            # Qolgan soni (to'g'ri formula: total_qty - cart_qty - order_qty)
            remaining_count = total_qty - cart_qty - order_qty
            remaining_count = max(remaining_count, 0)
            
            # Agar karzinkaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if cart_qty > 0:
                line += f" 🧺K:{cart_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            elif total_qty > 0:
                # Karzinkaga olingan yo'q, lekin soni bor
                line += f" (Soni: {total_qty} dona)"
            
            # Agar buyurtmaga olingan bo'lsa, qo'shish (qisqartirilgan)
            if order_qty > 0:
                line += f" ⚡B:{order_qty}"
                if total_qty > 0:
                    line += f" 📦Q:{remaining_count}"
            
            lines.append(line)
        
        lines.append("")  # Bo'sh qator har bir guruhdan keyin
    
    result_text = "\n".join(lines).strip()
    
    # Inline keyboard for code selection - TEXT ro'yxat tartibida yaratish
    inline_buttons = []
    row = []
    seen = set()  # Unique kodlarni tekshirish uchun
    # grouped.items() tartibida kodlarni yig'ish (text ro'yxat bilan bir xil tartib)
    for (model, kolleksiya), items in grouped.items():
        for item in items:
            item_code = item.get("code", "").strip()
            item_razmer = item.get("razmer", "").strip() or ""
            if not item_code:
                continue
            # Unique kodlarni tekshirish (tartibni saqlab)
            code_key = f"{item_code}|{item_razmer}"
            if code_key not in seen:
                seen.add(code_key)
                # 3 ta kod bir qatorda
                row.append(
                    InlineKeyboardButton(
                        text=item_code,
                        callback_data=f"ready_size_code:{item_code}|{item_razmer}"
                    )
                )
                if len(row) == 3:
                    inline_buttons.append(row)
                    row = []
    
    if row:
        inline_buttons.append(row)
    
    # "Barchasini ko'rish" va "Orqaga" tugmalari alohida qatorlarda
    inline_buttons.append([
        InlineKeyboardButton(
            text="🖼 Barchasini ko'rish",
            callback_data="ready_size_view_all"
        )
    ])
    inline_buttons.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="ready_sizes"
        )
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=inline_buttons)
    
    # YANGI xabar yuborish (faqat bitta marta)
    try:
        list_msg = await bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=keyboard
        )
        # Yangi list message ID ni saqlash
        await state.update_data(ready_size_list_message_id=list_msg.message_id)
    except Exception:
        pass


@router.callback_query(F.data.startswith("ready_size_order_success_back:"))
async def callback_ready_size_order_success_back(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Buyurtmaga qo'shilgandan keyin "Orqaga" tugmasi - oldingi natijalar ro'yxatiga qaytish.
    """
    # Code ni olish
    code = callback_query.data.split(":", 1)[1] if ":" in callback_query.data else ""
    
    # Hozirgi xabarni (tasdiq xabarini) o'chirish
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
    
    # State ni tozalash (view_all ro'yxatini tozalaymiz, lekin list/image ID larni qoldiramiz)
    await state.update_data(ready_size_view_all_ids=[], ready_size_is_view_all_page=False)
    
    # Har doim "Barcha razmerlar" ro'yxatiga qaytarish
    await callback_ready_size_back_to_list(callback_query, state, bot)
    await callback_query.answer()


@router.callback_query(F.data.startswith("ready_size_order"))
async def callback_ready_size_order(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Tayyor razmerlar mahsulotini buyurtmaga qo'shish.
    - Agar soni > 1 bo'lsa: inline tugmalar orqali miqdor tanlash.
    - Agar soni == 1 yoki noma'lum bo'lsa: bevosita 1 dona qo'shiladi.
    """
    # Callback data formatlari:
    # 1) "ready_size_order:<code>"
    # 2) "ready_size_order_qty:<code>:<qty>"
    # MUHIM: "ready_size_order_back:", "ready_size_order_success_back:" va "ready_size_orders_back" ni chetlab o'tish kerak
    data = callback_query.data or ""
    
    # Agar "back" yoki "success_back" yoki "orders_back" bo'lsa, bu handlerni o'tkazib yuborish
    if (data.startswith("ready_size_order_back:") or 
        data.startswith("ready_size_order_success_back:") or 
        data == "ready_size_orders_back"):
        return
    
    user = callback_query.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""

    # 2-variant: qty tanlangan holat
    if data.startswith("ready_size_order_qty:"):
        try:
            _, data_part, qty_str = data.split(":", 2)
        except ValueError:
            await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
            return
        try:
            qty = int(qty_str)
        except ValueError:
            await callback_query.answer("❌ Noto'g'ri son", show_alert=False)
            return

        # Parse code|razmer
        if "|" in data_part:
            code, razmer = data_part.split("|", 1)
            code = code.strip()
            razmer = razmer.strip()
        else:
            # Backward compatibility: faqat code bo'lsa
            code = data_part.strip()
            razmer = ""

        # Qolgan sonni tekshirish (qo'shishdan OLDIN)
        from services.google_sheet import CACHE
        from services.cart_service import get_cart_quantity_by_code
        from services.order_service import get_order_quantity_by_code
        
        sheets6_data = CACHE.get("sheets6", [])
        record = None
        for item in sheets6_data:
            item_code = item.get("code", "").strip()
            item_razmer = (item.get("razmer", "").strip() or "")
            if item_code == code and item_razmer == razmer:
                record = item
                break
        
        if not record:
            await callback_query.answer("❌ Mahsulot topilmadi", show_alert=False)
            return
        
        if not razmer:
            razmer = record.get("razmer", "").strip() or ""
        shtuk_raw = record.get("shtuk", "").strip() or ""
        
        try:
            total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
        except Exception:
            total_qty = 0
        
        cart_qty = get_cart_quantity_by_code(code, razmer)
        order_qty = get_order_quantity_by_code(code, razmer)
        remaining_count = total_qty - cart_qty - order_qty
        remaining_count = max(remaining_count, 0)  # Hech qachon manfiy bo'lmasin
        
        # Agar qolgan son 0 yoki kam bo'lsa, qo'shishga ruxsat berilmaydi
        if remaining_count <= 0:
            await callback_query.answer("❌ Qolmadi", show_alert=True)
            return
        
        # Agar tanlangan son qolgan sondan ko'p bo'lsa, qo'shishga ruxsat berilmaydi
        if qty > remaining_count:
            await callback_query.answer(f"❌ Faqat {remaining_count} dona qoldi", show_alert=True)
            return

        # Hamkor/sotuvchi kontekstini aniqlash
        is_partner, seller_id, seller_name, user_display = detect_partner_and_seller_order(
            user_id=user_id,
            first_name=first_name,
            username=username,
        )
        
        # Buyurtma beruvchi rolini aniqlash
        from services.admin_storage import is_seller
        is_seller_user = is_seller(user_id)

        item = get_or_create_order_item(
            user_id=user_id,
            code=code,
            qty=qty,
            user_name=user_display,
            is_partner=is_partner,
            seller_id=seller_id,
            seller_name=seller_name,
            razmer=razmer,
        )
        if not item:
            await callback_query.answer("❌ Ma'lumot topilmadi", show_alert=False)
            return

        # XABAR YUBORISH LOGIKASI:
        # 1. Agar HAMKOR buyurtma bersa - sotuvchiga xabar
        # 2. Agar SOTUVCHI o'zi bersa - hech kimga xabar yuborilmaydi
        # 3. Agar ODDIY USER bersa - super adminlarga xabar
        
        if is_partner and seller_id:
            # HAMKOR buyurtma berdi - sotuvchiga xabar
            try:
                await send_order_notification_to_seller(bot, item, seller_id, is_partner_order=True)
            except Exception:
                pass
        elif not is_seller_user:
            # ODDIY USER buyurtma berdi - super adminlarga xabar
            # Oddiy user ekanligini tekshirish (admin emas)
            from services.admin_utils import is_any_admin
            if not is_any_admin(user_id):
                # Barcha super adminlarga xabar yuborish
                super_admins = get_super_admins()
                for super_admin_id_str in super_admins.keys():
                    try:
                        super_admin_id = int(super_admin_id_str)
                        await send_order_notification_to_super_admin(bot, item, super_admin_id)
                    except Exception:
                        # Xabar yuborishda xato bo'lsa, e'tiborsiz qoldiriladi
                        pass
                
                # Barcha yordamchi adminlarga ham xabar yuborish
                helper_admins = get_admins()
                for helper_admin_id_str in helper_admins.keys():
                    try:
                        helper_admin_id = int(helper_admin_id_str)
                        await send_order_notification_to_super_admin(bot, item, helper_admin_id)
                    except Exception:
                        # Xabar yuborishda xato bo'lsa, e'tiborsiz qoldiriladi
                        pass

        # State dan ready_size_is_view_all_page flag ni olish
        state_data = await state.get_data()
        is_view_all_page = state_data.get("ready_size_is_view_all_page", False)
        
        # Agar "Barchasini ko'rish" sahifasida bo'lsa - faqat popup ko'rsatish
        if is_view_all_page:
            await callback_query.answer(f"⚡ Siz {qty} dona mahsulotni buyurtmaga qo'shdingiz", show_alert=True)
            return

        # BARCHA eski xabarlarni o'chirish (rasm + matn + sonli tugmalar + orqaga)
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.message_id

        # 1) Joriy xabarni (son tanlash ekrani / mahsulot kartasi) o'chirish
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

        # 2) State dan barcha view_all xabarlarini olish va o'chirish
        view_all_message_ids = state_data.get("ready_size_view_all_ids", [])
        for msg_id in view_all_message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass

        # 3) Agar oldin saqlangan image/list xabarlari bo'lsa, ularni ham o'chirish
        image_id = state_data.get("ready_size_image_id")
        list_message_id = state_data.get("ready_size_list_message_id")
        if image_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=image_id)
            except Exception:
                pass
        if list_message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=list_message_id)
            except Exception:
                pass

        # 4) State ni tozalash – ekranni butunlay yangidan quramiz
        await state.update_data(
            ready_size_view_all_ids=[],
            ready_size_is_view_all_page=False,
            ready_size_image_id=None,
            ready_size_list_message_id=None,
        )
        
        # Yangi tasdiq xabarini yuborish
        success_text = f"⚡ Siz {qty} dona mahsulotni buyurtmaga qo'shdingiz"
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data=f"ready_size_order_success_back:{code}|{razmer}",
                )
            ]
        ])
        
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=success_text,
                reply_markup=back_keyboard
            )
        except Exception:
            pass
        
        await callback_query.answer("⚡ Buyurtmaga qo'shildi", show_alert=False)
        return

    # 1-variant: "ready_size_order:<code>|razmer" → miqdor tanlash yoki bevosita 1 dona
    # Code va razmer ni ajratib olish
    try:
        _, data_part = data.split(":", 1)
    except ValueError:
        await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
        return

    # Parse code|razmer
    if "|" in data_part:
        code, razmer = data_part.split("|", 1)
        code = code.strip()
        razmer = razmer.strip()
    else:
        # Backward compatibility: faqat code bo'lsa
        code = data_part.strip()
        razmer = ""

    # Sheets6 dan code+razmer bo'yicha ma'lumotni topish
    from services.google_sheet import CACHE
    sheets6_data = CACHE.get("sheets6", [])
    record = None
    for item in sheets6_data:
        item_code = item.get("code", "").strip()
        item_razmer = (item.get("razmer", "").strip() or "")
        # Agar razmer bo'sh bo'lsa, faqat code bo'yicha qidirish
        if razmer:
            # Razmer mavjud bo'lsa, code+razmer bo'yicha qidirish
            if item_code == code and item_razmer == razmer:
                record = item
                break
        else:
            # Razmer bo'sh bo'lsa, faqat code bo'yicha qidirish (birinchi topilgan)
            if item_code == code:
                record = item
                break

    if not record:
        await callback_query.answer("❌ Mahsulot topilmadi", show_alert=False)
        return

    # Razmer va soni (shtuk) ma'lumotini olish
    if not razmer:
        razmer = record.get("razmer", "").strip() or ""
    shtuk_raw = record.get("shtuk", "").strip() or ""
    
    # Soni int ga o'tkazish
    try:
        total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
    except Exception:
        total_qty = 0

    # Buyurtmaga allaqachon olingan sonni hisoblash
    from services.order_service import get_order_quantity_by_code
    from services.cart_service import get_cart_quantity_by_code
    order_qty = get_order_quantity_by_code(code, razmer)
    cart_qty = get_cart_quantity_by_code(code, razmer)

    # Qolgan soni (karzinka + buyurtma hisobga olinadi)
    remaining_count = total_qty - cart_qty - order_qty if total_qty > 0 else 0
    remaining_count = max(remaining_count, 0)

    # Qolgan sonni tekshirish (qo'shishdan OLDIN)
    if remaining_count <= 0:
        await callback_query.answer("❌ Qolmadi", show_alert=True)
        return

    # Qolgan soni (eski format - backward compatibility)
    remaining = remaining_count
    available = remaining_count

    # Agar soni 0 yoki 1 bo'lsa, bevosita 1 dona qo'shish
    if available <= 1:
        # Hamkor/sotuvchi kontekstini aniqlash
        is_partner, seller_id, seller_name, user_display = detect_partner_and_seller_order(
            user_id=user_id,
            first_name=first_name,
            username=username,
        )
        
        # Buyurtma beruvchi rolini aniqlash
        from services.admin_storage import is_seller
        is_seller_user = is_seller(user_id)

        item = get_or_create_order_item(
            user_id=user_id,
            code=code,
            qty=1,
            user_name=user_display,
            is_partner=is_partner,
            seller_id=seller_id,
            seller_name=seller_name,
            razmer=razmer,
        )
        if not item:
            await callback_query.answer("❌ Ma'lumot topilmadi", show_alert=False)
            return

        # XABAR YUBORISH LOGIKASI:
        # 1. Agar HAMKOR buyurtma bersa - sotuvchiga xabar
        # 2. Agar SOTUVCHI o'zi bersa - hech kimga xabar yuborilmaydi
        # 3. Agar ODDIY USER bersa - super adminlarga xabar
        
        if is_partner and seller_id:
            # HAMKOR buyurtma berdi - sotuvchiga xabar
            try:
                await send_order_notification_to_seller(bot, item, seller_id, is_partner_order=True)
            except Exception:
                pass
        elif not is_seller_user:
            # ODDIY USER buyurtma berdi - super adminlarga xabar
            # Oddiy user ekanligini tekshirish (admin emas)
            from services.admin_utils import is_any_admin
            if not is_any_admin(user_id):
                # Barcha super adminlarga xabar yuborish
                super_admins = get_super_admins()
                for super_admin_id_str in super_admins.keys():
                    try:
                        super_admin_id = int(super_admin_id_str)
                        await send_order_notification_to_super_admin(bot, item, super_admin_id)
                    except Exception:
                        # Xabar yuborishda xato bo'lsa, e'tiborsiz qoldiriladi
                        pass
                
                # Barcha yordamchi adminlarga ham xabar yuborish
                helper_admins = get_admins()
                for helper_admin_id_str in helper_admins.keys():
                    try:
                        helper_admin_id = int(helper_admin_id_str)
                        await send_order_notification_to_super_admin(bot, item, helper_admin_id)
                    except Exception:
                        # Xabar yuborishda xato bo'lsa, e'tiborsiz qoldiriladi
                        pass

        # State dan ready_size_is_view_all_page flag ni olish
        state_data = await state.get_data()
        is_view_all_page = state_data.get("ready_size_is_view_all_page", False)
        
        # Agar "Barchasini ko'rish" sahifasida bo'lsa - faqat popup ko'rsatish
        if is_view_all_page:
            await callback_query.answer("⚡ Siz 1 dona mahsulotni buyurtmaga qo'shdingiz", show_alert=True)
            return

        # BARCHA eski xabarlarni o'chirish (rasm + matn + tugmalar)
        chat_id = callback_query.message.chat.id
        message_id = callback_query.message.message_id

        # 1) Joriy xabarni (mahsulot kartasi) o'chirish
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

        # 2) State dan barcha view_all xabarlarini olish va o'chirish
        view_all_message_ids = state_data.get("ready_size_view_all_ids", [])
        for msg_id in view_all_message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass

        # 3) Agar oldin saqlangan image/list xabarlari bo'lsa, ularni ham o'chirish
        image_id = state_data.get("ready_size_image_id")
        list_message_id = state_data.get("ready_size_list_message_id")
        if image_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=image_id)
            except Exception:
                pass
        if list_message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=list_message_id)
            except Exception:
                pass

        # 4) State ni tozalash – ekranni butunlay yangidan quramiz
        await state.update_data(
            ready_size_view_all_ids=[],
            ready_size_is_view_all_page=False,
            ready_size_image_id=None,
            ready_size_list_message_id=None,
        )
        
        # Yangi tasdiq xabarini yuborish
        success_text = f"⚡ Siz 1 dona mahsulotni buyurtmaga qo'shdingiz"
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data=f"ready_size_order_success_back:{code}|{razmer}",
                )
            ]
        ])
        
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=success_text,
                reply_markup=back_keyboard
            )
        except Exception:
            pass
        
        await callback_query.answer("⚡ Buyurtmaga qo'shildi", show_alert=False)
        return

    # Aks holda: tanlash uchun inline tugmalar (1..remaining)
    # Juda katta bo'lsa, 20 donagacha cheklaymiz
    max_qty = min(remaining, 20)
    buttons = []
    row = []
    for i in range(1, max_qty + 1):
        row.append(
            InlineKeyboardButton(
                text=str(i),
                callback_data=f"ready_size_order_qty:{code}|{razmer}:{i}",
            )
        )
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # ⬅️ "Orqaga" tugmasi raqamli tugmalar ostiga qo'shiladi
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Orqaga",
                callback_data=f"ready_size_order_back:{code}|{razmer}",
            )
        ]
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await bot.edit_message_reply_markup(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        # Agar edit_markup ishlamasa, shunchaki javob qaytaramiz
        pass

    await callback_query.answer("Miqdorini tanlang", show_alert=False)


@router.callback_query(F.data == "open_ready_sizes_cart")
async def open_ready_sizes_cart(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Display cart items for current user or admin.
    """
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    # Admin view: see all cart items from all users
    if is_admin(user_id) or is_super_admin(user_id):
        all_items = get_cart_items_for_admin_view()
        if not all_items:
            menu_text = "🧺 Karzinka\n\nKarzinka bo'sh."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="ready_sizes"
                    )
                ]
            ])
            try:
                await callback_query.message.edit_text(
                    menu_text,
                    reply_markup=keyboard
                )
            except TelegramBadRequest:
                await callback_query.message.answer(
                    menu_text,
                    reply_markup=keyboard
                )
            await callback_query.answer()
            return
        
        # Admin view: send each cart item as photo message with owner info
        # Delete the menu message first
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=callback_query.message.message_id
            )
        except Exception:
            pass
        
        # Send each cart item as a photo message
        from services.google_sheet import GoogleSheetService
        from services.cart_service import build_cart_owner_text
        sheet_service = GoogleSheetService()
        
        # Karzinka xabarlarining ID larini saqlash uchun ro'yxat
        cart_message_ids = []
        
        for item in all_items:
            # Admin uchun kartochka format
            caption_text = (
                f"⚡ Buyurtmalar hisoboti\n"
                f"━━━━━━━━━━━━━━\n"
                f"Kod: {item.code}\n"
                f"Razmer: {item.razmer}\n"
                f"Kolleksiya: {item.kolleksiya}\n"
                f"Model: {item.model_nomi}\n"
                f"Miqdor: {item.qty}\n\n"
            )
            
            # Owner ma'lumotini qo'shish
            owner_text = build_cart_owner_text(item)
            if owner_text:
                caption_text += owner_text
            
            caption_text += "\n━━━━━━━━━━━━━━"
            
            # Admin uchun tugmalar
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 Buyurtma berish",
                        callback_data=f"ready_size_cart_order:{item.cart_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 O'chirish",
                        callback_data=f"remove_cart_item:{item.cart_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Orqaga",
                        callback_data="ready_size_cart_back"
                    )
                ]
            ])
            
            if item.image_url:
                converted_url = sheet_service._convert_google_drive_link(item.image_url)
                try:
                    msg = await bot.send_photo(
                        chat_id=chat_id,
                        photo=converted_url,
                        caption=caption_text,
                        reply_markup=keyboard
                    )
                    cart_message_ids.append(msg.message_id)
                    track_bot_message(chat_id, msg.message_id)
                except Exception:
                    # If image fails, send as text
                    try:
                        msg = await bot.send_message(
                            chat_id=chat_id,
                            text=caption_text,
                            reply_markup=keyboard
                        )
                        cart_message_ids.append(msg.message_id)
                        track_bot_message(chat_id, msg.message_id)
                    except Exception:
                        pass
            else:
                try:
                    msg = await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=keyboard
                    )
                    cart_message_ids.append(msg.message_id)
                    track_bot_message(chat_id, msg.message_id)
                except Exception:
                    pass
        
        # Karzinka xabarlarining ID larini state da saqlash
        await state.update_data(ready_size_cart_message_ids=cart_message_ids)
        
        await callback_query.answer()
        return
    
    # Regular user view: see only their cart items
    user_items = get_cart_items_for_user(user_id)
    if not user_items:
        menu_text = "🧺 Sizning karzinkangiz\n\nKarzinka bo'sh."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_sizes"
                )
            ]
        ])
        try:
            await callback_query.message.edit_text(
                menu_text,
                reply_markup=keyboard
            )
        except TelegramBadRequest:
            await callback_query.message.answer(
                menu_text,
                reply_markup=keyboard
            )
        await callback_query.answer()
        return
    
    # Delete the menu message first
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=callback_query.message.message_id
        )
    except Exception:
        pass
    
    # Send each cart item as a photo message
    from services.google_sheet import GoogleSheetService
    sheet_service = GoogleSheetService()
    
    # Karzinka xabarlarining ID larini saqlash uchun ro'yxat
    cart_message_ids = []
    
    for item in user_items:
        caption_text = (
            f"🧺 Karzinkangiz\n\n"
            f"📌 Kod: {item.code}\n"
            f"📐 Razmer: {item.razmer}\n"
            f"📦 Kolleksiya: {item.kolleksiya}\n"
            f"🧩 Model: {item.model_nomi}\n"
            f"📊 Miqdor: {item.qty}"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 O'chirish",
                    callback_data=f"remove_cart_item:{item.cart_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_size_cart_back"
                ),
                InlineKeyboardButton(
                    text="⚡ Buyurtma qilish",
                    callback_data=f"ready_size_cart_order:{item.cart_id}"
                )
            ]
        ])
        
        if item.image_url:
            converted_url = sheet_service._convert_google_drive_link(item.image_url)
            try:
                msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=converted_url,
                    caption=caption_text,
                    reply_markup=keyboard
                )
                cart_message_ids.append(msg.message_id)
            except Exception:
                # If image fails, send as text
                try:
                    msg = await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=keyboard
                    )
                    cart_message_ids.append(msg.message_id)
                except Exception:
                    pass
        else:
            try:
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=caption_text,
                    reply_markup=keyboard
                )
                cart_message_ids.append(msg.message_id)
            except Exception:
                pass
    
    # Karzinka xabarlarining ID larini state da saqlash
    await state.update_data(ready_size_cart_message_ids=cart_message_ids)
    
    await callback_query.answer()


@router.callback_query(F.data.startswith("remove_cart_item:"))
async def remove_cart_item_handler(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    🗑 O'chirish tugmasi - Karzinka bo'limi.
    
    LOGIKA:
    1) Rolni tekshirish (faqat admin/sotuvchi/hamkor)
    2) Karzinka elementini topib, qty ni qoldiqga qaytarish va o'chirish
       FORMULA: Q_YANGI = Q_ESKI + cart_qty
    3) UI navigatsiya:
       - Agar karzinkada 1 ta mahsulot bo'lsa → Tayyor razmerlar menyusiga qaytish
       - Agar bir nechta bo'lsa → faqat shu mahsulot o'chadi, qolganlari qoladi
    """
    user_id = callback_query.from_user.id

    # Parse callback data:
    # - New: "remove_cart_item:<cart_id>"
    # - Old (backward compatibility): "remove_cart_item:<code>:<razmer>"
    data = callback_query.data or ""
    payload = data.split(":", 1)[1] if ":" in data else ""
    if not payload:
        await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
        return

    cart_id = ""
    code = ""
    razmer = ""
    if ":" in payload:
        code, razmer = payload.split(":", 1)
    else:
        cart_id = payload.strip()
    
    print(f"[DELETE_DEBUG] step=remove_cart_item_handler_START, code={code}, size={razmer}, user_id={user_id}")

    # 1) Rolni tekshirish (faqat admin / super admin / sotuvchi / hamkor)
    from services.admin_storage import is_seller as _is_seller_storage
    from services.cart_service import detect_partner_and_seller as _detect_partner_cart

    is_admin_user = is_admin(user_id) or is_super_admin(user_id)
    is_seller_user = _is_seller_storage(user_id)
    try:
        is_partner_user, _, _, _ = _detect_partner_cart(user_id)
    except Exception:
        is_partner_user = False

    if not (is_admin_user or is_seller_user or is_partner_user):
        # Oddiy user → faqat ko'radi, ishlamaydi
        await callback_query.answer("❌ Bu tugma faqat admin / sotuvchi / hamkor uchun", show_alert=True)
        return

    # 2) Karzinka elementini topib, qty ni qoldiqga qaytarish va o'chirish
    # FORMULA: Q_YANGI = Q_ESKI + cart_qty
    print(f"[DELETE_DEBUG] step=remove_cart_item_handler_BEFORE_cancel, code={code}, size={razmer}")
    cancel_status = _cancel_single_cart_any_user(code, razmer, cart_id=cart_id)
    print(f"[DELETE_DEBUG] step=remove_cart_item_handler_AFTER_cancel, code={code}, size={razmer}, status={cancel_status}")
    if cancel_status == "invalid_qty":
        # qty 0 yoki noto'g'ri bo'lsa → o'chirish umuman ishlamasin
        await callback_query.answer("❌ Noto'g'ri miqdor, o'chirish amalga oshmadi", show_alert=True)
        return
    if cancel_status == "not_found":
        # Item allaqachon o'chirilgan yoki topilmadi → hech narsa qilmaymiz
        await callback_query.answer("🗑 O'chirildi", show_alert=False)
        return

    # 3) UI / xabarlar bilan ishlash
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id

    state_data = await state.get_data()
    cart_message_ids = state_data.get("ready_size_cart_message_ids", [])

    # Joriy xabarni ro'yxatdan olib tashlash
    if message_id in cart_message_ids:
        cart_message_ids.remove(message_id)

    # Joriy xabarni o'chiramiz
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=message_id
        )
    except Exception:
        pass

    # Agar karzinkada boshqa mahsulotlar qolgan bo'lsa → faqat shu mahsulot o'chadi, qolganlari qoladi
    if cart_message_ids:
        await state.update_data(ready_size_cart_message_ids=cart_message_ids)
        await callback_query.answer("🗑 O'chirildi", show_alert=False)
        return

    # Agar karzinkada faqat 1 ta mahsulot bo'lgan bo'lsa:
    # - barcha karzinka xabarlari tozalanadi (yuqorida o'chirildi)
    # - foydalanuvchi Tayyor razmerlar menyusiga qaytadi
    await state.update_data(ready_size_cart_message_ids=[])

    # Tayyor razmerlar menyusini ko'rsatish
    if is_admin_user:
        keyboard = make_admin_ready_sizes_menu_keyboard()
    else:
        keyboard = make_ready_sizes_menu_keyboard()

    try:
        await callback_query.message.answer(
            "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:",
            reply_markup=keyboard
        )
    except Exception:
        pass

    await callback_query.answer("🗑 O'chirildi", show_alert=False)


@router.callback_query(F.data == "ready_size_cart_back")
async def callback_ready_size_cart_back_from_cart(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Karzinka bo'limidan "Orqaga" tugmasi bosilganda:
    - Barcha karzinka xabarlarini o'chirish (GLOBAL)
    - Tayyor razmerlar bo'limiga qaytish
    
    Qaysi mahsulot tagidagi "Orqaga" bosilishidan qat'iy nazar,
    barcha karzinka xabarlari o'chiriladi.
    """
    await callback_query.answer()
    
    chat_id = callback_query.message.chat.id
    
    # State dan barcha karzinka xabarlarining ID larini olish
    state_data = await state.get_data()
    cart_message_ids = state_data.get("ready_size_cart_message_ids", [])
    
    # Joriy xabarni ham qo'shish (agar state da bo'lmasa)
    current_message_id = callback_query.message.message_id
    all_message_ids = set(cart_message_ids) if cart_message_ids else set()
    all_message_ids.add(current_message_id)
    
    # BARCHA karzinka xabarlarini o'chirish (GLOBAL o'chirish)
    # Qaysi mahsulot tagidagi "Orqaga" bosilishidan qat'iy nazar,
    # barcha xabarlar o'chiriladi
    deleted_count = 0
    for msg_id in all_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted_count += 1
        except Exception:
            # Xabar allaqachon o'chirilgan yoki topilmagan bo'lishi mumkin
            pass
    
    # State ni tozalash (barcha karzinka xabarlari o'chirilgani uchun)
    await state.update_data(ready_size_cart_message_ids=[])
    
    # Tayyor razmerlar menyusini yuborish
    user_id = callback_query.from_user.id
    
    # Admin uchun buyurtmalar tugmasi ko'rsatiladi
    if is_admin(user_id) or is_super_admin(user_id):
        keyboard = make_admin_ready_sizes_menu_keyboard()
    else:
        keyboard = make_ready_sizes_menu_keyboard()
    
    menu_text = "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:"
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("ready_size_cart_order"))
async def callback_ready_size_cart_order(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Karzinka bo'limidan "Buyurtma qilish" tugmasi bosilganda.
    
    LOGIKA:
    1) Xabar caption dan code, razmer, qty ni olish
    2) Karzinka elementini buyurtmaga qo'shish
    3) Karzinka elementini o'chirish
    4) Barcha karzinka xabarlarini o'chirish
    5) Muvaffaqiyat xabarini ko'rsatish
    6) Tayyor razmerlar menyusiga qaytish
    """
    user = callback_query.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""
    chat_id = callback_query.message.chat.id

    # Callback data:
    # - New: ready_size_cart_order:<cart_id>
    # - Old (backward compatibility): ready_size_cart_order
    data = callback_query.data or ""
    cart_id = ""
    if ":" in data:
        _, payload = data.split(":", 1)
        cart_id = (payload or "").strip()

    code = None
    razmer = None
    qty = None

    if cart_id:
        cart_item = get_cart_item_by_id(cart_id)
        if not cart_item:
            await callback_query.answer("❌ Karzinka elementi topilmadi", show_alert=False)
            return
        code = (cart_item.code or "").strip()
        razmer = (cart_item.razmer or "").strip()
        try:
            qty = int(cart_item.qty)
        except Exception:
            qty = None
    else:
        # Xabar caption dan mahsulot ma'lumotlarini olish (old callback format uchun)
        message_text = ""
        if callback_query.message.caption:
            message_text = callback_query.message.caption
        elif callback_query.message.text:
            message_text = callback_query.message.text
        else:
            await callback_query.answer("❌ Ma'lumot topilmadi", show_alert=False)
            return
        
        # Parse code, razmer, qty from caption
        # Format: "🧺 Karzinkangiz\n\n📌 Kod: {code}\n📐 Razmer: {razmer}\n📦 Kolleksiya: {kolleksiya}\n🧩 Model: {model}\n📊 Miqdor: {qty}"
        lines = message_text.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("📌 Kod:"):
                code = line.replace("📌 Kod:", "").strip()
            elif line.startswith("📐 Razmer:"):
                razmer = line.replace("📐 Razmer:", "").strip()
            elif line.startswith("📊 Miqdor:"):
                qty_str = line.replace("📊 Miqdor:", "").strip()
                try:
                    qty = int(qty_str)
                except (ValueError, TypeError):
                    qty = None
    
    if not code or qty is None:
        await callback_query.answer("❌ Ma'lumot topilmadi", show_alert=False)
        return
    
    if not razmer:
        razmer = ""
    
    # Qolgan sonni tekshirish (qo'shishdan OLDIN)
    from services.google_sheet import CACHE
    sheets6_data = CACHE.get("sheets6", [])
    record = None
    for item in sheets6_data:
        item_code = item.get("code", "").strip()
        item_razmer = (item.get("razmer", "").strip() or "")
        if item_code == code and item_razmer == razmer:
            record = item
            break
    
    if not record:
        await callback_query.answer("❌ Mahsulot topilmadi", show_alert=False)
        return
    
    cart_qty = get_cart_quantity_by_code(code, razmer)
    order_qty = get_order_quantity_by_code(code, razmer)
    shtuk_raw = record.get("shtuk", "").strip() or ""
    
    try:
        total_qty = int(float(shtuk_raw.replace(",", ".").replace(" ", ""))) if shtuk_raw else 0
    except Exception:
        total_qty = 0
    
    # MUHIM: Karzinkadan buyurtmaga o'tkazishda, mahsulot hali karzinkada bo'lgani uchun
    # cart_qty ichida qty miqdori bor. O'tkazishdan keyin cart_qty - qty bo'ladi.
    # Shuning uchun qolgan stock: total_qty - (cart_qty - qty) - order_qty = total_qty - cart_qty - order_qty + qty
    # Lekin mahsulot karzinkada bo'lgani uchun, uni buyurtmaga o'tkazish mumkin.
    # Faqat mahsulot mavjudligini tekshiramiz.
    if total_qty <= 0:
        await callback_query.answer("❌ Qolmadi", show_alert=True)
        return
    
    # Hamkor/sotuvchi kontekstini aniqlash
    is_partner, seller_id, seller_name, user_display = detect_partner_and_seller_order(
        user_id=user_id,
        first_name=first_name,
        username=username,
    )
    
    # Buyurtma beruvchi rolini aniqlash
    from services.admin_storage import is_seller
    is_seller_user = is_seller(user_id)
    
    # Karzinka elementini buyurtmaga qo'shish
    item = get_or_create_order_item(
        user_id=user_id,
        code=code,
        qty=qty,
        user_name=user_display,
        is_partner=is_partner,
        seller_id=seller_id,
        seller_name=seller_name,
        razmer=razmer,
    )
    
    if not item:
        await callback_query.answer("❌ Ma'lumot topilmadi", show_alert=False)
        return
    
    # XABAR YUBORISH LOGIKASI:
    # 1. Agar HAMKOR buyurtma bersa - sotuvchiga xabar
    # 2. Agar SOTUVCHI o'zi bersa - hech kimga xabar yuborilmaydi
    # 3. Agar ODDIY USER bersa - super adminlarga xabar
    
    if is_partner and seller_id:
        # HAMKOR buyurtma berdi - sotuvchiga xabar
        try:
            await send_order_notification_to_seller(bot, item, seller_id, is_partner_order=True)
        except Exception:
            pass
    elif not is_seller_user:
        # ODDIY USER buyurtma berdi - super adminlarga xabar
        from services.admin_utils import is_any_admin
        if not is_any_admin(user_id):
            # Barcha super adminlarga xabar yuborish
            super_admins = get_super_admins()
            for super_admin_id_str in super_admins.keys():
                try:
                    super_admin_id = int(super_admin_id_str)
                    await send_order_notification_to_super_admin(bot, item, super_admin_id)
                except Exception:
                    pass
            
            # Barcha yordamchi adminlarga ham xabar yuborish
            helper_admins = get_admins()
            for helper_admin_id_str in helper_admins.keys():
                try:
                    helper_admin_id = int(helper_admin_id_str)
                    await send_order_notification_to_super_admin(bot, item, helper_admin_id)
                except Exception:
                    pass
    
    # Karzinka elementini o'chirish
    cancel_status = _cancel_single_cart_any_user(code, razmer, cart_id=cart_id)
    if cancel_status == "invalid_qty":
        await callback_query.answer("❌ Noto'g'ri miqdor", show_alert=True)
        return
    
    # BARCHA karzinka xabarlarini o'chirish
    state_data = await state.get_data()
    cart_message_ids = state_data.get("ready_size_cart_message_ids", [])
    
    # Joriy xabarni ham qo'shish
    current_message_id = callback_query.message.message_id
    all_message_ids = set(cart_message_ids) if cart_message_ids else set()
    all_message_ids.add(current_message_id)
    
    # BARCHA karzinka xabarlarini o'chirish
    for msg_id in all_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    
    # State ni tozalash
    await state.update_data(ready_size_cart_message_ids=[])
    
    # Muvaffaqiyat xabarini ko'rsatish
    success_text = f"⚡ Siz {qty} dona mahsulotni buyurtmaga qo'shdingiz"
    await callback_query.answer(success_text, show_alert=True)
    
    # Tayyor razmerlar menyusiga qaytish
    # Admin uchun buyurtmalar tugmasi ko'rsatiladi
    if is_admin(user_id) or is_super_admin(user_id):
        keyboard = make_admin_ready_sizes_menu_keyboard()
    else:
        keyboard = make_ready_sizes_menu_keyboard()
    
    menu_text = "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:"
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception:
        pass


@router.callback_query(F.data == "ready_sizes_orders_soon")
async def callback_ready_sizes_orders_soon(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    Display order items for current user or admin.
    """
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    # Admin view: see all order items from all users
    if is_admin(user_id) or is_super_admin(user_id):
        all_items = get_order_items_for_admin_view()
        if not all_items:
            menu_text = "⚡ Buyurtmalar\n\nBuyurtmalar bo'sh."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="ready_size_orders_back"
                    )
                ]
            ])
            try:
                await callback_query.message.edit_text(
                    menu_text,
                    reply_markup=keyboard
                )
                # Admin view uchun ham message ID ni saqlash
                await state.update_data(ready_size_order_message_ids=[callback_query.message.message_id])
            except TelegramBadRequest:
                msg = await callback_query.message.answer(
                    menu_text,
                    reply_markup=keyboard
                )
                # Admin view uchun ham message ID ni saqlash
                await state.update_data(ready_size_order_message_ids=[msg.message_id])
            await callback_query.answer()
            return
        
        # Admin view: send each order item as photo message with owner info
        # Delete the menu message first
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=callback_query.message.message_id
            )
        except Exception:
            pass
        
        # Send each order item as a photo message
        from services.google_sheet import GoogleSheetService
        from services.order_service import build_order_owner_text
        sheet_service = GoogleSheetService()
        
        # Buyurtma xabarlarining ID larini saqlash uchun ro'yxat
        order_message_ids = []
        
        for item in all_items:
            # Admin uchun kartochka format
            caption_text = (
                f"⚡ Buyurtmalar hisoboti\n"
                f"━━━━━━━━━━━━━━\n"
                f"Kod: {item.code}\n"
                f"Razmer: {item.razmer}\n"
                f"Kolleksiya: {item.kolleksiya}\n"
                f"Model: {item.model_nomi}\n"
                f"Miqdor: {item.qty}\n\n"
            )
            
            # Owner ma'lumotini qo'shish
            owner_text = build_order_owner_text(item)
            if owner_text:
                caption_text += owner_text
            
            caption_text += "\n━━━━━━━━━━━━━━"
            
            # Admin uchun tugmalar
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Tasdiqlash",
                        callback_data=f"order_confirm_placeholder:{item.code}:{item.razmer}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 O'chirish",
                        callback_data=f"remove_order_item:{item.code}:{item.razmer}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Orqaga",
                        callback_data="ready_size_orders_back"
                    )
                ]
            ])
            
            if item.image_url:
                converted_url = sheet_service._convert_google_drive_link(item.image_url)
                try:
                    msg = await bot.send_photo(
                        chat_id=chat_id,
                        photo=converted_url,
                        caption=caption_text,
                        reply_markup=keyboard
                    )
                    order_message_ids.append(msg.message_id)
                    track_bot_message(chat_id, msg.message_id)
                except Exception:
                    # If image fails, send as text
                    try:
                        msg = await bot.send_message(
                            chat_id=chat_id,
                            text=caption_text,
                            reply_markup=keyboard
                        )
                        order_message_ids.append(msg.message_id)
                        track_bot_message(chat_id, msg.message_id)
                    except Exception:
                        pass
            else:
                try:
                    msg = await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=keyboard
                    )
                    order_message_ids.append(msg.message_id)
                    track_bot_message(chat_id, msg.message_id)
                except Exception:
                    pass
        
        # Buyurtma xabarlarining ID larini state da saqlash
        await state.update_data(ready_size_order_message_ids=order_message_ids)
        
        await callback_query.answer()
        return
    
    # Role-based view: filter orders by user role
    user_items = get_order_items_by_role(user_id)
    if not user_items:
        menu_text = "⚡ Sizning buyurtmalaringiz\n\nBuyurtmalar bo'sh."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_size_orders_back"
                )
            ]
        ])
        try:
            await callback_query.message.edit_text(
                menu_text,
                reply_markup=keyboard
            )
            # Regular user view uchun ham message ID ni saqlash
            await state.update_data(ready_size_order_message_ids=[callback_query.message.message_id])
        except TelegramBadRequest:
            msg = await callback_query.message.answer(
                menu_text,
                reply_markup=keyboard
            )
            # Regular user view uchun ham message ID ni saqlash
            await state.update_data(ready_size_order_message_ids=[msg.message_id])
        await callback_query.answer()
        return
    
    # Determine current user's role for display logic
    is_partner, _, _, _ = detect_partner_and_seller_order(user_id)
    
    # Delete the menu message first
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=callback_query.message.message_id
        )
    except Exception:
        pass
    
    # Send each order item as a photo message
    from services.google_sheet import GoogleSheetService
    sheet_service = GoogleSheetService()
    
    # Buyurtma xabarlarining ID larini saqlash uchun ro'yxat
    order_message_ids = []
    
    for item in user_items:
        # Build caption based on role
        caption_text = (
            f"⚡ Buyurtmalaringiz\n\n"
            f"📌 Kod: {item.code}\n"
            f"📐 Razmer: {item.razmer}\n"
            f"📦 Kolleksiya: {item.kolleksiya}\n"
            f"🧩 Model: {item.model_nomi}\n"
            f"📊 Miqdor: {item.qty}"
        )
        
        # Hamkor uchun: sotuvchi ma'lumotini ko'rsatish
        # Faqat o'zi bergan buyurtmalar uchun (item.user_id == user_id)
        if is_partner and item.user_id == user_id and item.seller_id and item.seller_name:
            caption_text += f"\n\n🤝 Sotuvchi: {item.seller_name}"
        
        # Sotuvchi uchun: hamkor buyurtmasi bo'lsa hamkor nomini ko'rsatish
        # Sotuvchi o'zi bergan bo'lsa - hech qanday qo'shimcha ma'lumot yo'q
        from services.admin_storage import is_seller
        if is_seller(user_id) and item.is_partner and item.seller_id == user_id:
            # Hamkor buyurtmasi
            caption_text += f"\n\n🤝 Hamkor: {item.user_name}"
        
        # Buyurtma vaqtini qo'shish (DD.MM.YYYY HH:MM formatida)
        from datetime import datetime
        if item.added_at:
            try:
                # UTC vaqtni local vaqtga o'tkazish (yoki UTC ni to'g'ridan-to'g'ri ko'rsatish)
                order_time = item.added_at
                # Format: DD.MM.YYYY HH:MM
                time_str = order_time.strftime("%d.%m.%Y %H:%M")
                caption_text += f"\n🕒 Buyurtma vaqti: {time_str}"
            except Exception:
                # Vaqtni formatlashda xato bo'lsa, e'tiborsiz qoldiriladi
                pass
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash",
                    callback_data=f"order_confirm_placeholder:{item.code}:{item.razmer}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 O'chirish",
                    callback_data=f"remove_order_item:{item.code}:{item.razmer}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="ready_size_orders_back"
                )
            ]
        ])
        
        if item.image_url:
            converted_url = sheet_service._convert_google_drive_link(item.image_url)
            try:
                msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=converted_url,
                    caption=caption_text,
                    reply_markup=keyboard
                )
                order_message_ids.append(msg.message_id)
            except Exception:
                # If image fails, send as text
                try:
                    msg = await bot.send_message(
                        chat_id=chat_id,
                        text=caption_text,
                        reply_markup=keyboard
                    )
                    order_message_ids.append(msg.message_id)
                except Exception:
                    pass
        else:
            try:
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=caption_text,
                    reply_markup=keyboard
                )
                order_message_ids.append(msg.message_id)
            except Exception:
                pass
    
    # Buyurtma xabarlarining ID larini state da saqlash
    await state.update_data(ready_size_order_message_ids=order_message_ids)
    
    await callback_query.answer()


@router.callback_query(F.data.startswith("order_confirm_placeholder:"))
async def callback_order_confirm_placeholder(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    [✅ Tasdiqlash] tugmasi bosilganda (Buyurtmalar bo'limida).
    
    Umumiy logika:
    1) Kim bosganini tekshirish:
       - Admin / Super admin → ruxsat beriladi
       - Sotuvchi → ruxsat beriladi
       - Hamkor → ruxsat beriladi
       - Oddiy user → faqat xabar ko'radi, lekin ishlamaydi
    2) Tayyor razmerlar ro'yxatidan BUYURTMA MIQDORI ga teng miqdorda ayriladi.
       QOIDA: QOLDIQ_YANGI = QOLDIQ_ESKI - BUYURTMA_MIqdORI
       - Agar QOLDIQ_YANGI > 0 bo'lsa → yangilangan qoldiq yoziladi
       - Agar QOLDIQ_YANGI <= 0 bo'lsa → element ro'yxatdan butunlay o'chiriladi
    3) Buyurtmalar bo'limidagi xabarlar:
       - Shu mahsulot xabari o'chadi
       - Agar bu bo'limda faqat bitta xabar bo'lsa → Tayyor razmerlar menyusiga qaytadi
       - Aks holda qolgan xabarlar o'z holicha qoladi
    """
    user_id = callback_query.from_user.id
    data = callback_query.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
        return
    
    code = parts[1]
    razmer = parts[2]

    # 1) Rolni tekshirish (admin / super admin / sotuvchi / hamkor)
    from services.admin_storage import is_seller as _is_seller_storage
    is_admin_user = is_admin(user_id) or is_super_admin(user_id)
    is_seller_user = _is_seller_storage(user_id)
    is_partner_user, _, _, _ = detect_partner_and_seller_order(user_id)

    if not (is_admin_user or is_seller_user or is_partner_user):
        # Oddiy user → faqat ko'radi, ishlamaydi
        await callback_query.answer("❌ Bu tugma faqat admin / sotuvchi / hamkor uchun", show_alert=True)
        return

    # 2) RAM ichidagi buyurtmalar ro'yxatidan BIR DONA umumiy buyurtmani topish,
    #    BUYURTMA MIQDORI ga teng miqdorda stokdan ayrish va buyurtmani o'chirish
    # Get confirmer name
    confirmer_name = callback_query.from_user.first_name or callback_query.from_user.username or str(user_id)
    _remove_single_order_any_user(code, razmer, confirmer_id=user_id, confirmer_name=confirmer_name)

    # 3) Buyurtmalar bo'limidagi xabarlarni boshqarish
    message_id = callback_query.message.message_id
    state_data = await state.get_data()
    order_message_ids = state_data.get("ready_size_order_message_ids", [])

    if message_id in order_message_ids:
        order_message_ids.remove(message_id)
        await state.update_data(ready_size_order_message_ids=order_message_ids)

    # 4.1) Hozirgi xabarni o'chiramiz
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=message_id
        )
    except Exception:
        pass

    # 4.2) Agar boshqa buyurtma xabarlari qolmagan bo'lsa → Tayyor razmerlar menyusiga qaytish
    if not order_message_ids:
        # Foydalanuvchini Tayyor razmerlar menyusiga qaytaramiz
        keyboard = None
        if is_admin_user:
            keyboard = make_admin_ready_sizes_menu_keyboard()
        else:
            keyboard = make_ready_sizes_menu_keyboard()

        try:
            await callback_query.message.answer(
                "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:",
                reply_markup=keyboard
            )
        except Exception:
            pass

    # Foydalanuvchiga qisqa vizual bildirish (toast)
    await callback_query.answer("✅ Buyurtma tasdiqlandi", show_alert=False)


@router.callback_query(F.data.startswith("remove_order_item:"))
async def remove_order_item_handler(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    🗑 O'chirish tugmasi - Buyurtmalar bo'limi yoki xabarlar.
    
    LOGIKA:
    1) Rolni tekshirish (faqat admin/sotuvchi/hamkor)
    2) Buyurtma elementini topib, qty ni qoldiqga qaytarish va o'chirish
       FORMULA: Q_YANGI = Q_ESKI + order_qty
    3) UI navigatsiya:
       A) Buyurtmalar bo'limida:
          - Agar 1 ta mahsulot bo'lsa → Tayyor razmerlar menyusiga qaytish
          - Agar bir nechta bo'lsa → faqat shu mahsulot o'chadi, qolganlari qoladi
       B) Xabarlarda (hamkor→sotuvchi, user→admin):
          - Agar 1 tadan ko'p xabar bo'lsa → faqat shu xabar o'chadi
          - Agar faqat 1 ta xabar bo'lsa → asosiy menyu chiqadi
    """
    user_id = callback_query.from_user.id

    # Parse callback data: "remove_order_item:<code>:<razmer>"
    data = callback_query.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
        return

    code = parts[1]
    razmer = parts[2]
    
    print(f"[DELETE_DEBUG] step=remove_order_item_handler_START, code={code}, size={razmer}, user_id={user_id}")

    # 1) Rolni tekshirish (faqat admin / super admin / sotuvchi / hamkor)
    from services.admin_storage import is_seller as _is_seller_storage

    is_admin_user = is_admin(user_id) or is_super_admin(user_id)
    is_seller_user = _is_seller_storage(user_id)
    is_partner_user, _, _, _ = detect_partner_and_seller_order(user_id)

    if not (is_admin_user or is_seller_user or is_partner_user):
        # Oddiy user → faqat ko'radi, ishlamaydi
        await callback_query.answer("❌ Bu tugma faqat admin / sotuvchi / hamkor uchun", show_alert=True)
        return

    # 2) Buyurtma elementini topib, qty ni qoldiqga qaytarish va o'chirish
    # FORMULA: Q_YANGI = Q_ESKI + order_qty
    print(f"[DELETE_DEBUG] step=remove_order_item_handler_BEFORE_cancel, code={code}, size={razmer}")
    cancel_status = _cancel_single_order_any_user(code, razmer)
    print(f"[DELETE_DEBUG] step=remove_order_item_handler_AFTER_cancel, code={code}, size={razmer}, status={cancel_status}")
    if cancel_status == "invalid_qty":
        # qty 0 yoki noto'g'ri bo'lsa → o'chirish umuman ishlamasin
        await callback_query.answer("❌ Noto'g'ri miqdor, o'chirish amalga oshmadi", show_alert=True)
        return
    if cancel_status == "not_found":
        # Item allaqachon o'chirilgan yoki topilmadi → hech narsa qilmaymiz
        await callback_query.answer("🗑 O'chirildi", show_alert=False)
        return

    # 3) UI / xabarlar bilan ishlash
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.message_id

    state_data = await state.get_data()
    order_message_ids = state_data.get("ready_size_order_message_ids", [])

    # Buyurtmalar bo'limida ekanligini aniqlash
    in_orders_section = bool(order_message_ids) and message_id in order_message_ids

    # Joriy xabarni ro'yxatdan olib tashlash
    if message_id in order_message_ids:
        order_message_ids.remove(message_id)
        await state.update_data(ready_size_order_message_ids=order_message_ids)

    # Joriy xabarni o'chiramiz
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=message_id
        )
    except Exception:
        pass

    # A) Agar bu Buyurtmalar bo'limidan bosilgan bo'lsa:
    if in_orders_section:
        # Agar boshqa buyurtma xabarlari qolmagan bo'lsa → Tayyor razmerlar menyusiga qaytish
        if not order_message_ids:
            if is_admin_user:
                keyboard = make_admin_ready_sizes_menu_keyboard()
            else:
                keyboard = make_ready_sizes_menu_keyboard()

            try:
                await callback_query.message.answer(
                    "👟 Tayyor razmerlar bo'limi\n\nQuyidagi variantlardan birini tanlang:",
                    reply_markup=keyboard
                )
            except Exception:
                pass

        await callback_query.answer("🗑 O'chirildi", show_alert=False)
        return

    # B) Aks holda bu xabarlar (hamkor→sotuvchi yoki user→admin xabarlari tagidagi)
    # MUHIM: Agar chatda boshqa buyurtma xabarlari ham bo'lsa → faqat shu xabar o'chadi, qolganlari qoladi
    # FAQAT AGAR bu oxirgi buyurtma bo'lsa (chatda boshqa buyurtma xabari qolmagan bo'lsa) → asosiy menyu chiqadi
    from services.order_service import get_order_items_by_role

    # O'chirilgan buyurtmadan keyin qolgan buyurtmalarni tekshirish
    remaining_orders = get_order_items_by_role(user_id)
    
    # AGAR chatda hali boshqa buyurtma xabarlari ham qolgan bo'lsa (ya'ni bu oxirgisi bo'lmasa):
    # - Asosiy menyu umuman chiqarilmasin
    # - Bot hech qanday qo'shimcha matn yubormasin
    # - Faqat o'sha xabar o'chsin va qolgan buyurtmalar joyida tursin
    # FAQAT AGAR bu oxirgi buyurtma bo'lsa (chatda boshqa buyurtma xabari qolmagan bo'lsa):
    # - Bot 🏠 Asosiy menyuni chiqarishi kerak
    if not remaining_orders:
        # Oxirgi buyurtma o'chirildi → asosiy menyu va to'liq salomlashuv matni
        try:
            keyboard = make_main_menu_keyboard(user_id)
            await callback_query.message.answer(
                "Assalomu alaykum.\n"
                "Tizimga xush kelibsiz.\n"
                "Quyidagi bo'limlardan birini tanlang:",
                reply_markup=keyboard
            )
        except Exception:
            pass

    await callback_query.answer("🗑 O'chirildi", show_alert=False)


# ==================== SOTUVCHIGA BUYURTMA XABARI ====================

async def cleanup_seller_chat(bot: Bot, seller_chat_id: int):
    """
    Sotuvchi chatidagi BARCHA xabarlarni o'chirish.
    Track qilingan va track qilinmagan barcha xabarlarni o'chirishga harakat qiladi.
    """
    # 1) Track qilingan bot xabarlarini o'chirish
    await cleanup_all_bot_messages(bot, seller_chat_id)
    
    # 2) Oxirgi 100 ta xabarni o'chirishga harakat qilish
    # Telegram API limiti: faqat oxirgi 48 soat ichidagi xabarlarni o'chirish mumkin
    # Bizda barcha message_id larni bilmaymiz, shuning uchun eng katta message_id dan boshlab
    # pastga qarab o'chirishga harakat qilamiz
    # Eslatma: Bu metod to'liq ishonchli emas, lekin ko'p hollarda ishlaydi
    
    # Eng katta message_id ni taxmin qilish uchun hozirgi vaqtni ishlatamiz
    # Lekin bu aniq emas, shuning uchun biz faqat track qilingan xabarlarni o'chiramiz
    # Va agar kerak bo'lsa, foydalanuvchi qo'lda tozalashi mumkin
    pass


async def send_order_notification_to_seller(bot: Bot, item, seller_chat_id: int, is_partner_order: bool = False):
    """
    Sotuvchiga buyurtma notify xabarini yuborish.
    Faqat bitta notify xabarini yuboradi.
    Buyurtmalar ro'yxatini ko'rsatmaydi - bu alohida callback orqali amalga oshiriladi.
    
    Args:
        is_partner_order: Agar True bo'lsa, hamkor buyurtmasi, False bo'lsa sotuvchi o'zi bergan buyurtma
    """
    from services.google_sheet import GoogleSheetService
    
    # Sotuvchi ekranini tozalash OLIB TASHLANDI - xabarlar to'plansin
    # await cleanup_all_bot_messages(bot, seller_chat_id)
    
    # 1) Buyurtma matnini yaratish
    if is_partner_order:
        # Hamkor buyurtmasi
        order_text = (
            f"⚡ <b>Yangi buyurtma</b>\n\n"
            f"👤 <b>Hamkoringiz {item.user_name} buyurtma berdi</b>\n"
            f"📌 <b>Kod:</b> {item.code}\n"
            f"📐 <b>Razmer:</b> {item.razmer}\n"
            f"📦 <b>Kolleksiya:</b> {item.kolleksiya}\n"
            f"🧩 <b>Model:</b> {item.model_nomi}\n"
            f"📊 <b>Miqdor:</b> {item.qty} dona"
        )
    else:
        # Sotuvchi o'zi buyurtma berdi (bu holatda xabar yuborilmaydi, lekin funksiya mavjud)
        order_text = (
            f"⚡ <b>Yangi buyurtma</b>\n\n"
            f"👤 <b>Siz buyurtma berdingiz</b>\n"
            f"📌 <b>Kod:</b> {item.code}\n"
            f"📐 <b>Razmer:</b> {item.razmer}\n"
            f"📦 <b>Kolleksiya:</b> {item.kolleksiya}\n"
            f"🧩 <b>Model:</b> {item.model_nomi}\n"
            f"📊 <b>Miqdor:</b> {item.qty} dona"
        )
    
    # 2) Tugmalarni yaratish
    # Hamkor buyurtmasi xabari uchun tugmalar:
    # [✅ Tasdiqlash] [🗑 O'chirish] [🏠 Asosiy menyu]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Tasdiqlash",
                callback_data=f"seller_confirm_order:{item.code}:{item.razmer}"
            ),
            InlineKeyboardButton(
                text="🗑 O'chirish",
                callback_data=f"remove_order_item:{item.code}:{item.razmer}"
            ),
            InlineKeyboardButton(
                text="🏠 Asosiy menyu",
                callback_data=f"seller_order_main_menu:{item.code}:{item.razmer}"
            )
        ]
    ])
    
    # 3) Rasm bilan yoki rasmiz yuborish (FAQAT BITTA NOTIFY XABAR)
    sheet_service = GoogleSheetService()
    
    if item.image_url:
        converted_url = sheet_service._convert_google_drive_link(item.image_url)
        try:
            sent = await bot.send_photo(
                chat_id=seller_chat_id,
                photo=converted_url,
                caption=order_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            # Sotuvchi chatidagi buyurtma xabarini track qilamiz (keyin tozalash uchun)
            track_bot_message(seller_chat_id, sent.message_id)
        except Exception:
            # Rasm yuborishda xato bo'lsa, text xabar sifatida yuborish
            try:
                sent = await bot.send_message(
                    chat_id=seller_chat_id,
                    text=order_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                track_bot_message(seller_chat_id, sent.message_id)
            except Exception:
                pass
    else:
        # Text xabar sifatida yuborish
        try:
            sent = await bot.send_message(
                chat_id=seller_chat_id,
                text=order_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            track_bot_message(seller_chat_id, sent.message_id)
        except Exception:
            pass
    
    # Eslatma: Buyurtmalar ro'yxatini ko'rsatish alohida callback (ready_sizes_orders_soon) orqali amalga oshiriladi
    # Bu yerda faqat notify xabarini yuboramiz, buyurtma ma'lumoti allaqachon saqlangan


@router.callback_query(F.data.startswith("seller_confirm_order:"))
async def callback_seller_confirm_order(callback_query: CallbackQuery, bot: Bot):
    """
    Sotuvchi chatida (hamkor buyurtma berganda) [✅ Tasdiqlash] tugmasi bosilganda.
    
    Logika:
    - Faqat sotuvchi / admin / super admin tasdiqlashi mumkin
    - Tayyor razmerlar stokidan BUYURTMA MIQDORI ga teng miqdorda ayriladi
    - QOIDA: QOLDIQ_YANGI = QOLDIQ_ESKI - BUYURTMA_MIqdORI
    - Shu buyurtma xabari o'chiriladi
    - Agar chatda boshqa xabarlar bo'lsa, ular o'z holicha qoladi
    - Asosiy menyu avtomatik ko'rsatiladi
    """
    user_id = callback_query.from_user.id
    data = callback_query.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
        return

    code = parts[1]
    razmer = parts[2]

    from services.admin_storage import is_seller as _is_seller_storage
    is_admin_user = is_admin(user_id) or is_super_admin(user_id)
    is_seller_user = _is_seller_storage(user_id)

    # Hamkor bu chatga to'g'ridan-to'g'ri kelmaydi, lekin xavfsizlik uchun tekshiruv
    is_partner_user, _, _, _ = detect_partner_and_seller_order(user_id)

    if not (is_admin_user or is_seller_user or is_partner_user):
        await callback_query.answer("❌ Bu tugma faqat admin / sotuvchi / hamkor uchun", show_alert=True)
        return

    # RAM ichidagi buyurtmalardan BIR DONA umumiy buyurtmani topish,
    # BUYURTMA MIQDORI ga teng miqdorda stokdan ayrish va buyurtmani o'chirish
    _remove_single_order_any_user(code, razmer)

    # Xabarni o'chirish
    chat_id = callback_query.message.chat.id
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=callback_query.message.message_id
        )
    except Exception:
        pass

    # Qolgan buyurtmalarni tekshirish
    from services.order_service import get_order_items_by_role
    remaining_orders = get_order_items_by_role(user_id)
    
    if not remaining_orders:
        # Oxirgi buyurtma edi - asosiy menyu ko'rsatish
        keyboard = make_main_menu_keyboard(user_id)
        menu_text = (
            "Assalomu alaykum.\n"
            "Tizimga xush kelibsiz.\n\n"
            "Quyidagi bo'limlardan birini tanlang:"
        )
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            store_main_menu_message(chat_id, sent.message_id)
        except Exception:
            pass

    # Vizual bildirish
    await callback_query.answer("✅ Buyurtma tasdiqlandi", show_alert=False)


@router.callback_query(F.data.startswith("seller_order_main_menu:"))
async def callback_seller_order_main_menu(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    [Asosiy menyu] tugmasi bosilganda (buyurtma xabari tagida).
    SHU CHAT ichidagi barcha buyurtma xabarlarini o'chiradi
    va foydalanuvchini UMUMIY ASOSIY MENYUGA qaytaradi.
    """
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    # 1) Sotuvchi chatidagi BARCHA buyurtma xabarlarini o'chirish
    # (rasm + matn + tugmalar) — edit qilinmaydi, faqat tozalanadi
    try:
        await cleanup_seller_chat(bot, chat_id)
    except Exception:
        pass
    
    # 2) UMUMIY ASOSIY MENYUGA qaytarish (bitta xabar yuboriladi)
    keyboard = make_main_menu_keyboard(user_id)
    
    menu_text = (
        "Assalomu alaykum.\n"
        "Tizimga xush kelibsiz.\n\n"
        "Quyidagi bo'limlardan birini tanlang:"
    )
    
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        # Asosiy menyu xabarini track qilish
        store_main_menu_message(chat_id, sent.message_id)
    except Exception:
        pass
    
    await callback_query.answer()


# ==================== SUPER ADMINGA BUYURTMA XABARI ====================

async def cleanup_super_admin_chat(bot: Bot, super_admin_chat_id: int):
    """
    Super admin chatidagi BARCHA xabarlarni o'chirish.
    Track qilingan va track qilinmagan barcha xabarlarni o'chirishga harakat qiladi.
    """
    # 1) Track qilingan bot xabarlarini o'chirish
    await cleanup_all_bot_messages(bot, super_admin_chat_id)
    
    # 2) Qo'shimcha xabarlar uchun (agar kerak bo'lsa)
    pass


async def send_order_notification_to_super_admin(bot: Bot, item, super_admin_chat_id: int):
    """
    Super adminga buyurtma xabarini yuborish.
    Faqat notify xabarini yuboradi.
    """
    from services.google_sheet import GoogleSheetService
    
    # Admin ekranini tozalash OLIB TASHLANDI - xabarlar to'plansin
    # await cleanup_all_bot_messages(bot, super_admin_chat_id)
    
    # 1) Buyurtma matnini yaratish (sotuvchiga yuboriladigan format bilan bir xil)
    order_text = (
        f"⚡ <b>Yangi buyurtma</b>\n\n"
        f"👤 <b>Foydalanuvchi:</b> {item.user_name}\n"
        f"🏷 <b>Rol:</b> Oddiy user\n"
        f"📌 <b>Kod:</b> {item.code}\n"
        f"📐 <b>Razmer:</b> {item.razmer}\n"
        f"📦 <b>Kolleksiya:</b> {item.kolleksiya}\n"
        f"🧩 <b>Model:</b> {item.model_nomi}\n"
        f"📊 <b>Miqdor:</b> {item.qty} dona"
    )
    
    # 2) Tugmalarni yaratish
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Tasdiqlash",
                callback_data=f"super_admin_confirm_order:{item.code}:{item.razmer}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 O'chirish",
                callback_data=f"remove_order_item:{item.code}:{item.razmer}"
            )
        ],
        [
            InlineKeyboardButton(
                text="🏠 Asosiy menyu",
                callback_data=f"super_admin_order_main_menu:{item.code}:{item.razmer}"
            )
        ]
    ])
    
    # 3) Rasm bilan yoki rasmiz yuborish (FAQAT BITTA NOTIFY XABAR)
    sheet_service = GoogleSheetService()
    
    if item.image_url:
        converted_url = sheet_service._convert_google_drive_link(item.image_url)
        try:
            sent = await bot.send_photo(
                chat_id=super_admin_chat_id,
                photo=converted_url,
                caption=order_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            # Super admin chatidagi buyurtma xabarini track qilamiz (keyin tozalash uchun)
            track_bot_message(super_admin_chat_id, sent.message_id)
            return
        except Exception:
            # Rasm yuborishda xato bo'lsa, text xabar sifatida yuborish
            try:
                sent = await bot.send_message(
                    chat_id=super_admin_chat_id,
                    text=order_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                track_bot_message(super_admin_chat_id, sent.message_id)
            except Exception:
                pass
    else:
        # Text xabar sifatida yuborish
        try:
            sent = await bot.send_message(
                chat_id=super_admin_chat_id,
                text=order_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            track_bot_message(super_admin_chat_id, sent.message_id)
        except Exception:
            # Xabar yuborishda xato bo'lsa, e'tiborsiz qoldiriladi
            pass


@router.callback_query(F.data.startswith("super_admin_confirm_order:"))
async def callback_super_admin_confirm_order(callback_query: CallbackQuery, bot: Bot):
    """
    Super admin / admin chatida oddiy userning buyurtmasi tagidagi [✅ Tasdiqlash] tugmasi.
    
    Logika:
    - Faqat admin / super admin / sotuvchi / hamkor tasdiqlashi mumkin (rol cheklovi uchun)
    - Tayyor razmerlar stokidan BUYURTMA MIQDORI ga teng miqdorda ayriladi
    - QOIDA: QOLDIQ_YANGI = QOLDIQ_ESKI - BUYURTMA_MIqdORI
    - Shu buyurtma xabari o'chiriladi
    - Asosiy menyu avtomatik ko'rsatiladi (agar boshqa buyurtma xabarlari bo'lsa ham)
    """
    user_id = callback_query.from_user.id
    data = callback_query.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        await callback_query.answer("❌ Noto'g'ri ma'lumot", show_alert=False)
        return

    code = parts[1]
    razmer = parts[2]

    from services.admin_storage import is_seller as _is_seller_storage
    is_admin_user = is_admin(user_id) or is_super_admin(user_id)
    is_seller_user = _is_seller_storage(user_id)
    is_partner_user, _, _, _ = detect_partner_and_seller_order(user_id)

    if not (is_admin_user or is_seller_user or is_partner_user):
        await callback_query.answer("❌ Bu tugma faqat admin / sotuvchi / hamkor uchun", show_alert=True)
        return

    # RAM ichidagi buyurtmalardan BIR DONA umumiy buyurtmani topish,
    # BUYURTMA MIQDORI ga teng miqdorda stokdan ayrish va buyurtmani o'chirish
    _remove_single_order_any_user(code, razmer)

    # Xabarni o'chirish
    chat_id = callback_query.message.chat.id
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=callback_query.message.message_id
        )
    except Exception:
        pass

    # Qolgan buyurtmalarni tekshirish
    from services.order_service import get_order_items_by_role
    remaining_orders = get_order_items_by_role(user_id)
    
    if not remaining_orders:
        # Oxirgi buyurtma edi - asosiy menyu ko'rsatish
        keyboard = make_main_menu_keyboard(user_id)
        menu_text = (
            "Assalomu alaykum.\n"
            "Tizimga xush kelibsiz.\n\n"
            "Quyidagi bo'limlardan birini tanlang:"
        )
        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=keyboard
            )
            store_main_menu_message(chat_id, sent.message_id)
        except Exception:
            pass

    # Vizual bildirish
    await callback_query.answer("✅ Buyurtma tasdiqlandi", show_alert=False)


@router.callback_query(F.data.startswith("super_admin_order_main_menu:"))
async def callback_super_admin_order_main_menu(callback_query: CallbackQuery, bot: Bot, state: FSMContext):
    """
    [Asosiy menyu] tugmasi bosilganda (buyurtma xabari tagida).
    SHU CHAT ichidagi barcha buyurtma xabarlarini o'chiradi
    va foydalanuvchini UMUMIY ASOSIY MENYUGA qaytaradi.
    """
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    # 1) Super admin chatidagi BARCHA buyurtma xabarlarini o'chirish
    # (rasm + matn + tugmalar) — edit qilinmaydi, faqat tozalanadi
    try:
        await cleanup_super_admin_chat(bot, chat_id)
    except Exception:
        pass
    
    # 2) UMUMIY ASOSIY MENYUGA qaytarish (bitta xabar yuboriladi)
    keyboard = make_main_menu_keyboard(user_id)
    
    menu_text = (
        "Assalomu alaykum.\n"
        "Tizimga xush kelibsiz.\n\n"
        "Quyidagi bo'limlardan birini tanlang:"
    )
    
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=menu_text,
            reply_markup=keyboard
        )
        # Asosiy menyu xabarini track qilish
        store_main_menu_message(chat_id, sent.message_id)
    except Exception:
        pass
    
    await callback_query.answer()


# ==================== ID OLISH ====================

import logging

logger = logging.getLogger(__name__)


@router.callback_query(F.data == "id_get")
async def handle_id_get(callback_query: CallbackQuery):
    """
    🔹 ID olish tugmasi bosilganda.
    
    User o'zining Telegram user ID sini ko'radi.
    Hech qayerga saqlanmaydi - faqat ko'rsatiladi.
    """
    user_id = callback_query.from_user.id
    
    # ID matnini tayyorlash
    id_text = (
        "🆔 <b>Sizning Telegram ID raqamingiz:</b>\n\n"
        f"<code>{user_id}</code>\n\n"
        "📌 <i>Ushbu ID admin ruxsat va tizimda kerak bo'ladi.</i>"
    )
    
    # Orqaga tugmasi
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔙 Orqaga",
                callback_data="id_back"
            )
        ]
    ])
    
    # Xabarni edit qilish
    try:
        await callback_query.message.edit_text(
            text=id_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error showing user ID: {e}")
    
    await callback_query.answer()


@router.callback_query(F.data == "id_back")
async def handle_id_back(callback_query: CallbackQuery):
    """
    🔙 Orqaga tugmasi bosilganda (ID xabaridan).
    
    - Xabar edit bo'lib asosiy menyuga qaytadi
    """
    user_id = callback_query.from_user.id
    
    # Asosiy menyu tugmalari
    keyboard = make_main_menu_keyboard(user_id)
    
    menu_text = (
        "Assalomu alaykum.\n"
        "Tizimga xush kelibsiz.\n\n"
        "Quyidagi bo'limlardan birini tanlang:"
    )
    
    # Xabarni edit qilish
    try:
        await callback_query.message.edit_text(
            text=menu_text,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error editing message back to main menu: {e}")
    
    await callback_query.answer()


def register_handlers(dp):
    dp.include_router(router)
