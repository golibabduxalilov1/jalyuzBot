"""
Тестовый скрипт для проверки новых функций:
- Logging system
- Rate limiter
- LRU cache
- Input validation
"""

import asyncio
import logging
import sys
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Import new services
from services.logging_config import setup_logging, get_logger, LogContext
from services.rate_limiter import RateLimiter, RateLimitConfig
from services.lru_cache import LRUCache
from services.validation import (
    validate_product_code,
    validate_user_id,
    validate_text_input,
    validate_url
)


def test_logging():
    """Test logging system"""
    print("\n" + "="*80)
    print("🧪 Testing Logging System")
    print("="*80)
    
    # Setup logging
    setup_logging(
        console_level=logging.INFO,
        file_level=logging.DEBUG,
        enable_colored_console=True
    )
    
    logger = get_logger(__name__)
    
    # Test different log levels
    logger.debug("This is a DEBUG message")
    logger.info("✅ This is an INFO message")
    logger.warning("⚠️ This is a WARNING message")
    logger.error("❌ This is an ERROR message")
    
    # Test log context
    with LogContext(logger, "Test operation"):
        logger.info("Doing some work...")
    
    print("✅ Logging test completed. Check logs/ directory for log files.")


async def test_rate_limiter():
    """Test rate limiter"""
    print("\n" + "="*80)
    print("🧪 Testing Rate Limiter")
    print("="*80)
    
    # Create rate limiter with strict limits for testing
    config = RateLimitConfig(
        max_requests=5,        # Only 5 requests
        window_seconds=10,     # Per 10 seconds
        cooldown_seconds=5,    # 5 second cooldown
        exempt_admins=True
    )
    
    rate_limiter = RateLimiter(config)
    
    # Test normal usage
    print("\n📝 Testing normal usage (5 requests):")
    for i in range(5):
        allowed, error = await rate_limiter.check_rate_limit(user_id=123, is_admin=False)
        print(f"  Request {i+1}: {'✅ Allowed' if allowed else f'❌ Blocked - {error}'}")
    
    # Test rate limit exceeded
    print("\n📝 Testing rate limit exceeded (6th request):")
    allowed, error = await rate_limiter.check_rate_limit(user_id=123, is_admin=False)
    print(f"  Request 6: {'✅ Allowed' if allowed else f'❌ Blocked - {error}'}")
    
    # Test admin exemption
    print("\n📝 Testing admin exemption:")
    for i in range(3):
        allowed, error = await rate_limiter.check_rate_limit(user_id=456, is_admin=True)
        print(f"  Admin request {i+1}: {'✅ Allowed' if allowed else f'❌ Blocked - {error}'}")
    
    # Get statistics
    stats = rate_limiter.get_stats()
    print("\n📊 Rate Limiter Statistics:")
    print(f"  Total requests: {stats['total_requests']}")
    print(f"  Blocked requests: {stats['blocked_requests']}")
    print(f"  Active users: {stats['active_users']}")
    print(f"  Blocked users: {stats['blocked_users']}")
    print(f"  Block rate: {stats['block_rate']:.2%}")
    
    print("\n✅ Rate limiter test completed.")


def test_lru_cache():
    """Test LRU cache"""
    print("\n" + "="*80)
    print("🧪 Testing LRU Cache")
    print("="*80)
    
    # Create small cache for testing
    cache = LRUCache(max_size=5)
    
    # Add items
    print("\n📝 Adding 5 items to cache:")
    for i in range(5):
        cache.set(f"key{i}", f"value{i}")
        print(f"  Added: key{i} = value{i}")
    
    print(f"\n📊 Cache size: {len(cache)} / {cache.max_size}")
    
    # Test cache hit
    print("\n📝 Testing cache hit:")
    value = cache.get("key2")
    print(f"  Get key2: {value} ({'✅ Hit' if value else '❌ Miss'})")
    
    # Test cache miss
    print("\n📝 Testing cache miss:")
    value = cache.get("key999", default="not found")
    print(f"  Get key999: {value} ({'❌ Miss' if value == 'not found' else '✅ Hit'})")
    
    # Test LRU eviction
    print("\n📝 Testing LRU eviction (adding 6th item):")
    cache.set("key5", "value5")
    print(f"  Added: key5 = value5")
    print(f"  Cache size: {len(cache)} / {cache.max_size}")
    print(f"  key0 still in cache? {'✅ Yes' if 'key0' in cache else '❌ No (evicted)'}")
    
    # Get statistics
    stats = cache.get_stats()
    print("\n📊 LRU Cache Statistics:")
    print(f"  Size: {stats['size']} / {stats['max_size']}")
    print(f"  Hits: {stats['hits']}")
    print(f"  Misses: {stats['misses']}")
    print(f"  Hit rate: {stats['hit_rate']:.2%}")
    print(f"  Utilization: {stats['utilization']:.2%}")
    
    print("\n✅ LRU cache test completed.")


def test_validation():
    """Test input validation"""
    print("\n" + "="*80)
    print("🧪 Testing Input Validation")
    print("="*80)
    
    # Test product code validation
    print("\n📝 Testing product code validation:")
    test_codes = [
        ("MRC-1234", True),
        ("ABC123", True),
        ("", False),
        ("A" * 51, False),  # Too long
        ("MRC@123", False),  # Invalid character
    ]
    
    for code, expected_valid in test_codes:
        is_valid, error = validate_product_code(code)
        status = "✅" if is_valid == expected_valid else "❌"
        print(f"  {status} '{code[:20]}': {is_valid} {f'({error})' if error else ''}")
    
    # Test user ID validation
    print("\n📝 Testing user ID validation:")
    test_ids = [
        (123, True),
        ("456", True),
        (-1, False),
        (0, False),
        ("abc", False),
    ]
    
    for user_id, expected_valid in test_ids:
        is_valid, error = validate_user_id(user_id)
        status = "✅" if is_valid == expected_valid else "❌"
        print(f"  {status} {user_id}: {is_valid} {f'({error})' if error else ''}")
    
    # Test text input validation
    print("\n📝 Testing text input validation:")
    test_texts = [
        ("Hello world", True),
        ("", False),
        ("A" * 256, False),  # Too long (default max 255)
    ]
    
    for text, expected_valid in test_texts:
        is_valid, error = validate_text_input(text)
        status = "✅" if is_valid == expected_valid else "❌"
        print(f"  {status} '{text[:20]}...': {is_valid} {f'({error})' if error else ''}")
    
    # Test URL validation
    print("\n📝 Testing URL validation:")
    test_urls = [
        ("https://example.com", True),
        ("http://example.com/path", True),
        ("ftp://example.com", True),
        ("", False),
        ("not-a-url", False),
    ]
    
    for url, expected_valid in test_urls:
        is_valid, error = validate_url(url)
        status = "✅" if is_valid == expected_valid else "❌"
        print(f"  {status} '{url}': {is_valid} {f'({error})' if error else ''}")
    
    print("\n✅ Input validation test completed.")


async def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("🚀 Starting New Features Test Suite")
    print("="*80)
    
    # Test 1: Logging
    test_logging()
    
    # Test 2: Rate Limiter
    await test_rate_limiter()
    
    # Test 3: LRU Cache
    test_lru_cache()
    
    # Test 4: Validation
    test_validation()
    
    print("\n" + "="*80)
    print("✅ All tests completed!")
    print("="*80)
    print("\n📁 Check the following:")
    print("  - logs/ directory for log files")
    print("  - logs/bot.log for all logs")
    print("  - logs/errors.log for error logs")
    print("  - logs/daily.log for daily logs")


if __name__ == "__main__":
    asyncio.run(main())

