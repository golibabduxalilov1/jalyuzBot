"""
Utility functions for managing bot messages - deletion and cleanup.
"""
import logging
from typing import Optional, List
from aiogram import Bot
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)

# Har bir foydalanuvchi uchun oxirgi asosiy menyu xabarini saqlash (shared across modules)
_MAIN_MENU_MESSAGES: dict[int, int] = {}

# CONTENT xabarlarni track qilish (AI javoblari, savol javoblari, Astatka natijalari)
# chat_id -> set of message_ids
_CONTENT_MESSAGES: dict[int, set[int]] = {}

# Bot yuborgan barcha xabarlarni kuzatish (/start uchun tozalash)
# chat_id -> set of message_ids (rasm, media group, text, tugma)
_USER_BOT_MESSAGES: dict[int, set[int]] = {}


async def delete_message_safe(bot: Bot, chat_id: int, message_id: int, message_text: Optional[str] = None):
    """
    Safely delete a message, ignoring errors if message doesn't exist.
    IMPORTANT: CONTENT xabarlar HECH QACHON o'chirilmaydi!
    
    Args:
        bot: Bot instance
        chat_id: Chat ID
        message_id: Message ID to delete
        message_text: Optional message text to check if it's CONTENT (if provided, will check before deletion)
    """
    # CONTENT xabar ekanligini tekshirish (track qilingan)
    if is_content_message(chat_id, message_id):
        logger.debug(f"Skipping deletion of CONTENT message {message_id} (tracked)")
        return
    
    # Agar message_text berilgan bo'lsa, CONTENT ekanligini tekshirish (text-based)
    if message_text is not None:
        if is_result_message_text(message_text):
            logger.debug(f"Skipping deletion of CONTENT message {message_id} (text-based)")
            return
    
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug(f"Could not delete message {message_id}: {e}")


def is_result_message_text(text: Optional[str]) -> bool:
    """
    Check if message text contains CONTENT indicators that should NEVER be deleted.
    CONTENT includes:
    - Astatka natijalari (Sheets'dan kelgan text + rasm)
    - AI tomonidan berilgan javoblar
    - Savol-berish bo'limidagi barcha javoblar
    
    Args:
        text: Message text to check
        
    Returns:
        True if message is CONTENT (should not be deleted), False otherwise
    """
    if not text:
        return False
    
    # CONTENT xabarlarni aniqlash uchun protected phrases
    protected_phrases = [
        "Model:",                    # Astatka natijalari
        "Topilgan mahsulotlar:",     # Astatka natijalari
        "Kolleksiya:",               # Astatka natijalari
        "Umumiy qoldiq:",            # Astatka natijalari
        "Colleksiya:",               # Astatka natijalari (alternativ format)
        "Sana:",                     # Astatka natijalari
    ]
    
    text_lower = text.lower()
    for phrase in protected_phrases:
        if phrase.lower() in text_lower:
            return True
    
    # AI javoblari va savol javoblari odatda uzun matn bo'ladi va xabar uzunligi 100+ belgi
    # Lekin bu aniq emas, shuning uchun faqat yuqoridagi phrases ishlatiladi
    # AI javoblari va savol javoblari odatda reply_markup bilan yuboriladi va
    # ularni alohida track qilish kerak (message_utils da CONTENT tracking qo'shish mumkin)
    
    return False


def store_content_message(chat_id: int, message_id: int):
    """
    Store a CONTENT message ID (AI javoblari, savol javoblari, Astatka natijalari).
    CONTENT xabarlar HECH QACHON o'chirilmaydi.
    
    Args:
        chat_id: Chat ID
        message_id: Message ID of CONTENT message
    """
    if chat_id not in _CONTENT_MESSAGES:
        _CONTENT_MESSAGES[chat_id] = set()
    _CONTENT_MESSAGES[chat_id].add(message_id)


def is_content_message(chat_id: int, message_id: int) -> bool:
    """
    Check if a message is a CONTENT message that should not be deleted.
    
    Args:
        chat_id: Chat ID
        message_id: Message ID to check
        
    Returns:
        True if message is CONTENT (should not be deleted), False otherwise
    """
    return message_id in _CONTENT_MESSAGES.get(chat_id, set())


async def cleanup_before_result(
    bot: Bot,
    chat_id: int,
    user_message: Optional[Message] = None,
    callback_message: Optional[Message] = None,
    messages_to_delete: Optional[List[int]] = None
):
    """
    Clean up messages before showing result:
    1. Delete user's last message
    2. Delete callback message (button message) - BUT NOT if it's CONTENT
    3. Delete any additional specified messages - BUT NOT if they are CONTENT
    
    Args:
        bot: Bot instance
        chat_id: Chat ID
        user_message: User's message to delete
        callback_message: Callback message (button message) to delete
        messages_to_delete: List of additional message IDs to delete
    """
    # 1. Delete user's last message
    if user_message:
        await delete_message_safe(bot, chat_id, user_message.message_id)
    
    # 2. Delete callback message - BUT NOT if it's CONTENT
    if callback_message:
        # Check if callback message is CONTENT
        if not is_content_message(chat_id, callback_message.message_id):
            callback_text = callback_message.text or callback_message.caption or ""
            if not is_result_message_text(callback_text):
                await delete_message_safe(bot, chat_id, callback_message.message_id, callback_text)
    
    # 3. Delete additional messages - BUT NOT if they are CONTENT
    if messages_to_delete:
        for msg_id in messages_to_delete:
            if not is_content_message(chat_id, msg_id):
                await delete_message_safe(bot, chat_id, msg_id)


async def is_result_message(bot: Bot, chat_id: int, message_id: int) -> bool:
    """
    Check if a message is a result message that should not be deleted.
    Result messages contain "Model:" or "Topilgan mahsulotlar:" in their text.
    
    Args:
        bot: Bot instance
        chat_id: Chat ID
        message_id: Message ID to check
        
    Returns:
        True if message is a result message (should not be deleted), False otherwise
    """
    protected_phrases = ["Model:", "Topilgan mahsulotlar:"]
    
    try:
        # Try to get message using forward_message or similar approach
        # Since aiogram doesn't have direct get_message_by_id, we'll use a workaround
        # We can try to edit the message to check its content, but that's not ideal
        
        # Better approach: Use get_updates or store message text when sending
        # For now, we'll use a simpler approach: try to get message info
        # Actually, we can't easily get message by ID in aiogram without storing it
        
        # Workaround: We'll check message text by trying to access it
        # But since we can't get message by ID directly, we'll return False
        # and let the caller handle it differently
        
        # Actually, the best approach is to check message text when we have access to it
        # So we'll create a helper that checks message text if provided
        return False  # Default: not a result message (will be checked elsewhere)
    except Exception:
        return False


async def try_delete_recent_bot_messages(
    bot: Bot,
    chat_id: int,
    before_message_id: int,
    count: int = 10,
    exclude_message_ids: Optional[List[int]] = None
):
    """
    Try to delete recent bot messages before a given message ID.
    This attempts to delete up to 'count' messages with IDs less than before_message_id.
    BUT: Never delete messages that contain "Model:" or "Topilgan mahsulotlar:" in their text.
    
    Note: Since we can't easily get message text by ID, the caller should track
    result message IDs and pass them in exclude_message_ids.
    
    Args:
        bot: Bot instance
        chat_id: Chat ID
        before_message_id: Message ID to delete messages before
        count: Maximum number of messages to try deleting
        exclude_message_ids: List of message IDs to exclude from deletion (result messages)
    """
    deleted = 0
    exclude_set = set(exclude_message_ids or [])
    
    # Try deleting messages in reverse order (newest first)
    for i in range(count):
        msg_id = before_message_id - i - 1
        if msg_id <= 0:
            break
        
        # Skip if message is in exclude list (result messages)
        if msg_id in exclude_set:
            continue
            
        try:
            # Try to delete message
            # Note: We can't easily check message text by ID, so we rely on exclude_message_ids
            # If a message contains "Model:" or "Topilgan mahsulotlar:", it should be in exclude_message_ids
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted += 1
        except Exception:
            # Message doesn't exist, can't be deleted, or is protected
            # Continue to next message
            pass


# ===== Main Menu Management Functions =====

def store_main_menu_message(chat_id: int, message_id: int):
    """
    Store the main menu message ID for a chat.
    Also tracks it for /start cleanup.
    
    Args:
        chat_id: Chat ID
        message_id: Message ID of the main menu
    """
    _MAIN_MENU_MESSAGES[chat_id] = message_id
    track_bot_message(chat_id, message_id)


def get_main_menu_message_id(chat_id: int) -> Optional[int]:
    """
    Get the stored main menu message ID for a chat.
    
    Args:
        chat_id: Chat ID
        
    Returns:
        Message ID if exists, None otherwise
    """
    return _MAIN_MENU_MESSAGES.get(chat_id)


async def delete_main_menu_message(bot: Bot, chat_id: int):
    """
    Delete the stored main menu message for a chat.
    
    Args:
        bot: Bot instance
        chat_id: Chat ID
    """
    main_menu_id = _MAIN_MENU_MESSAGES.get(chat_id)
    if main_menu_id:
        await delete_message_safe(bot, chat_id, main_menu_id)
        _MAIN_MENU_MESSAGES.pop(chat_id, None)


# ===== Bot Messages Tracking Functions (for /start cleanup) =====

def track_bot_message(chat_id: int, message_id: int):
    """
    Track a bot message ID for cleanup on /start.
    
    Args:
        chat_id: Chat ID
        message_id: Message ID sent by bot
    """
    if chat_id not in _USER_BOT_MESSAGES:
        _USER_BOT_MESSAGES[chat_id] = set()
    _USER_BOT_MESSAGES[chat_id].add(message_id)


def track_bot_messages(chat_id: int, message_ids: list[int]):
    """
    Track multiple bot message IDs for cleanup on /start.
    
    Args:
        chat_id: Chat ID
        message_ids: List of message IDs sent by bot
    """
    if chat_id not in _USER_BOT_MESSAGES:
        _USER_BOT_MESSAGES[chat_id] = set()
    _USER_BOT_MESSAGES[chat_id].update(message_ids)


async def cleanup_all_bot_messages(bot: Bot, chat_id: int):
    """
    Delete all tracked bot messages for a chat (used on /start).
    Ignores errors if messages don't exist or can't be deleted.
    
    Args:
        bot: Bot instance
        chat_id: Chat ID
    """
    message_ids = _USER_BOT_MESSAGES.get(chat_id, set())
    if not message_ids:
        return
    
    # Delete all tracked messages (ignore errors)
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            # Message not found, already deleted, or can't be deleted
            # Ignore and continue
            pass
    
    # Clear tracked messages for this chat
    _USER_BOT_MESSAGES.pop(chat_id, None)
    _MAIN_MENU_MESSAGES.pop(chat_id, None)
    _CONTENT_MESSAGES.pop(chat_id, None)

