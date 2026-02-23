"""
Statistics tracking for admin panel.
Simple in-memory statistics (no database required).
"""
from datetime import datetime, date, timedelta
from typing import Dict, Optional, List
from collections import defaultdict
import json
import os
import sys

# In-memory statistics storage (RAM)
_stats: Dict[str, any] = {
    "today_requests": 0,
    "today_found": 0,
    "today_not_found": 0,
    "total_requests": 0,
    "total_not_found": 0,  # Total failed requests (all time)
    "last_request_time": None,
    "product_requests": defaultdict(int),  # product_code -> count
    "active_users": set(),  # user_id set (all time unique users)
    "today_active_users": set(),  # user_id set (today's unique users)
    "collection_selections": defaultdict(int),  # collection_name -> count
    "user_activity": defaultdict(int),  # user_id -> request count
    "user_successful_requests": defaultdict(int),  # user_id -> successful count
    "user_failed_requests": defaultdict(int),  # user_id -> failed count
    "user_first_activity": {},  # user_id -> datetime
    "user_last_activity": {},  # user_id -> datetime
    "user_info": {},  # user_id -> {"username": str, "first_name": str}
    "user_history": defaultdict(list),  # user_id -> list of history entries
    "last_user_id": None,  # Last user who made a request
    "last_username": None,  # Last user's username
    "last_date": None,  # Track date changes
    # Narx bo'limi bo'yicha jami so'rovlar soni
    "price_requests": 0,
    # Response time tracking (last 5 minutes)
    "response_times": [],  # List of {"timestamp": datetime, "response_time_ms": float}
}


def reset_daily_stats():
    """Reset daily statistics if date changed."""
    today = date.today()
    if _stats["last_date"] != today:
        _stats["today_requests"] = 0
        _stats["today_found"] = 0
        _stats["today_not_found"] = 0
        _stats["today_active_users"] = set()  # Reset today's active users
        _stats["last_date"] = today


def record_request(
    user_id: int,
    product_code: Optional[str] = None,
    found: bool = False,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    matched_count: int = 0,
):
    """
    Record a search request (ombor / astatka bo'limi uchun).
    """
    reset_daily_stats()
    
    # Avtomatik RAM limitini tekshirish
    check_and_auto_clear_stats()

    _stats["today_requests"] += 1
    _stats["total_requests"] += 1

    if found:
        _stats["today_found"] += 1
        _stats["user_successful_requests"][user_id] += 1
    else:
        _stats["today_not_found"] += 1
        _stats["total_not_found"] += 1
        _stats["user_failed_requests"][user_id] += 1

    if product_code:
        _stats["product_requests"][product_code] += 1

    # Track unique users
    _stats["active_users"].add(user_id)
    _stats["today_active_users"].add(user_id)

    # Track user activity count
    _stats["user_activity"][user_id] += 1

    # Track first and last activity
    now = datetime.now()
    if user_id not in _stats["user_first_activity"]:
        _stats["user_first_activity"][user_id] = now
    _stats["user_last_activity"][user_id] = now

    # Store user info
    if username or first_name:
        if user_id not in _stats["user_info"]:
            _stats["user_info"][user_id] = {}
        if username:
            _stats["user_info"][user_id]["username"] = username
        if first_name:
            _stats["user_info"][user_id]["first_name"] = first_name

    # Store last user info
    _stats["last_user_id"] = user_id
    _stats["last_username"] = (
        username
        or _stats["user_info"].get(user_id, {}).get("username")
        or f"ID: {user_id}"
    )
    _stats["last_request_time"] = now

    # Record history
    section = "Umumiy qoldiq"
    request_text = product_code or "N/A"
    if found:
        result_text = (
            f"✅ {matched_count} ta mahsulot" if matched_count > 0 else "✅ Topildi"
        )
    else:
        result_text = "❌ Topilmadi"

    _stats["user_history"][user_id].append(
        {
            "timestamp": now,
            "section": section,
            "request_text": request_text,
            "result": result_text,
        }
    )
    
    # Faolliklar jurnaliga yozish
    _add_activity_log_entry(
        user_id=user_id,
        section="Kod",
        action=request_text,
        result=result_text,
        username=username,
        first_name=first_name,
    )


def record_collection_selection(
    user_id: int,
    collection_name: str,
    found: bool = True,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    matched_count: int = 0,
):
    """
    Record a collection selection (ombor / astatka bo'limi uchun).
    """
    reset_daily_stats()

    _stats["collection_selections"][collection_name] += 1

    # Also count as a request
    _stats["today_requests"] += 1
    _stats["total_requests"] += 1

    if found:
        _stats["today_found"] += 1
        _stats["user_successful_requests"][user_id] += 1
    else:
        _stats["today_not_found"] += 1
        _stats["total_not_found"] += 1
        _stats["user_failed_requests"][user_id] += 1

    # Track user activity
    _stats["user_activity"][user_id] += 1
    _stats["active_users"].add(user_id)
    _stats["today_active_users"].add(user_id)

    # Track first and last activity
    now = datetime.now()
    if user_id not in _stats["user_first_activity"]:
        _stats["user_first_activity"][user_id] = now
    _stats["user_last_activity"][user_id] = now

    # Store user info
    if username or first_name:
        if user_id not in _stats["user_info"]:
            _stats["user_info"][user_id] = {}
        if username:
            _stats["user_info"][user_id]["username"] = username
        if first_name:
            _stats["user_info"][user_id]["first_name"] = first_name

    # Update last user info
    _stats["last_user_id"] = user_id
    _stats["last_username"] = (
        username
        or _stats["user_info"].get(user_id, {}).get("username")
        or f"ID: {user_id}"
    )
    _stats["last_request_time"] = now

    # Record history
    section = "Kolleksiya"
    request_text = collection_name
    if found:
        result_text = (
            f"✅ {matched_count} ta mahsulot" if matched_count > 0 else "✅ Topildi"
        )
    else:
        result_text = "❌ Topilmadi"

    _stats["user_history"][user_id].append(
        {
            "timestamp": now,
            "section": section,
            "request_text": request_text,
            "result": result_text,
        }
    )
    
    # Faolliklar jurnaliga yozish
    _add_activity_log_entry(
        user_id=user_id,
        section="Kolleksiya",
        action=request_text,
        result=result_text,
        username=username,
        first_name=first_name,
    )


def record_price_request(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
):
    """
    Narx bo'limi bo'yicha so'rovni hisoblash.

    Bu funksiya faqat bitta global counterni oshiradi:
    _stats["price_requests"] += 1

    Hech qanday qo'shimcha log yoki sana bo'yicha hisob yuritilmaydi.
    """
    # Faqat umumiy sonni oshiramiz, boshqa statistikaga tegmaymiz
    _stats["price_requests"] += 1


def record_price_history(
    user_id: int,
    product_code: str,
    found: bool = False,
    price_text: Optional[str] = None,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
):
    """
    Narx bo'limi bo'yicha so'rovni foydalanuvchi tarixiga yozish.

    Args:
        user_id: Foydalanuvchi ID
        product_code: Kiritilgan mahsulot kodi yoki model nomi
        found: Narx topildimi yoki yo'q
        price_text: Topilgan narx matni (masalan: "137,25" yoki "137,25$")
        username: Foydalanuvchi username
        first_name: Foydalanuvchi ismi
    """
    reset_daily_stats()

    # Umumiy statistika
    _stats["today_requests"] += 1
    _stats["total_requests"] += 1

    if found:
        _stats["today_found"] += 1
        _stats["user_successful_requests"][user_id] += 1
    else:
        _stats["today_not_found"] += 1
        _stats["total_not_found"] += 1
        _stats["user_failed_requests"][user_id] += 1

    # Track unique users
    _stats["active_users"].add(user_id)
    _stats["today_active_users"].add(user_id)

    # Track user activity count
    _stats["user_activity"][user_id] += 1

    # Track first and last activity
    now = datetime.now()
    if user_id not in _stats["user_first_activity"]:
        _stats["user_first_activity"][user_id] = now
    _stats["user_last_activity"][user_id] = now

    # Store user info
    if username or first_name:
        if user_id not in _stats["user_info"]:
            _stats["user_info"][user_id] = {}
        if username:
            _stats["user_info"][user_id]["username"] = username
        if first_name:
            _stats["user_info"][user_id]["first_name"] = first_name

    # Store last user info
    _stats["last_user_id"] = user_id
    _stats["last_username"] = (
        username
        or _stats["user_info"].get(user_id, {}).get("username")
        or f"ID: {user_id}"
    )
    _stats["last_request_time"] = now

    # Record history - Narx bo'limi uchun
    section = "Narx"
    request_text = product_code or "N/A"
    
    if found and price_text:
        # Narx topilgan holat
        result_text = f"💰 {price_text}"
    else:
        # Narx topilmagan holat
        result_text = "❌ Topilmadi"

    _stats["user_history"][user_id].append(
        {
            "timestamp": now,
            "section": section,
            "request_text": request_text,
            "result": result_text,
        }
    )
    
    # Faolliklar jurnaliga yozish
    _add_activity_log_entry(
        user_id=user_id,
        section="Narx",
        action=request_text,
        result=result_text,
        username=username,
        first_name=first_name,
    )


def record_discount_section_action(
    user_id: int,
    section: str,
    action: str,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
):
    """
    Skidkaga tushgan modellar bo'limi uchun faollikni jurnalga yozish.

    Args:
        user_id: Foydalanuvchi ID
        section: Bo'lim nomi (masalan: "Skidka")
        action: Amal matni (masalan: "Bo'limga kirish", "Turk kombo")
        username: Foydalanuvchi username
        first_name: Foydalanuvchi ismi
    """
    _add_activity_log_entry(
        user_id=user_id,
        section=section,
        action=action,
        result="OK",
        username=username,
        first_name=first_name,
    )


def get_stats() -> Dict:
    """
    Get current statistics.

    Returns:
        Dictionary with all statistics
    """
    reset_daily_stats()

    # Find TOP-5 most requested products
    top_products = sorted(
        _stats["product_requests"].items(),
        key=lambda x: x[1],
        reverse=True,
    )[:5]
    top_5_products = [(code, count) for code, count in top_products]

    # Find TOP-5 most selected collections
    top_collections = sorted(
        _stats["collection_selections"].items(),
        key=lambda x: x[1],
        reverse=True,
    )[:5]
    top_5_collections = [(name, count) for name, count in top_collections]

    # Find TOP-5 most active users
    top_users = sorted(
        _stats["user_activity"].items(),
        key=lambda x: x[1],
        reverse=True,
    )[:5]
    top_5_users = []
    for user_id, count in top_users:
        user_info = _stats["user_info"].get(user_id, {})
        username = user_info.get("username")
        first_name = user_info.get("first_name")

        if username:
            display_name = f"@{username}"
        elif first_name:
            display_name = first_name
        else:
            display_name = f"ID: {user_id}"

        top_5_users.append((display_name, count))

    last_request_str = "Hech qachon"
    if _stats["last_request_time"]:
        last_request_str = _stats["last_request_time"].strftime("%Y-%m-%d %H:%M:%S")

    last_user_display = (
        _stats["last_username"]
        or f"ID: {_stats['last_user_id']}"
        if _stats["last_user_id"]
        else "N/A"
    )

    # Faolliklar jurnalini olish
    activity_log = get_activity_log(limit=20)
    
    return {
        "today_requests": _stats["today_requests"],
        "today_found": _stats["today_found"],
        "today_not_found": _stats["today_not_found"],
        "total_requests": _stats["total_requests"],
        "total_not_found": _stats["total_not_found"],
        "total_unique_users": len(_stats["active_users"]),
        "today_active_users": len(_stats["today_active_users"]),
        "top_5_products": top_5_products,
        "top_5_collections": top_5_collections,
        "top_5_users": top_5_users,
        "last_request_time": last_request_str,
        "last_user": last_user_display,
        # Narx bo'limi bo'yicha jami so'rovlar
        "price_requests": _stats["price_requests"],
        # Faolliklar jurnali
        "activity_log": activity_log,
    }


def get_all_users() -> list:
    """
    Get list of all users with their basic info.

    Returns:
        List of tuples: (user_id, display_name, total_requests)
    """
    users = []
    for user_id in _stats["active_users"]:
        user_info = _stats["user_info"].get(user_id, {})
        username = user_info.get("username")
        first_name = user_info.get("first_name")

        # Create display name
        if first_name:
            display_name = first_name
        elif username:
            display_name = f"@{username}"
        else:
            display_name = f"ID: {user_id}"

        total_requests = _stats["user_activity"].get(user_id, 0)
        users.append((user_id, display_name, total_requests))

    # Sort by total requests (descending)
    users.sort(key=lambda x: x[2], reverse=True)
    return users


def get_user_details(user_id: int) -> Optional[Dict]:
    """
    Get detailed information about a specific user.
    
    Args:
        user_id: User ID to get details for
    
    Returns:
        Dictionary with user details or None if user not found
    """
    if user_id not in _stats["active_users"]:
        return None
    
    user_info = _stats["user_info"].get(user_id, {})
    username = user_info.get("username")
    first_name = user_info.get("first_name")
    
    total_requests = _stats["user_activity"].get(user_id, 0)
    successful_requests = _stats["user_successful_requests"].get(user_id, 0)
    failed_requests = _stats["user_failed_requests"].get(user_id, 0)
    
    first_activity = _stats["user_first_activity"].get(user_id)
    last_activity = _stats["user_last_activity"].get(user_id)
    
    first_activity_str = (
        first_activity.strftime("%Y-%m-%d %H:%M") if first_activity else "N/A"
    )
    last_activity_str = (
        last_activity.strftime("%Y-%m-%d %H:%M") if last_activity else "N/A"
    )
    
    # Get user history (sorted by timestamp, most recent first)
    history_entries = _stats["user_history"].get(user_id, [])
    history_sorted = sorted(history_entries, key=lambda x: x["timestamp"], reverse=True)
    
    return {
        "user_id": user_id,
        "first_name": first_name or "N/A",
        "username": username,
        "total_requests": total_requests,
        "successful_requests": successful_requests,
        "failed_requests": failed_requests,
        "first_activity": first_activity_str,
        "last_activity": last_activity_str,
        "history": history_sorted,
    }


def delete_user_from_stats(user_id: int):
    """Delete user from statistics (called from settings)"""
    _stats["active_users"].discard(user_id)
    _stats["today_active_users"].discard(user_id)
    _stats["user_activity"].pop(user_id, None)
    _stats["user_successful_requests"].pop(user_id, None)
    _stats["user_failed_requests"].pop(user_id, None)
    _stats["user_first_activity"].pop(user_id, None)
    _stats["user_last_activity"].pop(user_id, None)
    _stats["user_info"].pop(user_id, None)
    _stats["user_history"].pop(user_id, None)


# ==================== Foydalanuvchilar bo'limi uchun AVTO va QO'LDA tozalash ====================

# Faqat "Foydalanuvchilar" bo'limi uchun cheklovlar
USER_HISTORY_RETENTION_DAYS = 7          # 7 kundan eski tarixni avtomatik kesish
USER_HISTORY_MAX_ENTRIES = 10_000       # taxminan ~10MB atrofida yozuv (son bo'yicha limit)


def compact_users_stats_for_admin_users() -> None:
    """
    Admin paneldagi 'Foydalanuvchilar' bo'limi uchun AVTO-tozalash:
    - 7 kundan eski user_history yozuvlarini olib tashlaydi;
    - umumiy yozuvlar soni USER_HISTORY_MAX_ENTRIES dan oshsa, eng eski yozuvlarni kesadi.
    Umumiy statistika sanagichlariga (today_requests va hokazo) tegmaydi.
    """
    cutoff = datetime.now() - timedelta(days=USER_HISTORY_RETENTION_DAYS)

    total_entries = 0
    # 1) Har bir foydalanuvchi bo'yicha 7 kundan eski yozuvlarni filtrlash
    for user_id, history in list(_stats["user_history"].items()):
        new_history = []
        for item in history:
            ts = item.get("timestamp")
            try:
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                if isinstance(ts, datetime):
                    if ts >= cutoff:
                        new_history.append(item)
                else:
                    # timestamp noto'g'ri bo'lsa ham, xavfsizlik uchun qoldiramiz
                    new_history.append(item)
            except Exception:
                new_history.append(item)

        if new_history:
            _stats["user_history"][user_id] = new_history
            total_entries += len(new_history)
        else:
            # Tarix bo'sh bo'lib qolsa, faqat tarixni o'chiramiz
            _stats["user_history"].pop(user_id, None)

    # 2) Agar umumiy yozuvlar soni juda katta bo'lsa, eng eski yozuvlarni kesamiz
    if total_entries > USER_HISTORY_MAX_ENTRIES:
        all_items = []
        for user_id, history in _stats["user_history"].items():
            for item in history:
                ts = item.get("timestamp")
                try:
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts)
                    if isinstance(ts, datetime):
                        all_items.append((ts, user_id, item))
                except Exception:
                    # timestamp noto'g'ri bo'lsa, eng yangi deb hisoblamaymiz
                    all_items.append((datetime.min, user_id, item))

        # Eski -> yangi tartibda
        all_items.sort(key=lambda x: x[0])

        # Nechta yozuvni o'chirish kerak
        to_delete_count = len(all_items) - USER_HISTORY_MAX_ENTRIES
        to_delete = set(all_items[:to_delete_count])

        # Belgilangan eng eski yozuvlarni user_history dan o'chiramiz
        for ts, user_id, item in to_delete:
            history = _stats["user_history"].get(user_id, [])
            try:
                history.remove(item)
            except ValueError:
                pass


def clear_users_cache_for_admin_users() -> None:
    """
    Admin paneldagi 'Foydalanuvchilar' bo'limi uchun QO'LDA tozalash:
    - RAMdagi foydalanuvchi ro'yxatlari va tarixini tozalaydi.
    - Umumiy sanagichlar (today_requests, total_requests va hokazo)ga tegmaydi.
    """
    _stats["active_users"].clear()
    _stats["today_active_users"].clear()
    _stats["user_activity"].clear()
    _stats["user_successful_requests"].clear()
    _stats["user_failed_requests"].clear()
    _stats["user_first_activity"].clear()
    _stats["user_last_activity"].clear()
    _stats["user_info"].clear()
    _stats["user_history"].clear()


# ==================== FAOLLIKLAR JURNALI (ACTIVITY LOG) ====================

_ACTIVITY_LOG_FILE = "activity_log.json"
_MAX_LOG_ENTRIES = 500
_LOG_FILE_SIZE_LIMIT = 4 * 1024 * 1024  # 4 MB in bytes


def _get_user_role(user_id: int) -> str:
    """
    Foydalanuvchi rolini aniqlash.
    
    Returns:
        "User" | "PriceAccess" | "HelperAdmin" | "Admin"
    """
    from services.admin_utils import is_admin, is_helper_admin
    from services.settings import has_api_access
    
    if is_admin(user_id):
        return "Admin"
    elif is_helper_admin(user_id):
        return "HelperAdmin"
    elif has_api_access(user_id):
        return "PriceAccess"
    else:
        return "User"


def _get_user_display_name(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> str:
    """
    Foydalanuvchi ko'rinadigan ismini yaratish.
    
    Format: "Ism (user_id)" yoki "UserID"
    """
    user_info = _stats["user_info"].get(user_id, {})
    name = first_name or user_info.get("first_name")
    uname = username or user_info.get("username")
    
    if name:
        return f"{name} ({user_id})"
    elif uname:
        return f"@{uname} ({user_id})"
    else:
        return f"User {user_id}"


def _load_activity_log() -> List[Dict]:
    """Faolliklar jurnalini fayldan yuklash."""
    if not os.path.exists(_ACTIVITY_LOG_FILE):
        return []
    
    try:
        with open(_ACTIVITY_LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Timestamplarni datetime ga o'girish
            for entry in data:
                if "timestamp" in entry and isinstance(entry["timestamp"], str):
                    entry["timestamp"] = datetime.fromisoformat(entry["timestamp"])
            return data
    except Exception:
        return []


def _check_and_clear_log_file():
    """Fayl hajmini tekshirish va agar 4 MB dan oshsa to'liq tozalash."""
    try:
        if os.path.exists(_ACTIVITY_LOG_FILE):
            file_size = os.path.getsize(_ACTIVITY_LOG_FILE)
            if file_size > _LOG_FILE_SIZE_LIMIT:
                # Faylni to'liq tozalash - bo'sh JSON massiv yaratish
                with open(_ACTIVITY_LOG_FILE, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _save_activity_log(log_entries: List[Dict]):
    """Faolliklar jurnalini faylga saqlash."""
    try:
        # Avval fayl hajmini tekshirish va kerak bo'lsa tozalash
        _check_and_clear_log_file()
        
        # Timestamplarni string ga o'girish
        data_to_save = []
        for entry in log_entries:
            entry_copy = entry.copy()
            if "timestamp" in entry_copy and isinstance(entry_copy["timestamp"], datetime):
                entry_copy["timestamp"] = entry_copy["timestamp"].isoformat()
            data_to_save.append(entry_copy)
        
        with open(_ACTIVITY_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        
        # Yozilgandan keyin yana tekshirish (agar yozish jarayonida hajm oshgan bo'lsa)
        _check_and_clear_log_file()
    except Exception:
        pass


def _add_activity_log_entry(
    user_id: int,
    section: str,
    action: str,
    result: str,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
):
    """
    Faolliklar jurnaliga yangi yozuv qo'shish.
    
    Args:
        user_id: Foydalanuvchi ID
        section: Bo'lim nomi (Kolleksiya, Kod, Narx, Admin panel, API ruxsat, Statistika)
        action: Amal matni (so'rov matni yoki bajarilgan ish)
        result: Natija (topildi / topilmadi / ruxsat berildi / o'chirildi)
        username: Foydalanuvchi username
        first_name: Foydalanuvchi ismi
    """
    # Jurnalni yuklash
    log_entries = _load_activity_log()
    
    # Foydalanuvchi roli va ismini aniqlash
    role = _get_user_role(user_id)
    display_name = _get_user_display_name(user_id, username, first_name)
    
    # Yangi yozuv yaratish
    new_entry = {
        "timestamp": datetime.now(),
        "user_id": user_id,
        "user_name": display_name,
        "role": role,
        "section": section,
        "action": action,
        "result": result,
    }
    
    # Yangi yozuvni boshiga qo'shish (eng oxirgi harakat yuqorida)
    log_entries.insert(0, new_entry)
    
    # FIFO: 500 tadan oshsa eski yozuvlarni o'chirish
    if len(log_entries) > _MAX_LOG_ENTRIES:
        log_entries = log_entries[:_MAX_LOG_ENTRIES]
    
    # Saqlash
    _save_activity_log(log_entries)


def get_activity_log(limit: int = 20, filter_type: str = "all") -> List[Dict]:
    """
    Faolliklar jurnalini olish.
    
    Args:
        limit: Ko'rsatiladigan yozuvlar soni (default: 20)
        filter_type: Filtr turi - "all", "today", "yesterday", "last_7_days"
    
    Returns:
        List of activity log entries (eng oxirgi harakat birinchi)
    """
    log_entries = _load_activity_log()
    
    # Filtrlash
    if filter_type == "today":
        today = date.today()
        log_entries = [
            entry for entry in log_entries
            if _is_entry_date(entry, today)
        ]
    elif filter_type == "yesterday":
        yesterday = date.today() - timedelta(days=1)
        log_entries = [
            entry for entry in log_entries
            if _is_entry_date(entry, yesterday)
        ]
    elif filter_type == "last_7_days":
        seven_days_ago = date.today() - timedelta(days=7)
        log_entries = [
            entry for entry in log_entries
            if _is_entry_date_range(entry, seven_days_ago, date.today())
        ]
    # "all" holatida barcha yozuvlar qaytariladi
    
    # Eng oxirgi harakatlar birinchi (zaten insert(0) bilan qo'shilgan)
    return log_entries[:limit]


def _is_entry_date(entry: Dict, target_date: date) -> bool:
    """Yozuv sanasi ma'lum sanaga mos keladimi tekshirish."""
    try:
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        if isinstance(timestamp, datetime):
            return timestamp.date() == target_date
    except Exception:
        pass
    return False


def _is_entry_date_range(entry: Dict, start_date: date, end_date: date) -> bool:
    """Yozuv sanasi sanalar orasidami tekshirish."""
    try:
        timestamp = entry.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        if isinstance(timestamp, datetime):
            entry_date = timestamp.date()
            return start_date <= entry_date <= end_date
    except Exception:
        pass
    return False


def clear_all_log_files():
    """Barcha log/stat fayllarini to'liq tozalash."""
    try:
        # activity_log.json ni tozalash
        if os.path.exists(_ACTIVITY_LOG_FILE):
            with open(_ACTIVITY_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        # stats.json ni tozalash (agar mavjud bo'lsa)
        stats_file = "stats.json"
        if os.path.exists(stats_file):
            with open(stats_file, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_log_entry_by_index(entries: List[Dict], index: int) -> Optional[Dict]:
    """Index bo'yicha log yozuvini olish."""
    try:
        if 0 <= index < len(entries):
            return entries[index]
    except Exception:
        pass
    return None


def record_admin_action(
    admin_id: int,
    section: str,
    action: str,
    result: str,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
):
    """
    Admin panel amallarini faolliklar jurnaliga yozish.
    
    Args:
        admin_id: Admin ID
        section: Bo'lim nomi (Admin panel, API ruxsat, Statistika)
        action: Amal matni (masalan: "API ruxsat berildi")
        result: Natija (masalan: "✅ User 8007366646")
        username: Admin username
        first_name: Admin ismi
    """
    _add_activity_log_entry(
        user_id=admin_id,
        section=section,
        action=action,
        result=result,
        username=username,
        first_name=first_name,
    )


# ==================== RESPONSE TIME TRACKING ====================

def record_response_time(response_time_ms: float):
    """
    Response time ni yozish (so'nggi 5 daqiqadagi so'rovlar uchun).
    
    Args:
        response_time_ms: Javob vaqti millisekundlarda
    """
    now = datetime.now()
    
    # Eski yozuvlarni olib tashlash (5 daqiqadan eski)
    five_minutes_ago = now - timedelta(minutes=5)
    _stats["response_times"] = [
        entry for entry in _stats["response_times"]
        if entry["timestamp"] >= five_minutes_ago
    ]
    
    # Yangi yozuv qo'shish
    _stats["response_times"].append({
        "timestamp": now,
        "response_time_ms": response_time_ms
    })


def get_response_time_stats() -> Dict:
    """
    Response time statistikasini olish.
    
    Returns:
        Dictionary with:
        - avg_response_time_ms: O'rtacha javob vaqti (ms)
        - slowest_response_ms: Eng sekin javob (ms)
        - requests_last_5_min: So'nggi 5 daqiqadagi so'rovlar soni
        - last_slowdown_time: Oxirgi sekinlashuv vaqti (agar > 2000ms bo'lsa)
    """
    now = datetime.now()
    five_minutes_ago = now - timedelta(minutes=5)
    
    # Faqat so'nggi 5 daqiqadagi yozuvlarni olish
    recent_times = [
        entry for entry in _stats["response_times"]
        if entry["timestamp"] >= five_minutes_ago
    ]
    
    if not recent_times:
        return {
            "avg_response_time_ms": None,
            "slowest_response_ms": None,
            "requests_last_5_min": 0,
            "last_slowdown_time": None
        }
    
    response_times = [entry["response_time_ms"] for entry in recent_times]
    
    avg_time = sum(response_times) / len(response_times)
    slowest_time = max(response_times)
    
    # Oxirgi sekinlashuvni topish (> 2000ms)
    last_slowdown = None
    for entry in reversed(recent_times):  # Eng oxirgidan boshlab
        if entry["response_time_ms"] > 2000:
            last_slowdown = entry["timestamp"]
            break
    
    return {
        "avg_response_time_ms": avg_time,
        "slowest_response_ms": slowest_time,
        "requests_last_5_min": len(recent_times),
        "last_slowdown_time": last_slowdown
    }


# ==================== RAM Memory Management ====================

# RAM limiti (10 MB = 10 * 1024 * 1024 bytes)
_STATS_MEMORY_LIMIT = 10 * 1024 * 1024  # 10 MB


def _get_stats_memory_size() -> int:
    """
    Statistika ma'lumotlarining RAM'dagi hajmini hisoblash.
    
    Returns:
        Bytes soni
    """
    total_size = 0
    
    # _stats dictionary hajmini hisoblash
    for key, value in _stats.items():
        # Key hajmi
        total_size += sys.getsizeof(key)
        
        # Value hajmi
        if isinstance(value, dict):
            total_size += sys.getsizeof(value)
            for k, v in value.items():
                total_size += sys.getsizeof(k) + sys.getsizeof(v)
        elif isinstance(value, (list, set)):
            total_size += sys.getsizeof(value)
            for item in value:
                total_size += sys.getsizeof(item)
        elif isinstance(value, defaultdict):
            total_size += sys.getsizeof(value)
            for k, v in value.items():
                total_size += sys.getsizeof(k) + sys.getsizeof(v)
        else:
            total_size += sys.getsizeof(value)
    
    return total_size


def clear_general_stats_cache():
    """
    Umumiy statistika keshi tozalash (qo'lda tozalash).
    Faqat umumiy statistika tozalanadi, kunlik statistika saqlanadi.
    """
    # Umumiy statistika tozalash
    _stats["total_requests"] = 0
    _stats["total_not_found"] = 0
    _stats["product_requests"].clear()
    _stats["collection_selections"].clear()
    _stats["active_users"].clear()
    _stats["user_activity"].clear()
    _stats["user_successful_requests"].clear()
    _stats["user_failed_requests"].clear()
    _stats["user_first_activity"].clear()
    _stats["user_last_activity"].clear()
    _stats["user_info"].clear()
    _stats["user_history"].clear()
    _stats["last_user_id"] = None
    _stats["last_username"] = None
    _stats["price_requests"] = 0
    _stats["response_times"].clear()
    
    # Kunlik statistika saqlanadi (tozalanmaydi)
    # today_requests, today_found, today_not_found, today_active_users saqlanadi


def check_and_auto_clear_stats():
    """
    RAM hajmini tekshirish va agar 10 MB dan oshsa, avtomatik tozalash.
    
    Returns:
        Tuple[bool, int] - (tozalandimi, hajm_bytes)
    """
    memory_size = _get_stats_memory_size()
    
    if memory_size > _STATS_MEMORY_LIMIT:
        # Avtomatik tozalash
        clear_general_stats_cache()
        return True, memory_size
    
    return False, memory_size


def get_stats_memory_info() -> dict:
    """
    Statistika RAM hajmi ma'lumotlarini olish.
    
    Returns:
        {
            "current_size_bytes": int,
            "current_size_mb": float,
            "limit_bytes": int,
            "limit_mb": float,
            "percentage": float
        }
    """
    current_size = _get_stats_memory_size()
    limit_bytes = _STATS_MEMORY_LIMIT
    limit_mb = limit_bytes / (1024 * 1024)
    current_mb = current_size / (1024 * 1024)
    percentage = (current_size / limit_bytes) * 100 if limit_bytes > 0 else 0
    
    return {
        "current_size_bytes": current_size,
        "current_size_mb": round(current_mb, 2),
        "limit_bytes": limit_bytes,
        "limit_mb": limit_mb,
        "percentage": round(percentage, 2)
    }

