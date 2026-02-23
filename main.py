import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# Load .env file
load_dotenv(Path(__file__).parent / ".env")

from config import BOT_TOKEN, ADMINS, HELPER_ADMINS
from handlers import start, astatka, ai_generate
from handlers import general_questions, admin, model_images, model_prices, admin_section_status
from services.admin_storage import load_admins
from services.google_sheet import load_all_sheets_to_cache
from services.cart_service import _cleanup_expired_items

# Import new services
from services.logging_config import setup_logging, get_logger
from services.rate_limiter import (
    init_rate_limiter, 
    RateLimitConfig, 
    RateLimitMiddleware,
    rate_limiter_cleanup_task
)

# Setup logging BEFORE importing other modules
setup_logging(
    console_level=logging.WARNING,  # Faqat WARNING va ERROR ni ko'rsatish
    file_level=logging.DEBUG,       # Faylda barcha loglar saqlanadi
    enable_colored_console=True
)
logger = get_logger(__name__)

# Terminalda ko'rsatish uchun print() - Windows console uchun emoji siz
print("\n" + "="*60)
print("[START] Bot ishga tushmoqda...")
print("="*60)

# Faylda saqlanadi (emoji bilan)
logger.info("рџљЂ Bot starting...")


async def cleanup_task():
    """Har 1 soatda muddati o'tgan karzinka itemlarini tozalash."""
    while True:
        try:
            await asyncio.sleep(3600)  # 1 soat kutish (3600 sekund)
            _cleanup_expired_items()
            logger.info("рџ§№ Expired cart items cleaned up")
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")


async def main():
    """Main function to start the bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set! Please set it in environment variables or config.py")
        return

    # Initialize bot and dispatcher
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher(storage=MemoryStorage())
    
    # Initialize rate limiter
    rate_limit_config = RateLimitConfig(
        max_requests=20,        # Max 20 requests
        window_seconds=60,      # Per 60 seconds
        cooldown_seconds=300,   # 5 minutes cooldown
        exempt_admins=True
    )
    rate_limiter = init_rate_limiter(rate_limit_config)
    print("[SECURITY] Rate limiter initialized")
    logger.info("рџ›ЎпёЏ Rate limiter initialized")
    
    # Get all admin IDs (from config and admin_storage)
    admin_ids = set(ADMINS + HELPER_ADMINS)
    
    # Add rate limiting middleware
    rate_limit_middleware = RateLimitMiddleware(
        rate_limiter=rate_limiter,
        admin_ids=admin_ids
    )
    dp.message.middleware(rate_limit_middleware)
    dp.callback_query.middleware(rate_limit_middleware)
    logger.info("рџ›ЎпёЏ Rate limiting middleware registered")

    # Register routers
    dp.include_router(start.router)
    dp.include_router(astatka.router)
    dp.include_router(ai_generate.router)
    dp.include_router(general_questions.router)
    dp.include_router(admin.router)
    dp.include_router(admin_section_status.router)
    dp.include_router(model_images.router)
    dp.include_router(model_prices.router)

    # Load admins from admins.json
    load_admins()
    print("[OK] Adminlar yuklandi")
    logger.info("Admins loaded from admins.json")
    
    # Load all sheets into cache on startup
    print("[LOADING] Cache yuklanmoqda (bu bir necha daqiqa olishi mumkin)...")
    try:
        await load_all_sheets_to_cache()
        print("[OK] Cache muvaffaqiyatli yuklandi")
        logger.info("All sheets loaded into cache")
    except Exception as e:
        print(f"[ERROR] Cache yuklashda xatolik: {e}")
        logger.error(f"Error loading sheets into cache on startup: {e}")
        # Continue anyway - cache will be empty but bot can still work
    
    # Background cleanup task ni boshlash
    cleanup_task_handle = asyncio.create_task(cleanup_task())
    print("[TASK] Cart cleanup task started")
    logger.info("рџ§№ Cart cleanup task started (runs every 1 hour)")
    
    # Background rate limiter cleanup task ni boshlash
    rate_limiter_task_handle = asyncio.create_task(rate_limiter_cleanup_task(interval=300))
    print("[TASK] Rate limiter cleanup task started")
    logger.info("рџ›ЎпёЏ Rate limiter cleanup task started (runs every 5 minutes)")
    
    # Start polling
    print("="*60)
    print("[SUCCESS] Bot muvaffaqiyatli ishga tushdi!")
    print("[INFO] Telegram botga xabar yuboring...")
    print("="*60 + "\n")
    logger.info("Bot is starting...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
    finally:
        cleanup_task_handle.cancel()
        rate_limiter_task_handle.cancel()
        await bot.session.close()


if __name__=="__main__":
    asyncio.run(main())

