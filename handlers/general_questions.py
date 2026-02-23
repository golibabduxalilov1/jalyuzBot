import logging
import re
from typing import Optional, List, Dict, Tuple

from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    InputMediaPhoto,
)

from services.ai_service import get_openai_service
from services.google_sheet import CACHE, GoogleSheetService, get_file_id_for_code, get_image_url_for_code
from handlers.start import make_main_menu_keyboard

logger = logging.getLogger(__name__)

router = Router()


class QuestionStates(StatesGroup):
    waiting_for_question = State()


def question_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="questions_back"),
            ]
        ]
    )


# ==================== BAZA QIDIRUV FUNKSIYALARI ====================

def is_code_query(text: str) -> bool:
    """
    Detect if input is a MODEL CODE QUERY, not a general question.
    Rules:
    - Contains letters+digits (example: "SMF", "smf-02", "5960", "M1600")
    - Or short technical strings (length <= 12, no spaces or 1 space)
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


def normalize_code(code_str: str) -> str:
    """
    Normalize code for search: lower, remove spaces, dashes, dots, slashes, underscores.
    Example:
    "SMF-02" -> "smf02"
    "s m f 02" -> "smf02"
    """
    if not code_str:
        return ""
    normalized = str(code_str).strip().lower()
    normalized = normalized.replace(" ", "")
    for char in ["-", ".", "/", "_"]:
        normalized = normalized.replace(char, "")
    return normalized


def normalize_code_for_search(code_str: str) -> str:
    """Alias for normalize_code - backward compatibility"""
    return normalize_code(code_str)


def extract_numbers_only(code_str: str) -> str:
    """Extract only numbers from code string"""
    if not code_str:
        return ""
    return ''.join(filter(str.isdigit, code_str))


def normalize_text_for_search(text: str) -> str:
    """Normalize text for search: lower, remove extra spaces"""
    if not text:
        return ""
    normalized = str(text).strip().lower()
    # Multiple spaces to single space
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized


def find_stock_for_code(norm_code: str, stock_map: Dict[str, Dict]) -> Optional[str]:
    """
    Find stock quantity for a normalized code using flexible matching.
    
    Matching priority:
    1. Exact match
    2. Prefix match (stock key contains norm_code)
    3. Contains match (norm_code contains stock key)
    4. Numeric fallback (if norm_code has digits, match numeric part)
    
    Returns quantity string if found, None if not available.
    """
    if not norm_code or not stock_map:
        return None
    
    # Step 1: Try exact match
    if norm_code in stock_map:
        quantity = stock_map[norm_code].get("quantity", "")
        if quantity and str(quantity).strip():
            return str(quantity).strip()
    
    # Step 2: Try prefix match (stock key contains norm_code)
    for stock_key, stock_data in stock_map.items():
        if stock_key.startswith(norm_code) or norm_code.startswith(stock_key):
            quantity = stock_data.get("quantity", "")
            if quantity and str(quantity).strip():
                return str(quantity).strip()
    
    # Step 3: Try contains match (norm_code in stock_key or stock_key in norm_code)
    for stock_key, stock_data in stock_map.items():
        if norm_code in stock_key or stock_key in norm_code:
            quantity = stock_data.get("quantity", "")
            if quantity and str(quantity).strip():
                return str(quantity).strip()
    
    # Step 4: Numeric fallback
    norm_code_numbers = extract_numbers_only(norm_code)
    if norm_code_numbers and len(norm_code_numbers) >= 2:
        for stock_key, stock_data in stock_map.items():
            stock_key_numbers = extract_numbers_only(stock_key)
            if stock_key_numbers and norm_code_numbers in stock_key_numbers:
                quantity = stock_data.get("quantity", "")
                if quantity and str(quantity).strip():
                    return str(quantity).strip()
    
    return None


def find_models_by_code(query: str) -> List[Dict]:
    """
    Find models by code in sheets1 (stock) and sheets2 (images metadata).
    Search ONLY by "code" column with flexible matching.
    
    Search priority:
    1. Exact match
    2. Prefix match
    3. Contains match
    4. Numeric fallback (if query has digits, match numeric part)
    
    Returns list of matching models with:
    - code (from sheets1 or sheets2)
    - quantity (from sheets1)
    - image_url, rang/color, tur/type, kolleksiya/collection, material (from sheets2)
    """
    if not query:
        return []
    
    query_normalized = normalize_code(query)
    if not query_normalized or len(query_normalized) < 2:
        return []
    
    logger.info(f"AI search routed to DB - code query: {query}")
    
    results = []
    seen_codes = set()
    
    # Extract numeric part from query for fallback matching
    query_numbers = extract_numbers_only(query)
    
    # Build stock lookup map from sheets1 - normalize all codes
    stock_map = {}
    for item in CACHE.get("sheets1", []):
        code = item.get("code", "").strip()
        if code:
            code_normalized = normalize_code(code)
            stock_map[code_normalized] = {
                "quantity": item.get("quantity", ""),
                "collection": item.get("collection", ""),
                "date": item.get("date", ""),
            }
    
    # Get sheets2_full for model data - search by code column
    sheets2_data = CACHE.get("sheets2_full", [])
    
    # Search in sheets2_full
    for record in sheets2_data:
        code = record.get("code", "").strip()
        if not code:
            continue
        
        code_normalized = record.get("_code_normalized", "")
        if not code_normalized:
            code_normalized = normalize_code(code)
        
        if code_normalized in seen_codes:
            continue
        
        # Match rules with priority
        is_match = False
        match_type = None
        match_priority = 0
        
        # Step 1: Exact match
        if query_normalized == code_normalized:
            is_match = True
            match_type = "exact"
            match_priority = 4
            logger.info(f"Exact match: {query} -> {code}")
        
        # Step 2: Prefix match
        elif code_normalized.startswith(query_normalized):
            is_match = True
            match_type = "prefix"
            match_priority = 3
            logger.info(f"Prefix match used: {query} -> {code}")
        
        # Step 3: Contains match
        elif query_normalized in code_normalized:
            is_match = True
            match_type = "contains"
            match_priority = 2
        
        # Step 4: Numeric fallback (only if query has digits and no other match)
        elif query_numbers and len(query_numbers) >= 2:
            code_numbers = extract_numbers_only(code)
            if code_numbers and query_numbers in code_numbers:
                is_match = True
                match_type = "numeric"
                match_priority = 1
        
        # Filter: ignore matches where normalized query length < 2
        if is_match and len(query_normalized) >= 2:
            seen_codes.add(code_normalized)
            
            # Get fields from sheets2
            image_url = (record.get("image_url") or record.get("imageurl") or 
                        record.get("image url") or record.get("image") or "")
            rang = (record.get("rang") or record.get("color") or "").strip()
            tur = (record.get("tur") or record.get("type") or record.get("turi") or "").strip()
            kolleksiya = (record.get("kolleksiya") or record.get("collection") or "").strip()
            material = (record.get("material") or record.get("material") or "").strip()
            
            # Get quantity from sheets1 using flexible matching
            quantity = find_stock_for_code(code_normalized, stock_map)
            
            result = {
                "code": code,
                "quantity": quantity or "",  # Empty string if None
                "image_url": image_url,
                "rang": rang,
                "tur": tur,
                "kolleksiya": kolleksiya,
                "material": material,
                "match_type": match_type,
                "match_priority": match_priority,
                "code_length": len(code_normalized)  # For secondary sort
            }
            results.append(result)
    
    # Sort by: 1) match priority (exact > prefix > contains > numeric), 2) shortest code length
    results.sort(key=lambda x: (x.get("match_priority", 0), -x.get("code_length", 0)), reverse=True)
    
    # Limit to max 10 best matches
    results = results[:10]
    
    return results


def search_models_by_code(query: str) -> List[Dict]:
    """Alias for find_models_by_code - backward compatibility"""
    return find_models_by_code(query)


def extract_search_terms(query: str) -> Dict[str, any]:
    """
    Extract search terms from query: code, color, type, collection, etc.
    Returns dict with extracted terms.
    """
    query_lower = query.lower()
    terms = {
        "code": None,
        "code_numbers": None,
        "color": None,
        "type": None,
        "collection": None,
        "manufacturer": None,  # turk, xitoy
        "price_range": None,  # arzon, qimmat
        "style": None,  # classic, modern, luxury, simple
        "density": None,  # yengil, o'rtacha, qalin
        "pattern": None,  # gullik, chiziqli, soddaroq
        "has_discount": False,
        "has_image": False,
        "raw_query": query
    }
    
    # Extract code numbers
    code_numbers = extract_numbers_only(query)
    if code_numbers and len(code_numbers) >= 2:
        terms["code_numbers"] = code_numbers
        terms["code"] = normalize_code_for_search(query)
    
    # Color keywords
    color_keywords = {
        "oq": "oq", "white": "oq", "ак": "oq", "krem": "krem", "cream": "krem",
        "qora": "qora", "black": "qora", "қора": "qora",
        "qizil": "qizil", "red": "qizil", "қизил": "qizil",
        "ko'k": "ko'k", "blue": "ko'k", "кўк": "ko'k",
        "yashil": "yashil", "green": "yashil", "яшил": "yashil",
        "sariq": "sariq", "yellow": "sariq", "сариқ": "sariq",
        "pushti": "pushti", "pink": "pushti", "пушти": "pushti",
        "jigarrang": "jigarrang", "brown": "jigarrang", "жигарранг": "jigarrang",
        "kulrang": "kulrang", "gray": "kulrang", "grey": "kulrang",
        "karichniy": "karichniy", "коричневый": "karichniy"
    }
    for keyword, color in color_keywords.items():
        if keyword in query_lower:
            terms["color"] = color
            break
    
    # Type keywords
    type_keywords = {
        "kombo": "kombo", "combo": "kombo", "комбо": "kombo",
        "dikey": "dikey", "дикий": "dikey", "dikiy": "dikey",
        "plise": "plise", "плисе": "plise", "plis": "plise",
        "rollo": "rollo", "ролло": "rollo", "rolik": "rollo",
        "zebra": "zebra", "зебра": "zebra",
        "parter": "parter", "партер": "parter"
    }
    for keyword, model_type in type_keywords.items():
        if keyword in query_lower:
            terms["type"] = model_type
            break
    
    # Manufacturer keywords
    if "turk" in query_lower or "турк" in query_lower:
        terms["manufacturer"] = "turk"
    elif "xitoy" in query_lower or "китай" in query_lower or "kitay" in query_lower:
        terms["manufacturer"] = "xitoy"
    
    # Price range keywords
    if "arzon" in query_lower or "дешевый" in query_lower:
        terms["price_range"] = "arzon"
    elif "qimmat" in query_lower or "дорогой" in query_lower:
        terms["price_range"] = "qimmat"
    
    # Style keywords
    style_keywords = {
        "classic": "classic", "классический": "classic",
        "modern": "modern", "современный": "modern",
        "luxury": "luxury", "люкс": "luxury",
        "simple": "simple", "простой": "simple"
    }
    for keyword, style in style_keywords.items():
        if keyword in query_lower:
            terms["style"] = style
            break
    
    # Density keywords
    if "yengil" in query_lower or "легкий" in query_lower:
        terms["density"] = "yengil"
    elif "o'rtacha" in query_lower or "средний" in query_lower:
        terms["density"] = "o'rtacha"
    elif "qalin" in query_lower or "толстый" in query_lower:
        terms["density"] = "qalin"
    
    # Pattern keywords
    if "gullik" in query_lower or "pattern" in query_lower or "patterned" in query_lower or "гуллик" in query_lower:
        terms["pattern"] = "gullik"
    elif "chiziqli" in query_lower or "line" in query_lower or "линейный" in query_lower:
        terms["pattern"] = "chiziqli"
    elif "soddaroq" in query_lower or "простой" in query_lower or "naqshli" in query_lower:
        terms["pattern"] = "soddaroq"
    
    # Collection keywords
    collection_keywords = [
        "0-start", "1-stage", "2-middle", "3-optimal", "4-top", "5-perfect", "6-exclusive",
        "optimal", "start", "stage", "middle", "top", "perfect", "exclusive"
    ]
    for keyword in collection_keywords:
        if keyword in query_lower:
            terms["collection"] = keyword
            break
    
    # Discount and image keywords
    if any(word in query_lower for word in ["skidka", "discount", "chegirma", "скидка"]):
        terms["has_discount"] = True
    if any(word in query_lower for word in ["rasm", "image", "photo", "rasmi", "фото", "изображение"]):
        terms["has_image"] = True
    
    return terms


def search_in_database(query: str) -> List[Dict]:
    """
    Search ONLY in sheets1 (quantity) and sheets2_full (model data).
    Does NOT use sheets3 (prices) or sheets4 (discounts).
    Searches by: code, color, type, collection, manufacturer, price_range, style, density, pattern.
    Returns list of matching products with quantity from sheets1.
    """
    # Check for discount query - return special response
    query_lower = query.lower()
    if any(word in query_lower for word in ["skidka", "discount", "chegirma", "скидка"]):
        return []  # Will be handled separately in _process_question
    
    terms = extract_search_terms(query)
    
    # If no searchable terms, return empty
    if not any([terms["code"], terms["code_numbers"], terms["color"], terms["type"], 
                terms["collection"], terms["manufacturer"], terms["price_range"], 
                terms["style"], terms["density"], terms["pattern"]]):
        return []
    
    results = []
    seen_codes = set()
    
    # Get sheets2_full data (main AI database)
    sheets2_data = CACHE.get("sheets2_full", [])
    
    # Get sheets1 data for quantities
    sheets1_map = {}
    for item in CACHE.get("sheets1", []):
        code = item.get("code", "").strip()
        if code:
            code_normalized = normalize_code(code)
            sheets1_map[code_normalized] = {
                "quantity": item.get("quantity", ""),
                "collection": item.get("collection", ""),
                "date": item.get("date", ""),
            }
    
    # Search in sheets2_full
    for item in sheets2_data:
        code = item.get("code", "").strip()
        if not code:
            continue
        
        code_normalized_strict = normalize_code(code)
        if code_normalized_strict in seen_codes:
            continue
        
        # Get all fields from sheets2 (case-insensitive) - check multiple possible column names
        code_normalized = normalize_code_for_search(code)
        code_numbers = extract_numbers_only(code)
        
        # Get fields from sheets2 - check various possible column name variations
        rang = normalize_text_for_search(
            item.get("rang", "") or item.get("color", "") or item.get("rang/color", "") or ""
        )
        tur = normalize_text_for_search(
            item.get("tur", "") or item.get("type", "") or item.get("turi", "") or item.get("tur/type", "") or ""
        )
        naqsh = normalize_text_for_search(
            item.get("naqsh", "") or item.get("pattern", "") or item.get("texture", "") or item.get("naqsh/pattern", "") or ""
        )
        kolleksiya = normalize_text_for_search(
            item.get("kolleksiya", "") or item.get("collection", "") or ""
        )
        ishlab_chiqaruvchi = normalize_text_for_search(
            item.get("ishlab chiqaruvchi", "") or item.get("manufacturer", "") or 
            item.get("ishlabchiqaruvchi", "") or item.get("proizvoditel", "") or ""
        )
        arzon_qimmat = normalize_text_for_search(
            item.get("arzon/qimmat", "") or item.get("price_range", "") or 
            item.get("arzon", "") or item.get("qimmat", "") or ""
        )
        style = normalize_text_for_search(
            item.get("style", "") or item.get("uslub", "") or ""
        )
        density = normalize_text_for_search(
            item.get("density", "") or item.get("qalinlik", "") or 
            item.get("yengil", "") or item.get("o'rtacha", "") or item.get("qalin", "") or ""
        )
        
        is_match = False
        match_score = 0
        matched_criteria = []
        
        # 1. Code match (highest priority)
        if terms["code"] and code_normalized:
            if terms["code"] == code_normalized:
                is_match = True
                match_score = 100
                matched_criteria.append("code_exact")
            elif code_normalized.startswith(terms["code"]) or terms["code"] in code_normalized:
                is_match = True
                match_score = max(match_score, 80)
                matched_criteria.append("code_partial")
        
        # 2. Code numbers match
        if terms["code_numbers"] and code_numbers and len(terms["code_numbers"]) >= 2:
            if terms["code_numbers"] == code_numbers:
                is_match = True
                match_score = max(match_score, 70)
                matched_criteria.append("code_numbers")
            elif terms["code_numbers"] in code_numbers or code_numbers in terms["code_numbers"]:
                is_match = True
                match_score = max(match_score, 60)
                matched_criteria.append("code_numbers_partial")
        
        # 3. Type match (dikey, zebra, plis, kombo, rolik, parter)
        if terms["type"] and tur:
            type_lower = terms["type"].lower()
            if type_lower in tur or tur in type_lower:
                is_match = True
                match_score = max(match_score, 50)
                matched_criteria.append("type")
        
        # 4. Color match (oq, qora, qizil, ko'k, etc.)
        if terms["color"] and rang:
            color_lower = terms["color"].lower()
            if color_lower in rang or rang in color_lower:
                is_match = True
                match_score = max(match_score, 45)
                matched_criteria.append("color")
        
        # 5. Pattern match (gullik, chiziqli, soddaroq, naqshli)
        if terms["pattern"] and naqsh:
            pattern_lower = terms["pattern"].lower()
            if pattern_lower in naqsh or naqsh in pattern_lower:
                is_match = True
                match_score = max(match_score, 40)
                matched_criteria.append("pattern")
        
        # 6. Manufacturer match (turk, xitoy)
        if terms["manufacturer"] and ishlab_chiqaruvchi:
            manufacturer_lower = terms["manufacturer"].lower()
            if manufacturer_lower in ishlab_chiqaruvchi or ishlab_chiqaruvchi in manufacturer_lower:
                is_match = True
                match_score = max(match_score, 35)
                matched_criteria.append("manufacturer")
        
        # 7. Price range match (arzon, qimmat)
        if terms["price_range"] and arzon_qimmat:
            price_range_lower = terms["price_range"].lower()
            if price_range_lower in arzon_qimmat or arzon_qimmat in price_range_lower:
                is_match = True
                match_score = max(match_score, 30)
                matched_criteria.append("price_range")
        
        # 8. Style match (classic, modern, luxury, simple)
        if terms["style"] and style:
            style_lower = terms["style"].lower()
            if style_lower in style or style in style_lower:
                is_match = True
                match_score = max(match_score, 25)
                matched_criteria.append("style")
        
        # 9. Density match (yengil, o'rtacha, qalin)
        if terms["density"] and density:
            density_lower = terms["density"].lower()
            if density_lower in density or density in density_lower:
                is_match = True
                match_score = max(match_score, 20)
                matched_criteria.append("density")
        
        # 10. Collection match
        if terms["collection"] and kolleksiya:
            collection_lower = terms["collection"].lower()
            if collection_lower in kolleksiya or kolleksiya in collection_lower:
                is_match = True
                match_score = max(match_score, 15)
                matched_criteria.append("collection")
        
        # 11. Image filter
        if terms["has_image"]:
            image_url = get_image_url_for_code(code)
            if not image_url:
                continue
        
        if is_match:
            seen_codes.add(code_normalized_strict)
            
            # Get image URL from sheets2
            image_url = get_image_url_for_code(code)
            
            # Combine with sheets1 data
            result_item = {
                "code": code,
                "rang": rang,
                "tur": tur,
                "naqsh": naqsh,
                "kolleksiya": kolleksiya,
                "image_url": image_url,
                "match_score": match_score,
                "matched_criteria": matched_criteria
            }
            
            # Add quantity from sheets1 using flexible matching
            quantity = find_stock_for_code(code_normalized_strict, sheets1_map)
            result_item["quantity"] = quantity or ""
            
            # Add collection from sheets1 if available
            if code_normalized_strict in sheets1_map:
                result_item["date"] = sheets1_map[code_normalized_strict].get("date", "")
            
            results.append(result_item)
    
    # Sort by match score (highest first), then by code length (shorter codes first)
    results.sort(key=lambda x: (x.get("match_score", 0), -len(x.get("code", ""))), reverse=True)
    
    # Limit to top 20 results
    return results[:20]


async def process_image_and_match_models(image_bytes: bytes) -> Dict[str, any]:
    """
    Analyze image using AI and extract: color, type, texture/pattern.
    Returns dict with extracted features for database search.
    AI faqat sheets2 bazasidagi modellardan tanlaydi, yangi nom bermaydi.
    """
    try:
        ai_service = get_openai_service()
    except ValueError:
        logger.error("OpenAI service not available for image analysis")
        return {}
    
    # AI prompt for image analysis - more detailed
    analysis_prompt = """Bu rasmda jalyuzi modeli ko'rsatilgan. Quyidagilarni aniqlang:

1. RANG (faqat bitta asosiy rang):
   - oq, krem, qora, kulrang, jigarrang, qizil, ko'k, yashil, sariq, pushti, karichniy, yoki boshqa

2. TUR (faqat bitta asosiy tur):
   - kombo, dikey, plise, rollo, zebra, parter, yoki boshqa

3. TEKSTURA/NAQSH:
   - gullik (patterned), chiziqli, soddaroq, naqshli, yoki boshqa

4. UMUMIY USLUB:
   - classic, modern, luxury, simple

Javobni quyidagi formatda bering (faqat JSON, boshqa matn yozmang):
{
  "color": "oq",
  "type": "dikey",
  "texture": "gullik",
  "style": "modern"
}

Agar aniq aniqlab bo'lmasa, "unknown" yozing. Faqat JSON formatida javob bering."""
    
    try:
        # Use AI to analyze image
        result = await ai_service.generate_from_image_and_text(image_bytes, analysis_prompt)
        analysis_text = result.description if result.description else ""
        
        # Parse JSON response
        import json
        # Extract JSON from text (might have extra text)
        json_start = analysis_text.find("{")
        json_end = analysis_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = analysis_text[json_start:json_end]
            try:
                analysis_data = json.loads(json_str)
                logger.info(f"Image analysis result: {analysis_data}")
                # Normalize values
                if "texture" in analysis_data:
                    analysis_data["pattern"] = analysis_data.pop("texture")
                return analysis_data
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON from AI response: {json_str}")
        
        # Fallback: try to extract keywords from text
        analysis_lower = analysis_text.lower()
        extracted = {
            "color": None,
            "type": None,
            "pattern": None,
            "style": None
        }
        
        # Extract color
        color_keywords = {
            "oq": "oq", "white": "oq", "ак": "oq",
            "krem": "krem", "cream": "krem",
            "qora": "qora", "black": "qora", "қора": "qora",
            "qizil": "qizil", "red": "qizil", "қизил": "qizil",
            "ko'k": "ko'k", "blue": "ko'k", "кўк": "ko'k",
            "yashil": "yashil", "green": "yashil", "яшил": "yashil",
            "sariq": "sariq", "yellow": "sariq", "сариқ": "sariq",
            "pushti": "pushti", "pink": "pushti", "пушти": "pushti",
            "jigarrang": "jigarrang", "brown": "jigarrang", "жигарранг": "jigarrang",
            "kulrang": "kulrang", "gray": "kulrang", "grey": "kulrang",
            "karichniy": "karichniy", "коричневый": "karichniy"
        }
        for keyword, color in color_keywords.items():
            if keyword in analysis_lower:
                extracted["color"] = color
                break
        
        # Extract type
        type_keywords = {
            "kombo": "kombo", "combo": "kombo", "комбо": "kombo",
            "dikey": "dikey", "дикий": "dikey", "dikiy": "dikey",
            "plise": "plise", "плисе": "plise", "plis": "plise",
            "rollo": "rollo", "ролло": "rollo", "rolik": "rollo",
            "zebra": "zebra", "зебра": "zebra",
            "parter": "parter", "партер": "parter"
        }
        for keyword, model_type in type_keywords.items():
            if keyword in analysis_lower:
                extracted["type"] = model_type
                break
        
        # Extract texture/pattern
        if "gullik" in analysis_lower or "pattern" in analysis_lower or "patterned" in analysis_lower:
            extracted["pattern"] = "gullik"
        elif "chiziqli" in analysis_lower or "line" in analysis_lower or "линейный" in analysis_lower:
            extracted["pattern"] = "chiziqli"
        elif "soddaroq" in analysis_lower or "простой" in analysis_lower or "naqshli" in analysis_lower:
            extracted["pattern"] = "soddaroq"
        
        # Extract style
        style_keywords = {
            "classic": "classic", "классический": "classic",
            "modern": "modern", "современный": "modern",
            "luxury": "luxury", "люкс": "luxury",
            "simple": "simple", "простой": "simple"
        }
        for keyword, style in style_keywords.items():
            if keyword in analysis_lower:
                extracted["style"] = style
                break
        
        logger.info(f"Extracted features from text: {extracted}")
        return extracted
        
    except Exception as e:
        logger.error(f"Error analyzing image with AI: {e}", exc_info=True)
        return {}


def search_by_image_features(features: Dict[str, any]) -> List[Dict]:
    """
    Search ONLY in sheets2_full by extracted image features (color, type, texture/pattern).
    Does NOT use sheets3 or sheets4.
    Returns list of matching products with quantity from sheets1.
    AI faqat sheets2 bazasidagi modellardan tanlaydi, yangi nom bermaydi.
    """
    if not features:
        return []
    
    results = []
    seen_codes = set()
    
    # Get sheets2_full data (main AI database)
    sheets2_data = CACHE.get("sheets2_full", [])
    
    # Get sheets1 data for quantities
    sheets1_map = {}
    for item in CACHE.get("sheets1", []):
        code = item.get("code", "").strip()
        if code:
            code_normalized = normalize_code(code)
            sheets1_map[code_normalized] = {
                "quantity": item.get("quantity", ""),
                "collection": item.get("collection", ""),
                "date": item.get("date", ""),
            }
    
    # Search in sheets2_full by features
    for item in sheets2_data:
        code = item.get("code", "").strip()
        if not code:
            continue
        
        code_normalized_strict = normalize_code(code)
        if code_normalized_strict in seen_codes:
            continue
        
        # Get all fields from sheets2 (case-insensitive) - check multiple possible column names
        rang = normalize_text_for_search(
            item.get("rang", "") or item.get("color", "") or item.get("rang/color", "") or ""
        )
        tur = normalize_text_for_search(
            item.get("tur", "") or item.get("type", "") or item.get("turi", "") or item.get("tur/type", "") or ""
        )
        naqsh = normalize_text_for_search(
            item.get("naqsh", "") or item.get("pattern", "") or item.get("texture", "") or item.get("naqsh/pattern", "") or ""
        )
        kolleksiya = normalize_text_for_search(
            item.get("kolleksiya", "") or item.get("collection", "") or ""
        )
        style = normalize_text_for_search(
            item.get("style", "") or item.get("uslub", "") or ""
        )
        
        match_score = 0
        matched_features = []
        
        # Check type match (highest priority)
        if features.get("type"):
            feature_type = features["type"].lower()
            if feature_type in tur or tur in feature_type:
                match_score += 50
                matched_features.append("type")
        
        # Check color match
        if features.get("color"):
            feature_color = features["color"].lower()
            if feature_color in rang or rang in feature_color:
                match_score += 45
                matched_features.append("color")
        
        # Check texture/pattern match
        if features.get("texture") or features.get("pattern"):
            feature_texture = (features.get("texture") or features.get("pattern") or "").lower()
            if feature_texture in naqsh or naqsh in feature_texture:
                match_score += 40
                matched_features.append("pattern")
        
        # Check style match
        if features.get("style"):
            feature_style = features["style"].lower()
            if feature_style in style or style in feature_style:
                match_score += 25
                matched_features.append("style")
        
        # If at least one feature matches, include it
        if match_score > 0:
            seen_codes.add(code_normalized_strict)
            
            # Get image URL from sheets2
            image_url = get_image_url_for_code(code)
            
            # Combine with sheets1 data
            result_item = {
                "code": code,
                "rang": rang,
                "tur": tur,
                "naqsh": naqsh,
                "kolleksiya": kolleksiya,
                "image_url": image_url,
                "match_score": match_score,
                "matched_features": matched_features
            }
            
            # Add quantity from sheets1 using flexible matching
            quantity = find_stock_for_code(code_normalized_strict, sheets1_map)
            result_item["quantity"] = quantity or ""
            
            # Add collection from sheets1 if available
            if code_normalized_strict in sheets1_map:
                result_item["date"] = sheets1_map[code_normalized_strict].get("date", "")
            
            results.append(result_item)
    
    # Sort by match score (highest first), then by code length (shorter codes first)
    results.sort(key=lambda x: (x.get("match_score", 0), -len(x.get("code", ""))), reverse=True)
    
    # Limit to top 5 results for image search
    return results[:5]


def format_code_search_results(results: List[Dict]) -> Tuple[str, Optional[str], List[str]]:
    """
    Format code search results.
    Format: 🔹 CODE — QOLDIQ
            TUR | RANG | NAQSH | KOLLEKSIYA
    
    Returns: (answer_text, first_image_url, all_image_urls)
    """
    if not results:
        return "", None, []
    
    answer_lines = []
    first_image_url = None
    all_image_urls = []
    
    for idx, result in enumerate(results):
        if idx > 0:
            answer_lines.append("")
        
        code = result.get("code", "")
        quantity = result.get("quantity", "").strip()
        rang = result.get("rang", "").strip()
        tur = result.get("tur", "").strip()
        naqsh = result.get("naqsh", "").strip()
        kolleksiya = result.get("kolleksiya", "").strip()
        image_url = result.get("image_url", "").strip()
        
        # Get image URL (first result)
        if idx == 0 and image_url:
            first_image_url = image_url
        if image_url:
            all_image_urls.append(image_url)
        
        # Format: 🔹 CODE — QOLDIQ
        # Check if quantity is valid and > 0
        quantity_str = "qolmadi"
        if quantity:
            quantity_clean = str(quantity).strip()
            if quantity_clean:
                try:
                    # Try to parse as float
                    qty_float = float(quantity_clean.replace(",", "."))
                    if qty_float > 0:
                        quantity_str = f"{quantity_clean} m"
                    else:
                        quantity_str = "qolmadi"
                except (ValueError, TypeError):
                    # If not a number, check if it's a valid string
                    if quantity_clean.lower() not in ["0", "0.0", "0,0", ""]:
                        quantity_str = f"{quantity_clean} m"
                    else:
                        quantity_str = "qolmadi"
        
        answer_lines.append(f"🔹 {code} — {quantity_str}")
        
        # Format: TUR | RANG | NAQSH | KOLLEKSIYA
        info_parts = []
        if tur:
            info_parts.append(tur)
        if rang:
            info_parts.append(rang)
        if naqsh:
            info_parts.append(naqsh)
        if kolleksiya:
            info_parts.append(kolleksiya)
        
        if info_parts:
            answer_lines.append("   " + " | ".join(info_parts))
    
    answer_text = "\n".join(answer_lines)
    return answer_text, first_image_url, all_image_urls


def find_similar_models(query: str, exclude_codes: set = None) -> List[Dict]:
    """
    Find similar models when exact match not found.
    Searches ONLY in sheets1 and sheets2_full.
    Does NOT use sheets3 (prices) or sheets4 (discounts).
    Returns 3-5 similar models.
    """
    if exclude_codes is None:
        exclude_codes = set()
    
    terms = extract_search_terms(query)
    similar_results = []
    seen_codes = set(exclude_codes)
    
    # Get sheets2_full data (main AI database)
    sheets2_data = CACHE.get("sheets2_full", [])
    
    # Get sheets1 data for quantities
    sheets1_map = {}
    for item in CACHE.get("sheets1", []):
        code = item.get("code", "").strip()
        if code:
            code_normalized = normalize_code(code)
            sheets1_map[code_normalized] = {
                "quantity": item.get("quantity", ""),
                "collection": item.get("collection", ""),
                "date": item.get("date", ""),
            }
    
    # Search in sheets2_full for similar models
    for item in sheets2_data:
        code = item.get("code", "").strip()
        if not code:
            continue
        
        code_normalized_strict = normalize_code(code)
        if code_normalized_strict in seen_codes:
            continue
        
        # Get fields from sheets2
        code_normalized = normalize_code_for_search(code)
        code_numbers = extract_numbers_only(code)
        tur = normalize_text_for_search(item.get("tur", "") or item.get("type", "") or "")
        rang = normalize_text_for_search(item.get("rang", "") or item.get("color", "") or "")
        kolleksiya = normalize_text_for_search(item.get("kolleksiya", "") or item.get("collection", "") or "")
        
        is_similar = False
        match_score = 0
        
        # Partial code match
        if terms["code"] and code_normalized:
            if terms["code"] in code_normalized or code_normalized in terms["code"]:
                is_similar = True
                match_score = 30
        elif terms["code_numbers"] and code_numbers and len(terms["code_numbers"]) >= 2:
            if terms["code_numbers"] in code_numbers:
                is_similar = True
                match_score = 25
        
        # Type similarity
        if terms["type"] and tur:
            if terms["type"] in tur or tur in terms["type"]:
                is_similar = True
                match_score = max(match_score, 20)
        
        # Color similarity
        if terms["color"] and rang:
            if terms["color"] in rang or rang in terms["color"]:
                is_similar = True
                match_score = max(match_score, 15)
        
        if is_similar:
            seen_codes.add(code_normalized_strict)
            
            # Get image URL from sheets2
            image_url = get_image_url_for_code(code)
            
            # Get quantity from sheets1
            quantity = find_stock_for_code(code_normalized_strict, sheets1_map)
            
            similar_item = {
                "code": code,
                "rang": rang,
                "tur": tur,
                "naqsh": normalize_text_for_search(item.get("naqsh", "") or item.get("pattern", "") or ""),
                "kolleksiya": kolleksiya,
                "quantity": quantity or "",
                "image_url": image_url,
                "match_score": match_score
            }
            
            similar_results.append(similar_item)
            if len(similar_results) >= 5:
                break
    
    # Sort by match score (highest first)
    similar_results.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    
    return similar_results


def get_image_url_for_code(code: str) -> str:
    """Get image URL for code from sheets2 cache"""
    code_normalized = normalize_code(code)
    return CACHE.get("sheets2", {}).get(code_normalized, "")


def is_question_about_database(question: str) -> bool:
    """
    Check if question is about database (code, price, quantity, collection, model, color, type).
    Returns True if question seems to be asking about product data.
    """
    if not question or not question.strip():
        return False
    
    question_lower = question.lower().strip()
    
    # Keywords that indicate database query
    db_keywords = [
        "kod", "code", "model", "narx", "price", "qoldiq", "quantity", "kolleksiya", "collection",
        "rasm", "image", "photo", "rasmi", "bormi", "bor", "necha", "nechchi", "qancha", "qanday",
        "sng", "plise", "dikey", "rollo", "kombo", "turk", "xitoy", "kitay",
        "oq", "qora", "qizil", "ko'k", "yashil", "sariq", "pushti", "jigarrang", "gullik",
        "white", "black", "red", "blue", "green", "yellow", "pink", "brown", "pattern",
        "skidka", "discount", "chegirma", "kerak", "mavjud", "topiladi"
    ]
    
    # Check if question contains database-related keywords
    has_keyword = any(keyword in question_lower for keyword in db_keywords)
    
    # Check if question contains numbers (likely a code)
    has_numbers = bool(re.search(r'\d+', question))
    
    # Check if question is very short (likely a code query)
    is_short = len(question.strip()) <= 50
    
    # Check if question looks like a code (starts with letter and has numbers, or just numbers)
    looks_like_code = bool(re.match(r'^[a-zA-Z]?\d+[a-zA-Z]?$', question.strip())) or bool(re.match(r'^\d+$', question.strip()))
    
    # Extract terms to see if we can search
    terms = extract_search_terms(question)
    has_searchable_terms = any([terms["code"], terms["code_numbers"], terms["color"], terms["type"], terms["collection"]])
    
    return has_keyword or (has_numbers and is_short) or looks_like_code or has_searchable_terms


def format_database_answer(results: List[Dict], query: str, user_id: int) -> Tuple[str, Optional[str]]:
    """
    Format database search results into answer text and image URL.
    Format: 🔹 CODE — QOLDIQ
            TUR | RANG | NAQSH | KOLLEKSIYA
    Does NOT show prices (prices are handled separately by permission system).
    """
    if not results:
        return "", None
    
    # Limit to first 10 results
    results = results[:10]
    
    answer_lines = []
    image_url = None
    
    for idx, result in enumerate(results):
        if idx > 0:
            answer_lines.append("")
        
        code = result.get("code", "")
        quantity = result.get("quantity", "")
        rang = result.get("rang", "").strip()
        tur = result.get("tur", "").strip()
        naqsh = result.get("naqsh", "").strip()
        kolleksiya = result.get("kolleksiya", "").strip() or result.get("collection", "").strip()
        
        # Get image URL (first result only)
        if idx == 0:
            image_url = result.get("image_url") or get_image_url_for_code(code)
        
        # Format: 🔹 CODE — QOLDIQ
        # Check if quantity is valid and > 0
        quantity_str = "qolmadi"
        if quantity:
            quantity_clean = str(quantity).strip()
            if quantity_clean:
                try:
                    # Try to parse as float
                    qty_float = float(quantity_clean.replace(",", "."))
                    if qty_float > 0:
                        quantity_str = f"{quantity_clean} m"
                    else:
                        quantity_str = "qolmadi"
                except (ValueError, TypeError):
                    # If not a number, check if it's a valid string
                    if quantity_clean.lower() not in ["0", "0.0", "0,0", ""]:
                        quantity_str = f"{quantity_clean} m"
                    else:
                        quantity_str = "qolmadi"
        
        answer_lines.append(f"🔹 {code} — {quantity_str}")
        
        # Format: TUR | RANG | NAQSH | KOLLEKSIYA
        info_parts = []
        if tur:
            info_parts.append(tur)
        if rang:
            info_parts.append(rang)
        if naqsh:
            info_parts.append(naqsh)
        if kolleksiya:
            info_parts.append(kolleksiya)
        
        if info_parts:
            answer_lines.append("   " + " | ".join(info_parts))
    
    answer_text = "\n".join(answer_lines)
    return answer_text, image_url


@router.callback_query(F.data == "menu_questions")
async def callback_questions(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Yordam bo'limi - 2 ta tanlov: Baza qidiruvi yoki Umumiy savollar"""
    await callback_query.answer()
    
    text = """❓ <b>Yordam bo'limi</b>

Quyidagi xizmatlardan birini tanlang:

📦 <b>Baza bo'yicha qidiruv</b>
   • Model kodlari bo'yicha qidirish
   • Rang va tur bo'yicha qidirish
   • Rasm yuborib o'xshash modellarni topish
   • Qoldiq miqdorini bilish

🤖 <b>Umumiy savollar</b>
   • Jalyuzlar haqida ma'lumot
   • Maslahat va tavsiyalar
   • Boshqa savollar"""
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Baza bo'yicha qidiruv",
                    callback_data="questions_database"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤖 Umumiy savollar",
                    callback_data="questions_ai"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="menu_main"
                )
            ]
        ]
    )
    
    chat_id = callback_query.message.chat.id
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


@router.callback_query(F.data == "questions_database")
async def callback_questions_database(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Baza bo'yicha qidiruv"""
    await callback_query.answer()
    
    # State ni o'rnatish
    await state.set_state(QuestionStates.waiting_for_question)
    
    # Savol-javob xabarlari ro'yxatini boshlash
    await state.update_data(question_message_ids=[], question_type="database")
    
    text = """📦 <b>Baza bo'yicha qidiruv</b>

Bu yerda bazadagi modellar, ranglar, turlar va qoldiq bo'yicha so'rashingiz mumkin:

🔹 <b>Model kodi bo'yicha</b>
   Masalan: M1600, SMF-02, 5830

🔹 <b>Rang va tur bo'yicha</b>
   Masalan: jigarrang kombo, oq dikey, gullik plis

🔹 <b>Xususiyat bo'yicha</b>
   Masalan: turk plis, xitoy kombo, 1 kolleksiya

🔹 <b>Rasm yuborish</b>
   Model rasmini yuborsangiz - bot o'xshash modellarni topib beradi

✍️ Endi savolingizni yozing yoki rasm yuboring:"""
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="questions_back"
                )
            ]
        ]
    )
    
    chat_id = callback_query.message.chat.id
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        # Track message
        data = await state.get_data()
        question_message_ids = data.get("question_message_ids", [])
        question_message_ids.append(callback_query.message.message_id)
        await state.update_data(question_message_ids=question_message_ids)
    except Exception:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        data = await state.get_data()
        question_message_ids = data.get("question_message_ids", [])
        question_message_ids.append(sent.message_id)
        await state.update_data(question_message_ids=question_message_ids)


@router.callback_query(F.data == "questions_ai")
async def callback_questions_ai(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Umumiy savollar - AI orqali"""
    await callback_query.answer()
    
    # State ni o'rnatish
    await state.set_state(QuestionStates.waiting_for_question)
    
    # Savol-javob xabarlari ro'yxatini boshlash
    await state.update_data(question_message_ids=[], question_type="ai")
    
    text = """🤖 <b>Umumiy savollar</b>

Bu yerda istalgan savolingizni berishingiz mumkin:

💡 <b>Savollar misoli:</b>
   • Jalyuzlar qanday turlari bor?
   • Plis va dikey o'rtasida qanday farq bor?
   • Qaysi jalyuz issiq ob-havo uchun yaxshi?
   • Kombo jalyuz nima?
   • Qanday jalyuz tanlashni maslahat berasiz?

✍️ Endi savolingizni yozing:"""
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="questions_back"
                )
            ]
        ]
    )
    
    chat_id = callback_query.message.chat.id
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        # Track message
        data = await state.get_data()
        question_message_ids = data.get("question_message_ids", [])
        question_message_ids.append(callback_query.message.message_id)
        await state.update_data(question_message_ids=question_message_ids)
    except Exception:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        data = await state.get_data()
        question_message_ids = data.get("question_message_ids", [])
        question_message_ids.append(sent.message_id)
        await state.update_data(question_message_ids=question_message_ids)


@router.message(QuestionStates.waiting_for_question, F.text)
async def handle_text_question(message: Message, state: FSMContext, bot: Bot):
    await _process_question(message, state=state, bot=bot, text=message.text)


@router.message(QuestionStates.waiting_for_question, F.photo)
async def handle_photo_question(message: Message, state: FSMContext, bot: Bot):
    """Handle photo questions - analyze image and find matching models"""
    # Agar AI rejimi bo'lsa, rasm qabul qilinmaydi
    data = await state.get_data()
    question_type = data.get("question_type", "database")
    
    if question_type == "ai":
        # AI rejimida faqat matn savollari qabul qilinadi
        question_message_ids = data.get("question_message_ids", [])
        question_message_ids.append(message.message_id)
        
        error_msg = await message.answer(
            "❌ Bu rejimda faqat matn savollari qabul qilinadi.\n\n"
            "Agar rasm bo'yicha qidirishni xohlasangiz, '🔙 Orqaga' tugmasini bosing va "
            "'📦 Baza bo'yicha qidiruv' rejimini tanlang.",
            reply_markup=question_keyboard()
        )
        question_message_ids.append(error_msg.message_id)
        await state.update_data(question_message_ids=question_message_ids)
        return
    
    # Database rejimida rasm qabul qilinadi
    await process_question_with_db_and_image(message, state=state, bot=bot)


@router.message(QuestionStates.waiting_for_question)
async def handle_invalid_question(message: Message):
    await message.answer("❌ Savolingizni matn yoki rasm ko'rinishida yuboring.")


async def process_question_with_db_and_image(message: Message, state: FSMContext, bot: Bot):
    """
    Process question with image - analyze image and find matching models.
    Main function for image-based questions.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    try:
        # Track message
        data = await state.get_data()
        question_message_ids = data.get("question_message_ids", [])
        question_message_ids.append(message.message_id)
        await state.update_data(question_message_ids=question_message_ids)
        
        # Get photo
        photo = message.photo[-1] if message.photo else None
        if not photo:
            await message.answer("❌ Rasm topilmadi. Iltimos, rasm yuboring.")
            return
        
        # Send waiting message
        waiting_msg = await bot.send_message(
            chat_id=chat_id,
            text="⏳ Rasm tahlil qilinmoqda, iltimos kuting..."
        )
        waiting_message_id = waiting_msg.message_id
        question_message_ids.append(waiting_message_id)
        await state.update_data(question_message_ids=question_message_ids)
        
        # Download image
        file_info = await bot.get_file(photo.file_id)
        image_bytes = await bot.download_file(file_info.file_path)
        image_data = image_bytes.read() if hasattr(image_bytes, 'read') else image_bytes
        
        # Analyze image
        features = await process_image_and_match_models(image_data)
        
        if not features:
            # Delete waiting message
            try:
                await bot.delete_message(chat_id=chat_id, message_id=waiting_message_id)
            except Exception:
                pass
            
            error_msg = await bot.send_message(
                chat_id=chat_id,
                text="❌ Rasmni tahlil qilishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.",
                reply_markup=question_keyboard()
            )
            question_message_ids.append(error_msg.message_id)
            await state.update_data(question_message_ids=question_message_ids)
            await state.set_state(QuestionStates.waiting_for_question)
            return
        
        # Search database by features
        results = search_by_image_features(features)
        
        # Delete waiting message
        try:
            await bot.delete_message(chat_id=chat_id, message_id=waiting_message_id)
        except Exception:
            pass
        
        if results:
            # High confidence - show direct results
            if len(results) == 1 or results[0].get("match_score", 0) >= 70:
                # Single high-confidence match
                answer_text, image_url = format_database_answer(results[:1], "", user_id)
                
                if image_url:
                    try:
                        # RASM YUBORISH PRIORITETI: 1) file_id, 2) URL
                        # Get code from first result
                        first_code = results[0].get("code", "") if results else ""
                        image_file_id = get_file_id_for_code(first_code) if first_code else None
                        
                        if image_file_id:
                            # 1. file_id mavjud - eng tez variant
                            result_msg = await bot.send_photo(
                                chat_id=chat_id,
                                photo=image_file_id,
                                caption=answer_text,
                                reply_markup=question_keyboard(),
                                parse_mode="HTML"
                            )
                        else:
                            # 2. URL fallback
                            sheet_service = GoogleSheetService()
                            converted_url = sheet_service._convert_google_drive_link(image_url)
                            result_msg = await bot.send_photo(
                                chat_id=chat_id,
                                photo=converted_url,
                                caption=answer_text,
                                reply_markup=question_keyboard(),
                                parse_mode="HTML"
                            )
                    except Exception as e:
                        logger.error(f"Error sending photo: {e}")
                        result_msg = await bot.send_message(
                            chat_id=chat_id,
                            text=answer_text,
                            reply_markup=question_keyboard(),
                            parse_mode="HTML"
                        )
                else:
                    result_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=answer_text,
                        reply_markup=question_keyboard(),
                        parse_mode="HTML"
                    )
            else:
                # Multiple matches - show similar models
                answer_text, image_url = format_database_answer(results[:5], "", user_id)
                header = "Shunga eng o'xshash modellar:\n\n"
                
                if image_url:
                    try:
                        # RASM YUBORISH PRIORITETI: 1) file_id, 2) URL
                        # Get code from first result
                        first_code = results[0].get("code", "") if results else ""
                        image_file_id = get_file_id_for_code(first_code) if first_code else None
                        
                        if image_file_id:
                            # 1. file_id mavjud - eng tez variant
                            result_msg = await bot.send_photo(
                                chat_id=chat_id,
                                photo=image_file_id,
                                caption=header + answer_text,
                                reply_markup=question_keyboard(),
                                parse_mode="HTML"
                            )
                        else:
                            # 2. URL fallback
                            sheet_service = GoogleSheetService()
                            converted_url = sheet_service._convert_google_drive_link(image_url)
                            result_msg = await bot.send_photo(
                                chat_id=chat_id,
                                photo=converted_url,
                                caption=header + answer_text,
                                reply_markup=question_keyboard(),
                                parse_mode="HTML"
                            )
                    except Exception as e:
                        logger.error(f"Error sending photo: {e}")
                        result_msg = await bot.send_message(
                            chat_id=chat_id,
                            text=header + answer_text,
                            reply_markup=question_keyboard(),
                            parse_mode="HTML"
                        )
                else:
                    result_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=header + answer_text,
                        reply_markup=question_keyboard(),
                        parse_mode="HTML"
                    )
        else:
            # No matches found
            error_msg = await bot.send_message(
                chat_id=chat_id,
                text="❌ Bazadan aniq topilmadi, lekin shunga o'xshash modellar mavjud emas.\n\nIltimos, boshqa rasm yuborib ko'ring yoki matn orqali qidiring.",
                reply_markup=question_keyboard()
            )
            question_message_ids.append(error_msg.message_id)
            await state.update_data(question_message_ids=question_message_ids)
            await state.set_state(QuestionStates.waiting_for_question)
            return
        
        # Track result message
        question_message_ids.append(result_msg.message_id)
        await state.update_data(question_message_ids=question_message_ids)
        await state.set_state(QuestionStates.waiting_for_question)
        
    except Exception as exc:
        logger.error(f"Error processing image question: {exc}", exc_info=True)
        try:
            await state.set_state(QuestionStates.waiting_for_question)
        except Exception:
            pass


async def _process_question(message: Message, state: FSMContext, bot: Bot, text: Optional[str] = None):
    chat_id = message.chat.id
    
    try:
        # Foydalanuvchi xabarini saqlash (o'chirilmaydi)
        # Foydalanuvchi savolini chatda qoldiramiz
        
        # Foydalanuvchi savol xabar ID sini track qilish
        data = await state.get_data()
        question_message_ids = data.get("question_message_ids", [])
        question_type = data.get("question_type", "database")  # Default: database
        question_message_ids.append(message.message_id)
        await state.update_data(question_message_ids=question_message_ids)
        
        # "Ishlanmoqda..." xabarini yuborish
        waiting_msg = await bot.send_message(
            chat_id=chat_id,
            text="⏳ Savolingiz ishlanmoqda, iltimos kuting..."
        )
        waiting_message_id = waiting_msg.message_id
        
        # Waiting message ID ni ham track qilish
        question_message_ids.append(waiting_message_id)
        await state.update_data(question_message_ids=question_message_ids)

        # 1) SKIDKA SO'RALGANDA - MAXSUS JAVOB
        query_lower = text.lower() if text else ""
        if any(word in query_lower for word in ["skidka", "discount", "chegirma", "скидка"]):
            # Delete waiting message
            try:
                await bot.delete_message(chat_id=chat_id, message_id=waiting_message_id)
                question_message_ids.remove(waiting_message_id)
            except Exception:
                pass
            
            skidka_msg = await bot.send_message(
                chat_id=chat_id,
                text="Skidka bo'limidan ko'ring, agar ko'rinmasa admin ruxsat kerak.",
                reply_markup=question_keyboard()
            )
            question_message_ids.append(skidka_msg.message_id)
            await state.update_data(question_message_ids=question_message_ids)
            await state.set_state(QuestionStates.waiting_for_question)
            return
        
        # AGAR "AI" REJIMI TANLANGAN BO'LSA - FAQAT AI JAVOB BERADI
        if question_type == "ai":
            # Waiting xabarini o'chirish
            try:
                await bot.delete_message(chat_id=chat_id, message_id=waiting_message_id)
                question_message_ids.remove(waiting_message_id)
            except Exception:
                pass
            
            # Faqat AI javob beradi
            try:
                ai_service = get_openai_service()
            except ValueError:
                error_msg = await bot.send_message(
                    chat_id=chat_id,
                    text="❌ OPENAI_API_KEY sozlanmagan. Admin bilan bog'laning.",
                    reply_markup=question_keyboard()
                )
                question_message_ids.append(error_msg.message_id)
                await state.update_data(question_message_ids=question_message_ids)
                await state.set_state(QuestionStates.waiting_for_question)
                return

            try:
                response_text = await ai_service.ai_generate_answer(text=text)
            except Exception as exc:
                logger.error("Savolga javob generatsiyasida xatolik: %s", exc, exc_info=True)
                error_msg = await bot.send_message(
                    chat_id=chat_id,
                    text="❌ AI javob berishda xatolik yuz berdi. Keyinroq urinib ko'ring.",
                    reply_markup=question_keyboard()
                )
                question_message_ids.append(error_msg.message_id)
                await state.update_data(question_message_ids=question_message_ids)
                await state.set_state(QuestionStates.waiting_for_question)
                return

            # AI javobini yuborish
            result_msg = await bot.send_message(
                chat_id=chat_id,
                text=response_text or "❗️ Javob olinmadi.",
                reply_markup=question_keyboard()
            )
            
            question_message_ids.append(result_msg.message_id)
            await state.update_data(question_message_ids=question_message_ids)
            await state.set_state(QuestionStates.waiting_for_question)
            return
        
        # AGAR "DATABASE" REJIMI TANLANGAN BO'LSA - FAQAT BAZA QIDIRADI
        # 2) CODE QUERY DETECTION - FIRST PRIORITY
        is_code_query_detected = is_code_query(text) if text else False
        
        if is_code_query_detected:
            logger.info(f"AI search routed to DB - code query detected: {text}")
            try:
                code_results = search_models_by_code(text)
                
                # Delete waiting message
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=waiting_message_id)
                    question_message_ids.remove(waiting_message_id)
                except Exception:
                    pass
                
                if not code_results:
                    # No results
                    result_msg = await bot.send_message(
                        chat_id=chat_id,
                        text="Bunday kod topilmadi, o'xshashlari ham yo'q.",
                        reply_markup=question_keyboard()
                    )
                elif len(code_results) > 15:
                    # Too many results - ask to clarify
                    codes_list = ", ".join([r.get("code", "") for r in code_results[:10]])
                    result_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=f"Juda ko'p natija topildi. Iltimos, aniqroq yozing.\n\nTopilganlar: {codes_list}...",
                        reply_markup=question_keyboard()
                    )
                else:
                    # 1-15 results - show all
                    answer_text, first_image_url, all_image_urls = format_code_search_results(code_results)
                    
                    # Send first result with image if available
                    if first_image_url:
                        try:
                            # RASM YUBORISH PRIORITETI: 1) file_id, 2) URL
                            # Get code from first result
                            first_code = code_results[0].get("code", "") if code_results else ""
                            image_file_id = get_file_id_for_code(first_code) if first_code else None
                            
                            if image_file_id:
                                # 1. file_id mavjud - eng tez variant
                                result_msg = await bot.send_photo(
                                    chat_id=chat_id,
                                    photo=image_file_id,
                                    caption=answer_text,
                                    reply_markup=question_keyboard(),
                                    parse_mode="HTML"
                                )
                            else:
                                # 2. URL fallback
                                sheet_service = GoogleSheetService()
                                converted_url = sheet_service._convert_google_drive_link(first_image_url)
                                result_msg = await bot.send_photo(
                                    chat_id=chat_id,
                                    photo=converted_url,
                                    caption=answer_text,
                                    reply_markup=question_keyboard(),
                                    parse_mode="HTML"
                                )
                        except Exception as e:
                            logger.error(f"Error sending photo: {e}")
                            result_msg = await bot.send_message(
                                chat_id=chat_id,
                                text=answer_text,
                                reply_markup=question_keyboard(),
                                parse_mode="HTML"
                            )
                    else:
                        result_msg = await bot.send_message(
                            chat_id=chat_id,
                            text=answer_text,
                            reply_markup=question_keyboard(),
                            parse_mode="HTML"
                        )
                
                # Track result message
                question_message_ids.append(result_msg.message_id)
                await state.update_data(question_message_ids=question_message_ids)
                await state.set_state(QuestionStates.waiting_for_question)
                return
                
            except Exception as e:
                logger.error(f"Error in code search: {e}")
                # Fall through to AI if code search fails
        
        # 3) AVVAL BAZADAN QIDIRISH (faqat bazaga oid savollar uchun)
        database_results = []
        use_database = False
        database_searched = False
        
        if is_question_about_database(text):
            try:
                database_searched = True
                database_results = search_in_database(text)
                if database_results:
                    use_database = True
                    logger.info(f"Found {len(database_results)} database results for query: {text}")
                else:
                    logger.info(f"No database results found for query: {text}")
            except Exception as e:
                logger.error(f"Error searching database: {e}")
                # Database xatolik bo'lsa ham AI ga o'tadi
        
        # 4) AGAR BAZADAN TOPILSA - BAZADAN JAVOB
        if use_database and database_results:
            # Waiting xabarini o'chirish
            try:
                await bot.delete_message(chat_id=chat_id, message_id=waiting_message_id)
                question_message_ids.remove(waiting_message_id)
            except Exception:
                pass
            
            user_id = message.from_user.id
            sheet_service = GoogleSheetService()
            
            # Har bir model uchun alohida rasm + matn yuborish
            # Limit to 10 results
            limited_results = database_results[:10]
            last_message_id = None
            
            for idx, model in enumerate(limited_results):
                code = model.get("code", "")
                quantity = model.get("quantity", "")
                rang = model.get("rang", "").strip()
                tur = model.get("tur", "").strip()
                naqsh = model.get("naqsh", "").strip()
                kolleksiya = model.get("kolleksiya", "").strip()
                image_url = model.get("image_url", "").strip()
                
                # Format matn: 🔹 CODE — QOLDIQ
                quantity_str = "qolmadi"
                if quantity:
                    quantity_clean = str(quantity).strip()
                    if quantity_clean:
                        try:
                            qty_float = float(quantity_clean.replace(",", "."))
                            if qty_float > 0:
                                quantity_str = f"{quantity_clean} m"
                        except (ValueError, TypeError):
                            if quantity_clean.lower() not in ["0", "0.0", "0,0", ""]:
                                quantity_str = f"{quantity_clean} m"
                
                model_text = f"🔹 {code} — {quantity_str}\n"
                
                # Format: TUR | RANG | NAQSH | KOLLEKSIYA
                info_parts = []
                if tur:
                    info_parts.append(tur)
                if rang:
                    info_parts.append(rang)
                if naqsh:
                    info_parts.append(naqsh)
                if kolleksiya:
                    info_parts.append(kolleksiya)
                
                if info_parts:
                    model_text += "   " + " | ".join(info_parts)
                
                # Faqat oxirgi model uchun reply_markup
                is_last = (idx == len(limited_results) - 1)
                reply_markup = question_keyboard() if is_last else None
                
                # AVVAL RASM yuborish (agar mavjud bo'lsa)
                if image_url:
                    try:
                        # RASM YUBORISH PRIORITETI: 1) file_id, 2) URL
                        image_file_id = get_file_id_for_code(code) if code else None
                        
                        if image_file_id:
                            # 1. file_id mavjud - eng tez variant
                            photo_msg = await bot.send_photo(
                                chat_id=chat_id,
                                photo=image_file_id,
                                reply_markup=None  # Rasmda reply_markup yo'q
                            )
                        else:
                            # 2. URL fallback
                            converted_url = sheet_service._convert_google_drive_link(image_url)
                            photo_msg = await bot.send_photo(
                                chat_id=chat_id,
                                photo=converted_url,
                                reply_markup=None  # Rasmda reply_markup yo'q
                            )
                        last_message_id = photo_msg.message_id
                        question_message_ids.append(photo_msg.message_id)
                    except Exception as e:
                        logger.error(f"Error sending photo for model {code}: {e}")
                        # Rasm yuborishda xatolik bo'lsa, faqat matn yuboriladi
                
                # Keyin MATN yuborish
                text_msg = await bot.send_message(
                    chat_id=chat_id,
                    text=model_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                last_message_id = text_msg.message_id
                question_message_ids.append(text_msg.message_id)
            
            # Javob xabar ID sini track qilish (oxirgi xabar)
            if last_message_id:
                await state.update_data(question_message_ids=question_message_ids)
            await state.set_state(QuestionStates.waiting_for_question)
            return
        
        # 2.5) AGAR BAZADAN TOPILMASA - "TOPILMADI" XABARI
        # DATABASE rejimida AI javob bermaydi, faqat bazadan qidiradi
        if database_searched and not use_database and not database_results:
            # Waiting xabarini o'chirish
            try:
                await bot.delete_message(chat_id=chat_id, message_id=waiting_message_id)
                question_message_ids.remove(waiting_message_id)
            except Exception:
                pass
            
            # "Bazada topilmadi" xabarini yuborish
            not_found_msg = await bot.send_message(
                chat_id=chat_id,
                text="❌ Bazada mos model topilmadi.\nIltimos, model kodi, rang yoki turini aniqroq yozing.",
                reply_markup=question_keyboard()
            )
            question_message_ids.append(not_found_msg.message_id)
            await state.update_data(question_message_ids=question_message_ids)
            await state.set_state(QuestionStates.waiting_for_question)
            return
        
        # DATABASE rejimi - AI javob bermaydi
        # Faqat baza qidiruvdan keyin to'xtatamiz
        # State ni qayta waiting_for_question holatiga qaytarish (ketma-ket savollar uchun)
        await state.set_state(QuestionStates.waiting_for_question)
        
    except Exception as exc:
        logger.error("Savolni qayta ishlashda xatolik: %s", exc, exc_info=True)
        # Xatolik bo'lsa ham state ni qayta tiklash
        try:
            await state.set_state(QuestionStates.waiting_for_question)
        except Exception:
            pass
    finally:
        # Har doim state ni waiting_for_question holatiga qaytarish (xatolik bo'lsa ham)
        # Bu "Update is not handled" muammosini oldini oladi
        try:
            current_state = await state.get_state()
            if current_state != QuestionStates.waiting_for_question:
                await state.set_state(QuestionStates.waiting_for_question)
        except Exception:
            pass


@router.callback_query(F.data == "questions_back")
async def callback_questions_back(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    """Orqaga bosilganda: Yordam menyusiga qaytish"""
    chat_id = callback_query.message.chat.id
    
    # Track qilingan barcha xabarlarni olish
    data = await state.get_data()
    question_message_ids = data.get("question_message_ids", [])
    
    # Barcha track qilingan xabarlarni o'chirish (foydalanuvchi savollari va javoblari)
    for msg_id in question_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            # Xabar allaqachon o'chirilgan yoki o'chirib bo'lmaydi
            pass
    
    # State ni tozalash
    await state.clear()
    
    # Yordam menyusiga qaytish
    text = """❓ <b>Yordam bo'limi</b>

Quyidagi xizmatlardan birini tanlang:

📦 <b>Baza bo'yicha qidiruv</b>
   • Model kodlari bo'yicha qidirish
   • Rang va tur bo'yicha qidirish
   • Rasm yuborib o'xshash modellarni topish
   • Qoldiq miqdorini bilish

🤖 <b>Umumiy savollar</b>
   • Jalyuzlar haqida ma'lumot
   • Maslahat va tavsiyalar
   • Boshqa savollar"""
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Baza bo'yicha qidiruv",
                    callback_data="questions_database"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤖 Umumiy savollar",
                    callback_data="questions_ai"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Orqaga",
                    callback_data="menu_main"
                )
            ]
        ]
    )
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    await callback_query.answer()

