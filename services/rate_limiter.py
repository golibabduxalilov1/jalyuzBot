"""
Rate limiting для защиты бота от спама и злоупотреблений.

Использует алгоритм "скользящего окна" (sliding window) для отслеживания
количества запросов пользователя в заданный период времени.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Dict, Deque, Tuple, Optional
from dataclasses import dataclass

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, Update


logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Конфигурация для rate limiting."""
    
    # Максимальное количество запросов в окне
    max_requests: int = 20
    
    # Размер окна в секундах
    window_seconds: int = 60
    
    # Время блокировки при превышении лимита (в секундах)
    cooldown_seconds: int = 300  # 5 минут
    
    # Сообщение при превышении лимита
    limit_message: str = (
        "⚠️ Siz juda ko'p so'rov yuboryapsiz!\n"
        "Iltimos, {cooldown} soniyadan keyin qayta urinib ko'ring."
    )
    
    # Админы освобождены от лимитов?
    exempt_admins: bool = True
    
    # ID пользователей, освобожденных от лимитов
    exempt_user_ids: set = None
    
    def __post_init__(self):
        if self.exempt_user_ids is None:
            self.exempt_user_ids = set()


class RateLimiter:
    """
    Класс для отслеживания и ограничения частоты запросов пользователей.
    
    Использует алгоритм "скользящего окна":
    - Храним временные метки последних запросов пользователя
    - Удаляем метки старше window_seconds
    - Если количество меток превышает max_requests, блокируем пользователя
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        """
        Инициализация rate limiter.
        
        Args:
            config: Конфигурация для rate limiting
        """
        self.config = config or RateLimitConfig()
        
        # {user_id: deque([timestamp1, timestamp2, ...])}
        self._user_requests: Dict[int, Deque[float]] = defaultdict(deque)
        
        # {user_id: blocked_until_timestamp}
        self._blocked_users: Dict[int, float] = {}
        
        # Статистика
        self._total_requests = 0
        self._blocked_requests = 0
        
        # Lock для потокобезопасности
        self._lock = asyncio.Lock()
        
        logger.info(
            f"🛡️ Rate limiter initialized: "
            f"{self.config.max_requests} requests per {self.config.window_seconds}s"
        )
    
    async def check_rate_limit(
        self,
        user_id: int,
        is_admin: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Проверить, не превышен ли лимит запросов для пользователя.
        
        Args:
            user_id: ID пользователя
            is_admin: Является ли пользователь админом
            
        Returns:
            Tuple[allowed, error_message]:
                - allowed: True если запрос разрешен, False если заблокирован
                - error_message: Сообщение об ошибке (если заблокирован)
        """
        async with self._lock:
            self._total_requests += 1
            current_time = time.time()
            
            # Проверка: является ли пользователь освобожденным от лимитов
            if is_admin and self.config.exempt_admins:
                return True, None
            
            if user_id in self.config.exempt_user_ids:
                return True, None
            
            # Проверка: заблокирован ли пользователь
            if user_id in self._blocked_users:
                blocked_until = self._blocked_users[user_id]
                if current_time < blocked_until:
                    # Пользователь все еще заблокирован
                    remaining_time = int(blocked_until - current_time)
                    self._blocked_requests += 1
                    error_msg = self.config.limit_message.format(
                        cooldown=remaining_time
                    )
                    logger.warning(
                        f"🚫 User {user_id} blocked for {remaining_time}s "
                        f"(total blocked: {self._blocked_requests})"
                    )
                    return False, error_msg
                else:
                    # Время блокировки истекло
                    del self._blocked_users[user_id]
                    logger.info(f"✅ User {user_id} unblocked")
            
            # Получаем очередь запросов пользователя
            user_queue = self._user_requests[user_id]
            
            # Удаляем старые запросы (вне окна)
            cutoff_time = current_time - self.config.window_seconds
            while user_queue and user_queue[0] < cutoff_time:
                user_queue.popleft()
            
            # Проверка: превышен ли лимит
            if len(user_queue) >= self.config.max_requests:
                # Блокируем пользователя
                self._blocked_users[user_id] = current_time + self.config.cooldown_seconds
                self._blocked_requests += 1
                
                error_msg = self.config.limit_message.format(
                    cooldown=self.config.cooldown_seconds
                )
                
                logger.warning(
                    f"🚫 User {user_id} rate limit exceeded: "
                    f"{len(user_queue)} requests in {self.config.window_seconds}s "
                    f"(blocked for {self.config.cooldown_seconds}s)"
                )
                
                return False, error_msg
            
            # Добавляем текущий запрос в очередь
            user_queue.append(current_time)
            
            return True, None
    
    def get_stats(self) -> Dict:
        """
        Получить статистику rate limiter.
        
        Returns:
            Словарь со статистикой:
                - total_requests: Общее количество запросов
                - blocked_requests: Количество заблокированных запросов
                - active_users: Количество активных пользователей
                - blocked_users: Количество заблокированных пользователей
        """
        return {
            "total_requests": self._total_requests,
            "blocked_requests": self._blocked_requests,
            "active_users": len(self._user_requests),
            "blocked_users": len(self._blocked_users),
            "block_rate": (
                self._blocked_requests / self._total_requests
                if self._total_requests > 0 else 0
            )
        }
    
    async def cleanup(self):
        """
        Очистить устаревшие данные (вызывается периодически).
        """
        async with self._lock:
            current_time = time.time()
            
            # Очистка заблокированных пользователей
            expired_blocks = [
                user_id for user_id, blocked_until in self._blocked_users.items()
                if current_time >= blocked_until
            ]
            for user_id in expired_blocks:
                del self._blocked_users[user_id]
            
            # Очистка пользовательских очередей
            cutoff_time = current_time - self.config.window_seconds
            inactive_users = []
            
            for user_id, user_queue in self._user_requests.items():
                # Удаляем старые запросы
                while user_queue and user_queue[0] < cutoff_time:
                    user_queue.popleft()
                
                # Если очередь пуста, помечаем пользователя для удаления
                if not user_queue:
                    inactive_users.append(user_id)
            
            # Удаляем неактивных пользователей
            for user_id in inactive_users:
                del self._user_requests[user_id]
            
            if expired_blocks or inactive_users:
                logger.info(
                    f"🧹 Cleaned up: {len(expired_blocks)} expired blocks, "
                    f"{len(inactive_users)} inactive users"
                )


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware для применения rate limiting к сообщениям и callback'ам.
    """
    
    def __init__(
        self,
        rate_limiter: RateLimiter,
        admin_ids: set = None
    ):
        """
        Инициализация middleware.
        
        Args:
            rate_limiter: Экземпляр RateLimiter
            admin_ids: Множество ID админов (для освобождения от лимитов)
        """
        super().__init__()
        self.rate_limiter = rate_limiter
        self.admin_ids = admin_ids or set()
    
    async def __call__(self, handler, event: Update, data: dict):
        """
        Обработка события (сообщение или callback).
        """
        # Получаем user_id из события
        user_id = None
        is_admin = False
        
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
        
        # Если не удалось получить user_id, пропускаем проверку
        if user_id is None:
            return await handler(event, data)
        
        # Проверяем, является ли пользователь админом
        is_admin = user_id in self.admin_ids
        
        # Проверяем rate limit
        allowed, error_message = await self.rate_limiter.check_rate_limit(
            user_id=user_id,
            is_admin=is_admin
        )
        
        if not allowed:
            # Отправляем сообщение о превышении лимита
            if isinstance(event, Message):
                await event.answer(error_message)
            elif isinstance(event, CallbackQuery):
                await event.answer(error_message, show_alert=True)
            
            # Не вызываем handler
            return
        
        # Разрешаем обработку события
        return await handler(event, data)


# Глобальный экземпляр rate limiter (инициализируется в main.py)
_global_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> Optional[RateLimiter]:
    """Получить глобальный экземпляр rate limiter."""
    return _global_rate_limiter


def init_rate_limiter(config: Optional[RateLimitConfig] = None) -> RateLimiter:
    """
    Инициализировать глобальный rate limiter.
    
    Args:
        config: Конфигурация для rate limiting
        
    Returns:
        Инициализированный RateLimiter
    """
    global _global_rate_limiter
    _global_rate_limiter = RateLimiter(config)
    return _global_rate_limiter


async def rate_limiter_cleanup_task(interval: int = 300):
    """
    Фоновая задача для очистки устаревших данных rate limiter.
    
    Args:
        interval: Интервал очистки в секундах (по умолчанию: 5 минут)
    """
    rate_limiter = get_rate_limiter()
    if not rate_limiter:
        logger.warning("Rate limiter not initialized, cleanup task skipped")
        return
    
    logger.info(f"🧹 Rate limiter cleanup task started (interval: {interval}s)")
    
    while True:
        try:
            await asyncio.sleep(interval)
            await rate_limiter.cleanup()
        except asyncio.CancelledError:
            logger.info("Rate limiter cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in rate limiter cleanup task: {e}")

