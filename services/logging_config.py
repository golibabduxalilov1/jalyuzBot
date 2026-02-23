"""
Улучшенная система логирования для бота.

Особенности:
- Ротация логов по размеру и времени
- Разные уровни логирования для консоли и файла
- Форматирование с дополнительной информацией
- Отдельные логи для разных компонентов (errors, bot, services)
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """Цветной форматтер для консольного вывода."""
    
    # ANSI escape коды для цветов
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }
    
    def format(self, record):
        # Добавляем цвет к уровню логирования
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
        return super().format(record)


def setup_logging(
    log_dir: Optional[Path] = None,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    enable_colored_console: bool = True
) -> None:
    """
    Настройка системы логирования для бота.
    
    Args:
        log_dir: Директория для хранения логов (по умолчанию: logs/)
        console_level: Уровень логирования для консоли (по умолчанию: INFO)
        file_level: Уровень логирования для файлов (по умолчанию: DEBUG)
        max_file_size: Максимальный размер файла логов в байтах (по умолчанию: 10 MB)
        backup_count: Количество резервных копий логов (по умолчанию: 5)
        enable_colored_console: Включить цветной вывод в консоль (по умолчанию: True)
    """
    # Создаем директорию для логов
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # Формат логов
    detailed_format = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    simple_format = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Цветной форматтер для консоли
    console_formatter = ColoredFormatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    ) if enable_colored_console else simple_format
    
    # Получаем root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Минимальный уровень для всех обработчиков
    
    # Удаляем существующие обработчики
    root_logger.handlers.clear()
    
    # 1. Консольный обработчик (INFO и выше)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # 2. Основной файл логов с ротацией по размеру (все логи DEBUG и выше)
    main_log_file = log_dir / "bot.log"
    main_file_handler = RotatingFileHandler(
        filename=main_log_file,
        maxBytes=max_file_size,
        backupCount=backup_count,
        encoding='utf-8'
    )
    main_file_handler.setLevel(file_level)
    main_file_handler.setFormatter(detailed_format)
    root_logger.addHandler(main_file_handler)
    
    # 3. Файл только для ошибок (ERROR и CRITICAL)
    error_log_file = log_dir / "errors.log"
    error_file_handler = RotatingFileHandler(
        filename=error_log_file,
        maxBytes=max_file_size,
        backupCount=backup_count,
        encoding='utf-8'
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(detailed_format)
    root_logger.addHandler(error_file_handler)
    
    # 4. Отдельный файл для важных событий (с ротацией по дням)
    daily_log_file = log_dir / "daily.log"
    daily_handler = TimedRotatingFileHandler(
        filename=daily_log_file,
        when='midnight',
        interval=1,
        backupCount=30,  # Храним логи за 30 дней
        encoding='utf-8'
    )
    daily_handler.setLevel(logging.INFO)
    daily_handler.setFormatter(detailed_format)
    root_logger.addHandler(daily_handler)
    
    # Настройка уровней логирования для сторонних библиотек
    # (чтобы они не захламляли логи)
    logging.getLogger('aiogram').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('gspread').setLevel(logging.WARNING)
    logging.getLogger('google').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    
    # Логируем успешную инициализацию
    root_logger.info("=" * 80)
    root_logger.info("🚀 Logging system initialized")
    root_logger.info(f"📁 Log directory: {log_dir.absolute()}")
    root_logger.info(f"📊 Console level: {logging.getLevelName(console_level)}")
    root_logger.info(f"📊 File level: {logging.getLevelName(file_level)}")
    root_logger.info(f"📦 Max file size: {max_file_size / (1024 * 1024):.1f} MB")
    root_logger.info(f"💾 Backup count: {backup_count}")
    root_logger.info("=" * 80)


def get_logger(name: str) -> logging.Logger:
    """
    Получить logger с указанным именем.
    
    Args:
        name: Имя logger'а (обычно __name__)
        
    Returns:
        Настроенный logger
    """
    return logging.getLogger(name)


# Дополнительные утилиты для логирования

class LogContext:
    """Контекстный менеджер для логирования начала и окончания операции."""
    
    def __init__(self, logger: logging.Logger, operation: str, level: int = logging.INFO):
        self.logger = logger
        self.operation = operation
        self.level = level
    
    def __enter__(self):
        self.logger.log(self.level, f"▶️  Starting: {self.operation}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.logger.log(self.level, f"✅ Completed: {self.operation}")
        else:
            self.logger.error(f"❌ Failed: {self.operation} - {exc_type.__name__}: {exc_val}")
        return False  # Don't suppress exceptions


def log_function_call(logger: logging.Logger):
    """
    Декоратор для логирования вызовов функций.
    
    Пример использования:
        @log_function_call(logger)
        async def my_function(arg1, arg2):
            ...
    """
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            logger.debug(f"📞 Calling {func.__name__}(args={args}, kwargs={kwargs})")
            try:
                result = await func(*args, **kwargs)
                logger.debug(f"✅ {func.__name__} completed successfully")
                return result
            except Exception as e:
                logger.error(f"❌ {func.__name__} failed: {e}")
                raise
        
        def sync_wrapper(*args, **kwargs):
            logger.debug(f"📞 Calling {func.__name__}(args={args}, kwargs={kwargs})")
            try:
                result = func(*args, **kwargs)
                logger.debug(f"✅ {func.__name__} completed successfully")
                return result
            except Exception as e:
                logger.error(f"❌ {func.__name__} failed: {e}")
                raise
        
        # Определяем, асинхронная ли функция
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

