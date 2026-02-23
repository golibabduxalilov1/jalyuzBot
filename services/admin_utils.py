"""
Admin utility functions for checking admin permissions.
"""
from config import ADMINS, HELPER_ADMINS
from services.admin_storage import (
    is_super_admin as is_super_admin_storage_func,
    is_admin_storage, is_any_admin_storage,
    get_super_admins, get_admins, get_main_admins
)


def is_super_admin(user_id: int) -> bool:
    """
    Check if user is a super admin (has all permissions).
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        True if user is a super admin, False otherwise
    """
    # Avval admins.json dan tekshirish
    if is_super_admin_storage_func(user_id):
        return True
    # Keyin config.py dan (eski adminlar uchun)
    return user_id in ADMINS


def is_admin(user_id: int) -> bool:
    """
    Check if user is an admin (has limited permissions).
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        True if user is an admin, False otherwise
    """
    # Avval admins.json dan tekshirish
    if is_admin_storage(user_id):
        return True
    # Keyin config.py dan (eski adminlar uchun)
    return user_id in HELPER_ADMINS


def is_any_admin(user_id: int) -> bool:
    """
    Check if user is any type of admin (super or admin).
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        True if user is any admin, False otherwise
    """
    return is_super_admin(user_id) or is_admin(user_id)


# Backward compatibility
def is_helper_admin(user_id: int) -> bool:
    """Backward compatibility - same as is_admin"""
    return is_admin(user_id)


def get_all_main_admin_ids() -> list:
    """Barcha asosiy admin ID larini olish (admins.json + config.py)"""
    from config import ADMINS
    storage_admins = [int(uid) for uid in get_main_admins().keys()]
    # config.py dagi adminlarni ham qo'shish (duplikatlarni olib tashlash)
    all_admins = list(set(storage_admins + ADMINS))
    return all_admins


def get_all_helper_admin_ids() -> list:
    """Barcha yordamchi admin ID larini olish (admins.json + config.py)"""
    from config import HELPER_ADMINS
    storage_helpers = [int(uid) for uid in get_helper_admins().keys()]
    # config.py dagi helper adminlarni ham qo'shish (duplikatlarni olib tashlash)
    all_helpers = list(set(storage_helpers + HELPER_ADMINS))
    return all_helpers

