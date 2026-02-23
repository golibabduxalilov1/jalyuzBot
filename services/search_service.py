"""
Search Service - Mahsulot qidiruv business logic.

Bu service quyidagi vazifalarni bajaradi:
- Kod bo'yicha qidiruv
- Matn bo'yicha qidiruv
- Rasm bo'yicha qidiruv
- O'xshash modellarni topish
"""

import logging
import re
from typing import Optional, List, Dict, Tuple

from services.google_sheet import CACHE
from services.product_utils import normalize_code

logger = logging.getLogger(__name__)


# ==================== QUERY DETECTION ====================

def is_code_query(text: str) -> bool:
    """
    Detect if input is a MODEL CODE QUERY, not a general question.
    
    Rules:
    - Contains letters+digits (example: "SMF", "smf-02", "5960", "M1600")
    - Or short technical strings (length <= 12, no spaces or 1 space)
    
    Args:
        text: User input text
        
    Returns:
        True if it's a code query, False otherwise
    """
    if not text:
        return False
    
    text = text.strip()
    
    # Check length
    if len(text) > 12:
        return False
    
    # Check space count (max 1 space)
    space_count = text.count(" ")
    if space_count > 1:
        return False
    
    # Check if contains letters AND digits (or just digits, or just letters in short form)
    has_letters = bool(re.search(r'[a-zA-Zа-яА-Я]', text))
    has_digits = bool(re.search(r'\d', text))
    
    # If has both letters and digits, likely a code
    if has_letters and has_digits:
        return True
    
    # If only digits and short, likely a code
    if has_digits and not has_letters and len(text) <= 6:
        return True
    
    # If only letters and very short (<= 5), might be code
    if has_letters and not has_digits and len(text) <= 5:
        return True
    
    return False


def is_question_about_database(question: str) -> bool:
    """
    Detect if question is about database/stock, not general AI question.
    
    Args:
        question: User question text
        
    Returns:
        True if it's a database question, False otherwise
    """
    if not question:
        return False
    
    question_lower = question.lower()
    
    # Database-related keywords
    database_keywords = [
        "qoldiq", "astatka", "mavjud", "bor", "yo'q", "kod",
        "model", "mahsulot", "narx", "kolleksiya", "rang",
        "turi", "naqsh", "o'lchov", "razmer", "stock",
        "available", "price", "collection", "color", "type"
    ]
    
    # Check if any database keyword is present
    for keyword in database_keywords:
        if keyword in question_lower:
            return True
    
    # Check if it's a code-like query
    if is_code_query(question):
        return True
    
    return False


# ==================== TEXT NORMALIZATION ====================

def extract_numbers_only(code_str: str) -> str:
    """Extract only numbers from code string."""
    if not code_str:
        return ""
    return "".join(c for c in str(code_str) if c.isdigit())


def normalize_text_for_search(text: str) -> str:
    """
    Normalize text for search: lowercase, remove extra spaces.
    """
    if not text:
        return ""
    normalized = str(text).strip().lower()
    normalized = " ".join(normalized.split())
    return normalized


# ==================== STOCK SEARCH ====================

def find_stock_for_code(norm_code: str, stock_map: Dict[str, Dict]) -> Optional[str]:
    """
    Find stock quantity for a given normalized code.
    
    Tries multiple matching strategies:
    1. Exact match
    2. Startswith match
    3. Endswith match
    4. Contains match
    5. Number-only match
    
    Args:
        norm_code: Normalized product code
        stock_map: Dictionary mapping codes to stock info
        
    Returns:
        Stock quantity string or None if not found
    """
    if not norm_code or not stock_map:
        return None
    
    # Strategy 1: Exact match
    if norm_code in stock_map:
        return stock_map[norm_code].get("quantity", "")
    
    # Strategy 2: Startswith
    for key, value in stock_map.items():
        if key.startswith(norm_code) or norm_code.startswith(key):
            return value.get("quantity", "")
    
    # Strategy 3: Endswith
    for key, value in stock_map.items():
        if key.endswith(norm_code) or norm_code.endswith(key):
            return value.get("quantity", "")
    
    # Strategy 4: Contains
    for key, value in stock_map.items():
        if norm_code in key or key in norm_code:
            return value.get("quantity", "")
    
    # Strategy 5: Number-only match
    norm_numbers = extract_numbers_only(norm_code)
    if norm_numbers:
        for key, value in stock_map.items():
            key_numbers = extract_numbers_only(key)
            if key_numbers and norm_numbers == key_numbers:
                return value.get("quantity", "")
    
    return None


# ==================== CODE SEARCH ====================

def find_models_by_code(query: str) -> List[Dict]:
    """
    Find models by code in sheets2_full (images sheet).
    
    Searches in:
    - code
    - rang (color)
    - turi (type)
    - naqsh (pattern)
    - kolleksiya (collection)
    
    Args:
        query: Search query
        
    Returns:
        List of matching model dictionaries
    """
    if not query:
        return []
    
    query_norm = normalize_code(query)
    query_lower = query.lower()
    
    sheets2_full = CACHE.get("sheets2_full", [])
    if not sheets2_full:
        logger.warning("sheets2_full is empty")
        return []
    
    results = []
    
    for record in sheets2_full:
        code = record.get("code", "")
        code_norm = record.get("_code_normalized", "")
        
        if not code_norm:
            code_norm = normalize_code(code)
        
        # Match strategies
        matched = False
        
        # 1. Exact code match
        if code_norm == query_norm:
            matched = True
        
        # 2. Code contains query or query contains code
        elif query_norm in code_norm or code_norm in query_norm:
            matched = True
        
        # 3. Search in other fields (rang, turi, naqsh, kolleksiya)
        else:
            searchable_fields = ["rang", "turi", "naqsh", "kolleksiya", "color", "type", "pattern", "collection"]
            for field in searchable_fields:
                field_value = record.get(field, "")
                if field_value and query_lower in str(field_value).lower():
                    matched = True
                    break
        
        if matched:
            results.append(record)
    
    logger.info(f"Found {len(results)} models for query '{query}'")
    return results


def search_models_by_code(query: str) -> List[Dict]:
    """Alias for find_models_by_code - backward compatibility."""
    return find_models_by_code(query)


# ==================== ADVANCED SEARCH ====================

def extract_search_terms(query: str) -> Dict[str, any]:
    """
    Extract search terms from query.
    
    Recognizes:
    - Colors (rang)
    - Types (turi)
    - Patterns (naqsh)
    - Collections (kolleksiya)
    - Sizes (razmer)
    
    Args:
        query: Search query
        
    Returns:
        Dictionary with extracted terms
    """
    query_lower = query.lower()
    
    terms = {
        "colors": [],
        "types": [],
        "patterns": [],
        "collections": [],
        "sizes": [],
        "raw_query": query
    }
    
    # Common colors
    colors = ["oq", "qora", "ko'k", "qizil", "sariq", "yashil", "jigarrang", 
              "kulrang", "pushti", "to'q", "och", "white", "black", "blue", 
              "red", "yellow", "green", "brown", "gray", "pink"]
    
    # Common types
    types = ["mini", "asosiy", "kasetniy", "kaset", "standart", "katta", 
             "kichik", "main", "cassette", "standard", "large", "small"]
    
    # Common patterns
    patterns = ["gul", "chiziq", "nuqta", "geometrik", "klassik", "zamonaviy",
                "flower", "line", "dot", "geometric", "classic", "modern"]
    
    # Extract colors
    for color in colors:
        if color in query_lower:
            terms["colors"].append(color)
    
    # Extract types
    for type_name in types:
        if type_name in query_lower:
            terms["types"].append(type_name)
    
    # Extract patterns
    for pattern in patterns:
        if pattern in query_lower:
            terms["patterns"].append(pattern)
    
    # Extract sizes (numbers like 1.5, 2.0, etc.)
    size_pattern = r'\d+\.?\d*\s*(?:m|metr|meter)?'
    sizes = re.findall(size_pattern, query_lower)
    if sizes:
        terms["sizes"] = sizes
    
    return terms


def search_in_database(query: str) -> List[Dict]:
    """
    Comprehensive database search.
    
    Searches in:
    - sheets2_full (images/models)
    - sheets3 (prices)
    - sheets4 (discount prices)
    - sheets5 (ready sizes - magazin)
    - sheets6 (ready sizes - shtuk)
    
    Args:
        query: Search query
        
    Returns:
        List of matching records with metadata
    """
    if not query:
        return []
    
    query_norm = normalize_code(query)
    query_lower = query.lower()
    search_terms = extract_search_terms(query)
    
    all_results = []
    
    # Search in sheets2_full (models/images)
    sheets2_results = find_models_by_code(query)
    for result in sheets2_results:
        result["_source"] = "sheets2"
        result["_type"] = "model"
        all_results.append(result)
    
    # Search in sheets3 (prices)
    sheets3 = CACHE.get("sheets3", [])
    for record in sheets3:
        code_norm = record.get("code_normalized", "")
        if not code_norm:
            continue
        
        if (query_norm == code_norm or 
            query_norm in code_norm or 
            code_norm in query_norm):
            record["_source"] = "sheets3"
            record["_type"] = "price"
            all_results.append(record)
    
    # Search in sheets4 (discount prices)
    sheets4 = CACHE.get("sheets4", [])
    for record in sheets4:
        code_norm = record.get("code_normalized", "")
        if not code_norm:
            continue
        
        if (query_norm == code_norm or 
            query_norm in code_norm or 
            code_norm in query_norm):
            record["_source"] = "sheets4"
            record["_type"] = "discount"
            all_results.append(record)
    
    # Search in sheets5 (ready sizes - magazin)
    sheets5 = CACHE.get("sheets5", [])
    for record in sheets5:
        code_norm = record.get("code_normalized", "")
        if not code_norm:
            continue
        
        if (query_norm == code_norm or 
            query_norm in code_norm or 
            code_norm in query_norm):
            record["_source"] = "sheets5"
            record["_type"] = "ready_size_magazin"
            all_results.append(record)
    
    # Search in sheets6 (ready sizes - shtuk)
    sheets6 = CACHE.get("sheets6", [])
    for record in sheets6:
        code_norm = record.get("code_normalized", "")
        if not code_norm:
            continue
        
        if (query_norm == code_norm or 
            query_norm in code_norm or 
            code_norm in query_norm):
            record["_source"] = "sheets6"
            record["_type"] = "ready_size_shtuk"
            all_results.append(record)
    
    logger.info(f"Database search for '{query}': found {len(all_results)} results")
    return all_results


def find_similar_models(query: str, exclude_codes: set = None) -> List[Dict]:
    """
    Find similar models based on query.
    
    Uses:
    - Same collection
    - Same color
    - Same type
    - Similar code patterns
    
    Args:
        query: Search query
        exclude_codes: Set of codes to exclude from results
        
    Returns:
        List of similar model dictionaries
    """
    if not query:
        return []
    
    if exclude_codes is None:
        exclude_codes = set()
    
    # First, find the query model
    query_models = find_models_by_code(query)
    if not query_models:
        return []
    
    # Get characteristics of the first matching model
    reference_model = query_models[0]
    ref_collection = reference_model.get("kolleksiya", "") or reference_model.get("collection", "")
    ref_color = reference_model.get("rang", "") or reference_model.get("color", "")
    ref_type = reference_model.get("turi", "") or reference_model.get("type", "")
    
    sheets2_full = CACHE.get("sheets2_full", [])
    similar = []
    
    for record in sheets2_full:
        code = record.get("code", "")
        code_norm = record.get("_code_normalized", "")
        
        if not code_norm:
            code_norm = normalize_code(code)
        
        # Skip excluded codes
        if code_norm in exclude_codes:
            continue
        
        # Calculate similarity score
        score = 0
        
        # Same collection (highest priority)
        record_collection = record.get("kolleksiya", "") or record.get("collection", "")
        if ref_collection and record_collection and ref_collection.lower() == record_collection.lower():
            score += 10
        
        # Same color
        record_color = record.get("rang", "") or record.get("color", "")
        if ref_color and record_color and ref_color.lower() == record_color.lower():
            score += 5
        
        # Same type
        record_type = record.get("turi", "") or record.get("type", "")
        if ref_type and record_type and ref_type.lower() == record_type.lower():
            score += 3
        
        # Similar code pattern (same prefix)
        if len(code_norm) >= 3 and len(normalize_code(query)) >= 3:
            if code_norm[:3] == normalize_code(query)[:3]:
                score += 2
        
        if score > 0:
            record["_similarity_score"] = score
            similar.append(record)
    
    # Sort by similarity score (descending)
    similar.sort(key=lambda x: x.get("_similarity_score", 0), reverse=True)
    
    logger.info(f"Found {len(similar)} similar models for '{query}'")
    return similar[:10]  # Return top 10


# ==================== RESULT FORMATTING ====================

def format_code_search_results(results: List[Dict]) -> Tuple[str, Optional[str], List[str]]:
    """
    Format code search results for display.
    
    Args:
        results: List of search result dictionaries
        
    Returns:
        Tuple of (text_message, image_url, additional_images)
    """
    if not results:
        return "❌ Hech narsa topilmadi.", None, []
    
    text_parts = []
    image_url = None
    additional_images = []
    
    for idx, result in enumerate(results[:5], 1):  # Limit to 5 results
        code = result.get("code", "N/A")
        collection = result.get("kolleksiya", "") or result.get("collection", "N/A")
        color = result.get("rang", "") or result.get("color", "N/A")
        type_name = result.get("turi", "") or result.get("type", "N/A")
        
        text_parts.append(f"{idx}. Kod: {code}")
        text_parts.append(f"   Kolleksiya: {collection}")
        text_parts.append(f"   Rang: {color}")
        text_parts.append(f"   Turi: {type_name}")
        text_parts.append("")
        
        # Get image URL
        img_url = result.get("image_url", "") or result.get("imageurl", "")
        if img_url and not image_url:
            image_url = img_url
        elif img_url:
            additional_images.append(img_url)
    
    text = "\n".join(text_parts)
    return text, image_url, additional_images


def format_database_answer(results: List[Dict], query: str, user_id: int) -> Tuple[str, Optional[str]]:
    """
    Format database search results into a user-friendly answer.
    
    Args:
        results: List of search results
        query: Original search query
        user_id: User ID (for logging)
        
    Returns:
        Tuple of (answer_text, image_url)
    """
    if not results:
        return f"❌ '{query}' bo'yicha ma'lumot topilmadi.", None
    
    text_parts = [f"📊 '{query}' bo'yicha natijalar:\n"]
    image_url = None
    
    # Group results by source
    by_source = {}
    for result in results:
        source = result.get("_source", "unknown")
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(result)
    
    # Format each source
    if "sheets2" in by_source:
        text_parts.append("🖼️ Modellar:")
        for result in by_source["sheets2"][:3]:
            code = result.get("code", "N/A")
            collection = result.get("kolleksiya", "") or result.get("collection", "N/A")
            text_parts.append(f"  • {code} ({collection})")
            
            if not image_url:
                image_url = result.get("image_url", "") or result.get("imageurl", "")
        text_parts.append("")
    
    if "sheets3" in by_source:
        text_parts.append("💰 Narxlar:")
        for result in by_source["sheets3"][:3]:
            code = result.get("code", "N/A")
            asosiy = result.get("asosiy_price", "N/A")
            mini = result.get("mini_price", "N/A")
            text_parts.append(f"  • {code}: Asosiy={asosiy}, Mini={mini}")
        text_parts.append("")
    
    if "sheets5" in by_source or "sheets6" in by_source:
        text_parts.append("📦 Tayyor razmerlar:")
        for source in ["sheets5", "sheets6"]:
            if source in by_source:
                for result in by_source[source][:3]:
                    code = result.get("code", "N/A")
                    razmer = result.get("razmer", "N/A")
                    text_parts.append(f"  • {code}: {razmer}")
        text_parts.append("")
    
    logger.info(f"Formatted database answer for user {user_id}: {len(results)} results")
    return "\n".join(text_parts), image_url

