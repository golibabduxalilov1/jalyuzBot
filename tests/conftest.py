"""
Pytest configuration and fixtures.

Shared fixtures for all tests.
"""

import pytest
import asyncio
from typing import Dict, List
from unittest.mock import Mock, AsyncMock, MagicMock


# ==================== PYTEST CONFIGURATION ====================

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ==================== SAMPLE DATA FIXTURES ====================

@pytest.fixture
def sample_product():
    """Sample product data."""
    return {
        "code": "MRC-1234",
        "code_normalized": "mrc1234",
        "quantity": "10.5",
        "collection": "0-start",
        "date": "2026-01-01"
    }


@pytest.fixture
def sample_products():
    """Sample list of products."""
    return [
        {
            "code": "MRC-1234",
            "code_normalized": "mrc1234",
            "quantity": "10.5",
            "collection": "0-start",
            "date": "2026-01-01"
        },
        {
            "code": "SMF-02",
            "code_normalized": "smf02",
            "quantity": "5.0",
            "collection": "1-stage",
            "date": "2026-01-02"
        },
        {
            "code": "ABC-123",
            "code_normalized": "abc123",
            "quantity": "0",
            "collection": "0-start",
            "date": "2026-01-03"
        }
    ]


@pytest.fixture
def sample_model():
    """Sample model/image data."""
    return {
        "code": "MRC-1234",
        "_code_normalized": "mrc1234",
        "image_url": "https://example.com/image.jpg",
        "rang": "qizil",
        "color": "red",
        "turi": "mini",
        "type": "mini",
        "naqsh": "gul",
        "pattern": "flower",
        "kolleksiya": "0-start",
        "collection": "0-start"
    }


@pytest.fixture
def sample_models():
    """Sample list of models."""
    return [
        {
            "code": "MRC-1234",
            "_code_normalized": "mrc1234",
            "image_url": "https://example.com/image1.jpg",
            "rang": "qizil",
            "turi": "mini",
            "kolleksiya": "0-start"
        },
        {
            "code": "SMF-02",
            "_code_normalized": "smf02",
            "image_url": "https://example.com/image2.jpg",
            "rang": "ko'k",
            "turi": "asosiy",
            "kolleksiya": "1-stage"
        }
    ]


@pytest.fixture
def sample_price():
    """Sample price data."""
    return {
        "code": "MRC-1234",
        "code_normalized": "mrc1234",
        "collection": "0-start",
        "model_name": "Model 1234",
        "asosiy_price": "100000",
        "mini_price": "80000",
        "kasetniy_price": "120000",
        "izoh": "Test comment",
        "asosiy_qimmat": "150000",
        "mini_qimmat": "130000",
        "kasetniy_qimmat": "170000"
    }


@pytest.fixture
def sample_prices():
    """Sample list of prices."""
    return [
        {
            "code": "MRC-1234",
            "code_normalized": "mrc1234",
            "asosiy_price": "100000",
            "mini_price": "80000"
        },
        {
            "code": "SMF-02",
            "code_normalized": "smf02",
            "asosiy_price": "120000",
            "mini_price": "95000"
        }
    ]


# ==================== CACHE FIXTURES ====================

@pytest.fixture
def mock_cache(sample_products, sample_models, sample_prices):
    """Mock CACHE with sample data."""
    return {
        "sheets1": sample_products,
        "sheets2": {
            "mrc1234": "https://example.com/image1.jpg",
            "smf02": "https://example.com/image2.jpg"
        },
        "sheets2_full": sample_models,
        "sheets3": sample_prices,
        "sheets4": [],
        "sheets5": [],
        "sheets6": [],
        "image_map": {},
        "collection_index": {
            "0-start": [sample_products[0]],
            "1-stage": [sample_products[1]]
        }
    }


@pytest.fixture
def empty_cache():
    """Empty CACHE."""
    return {
        "sheets1": [],
        "sheets2": {},
        "sheets2_full": [],
        "sheets3": [],
        "sheets4": [],
        "sheets5": [],
        "sheets6": [],
        "image_map": {},
        "collection_index": {}
    }


# ==================== MOCK FIXTURES ====================

@pytest.fixture
def mock_message():
    """Mock Telegram Message object."""
    message = Mock()
    message.text = "Test message"
    message.from_user = Mock()
    message.from_user.id = 12345
    message.from_user.username = "testuser"
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock()
    message.reply = AsyncMock()
    return message


@pytest.fixture
def mock_callback_query():
    """Mock Telegram CallbackQuery object."""
    callback = Mock()
    callback.data = "test_callback"
    callback.from_user = Mock()
    callback.from_user.id = 12345
    callback.message = Mock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


@pytest.fixture
def mock_bot():
    """Mock Bot object."""
    bot = Mock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.send_document = AsyncMock()
    return bot


@pytest.fixture
def mock_state():
    """Mock FSMContext object."""
    state = Mock()
    state.get_data = AsyncMock(return_value={})
    state.set_data = AsyncMock()
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    return state


# ==================== REPOSITORY FIXTURES ====================

@pytest.fixture
def mock_product_repository(sample_products):
    """Mock ProductRepository."""
    from repositories.product_repository import ProductRepository
    
    repo = ProductRepository()
    repo.find_all = AsyncMock(return_value=sample_products)
    repo.find_by_id = AsyncMock(return_value=sample_products[0])
    repo.find_by_criteria = AsyncMock(return_value=sample_products[:2])
    
    return repo


@pytest.fixture
def mock_image_repository(sample_models):
    """Mock ImageRepository."""
    from repositories.image_repository import ImageRepository
    
    repo = ImageRepository()
    repo.find_all = AsyncMock(return_value=sample_models)
    repo.find_by_id = AsyncMock(return_value=sample_models[0])
    repo.find_by_criteria = AsyncMock(return_value=sample_models[:1])
    
    return repo


@pytest.fixture
def mock_price_repository(sample_prices):
    """Mock PriceRepository."""
    from repositories.price_repository import PriceRepository
    
    repo = PriceRepository()
    repo.find_all = AsyncMock(return_value=sample_prices)
    repo.find_by_id = AsyncMock(return_value=sample_prices[0])
    repo.find_by_criteria = AsyncMock(return_value=sample_prices[:1])
    
    return repo


# ==================== HELPER FUNCTIONS ====================

@pytest.fixture
def assert_called_once_with_text():
    """Helper to assert message.answer was called with specific text."""
    def _assert(mock_answer, expected_text):
        mock_answer.assert_called_once()
        actual_text = mock_answer.call_args[0][0] if mock_answer.call_args[0] else mock_answer.call_args.kwargs.get('text', '')
        assert expected_text in actual_text, f"Expected '{expected_text}' in '{actual_text}'"
    return _assert


# ==================== ASYNC HELPERS ====================

@pytest.fixture
def async_return():
    """Helper to create async return value."""
    def _async_return(value):
        async def _inner():
            return value
        return _inner()
    return _async_return

