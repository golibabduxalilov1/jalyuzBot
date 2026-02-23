"""
Error Handler - Xatolarni boshqarish va logging.

Bu service quyidagi vazifalarni bajaradi:
- Spetsifik exception handling
- Error logging
- User-friendly error messages
- Error recovery strategies
"""

import logging
import traceback
from typing import Optional, Callable, Any, Dict
from functools import wraps

from aiogram.types import Message, CallbackQuery

logger = logging.getLogger(__name__)


# ==================== CUSTOM EXCEPTIONS ====================

class BotError(Exception):
    """Base exception for bot errors."""
    def __init__(self, message: str, user_message: Optional[str] = None):
        self.message = message
        self.user_message = user_message or "Xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."
        super().__init__(self.message)


class DatabaseError(BotError):
    """Database-related errors."""
    def __init__(self, message: str):
        super().__init__(
            message=message,
            user_message="Ma'lumotlar bazasi bilan bog'lanishda xatolik yuz berdi."
        )


class GoogleSheetsError(BotError):
    """Google Sheets API errors."""
    def __init__(self, message: str):
        super().__init__(
            message=message,
            user_message="Google Sheets bilan bog'lanishda xatolik yuz berdi."
        )


class AIServiceError(BotError):
    """AI service errors."""
    def __init__(self, message: str):
        super().__init__(
            message=message,
            user_message="AI xizmati bilan bog'lanishda xatolik yuz berdi."
        )


class ValidationError(BotError):
    """Input validation errors."""
    def __init__(self, message: str, user_message: Optional[str] = None):
        super().__init__(
            message=message,
            user_message=user_message or "Noto'g'ri ma'lumot kiritildi."
        )


class ImageProcessingError(BotError):
    """Image processing errors."""
    def __init__(self, message: str):
        super().__init__(
            message=message,
            user_message="Rasmni qayta ishlashda xatolik yuz berdi."
        )


class RateLimitError(BotError):
    """Rate limiting errors."""
    def __init__(self, message: str, user_message: Optional[str] = None):
        super().__init__(
            message=message,
            user_message=user_message or "So'rov limiti oshib ketdi. Iltimos, keyinroq qayta urinib ko'ring."
        )


# ==================== ERROR HANDLER DECORATOR ====================

def handle_errors(user_message: Optional[str] = None):
    """
    Decorator to handle errors in handler functions.
    
    Usage:
        @handle_errors("Mahsulot topishda xatolik yuz berdi.")
        async def my_handler(message: Message):
            # handler code
    
    Args:
        user_message: Custom error message for user
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            try:
                return await func(*args, **kwargs)
            
            except ValidationError as e:
                logger.warning(f"Validation error in {func.__name__}: {e.message}")
                await _send_error_message(args, e.user_message)
            
            except DatabaseError as e:
                logger.error(f"Database error in {func.__name__}: {e.message}")
                await _send_error_message(args, e.user_message)
            
            except GoogleSheetsError as e:
                logger.error(f"Google Sheets error in {func.__name__}: {e.message}")
                await _send_error_message(args, e.user_message)
            
            except AIServiceError as e:
                logger.error(f"AI service error in {func.__name__}: {e.message}")
                await _send_error_message(args, e.user_message)
            
            except ImageProcessingError as e:
                logger.error(f"Image processing error in {func.__name__}: {e.message}")
                await _send_error_message(args, e.user_message)
            
            except RateLimitError as e:
                logger.warning(f"Rate limit error in {func.__name__}: {e.message}")
                await _send_error_message(args, e.user_message)
            
            except BotError as e:
                logger.error(f"Bot error in {func.__name__}: {e.message}")
                await _send_error_message(args, e.user_message)
            
            except Exception as e:
                logger.error(
                    f"Unexpected error in {func.__name__}: {e}",
                    exc_info=True
                )
                error_msg = user_message or "Xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."
                await _send_error_message(args, error_msg)
        
        return wrapper
    return decorator


async def _send_error_message(args: tuple, message: str):
    """
    Send error message to user.
    
    Args:
        args: Function arguments (should contain Message or CallbackQuery)
        message: Error message to send
    """
    try:
        # Find Message or CallbackQuery in args
        for arg in args:
            if isinstance(arg, Message):
                await arg.answer(f"❌ {message}")
                return
            elif isinstance(arg, CallbackQuery):
                await arg.message.answer(f"❌ {message}")
                await arg.answer()
                return
    except Exception as e:
        logger.error(f"Error sending error message: {e}")


# ==================== ERROR LOGGING ====================

def log_error(error: Exception, context: Optional[Dict[str, Any]] = None):
    """
    Log error with context information.
    
    Args:
        error: Exception object
        context: Additional context (user_id, handler_name, etc.)
    """
    error_info = {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": traceback.format_exc()
    }
    
    if context:
        error_info.update(context)
    
    logger.error(f"Error occurred: {error_info}")


def log_warning(message: str, context: Optional[Dict[str, Any]] = None):
    """
    Log warning with context information.
    
    Args:
        message: Warning message
        context: Additional context
    """
    warning_info = {"message": message}
    
    if context:
        warning_info.update(context)
    
    logger.warning(f"Warning: {warning_info}")


# ==================== ERROR RECOVERY ====================

async def retry_on_error(
    func: Callable,
    max_retries: int = 3,
    delay_seconds: int = 1,
    *args,
    **kwargs
) -> Any:
    """
    Retry function on error.
    
    Args:
        func: Function to retry
        max_retries: Maximum number of retries
        delay_seconds: Delay between retries in seconds
        *args: Function arguments
        **kwargs: Function keyword arguments
        
    Returns:
        Function result
        
    Raises:
        Last exception if all retries failed
    """
    import asyncio
    
    last_error = None
    
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e
            logger.warning(
                f"Retry {attempt + 1}/{max_retries} failed for {func.__name__}: {e}"
            )
            
            if attempt < max_retries - 1:
                await asyncio.sleep(delay_seconds)
    
    # All retries failed
    logger.error(f"All {max_retries} retries failed for {func.__name__}")
    raise last_error


# ==================== ERROR REPORTING ====================

def format_error_for_admin(error: Exception, context: Optional[Dict[str, Any]] = None) -> str:
    """
    Format error message for admin notification.
    
    Args:
        error: Exception object
        context: Additional context
        
    Returns:
        Formatted error message
    """
    lines = [
        "🚨 Bot Error Report",
        "",
        f"Error Type: {type(error).__name__}",
        f"Error Message: {str(error)}",
        ""
    ]
    
    if context:
        lines.append("Context:")
        for key, value in context.items():
            lines.append(f"  {key}: {value}")
        lines.append("")
    
    lines.append("Traceback:")
    lines.append(traceback.format_exc())
    
    return "\n".join(lines)


async def notify_admin_about_error(
    bot,
    admin_id: int,
    error: Exception,
    context: Optional[Dict[str, Any]] = None
):
    """
    Notify admin about critical error.
    
    Args:
        bot: Bot instance
        admin_id: Admin user ID
        error: Exception object
        context: Additional context
    """
    try:
        error_message = format_error_for_admin(error, context)
        
        # Split message if too long
        max_length = 4000
        if len(error_message) > max_length:
            error_message = error_message[:max_length] + "\n\n... (truncated)"
        
        await bot.send_message(
            chat_id=admin_id,
            text=error_message
        )
        
        logger.info(f"Admin {admin_id} notified about error")
        
    except Exception as e:
        logger.error(f"Error notifying admin: {e}")


# ==================== ERROR STATISTICS ====================

class ErrorStats:
    """Track error statistics."""
    
    def __init__(self):
        self._errors = {}
        self._total_errors = 0
    
    def record_error(self, error_type: str):
        """Record an error occurrence."""
        if error_type not in self._errors:
            self._errors[error_type] = 0
        self._errors[error_type] += 1
        self._total_errors += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get error statistics."""
        return {
            "total_errors": self._total_errors,
            "errors_by_type": self._errors.copy(),
            "most_common": max(self._errors.items(), key=lambda x: x[1]) if self._errors else None
        }
    
    def reset(self):
        """Reset error statistics."""
        self._errors.clear()
        self._total_errors = 0


# Global error stats instance
_error_stats = ErrorStats()


def get_error_stats() -> Dict[str, Any]:
    """Get global error statistics."""
    return _error_stats.get_stats()


def record_error(error_type: str):
    """Record error in global statistics."""
    _error_stats.record_error(error_type)

