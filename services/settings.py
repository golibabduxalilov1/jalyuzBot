"""
Real-time settings storage for admin panel.
All settings are stored in memory and take effect immediately without restart.
"""
from typing import Dict, Optional
from collections import defaultdict

# In-memory settings storage
_settings: Dict[str, any] = {
    "contact_phone": "97-310-31-11 raqamiga murojaat qiling.",
    "error_message": "❌ Ma'lumot topilmadi. Iltimos: 97-310-31-11",
    
    # Broadcast settings
    "broadcast_enabled": True,
    "broadcast_active_users_only": False,  # Only users active in last 7 days
    "broadcast_skip_blocked": True,  # Skip blocked users
    
    # Statistics settings
    "history_logging_enabled": True,
    "daily_stats_auto_reset": True,
    "max_logs_per_user": 100,
    
    # Logs and history settings
    "user_request_history_enabled": True,
    "show_user_id": False,  # DEFAULT OFF, never enable
    "show_first_name": True,
    "show_username": True,
    
    # User management
    "blocked_users": set(),  # user_id set
    "user_limits": {},  # user_id -> {"daily_limit": int, "current_count": int, "reset_date": str}
    "api_users": {},  # user_id -> {"name": str, "granted_at": str} - users with API access for model prices
    "admin_names": {},  # user_id -> {"name": str} - admin ismlari (asosiy adminlar)
    "helper_admin_names": {},  # user_id -> {"name": str} - yordamchi admin ismlari
}

def get_contact_phone() -> str:
    """Get contact phone number"""
    return _settings["contact_phone"]

def set_contact_phone(phone: str):
    """Set contact phone number"""
    _settings["contact_phone"] = phone

def get_error_message() -> str:
    """Get error message"""
    return _settings["error_message"]

def set_error_message(message: str):
    """Set error message"""
    _settings["error_message"] = message

def get_broadcast_enabled() -> bool:
    """Get broadcast enabled status"""
    return _settings["broadcast_enabled"]

def toggle_broadcast_enabled():
    """Toggle broadcast enabled status"""
    _settings["broadcast_enabled"] = not _settings["broadcast_enabled"]
    return _settings["broadcast_enabled"]

def get_broadcast_active_users_only() -> bool:
    """Get broadcast active users only status"""
    return _settings["broadcast_active_users_only"]

def toggle_broadcast_active_users_only():
    """Toggle broadcast active users only status"""
    _settings["broadcast_active_users_only"] = not _settings["broadcast_active_users_only"]
    return _settings["broadcast_active_users_only"]

def get_broadcast_skip_blocked() -> bool:
    """Get broadcast skip blocked status"""
    return _settings["broadcast_skip_blocked"]

def toggle_broadcast_skip_blocked():
    """Toggle broadcast skip blocked status"""
    _settings["broadcast_skip_blocked"] = not _settings["broadcast_skip_blocked"]
    return _settings["broadcast_skip_blocked"]

def get_history_logging_enabled() -> bool:
    """Get history logging enabled status"""
    return _settings["history_logging_enabled"]

def toggle_history_logging_enabled():
    """Toggle history logging enabled status"""
    _settings["history_logging_enabled"] = not _settings["history_logging_enabled"]
    return _settings["history_logging_enabled"]

def get_daily_stats_auto_reset() -> bool:
    """Get daily stats auto reset status"""
    return _settings["daily_stats_auto_reset"]

def toggle_daily_stats_auto_reset():
    """Toggle daily stats auto reset status"""
    _settings["daily_stats_auto_reset"] = not _settings["daily_stats_auto_reset"]
    return _settings["daily_stats_auto_reset"]

def get_max_logs_per_user() -> int:
    """Get max logs per user"""
    return _settings["max_logs_per_user"]

def set_max_logs_per_user(count: int):
    """Set max logs per user"""
    _settings["max_logs_per_user"] = count

def get_user_request_history_enabled() -> bool:
    """Get user request history enabled status"""
    return _settings["user_request_history_enabled"]

def toggle_user_request_history_enabled():
    """Toggle user request history enabled status"""
    _settings["user_request_history_enabled"] = not _settings["user_request_history_enabled"]
    return _settings["user_request_history_enabled"]

def get_show_user_id() -> bool:
    """Get show user ID status (always False)"""
    return False  # Always False, never enable

def get_show_first_name() -> bool:
    """Get show first name status"""
    return _settings["show_first_name"]

def toggle_show_first_name():
    """Toggle show first name status"""
    _settings["show_first_name"] = not _settings["show_first_name"]
    return _settings["show_first_name"]

def get_show_username() -> bool:
    """Get show username status"""
    return _settings["show_username"]

def toggle_show_username():
    """Toggle show username status"""
    _settings["show_username"] = not _settings["show_username"]
    return _settings["show_username"]

# User management functions
def is_user_blocked(user_id: int) -> bool:
    """Check if user is blocked"""
    return user_id in _settings["blocked_users"]

def block_user(user_id: int):
    """Block a user"""
    _settings["blocked_users"].add(user_id)

def unblock_user(user_id: int):
    """Unblock a user"""
    _settings["blocked_users"].discard(user_id)

def get_user_limit(user_id: int) -> Optional[Dict]:
    """Get user limit"""
    return _settings["user_limits"].get(user_id)

def set_user_limit(user_id: int, daily_limit: int):
    """Set user daily limit"""
    from datetime import date
    _settings["user_limits"][user_id] = {
        "daily_limit": daily_limit,
        "current_count": 0,
        "reset_date": str(date.today())
    }

def remove_user_limit(user_id: int):
    """Remove user limit"""
    _settings["user_limits"].pop(user_id, None)

def increment_user_request_count(user_id: int) -> bool:
    """Increment user request count and check if limit exceeded"""
    from datetime import date
    today = str(date.today())
    
    if user_id not in _settings["user_limits"]:
        return True  # No limit
    
    limit_info = _settings["user_limits"][user_id]
    
    # Reset if new day
    if limit_info["reset_date"] != today:
        limit_info["current_count"] = 0
        limit_info["reset_date"] = today
    
    limit_info["current_count"] += 1
    
    return limit_info["current_count"] <= limit_info["daily_limit"]

def delete_user_data(user_id: int):
    """Delete all user data (for user deletion)"""
    from services.stats import delete_user_from_stats
    
    # Remove from stats
    delete_user_from_stats(user_id)
    
    # Remove from settings
    _settings["blocked_users"].discard(user_id)
    _settings["user_limits"].pop(user_id, None)
    # price_access admins.json da saqlanadi, shuning uchun shu yerda o'chirish kerak
    from services.admin_storage import remove_price_access, remove_discount_access
    remove_price_access(user_id)  # admins.json dan o'chiradi
    remove_discount_access(user_id)  # admins.json dan o'chiradi


# API access functions for model prices
def has_api_access(user_id: int) -> bool:
    """
    Check if user has API access for model prices.
    admins.json dagi price_access ro'yxatidan o'qiladi.
    """
    # admins.json dan price_access ni tekshirish
    from services.admin_storage import has_price_access
    return has_price_access(user_id)


def grant_api_access(user_id: int, name: str = ""):
    """
    Grant API access to user with name - admins.json ga yoziladi.
    """
    from services.admin_storage import add_price_access
    user_name = name.strip() if name else f"User {user_id}"
    add_price_access(user_id, user_name)  # Bu darhol admins.json ga yozadi


def revoke_api_access(user_id: int):
    """Revoke API access from user - admins.json dan o'chiriladi."""
    from services.admin_storage import remove_price_access
    remove_price_access(user_id)  # Bu darhol admins.json dan o'chiradi


def get_api_users() -> dict:
    """
    Get dict of all API users from admins.json price_access section.
    Returns: {user_id: {"name": str}} format
    """
    from services.admin_storage import get_price_access
    price_access_dict = get_price_access()  # admins.json dan o'qiladi
    # Format conversion: {str(user_id): name} -> {int(user_id): {"name": name}}
    result = {}
    for user_id_str, name in price_access_dict.items():
        try:
            user_id_int = int(user_id_str)
            result[user_id_int] = {"name": name}
        except ValueError:
            continue
    return result


# ==================== ADMIN NAMES ====================

def set_admin_name(user_id: int, name: str):
    """Set admin name"""
    _settings["admin_names"][user_id] = {"name": name.strip()}


def get_admin_name(user_id: int) -> str:
    """Get admin name, return default if not found"""
    if user_id in _settings["admin_names"]:
        return _settings["admin_names"][user_id].get("name", f"User {user_id}")
    return f"User {user_id}"


def remove_admin_name(user_id: int):
    """Remove admin name"""
    _settings["admin_names"].pop(user_id, None)


def set_helper_admin_name(user_id: int, name: str):
    """Set helper admin name"""
    _settings["helper_admin_names"][user_id] = {"name": name.strip()}


def get_helper_admin_name(user_id: int) -> str:
    """Get helper admin name, return default if not found"""
    if user_id in _settings["helper_admin_names"]:
        return _settings["helper_admin_names"][user_id].get("name", f"User {user_id}")
    return f"User {user_id}"


def remove_helper_admin_name(user_id: int):
    """Remove helper admin name"""
    _settings["helper_admin_names"].pop(user_id, None)


# ==================== DISCOUNT ACCESS ====================

def has_discount_access(user_id: int) -> bool:
    """
    Check if user has discount access.
    admins.json dagi discount_access ro'yxatidan o'qiladi.
    """
    from services.admin_storage import has_discount_access as storage_has_discount_access
    return storage_has_discount_access(user_id)


def grant_discount_access(user_id: int, name: str = ""):
    """
    Grant discount access to user with name - admins.json ga yoziladi.
    """
    from services.admin_storage import add_discount_access
    user_name = name.strip() if name else f"User {user_id}"
    add_discount_access(user_id, user_name)


def revoke_discount_access(user_id: int):
    """Revoke discount access from user - admins.json dan o'chiriladi."""
    from services.admin_storage import remove_discount_access
    remove_discount_access(user_id)


def get_discount_users() -> dict:
    """
    Get dict of all discount access users from admins.json discount_access section.
    Returns: {user_id: {"name": str}} format
    """
    from services.admin_storage import get_discount_access
    discount_access_dict = get_discount_access()
    # Format conversion: {str(user_id): name} -> {int(user_id): {"name": name}}
    result = {}
    for user_id_str, name in discount_access_dict.items():
        try:
            user_id_int = int(user_id_str)
            result[user_id_int] = {"name": name}
        except ValueError:
            continue
    return result

