"""
LRU (Least Recently Used) Cache для ограничения размера кеша.

Используется для image_map в google_sheet.py, чтобы предотвратить
неограниченный рост памяти.
"""

import logging
from collections import OrderedDict
from typing import Any, Optional, Dict


logger = logging.getLogger(__name__)


class LRUCache:
    """
    LRU (Least Recently Used) кеш с ограниченным размером.
    
    Особенности:
    - Автоматически удаляет наименее используемые элементы при превышении лимита
    - Использует OrderedDict для эффективного отслеживания порядка использования
    - Thread-safe (если используется с asyncio.Lock)
    """
    
    def __init__(self, max_size: int = 10000):
        """
        Инициализация LRU cache.
        
        Args:
            max_size: Максимальное количество элементов в кеше
        """
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        
        self.max_size = max_size
        self._cache: OrderedDict = OrderedDict()
        self._hits = 0
        self._misses = 0
        
        logger.info(f"💾 LRU Cache initialized with max_size={max_size}")
    
    def get(self, key: Any, default: Any = None) -> Any:
        """
        Получить значение по ключу.
        
        Args:
            key: Ключ для поиска
            default: Значение по умолчанию, если ключ не найден
            
        Returns:
            Значение из кеша или default
        """
        if key in self._cache:
            # Перемещаем элемент в конец (отмечаем как недавно использованный)
            self._cache.move_to_end(key)
            self._hits += 1
            return self._cache[key]
        else:
            self._misses += 1
            return default
    
    def set(self, key: Any, value: Any) -> None:
        """
        Установить значение по ключу.
        
        Если ключ уже существует, обновляет значение и перемещает в конец.
        Если кеш заполнен, удаляет самый старый элемент.
        
        Args:
            key: Ключ для сохранения
            value: Значение для сохранения
        """
        if key in self._cache:
            # Обновляем существующий элемент
            self._cache.move_to_end(key)
            self._cache[key] = value
        else:
            # Добавляем новый элемент
            if len(self._cache) >= self.max_size:
                # Удаляем самый старый элемент (первый в OrderedDict)
                oldest_key = next(iter(self._cache))
                removed_value = self._cache.pop(oldest_key)
                logger.debug(
                    f"🗑️ LRU eviction: removed key={oldest_key} "
                    f"(cache size: {len(self._cache)}/{self.max_size})"
                )
            
            self._cache[key] = value
    
    def __contains__(self, key: Any) -> bool:
        """Проверить, существует ли ключ в кеше."""
        return key in self._cache
    
    def __len__(self) -> int:
        """Получить текущий размер кеша."""
        return len(self._cache)
    
    def __getitem__(self, key: Any) -> Any:
        """Получить значение по ключу (как dict)."""
        return self.get(key)
    
    def __setitem__(self, key: Any, value: Any) -> None:
        """Установить значение по ключу (как dict)."""
        self.set(key, value)
    
    def clear(self) -> None:
        """Очистить весь кеш."""
        size_before = len(self._cache)
        self._cache.clear()
        logger.info(f"🧹 LRU Cache cleared ({size_before} items removed)")
    
    def pop(self, key: Any, default: Any = None) -> Any:
        """
        Удалить и вернуть значение по ключу.
        
        Args:
            key: Ключ для удаления
            default: Значение по умолчанию, если ключ не найден
            
        Returns:
            Значение из кеша или default
        """
        return self._cache.pop(key, default)
    
    def items(self):
        """Получить итератор по элементам кеша (key, value)."""
        return self._cache.items()
    
    def keys(self):
        """Получить итератор по ключам кеша."""
        return self._cache.keys()
    
    def values(self):
        """Получить итератор по значениям кеша."""
        return self._cache.values()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Получить статистику кеша.
        
        Returns:
            Словарь со статистикой:
                - size: Текущий размер кеша
                - max_size: Максимальный размер кеша
                - hits: Количество попаданий в кеш
                - misses: Количество промахов
                - hit_rate: Процент попаданий
        """
        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests) if total_requests > 0 else 0.0
        
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "utilization": len(self._cache) / self.max_size
        }
    
    def resize(self, new_max_size: int) -> None:
        """
        Изменить максимальный размер кеша.
        
        Если новый размер меньше текущего количества элементов,
        удаляет самые старые элементы.
        
        Args:
            new_max_size: Новый максимальный размер
        """
        if new_max_size <= 0:
            raise ValueError("new_max_size must be positive")
        
        old_max_size = self.max_size
        self.max_size = new_max_size
        
        # Удаляем лишние элементы, если новый размер меньше текущего
        while len(self._cache) > self.max_size:
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key)
        
        logger.info(
            f"📏 LRU Cache resized: {old_max_size} -> {new_max_size} "
            f"(current size: {len(self._cache)})"
        )


class LRUCacheDict(dict):
    """
    Обертка над dict с LRU-логикой для обратной совместимости.
    
    Позволяет заменить обычный dict на LRU-кеш без изменения кода.
    """
    
    def __init__(self, max_size: int = 10000, *args, **kwargs):
        """
        Инициализация LRU cache dict.
        
        Args:
            max_size: Максимальное количество элементов
            *args, **kwargs: Аргументы для инициализации dict
        """
        super().__init__(*args, **kwargs)
        self._lru_cache = LRUCache(max_size=max_size)
        
        # Копируем начальные элементы в LRU cache
        for key, value in self.items():
            self._lru_cache.set(key, value)
    
    def __getitem__(self, key):
        return self._lru_cache.get(key)
    
    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._lru_cache.set(key, value)
    
    def __contains__(self, key):
        return key in self._lru_cache
    
    def __len__(self):
        return len(self._lru_cache)
    
    def get(self, key, default=None):
        return self._lru_cache.get(key, default)
    
    def pop(self, key, default=None):
        value = self._lru_cache.pop(key, default)
        super().pop(key, default)
        return value
    
    def clear(self):
        self._lru_cache.clear()
        super().clear()
    
    def items(self):
        return self._lru_cache.items()
    
    def keys(self):
        return self._lru_cache.keys()
    
    def values(self):
        return self._lru_cache.values()
    
    def get_stats(self):
        """Получить статистику LRU cache."""
        return self._lru_cache.get_stats()
    
    def copy(self):
        """Создать копию кеша."""
        return dict(self.items())

