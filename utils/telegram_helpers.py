"""
Telegram Helper Functions - Telegram bilan ishlashni osonlashtirish.

Bu modul quyidagi vazifalarni bajaradi:
- Xabarni xavfsiz tahrirlash
- Callback query ni to'g'ri handle qilish
- Telegram API xatolarini to'g'ri qayta ishlash
"""

import logging
from typing import Optional, Union

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


async def safe_edit_message(
    message: Message,
    text: Optional[str] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    fallback_to_new: bool = True
) -> bool:
    """
    Xabarni xavfsiz tahrirlash.
    
    Agar tahrirlash muvaffaqiyatsiz bo'lsa (masalan, xabar o'chirilgan),
    yangi xabar yuboradi.
    
    Args:
        message: Message object
        text: Yangi matn (optional)
        reply_markup: Yangi klaviatura (optional)
        fallback_to_new: Agar tahrirlash muvaffaqiyatsiz bo'lsa, yangi xabar yuborish
        
    Returns:
        True agar muvaffaqiyatli, False agar xatolik
    """
    try:
        # Try to edit existing message
        if text and reply_markup:
            await message.edit_text(text=text, reply_markup=reply_markup)
        elif text:
            await message.edit_text(text=text)
        elif reply_markup:
            await message.edit_reply_markup(reply_markup=reply_markup)
        
        logger.debug(f"Message edited successfully: {message.message_id}")
        return True
        
    except TelegramBadRequest as e:
        error_text = str(e).lower()
        
        # Message not found or deleted
        if "message to edit not found" in error_text:
            logger.warning(f"Message {message.message_id} not found, cannot edit")
            
            if fallback_to_new and text:
                # Send new message instead
                await message.answer(text=text, reply_markup=reply_markup)
                logger.info("Sent new message as fallback")
                return True
            
            return False
        
        # Message is not modified
        elif "message is not modified" in error_text:
            logger.debug("Message content is the same, skipping edit")
            return True
        
        # Message to delete not found
        elif "message to delete not found" in error_text:
            logger.warning(f"Message {message.message_id} already deleted")
            return False
        
        # Other Telegram errors
        else:
            logger.error(f"Telegram error while editing message: {e}")
            raise
    
    except Exception as e:
        logger.error(f"Unexpected error while editing message: {e}", exc_info=True)
        return False


async def safe_delete_message(message: Message) -> bool:
    """
    Xabarni xavfsiz o'chirish.
    
    Args:
        message: Message object
        
    Returns:
        True agar muvaffaqiyatli, False agar xatolik
    """
    try:
        await message.delete()
        logger.debug(f"Message deleted successfully: {message.message_id}")
        return True
        
    except TelegramBadRequest as e:
        error_text = str(e).lower()
        
        if "message to delete not found" in error_text:
            logger.warning(f"Message {message.message_id} already deleted")
            return False
        else:
            logger.error(f"Error deleting message: {e}")
            return False
    
    except Exception as e:
        logger.error(f"Unexpected error deleting message: {e}")
        return False


async def safe_answer_callback(
    callback_query: CallbackQuery,
    text: Optional[str] = None,
    show_alert: bool = False
) -> bool:
    """
    Callback query ni xavfsiz javoblash.
    
    Args:
        callback_query: CallbackQuery object
        text: Javob matni (optional)
        show_alert: Alert ko'rsatish (default: False)
        
    Returns:
        True agar muvaffaqiyatli, False agar xatolik
    """
    try:
        await callback_query.answer(text=text, show_alert=show_alert)
        logger.debug(f"Callback answered: {callback_query.id}")
        return True
        
    except TelegramBadRequest as e:
        error_text = str(e).lower()
        
        if "query is too old" in error_text:
            logger.warning(f"Callback query {callback_query.id} is too old")
            return False
        else:
            logger.error(f"Error answering callback: {e}")
            return False
    
    except Exception as e:
        logger.error(f"Unexpected error answering callback: {e}")
        return False


async def edit_or_send_message(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    prefer_edit: bool = True
) -> Message:
    """
    Xabarni tahrirlash yoki yangi xabar yuborish.
    
    Args:
        message: Message object
        text: Xabar matni
        reply_markup: Klaviatura (optional)
        prefer_edit: Avval tahrirlashga harakat qilish (default: True)
        
    Returns:
        Tahrirlangan yoki yangi yuborilgan Message object
    """
    if prefer_edit:
        success = await safe_edit_message(
            message=message,
            text=text,
            reply_markup=reply_markup,
            fallback_to_new=False
        )
        
        if success:
            return message
    
    # Send new message
    new_message = await message.answer(text=text, reply_markup=reply_markup)
    logger.debug(f"Sent new message: {new_message.message_id}")
    return new_message


async def handle_callback_with_edit(
    callback_query: CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    answer_text: Optional[str] = None,
    show_alert: bool = False
) -> bool:
    """
    Callback ni handle qilish va xabarni tahrirlash (to'liq flow).
    
    Args:
        callback_query: CallbackQuery object
        text: Yangi xabar matni
        reply_markup: Klaviatura (optional)
        answer_text: Callback javob matni (optional)
        show_alert: Alert ko'rsatish (default: False)
        
    Returns:
        True agar muvaffaqiyatli, False agar xatolik
    """
    try:
        # Edit message
        await safe_edit_message(
            message=callback_query.message,
            text=text,
            reply_markup=reply_markup,
            fallback_to_new=True
        )
        
        # Answer callback
        await safe_answer_callback(
            callback_query=callback_query,
            text=answer_text,
            show_alert=show_alert
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Error handling callback: {e}", exc_info=True)
        
        # Try to answer callback at least
        try:
            await callback_query.answer("❌ Xatolik yuz berdi", show_alert=True)
        except:
            pass
        
        return False


# Backward compatibility aliases
async def try_edit_message(message: Message, text: str, reply_markup=None):
    """Legacy function - use safe_edit_message instead."""
    return await safe_edit_message(message, text, reply_markup)


async def try_delete_message(message: Message):
    """Legacy function - use safe_delete_message instead."""
    return await safe_delete_message(message)

