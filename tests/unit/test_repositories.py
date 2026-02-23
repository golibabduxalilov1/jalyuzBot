"""
Unit tests for repositories.
"""

import pytest
from unittest.mock import patch, AsyncMock

from repositories.product_repository import ProductRepository
from repositories.image_repository import ImageRepository
from repositories.price_repository import PriceRepository


# ==================== PRODUCT REPOSITORY TESTS ====================

@pytest.mark.unit
@pytest.mark.asyncio
class TestProductRepository:
    """Tests for ProductRepository."""
    
    @patch('repositories.product_repository.CACHE')
    async def test_find_by_id(self, mock_cache, sample_products):
        """Test finding product by ID."""
        mock_cache.get.return_value = sample_products
        
        repo = ProductRepository()
        result = await repo.find_by_id("MRC-1234")
        
        assert result is not None
        assert result["code"] == "MRC-1234"
    
    @patch('repositories.product_repository.CACHE')
    async def test_find_by_id_not_found(self, mock_cache, sample_products):
        """Test finding product when not found."""
        mock_cache.get.return_value = sample_products
        
        repo = ProductRepository()
        result = await repo.find_by_id("XYZ-999")
        
        assert result is None
    
    @patch('repositories.product_repository.CACHE')
    async def test_find_all(self, mock_cache, sample_products):
        """Test finding all products."""
        mock_cache.get.return_value = sample_products
        
        repo = ProductRepository()
        result = await repo.find_all()
        
        assert len(result) == len(sample_products)
        assert result[0]["code"] == "MRC-1234"
    
    @patch('repositories.product_repository.CACHE')
    async def test_find_by_collection(self, mock_cache, sample_products):
        """Test finding products by collection."""
        mock_cache.get.return_value = sample_products
        
        repo = ProductRepository()
        result = await repo.find_by_collection("0-start")
        
        assert len(result) >= 1
        assert all(p["collection"] == "0-start" for p in result)
    
    @patch('repositories.product_repository.CACHE')
    async def test_find_available_products(self, mock_cache, sample_products):
        """Test finding available products."""
        mock_cache.get.return_value = sample_products
        
        repo = ProductRepository()
        result = await repo.find_available_products(min_quantity=1.0)
        
        assert len(result) >= 1
        assert all(float(p["quantity"]) >= 1.0 for p in result if p["quantity"])
    
    @patch('repositories.product_repository.CACHE')
    async def test_get_all_collections(self, mock_cache, sample_products):
        """Test getting all collections."""
        mock_cache.get.return_value = sample_products
        
        repo = ProductRepository()
        result = await repo.get_all_collections()
        
        assert "0-start" in result
        assert "1-stage" in result
    
    @patch('repositories.product_repository.CACHE')
    async def test_count_by_collection(self, mock_cache, sample_products):
        """Test counting products by collection."""
        mock_cache.get.return_value = sample_products
        
        repo = ProductRepository()
        result = await repo.count_by_collection("0-start")
        
        assert result >= 1


# ==================== IMAGE REPOSITORY TESTS ====================

@pytest.mark.unit
@pytest.mark.asyncio
class TestImageRepository:
    """Tests for ImageRepository."""
    
    @patch('repositories.image_repository.CACHE')
    async def test_find_by_id(self, mock_cache, sample_models):
        """Test finding model by ID."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.find_by_id("MRC-1234")
        
        assert result is not None
        assert result["code"] == "MRC-1234"
    
    @patch('repositories.image_repository.CACHE')
    async def test_find_all(self, mock_cache, sample_models):
        """Test finding all models."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.find_all()
        
        assert len(result) == len(sample_models)
    
    @patch('repositories.image_repository.CACHE')
    async def test_find_by_color(self, mock_cache, sample_models):
        """Test finding models by color."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.find_by_color("qizil")
        
        assert len(result) >= 1
        assert all("qizil" in p.get("rang", "").lower() for p in result)
    
    @patch('repositories.image_repository.CACHE')
    async def test_find_by_type(self, mock_cache, sample_models):
        """Test finding models by type."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.find_by_type("mini")
        
        assert len(result) >= 1
        assert all("mini" in p.get("turi", "").lower() for p in result)
    
    @patch('repositories.image_repository.CACHE')
    async def test_find_by_collection(self, mock_cache, sample_models):
        """Test finding models by collection."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.find_by_collection("0-start")
        
        assert len(result) >= 1
    
    @patch('repositories.image_repository.CACHE')
    async def test_get_image_url(self, mock_cache, sample_models):
        """Test getting image URL."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.get_image_url("MRC-1234")
        
        assert result is not None
        assert "http" in result
    
    @patch('repositories.image_repository.CACHE')
    async def test_get_all_colors(self, mock_cache, sample_models):
        """Test getting all colors."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.get_all_colors()
        
        assert "qizil" in result
    
    @patch('repositories.image_repository.CACHE')
    async def test_get_all_types(self, mock_cache, sample_models):
        """Test getting all types."""
        mock_cache.get.return_value = sample_models
        
        repo = ImageRepository()
        result = await repo.get_all_types()
        
        assert "mini" in result


# ==================== PRICE REPOSITORY TESTS ====================

@pytest.mark.unit
@pytest.mark.asyncio
class TestPriceRepository:
    """Tests for PriceRepository."""
    
    @patch('repositories.price_repository.CACHE')
    async def test_find_by_id(self, mock_cache, sample_prices):
        """Test finding price by ID."""
        mock_cache.get.return_value = sample_prices
        
        repo = PriceRepository()
        result = await repo.find_by_id("MRC-1234")
        
        assert result is not None
        assert result["code"] == "MRC-1234"
    
    @patch('repositories.price_repository.CACHE')
    async def test_find_all(self, mock_cache, sample_prices):
        """Test finding all prices."""
        mock_cache.get.return_value = sample_prices
        
        repo = PriceRepository()
        result = await repo.find_all()
        
        assert len(result) == len(sample_prices)
    
    @patch('repositories.price_repository.CACHE')
    async def test_get_price_info(self, mock_cache, sample_prices):
        """Test getting price info."""
        mock_cache.get.return_value = sample_prices
        
        repo = PriceRepository()
        result = await repo.get_price_info("MRC-1234")
        
        assert result is not None
        assert "asosiy" in result
        assert "mini" in result
    
    @patch('repositories.price_repository.CACHE')
    async def test_find_in_price_range(self, mock_cache, sample_prices):
        """Test finding models in price range."""
        mock_cache.get.return_value = sample_prices
        
        repo = PriceRepository()
        result = await repo.find_in_price_range(50000, 150000)
        
        assert len(result) >= 1
    
    @patch('repositories.price_repository.CACHE')
    async def test_get_average_price(self, mock_cache, sample_prices):
        """Test getting average price."""
        mock_cache.get.return_value = sample_prices
        
        repo = PriceRepository()
        result = await repo.get_average_price()
        
        assert result > 0
    
    @patch('repositories.price_repository.CACHE')
    async def test_get_price_statistics(self, mock_cache, sample_prices):
        """Test getting price statistics."""
        mock_cache.get.return_value = sample_prices
        
        repo = PriceRepository()
        result = await repo.get_price_statistics()
        
        assert "total_models" in result
        assert "average_price" in result
        assert "min_price" in result
        assert "max_price" in result
        assert result["total_models"] == len(sample_prices)


# ==================== BASE REPOSITORY TESTS ====================

@pytest.mark.unit
@pytest.mark.asyncio
class TestBaseRepository:
    """Tests for BaseRepository."""
    
    async def test_count(self, mock_product_repository, sample_products):
        """Test counting records."""
        mock_product_repository.find_all.return_value = sample_products
        
        result = await mock_product_repository.count()
        
        assert result == len(sample_products)
    
    async def test_exists_true(self, mock_product_repository, sample_product):
        """Test checking if record exists (true case)."""
        mock_product_repository.find_by_id.return_value = sample_product
        
        result = await mock_product_repository.exists("MRC-1234")
        
        assert result is True
    
    async def test_exists_false(self, mock_product_repository):
        """Test checking if record exists (false case)."""
        mock_product_repository.find_by_id.return_value = None
        
        result = await mock_product_repository.exists("XYZ-999")
        
        assert result is False

