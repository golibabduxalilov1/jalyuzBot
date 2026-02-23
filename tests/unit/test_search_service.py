"""
Unit tests for search_service.py
"""

import pytest
from unittest.mock import patch, Mock

from services.search_service import (
    is_code_query,
    is_question_about_database,
    extract_numbers_only,
    normalize_text_for_search,
    find_stock_for_code,
    find_models_by_code,
    extract_search_terms,
    search_in_database,
    find_similar_models,
    format_code_search_results,
    format_database_answer
)


# ==================== QUERY DETECTION TESTS ====================

@pytest.mark.unit
class TestQueryDetection:
    """Tests for query detection functions."""
    
    def test_is_code_query_with_letters_and_digits(self):
        """Test code detection with letters and digits."""
        assert is_code_query("MRC-1234") == True
        assert is_code_query("SMF02") == True
        assert is_code_query("ABC123") == True
    
    def test_is_code_query_with_only_digits(self):
        """Test code detection with only digits."""
        assert is_code_query("1234") == True
        assert is_code_query("5960") == True
        assert is_code_query("12") == True
    
    def test_is_code_query_with_only_letters(self):
        """Test code detection with only letters."""
        assert is_code_query("SMF") == True
        assert is_code_query("MRC") == True
        assert is_code_query("AB") == True
    
    def test_is_code_query_with_long_text(self):
        """Test code detection with long text."""
        assert is_code_query("Bu juda uzun matn") == False
        assert is_code_query("Qizil mini jalyuzi") == False
    
    def test_is_code_query_with_multiple_spaces(self):
        """Test code detection with multiple spaces."""
        assert is_code_query("A B C D") == False
    
    def test_is_code_query_with_empty_string(self):
        """Test code detection with empty string."""
        assert is_code_query("") == False
        assert is_code_query(None) == False
    
    def test_is_question_about_database(self):
        """Test database question detection."""
        assert is_question_about_database("qoldiq bormi?") == True
        assert is_question_about_database("narxi qancha?") == True
        assert is_question_about_database("mavjudmi?") == True
        assert is_question_about_database("MRC-1234") == True
    
    def test_is_not_question_about_database(self):
        """Test non-database question detection."""
        assert is_question_about_database("salom") == False
        assert is_question_about_database("qanday o'rnatish kerak?") == False


# ==================== TEXT NORMALIZATION TESTS ====================

@pytest.mark.unit
class TestTextNormalization:
    """Tests for text normalization functions."""
    
    def test_extract_numbers_only(self):
        """Test extracting numbers from text."""
        assert extract_numbers_only("MRC-1234") == "1234"
        assert extract_numbers_only("SMF02") == "02"
        assert extract_numbers_only("ABC") == ""
    
    def test_normalize_text_for_search(self):
        """Test text normalization for search."""
        assert normalize_text_for_search("  QIZIL   MINI  ") == "qizil mini"
        assert normalize_text_for_search("Ko'k Asosiy") == "ko'k asosiy"
        assert normalize_text_for_search("") == ""


# ==================== STOCK SEARCH TESTS ====================

@pytest.mark.unit
class TestStockSearch:
    """Tests for stock search function."""
    
    def test_find_stock_exact_match(self):
        """Test finding stock with exact match."""
        stock_map = {
            "mrc1234": {"quantity": "10.5"},
            "smf02": {"quantity": "5.0"}
        }
        
        result = find_stock_for_code("mrc1234", stock_map)
        assert result == "10.5"
    
    def test_find_stock_partial_match(self):
        """Test finding stock with partial match."""
        stock_map = {
            "mrc1234": {"quantity": "10.5"}
        }
        
        result = find_stock_for_code("mrc", stock_map)
        assert result == "10.5"
    
    def test_find_stock_not_found(self):
        """Test finding stock when not found."""
        stock_map = {
            "mrc1234": {"quantity": "10.5"}
        }
        
        result = find_stock_for_code("xyz999", stock_map)
        assert result is None
    
    def test_find_stock_empty_map(self):
        """Test finding stock with empty map."""
        result = find_stock_for_code("mrc1234", {})
        assert result is None


# ==================== CODE SEARCH TESTS ====================

@pytest.mark.unit
class TestCodeSearch:
    """Tests for code search functions."""
    
    @patch('services.search_service.CACHE')
    def test_find_models_by_code_exact_match(self, mock_cache, sample_models):
        """Test finding models by exact code match."""
        mock_cache.get.return_value = sample_models
        
        results = find_models_by_code("MRC-1234")
        
        assert len(results) >= 1
        assert any(r["code"] == "MRC-1234" for r in results)
    
    @patch('services.search_service.CACHE')
    def test_find_models_by_code_partial_match(self, mock_cache, sample_models):
        """Test finding models by partial code match."""
        mock_cache.get.return_value = sample_models
        
        results = find_models_by_code("MRC")
        
        assert len(results) >= 1
    
    @patch('services.search_service.CACHE')
    def test_find_models_by_code_empty_cache(self, mock_cache):
        """Test finding models with empty cache."""
        mock_cache.get.return_value = []
        
        results = find_models_by_code("MRC-1234")
        
        assert results == []
    
    @patch('services.search_service.CACHE')
    def test_find_models_by_code_by_color(self, mock_cache, sample_models):
        """Test finding models by color."""
        mock_cache.get.return_value = sample_models
        
        results = find_models_by_code("qizil")
        
        assert len(results) >= 1
        assert any(r.get("rang") == "qizil" for r in results)


# ==================== SEARCH TERMS EXTRACTION TESTS ====================

@pytest.mark.unit
class TestSearchTermsExtraction:
    """Tests for search terms extraction."""
    
    def test_extract_colors(self):
        """Test extracting colors from query."""
        result = extract_search_terms("qizil mini jalyuzi")
        
        assert "qizil" in result["colors"]
    
    def test_extract_types(self):
        """Test extracting types from query."""
        result = extract_search_terms("qizil mini jalyuzi")
        
        assert "mini" in result["types"]
    
    def test_extract_sizes(self):
        """Test extracting sizes from query."""
        result = extract_search_terms("1.5 metr")
        
        assert len(result["sizes"]) > 0


# ==================== DATABASE SEARCH TESTS ====================

@pytest.mark.unit
class TestDatabaseSearch:
    """Tests for comprehensive database search."""
    
    @patch('services.search_service.CACHE')
    def test_search_in_database(self, mock_cache, sample_models, sample_prices):
        """Test comprehensive database search."""
        mock_cache.get.side_effect = lambda x: {
            "sheets2_full": sample_models,
            "sheets3": sample_prices,
            "sheets4": [],
            "sheets5": [],
            "sheets6": []
        }.get(x, [])
        
        results = search_in_database("MRC")
        
        assert len(results) >= 1
        assert any(r["_source"] == "sheets2" for r in results)


# ==================== RESULT FORMATTING TESTS ====================

@pytest.mark.unit
class TestResultFormatting:
    """Tests for result formatting functions."""
    
    def test_format_code_search_results_with_results(self, sample_models):
        """Test formatting search results."""
        text, image_url, additional_images = format_code_search_results(sample_models)
        
        assert "MRC-1234" in text
        assert image_url is not None
    
    def test_format_code_search_results_empty(self):
        """Test formatting empty search results."""
        text, image_url, additional_images = format_code_search_results([])
        
        assert "topilmadi" in text.lower()
        assert image_url is None
    
    def test_format_database_answer_with_results(self, sample_models):
        """Test formatting database answer."""
        results = [{"_source": "sheets2", **sample_models[0]}]
        
        text, image_url = format_database_answer(results, "MRC", 12345)
        
        assert "MRC" in text
        assert "Modellar" in text
    
    def test_format_database_answer_empty(self):
        """Test formatting empty database answer."""
        text, image_url = format_database_answer([], "XYZ", 12345)
        
        assert "topilmadi" in text.lower()
        assert image_url is None


# ==================== SIMILAR MODELS TESTS ====================

@pytest.mark.unit
class TestSimilarModels:
    """Tests for finding similar models."""
    
    @patch('services.search_service.CACHE')
    @patch('services.search_service.find_models_by_code')
    def test_find_similar_models(self, mock_find, mock_cache, sample_models):
        """Test finding similar models."""
        mock_find.return_value = [sample_models[0]]
        mock_cache.get.return_value = sample_models
        
        results = find_similar_models("MRC-1234")
        
        assert isinstance(results, list)
    
    @patch('services.search_service.find_models_by_code')
    def test_find_similar_models_not_found(self, mock_find):
        """Test finding similar models when query not found."""
        mock_find.return_value = []
        
        results = find_similar_models("XYZ-999")
        
        assert results == []

