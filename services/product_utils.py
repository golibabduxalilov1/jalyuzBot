"""
Product utility functions for normalizing codes, parsing quantities, and formatting data.
"""
import re
from typing import Dict, List, Tuple


def normalize_code(code: str) -> str:
    """
    Normalize product code by removing spaces, converting to uppercase, and removing non-alphanumeric characters.
    
    Args:
        code: Product code string (e.g., " bn22-1 ", "8572", "ABC-123")
        
    Returns:
        Normalized code string (e.g., "BN221", "8572", "ABC123")
    """
    if not code:
        return ""
    
    # Remove leading/trailing whitespace and convert to uppercase
    normalized = str(code).strip().upper()
    
    # Remove all spaces
    normalized = re.sub(r'\s+', '', normalized)
    
    # Remove all non-alphanumeric characters (keep only letters and numbers)
    normalized = re.sub(r'[^A-Z0-9]', '', normalized)
    
    return normalized


def generate_fuzzy_code_variants(code: str) -> List[str]:
    """
    Generate fuzzy matching variants for a product code.
    This helps match codes like "1340-1" with "1340-01" bidirectionally.
    
    Args:
        code: Normalized product code (e.g., "13401", "ABC13401", "134001")
        
    Returns:
        List of code variants including the original
        
    Examples:
        "13401" -> ["13401", "134001", "1340001"]
        "134001" -> ["134001", "13401", "1340001"]
        "ABC1" -> ["ABC1", "ABC01", "ABC001"]
        "1340" -> ["1340"]  # No variants if doesn't end with 1-6 digits
    """
    if not code:
        return []
    
    variants = [code]
    
    # Find trailing numbers in the code
    match = re.search(r'(\d+)$', code)
    if match:
        number_part = match.group(1)
        prefix = code[:match.start()]
        number_len = len(number_part)
        
        # Strategy 1: Add zeros at the BEGINNING (for codes with 1-6 digits at the end)
        # This handles: 1 -> 01, 001, 0001
        if 1 <= number_len <= 6:
            if number_len == 1:
                # Single digit: add 1, 2, and 3 zeros at beginning
                variants.append(prefix + "0" + number_part)      # 1 -> 01
                variants.append(prefix + "00" + number_part)     # 1 -> 001
                variants.append(prefix + "000" + number_part)    # 1 -> 0001
            elif number_len == 2:
                # Two digits: add 1 and 2 zeros at beginning
                variants.append(prefix + "0" + number_part)      # 12 -> 012
                variants.append(prefix + "00" + number_part)     # 12 -> 0012
            elif number_len == 3:
                # Three digits: add 1 and 2 zeros at beginning
                variants.append(prefix + "0" + number_part)      # 123 -> 0123
                variants.append(prefix + "00" + number_part)     # 123 -> 00123
            elif number_len == 4:
                # Four digits: add 1 and 2 zeros at beginning
                variants.append(prefix + "0" + number_part)      # 1234 -> 01234
                variants.append(prefix + "00" + number_part)     # 1234 -> 001234
            elif number_len == 5:
                # Five digits: add 1 and 2 zeros at beginning
                variants.append(prefix + "0" + number_part)      # 13401 -> 013401
                variants.append(prefix + "00" + number_part)     # 13401 -> 0013401
            elif number_len == 6:
                # Six digits: add 1 zero at beginning
                variants.append(prefix + "0" + number_part)      # 134001 -> 0134001
        
        # Strategy 2: Add zeros INSIDE the number (for last 1-2 digits)
        # This handles: 13401 -> 134001 (add 0 before last digit)
        # Extract last 1-2 digits and add 0 before them
        if number_len >= 2:
            # Try adding 0 before last digit: 13401 -> 134001
            last_digit = number_part[-1]
            rest = number_part[:-1]
            variants.append(prefix + rest + "0" + last_digit)
            
            # Try adding 0 before last 2 digits: 13401 -> 130401
            if number_len >= 3:
                last_two = number_part[-2:]
                rest = number_part[:-2]
                variants.append(prefix + rest + "0" + last_two)
        
        # Strategy 3: Remove leading zeros (bidirectional matching!)
        # This allows "134001" to match with "13401"
        # "001" -> ["1", "01", "001"]
        # "0001" -> ["1", "01", "001", "0001"]
        if number_part.startswith("0") and len(number_part) > 1:
            # Remove leading zeros one by one
            stripped = number_part.lstrip("0")
            if stripped:  # If not all zeros
                # Add fully stripped version first (highest priority)
                variants.append(prefix + stripped)
                
                # Add intermediate variants (with some zeros removed)
                # "0001" -> also add "001" and "01"
                zeros_count = len(number_part) - len(stripped)
                for i in range(1, zeros_count):
                    variant_num = "0" * i + stripped
                    variants.append(prefix + variant_num)
        
        # Strategy 4: Remove zeros from INSIDE the number
        # This handles: 134001 -> 13401 (remove 0 before last digit)
        if "0" in number_part and number_len >= 3:
            # Try removing 0 before last digit: 134001 -> 13401
            if number_part[-2] == "0":
                last_digit = number_part[-1]
                rest = number_part[:-2]
                variants.append(prefix + rest + last_digit)
            
            # Try removing 0 before last 2 digits: 130401 -> 13401
            if number_len >= 4 and number_part[-3] == "0":
                last_two = number_part[-2:]
                rest = number_part[:-3]
                variants.append(prefix + rest + last_two)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_variants = []
    for variant in variants:
        if variant not in seen:
            seen.add(variant)
            unique_variants.append(variant)
    
    return unique_variants


def normalize_razmer(razmer: str) -> str:
    """
    Normalize razmer (size) by removing leading/trailing whitespace and normalizing whitespace.
    This ensures consistent comparison of razmer values.
    
    Args:
        razmer: Razmer string (e.g., " 1.40×2.00 ", "1.40 x 2.00", "1,40×2,00")
        
    Returns:
        Normalized razmer string with trimmed whitespace
    """
    if not razmer:
        return ""
    
    # Remove leading/trailing whitespace
    normalized = str(razmer).strip()
    
    # Normalize multiple spaces to single space
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized


def parse_quantity(quantity_str: str) -> float:
    """
    Parse quantity string to float, handling both comma and dot as decimal separators.
    Also removes "KV", "kv" and other text suffixes.
    
    Args:
        quantity_str: Quantity string (e.g., "69,75", " 10.5 ", "7.5 KV", "98.8 kv")
        
    Returns:
        Float value
    """
    if not quantity_str:
        return 0.0
    
    # Remove whitespace
    cleaned = str(quantity_str).strip()
    
    # Remove "KV", "kv" and other common suffixes (case insensitive)
    cleaned = re.sub(r'\s*(kv|кв|m2|m²|sq|sqm)\s*$', '', cleaned, flags=re.IGNORECASE)
    
    # Remove any remaining non-numeric characters except comma, dot, and minus
    # This handles cases like "7.5 KV" -> "7.5"
    cleaned = re.sub(r'[^\d.,\-]', '', cleaned)
    
    # Replace comma with dot for decimal separator
    cleaned = cleaned.replace(',', '.')
    
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def format_quantity(quantity: float) -> str:
    """
    Format quantity value for display.
    
    Args:
        quantity: Quantity value as float
        
    Returns:
        Formatted string (e.g., "69.75 kv", "150 kv")
    """
    if quantity is None:
        return "0 kv"
    
    try:
        qty_float = float(quantity)
        # Format with 2 decimal places if needed, otherwise as integer
        if qty_float == int(qty_float):
            return f"{int(qty_float)} kv"
        else:
            return f"{qty_float:.2f} kv"
    except (ValueError, TypeError):
        return "0 kv"


def get_product_total(records: List[Dict], normalized_code: str, partial_match: bool = False) -> Tuple[float, List[Dict]]:
    """
    Get total quantity for a product code and all matching rows.
    Supports both exact match and partial match (e.g., "3209-8" matches "JL3209-8").
    
    Args:
        records: List of normalized record dictionaries
        normalized_code: Normalized product code to search for
        partial_match: If True, also matches codes that end with the search code or contain it
        
    Returns:
        Tuple of (total_quantity, list_of_matched_rows)
    """
    if not records or not normalized_code:
        return 0.0, []
    
    matched_rows = []
    total = 0.0
    
    for record in records:
        record_normalized = record.get("code_normalized", "")
        code_original = record.get("code", "")
        
        # Exact match
        if record_normalized == normalized_code:
            matched_rows.append(record)
            # Try to get quantity from various possible fields
            qty = record.get("quantity", 0)
            if isinstance(qty, str):
                qty = parse_quantity(qty)
            elif not isinstance(qty, (int, float)):
                qty = 0.0
            total += float(qty)
        # Partial match: search code is contained in record code (e.g., "3209-8" in "JL3209-8")
        elif partial_match:
            # Check if search code ends with record code or record code ends with search code
            # Or if search code is contained in record code
            if (normalized_code in record_normalized or 
                record_normalized.endswith(normalized_code) or 
                normalized_code.endswith(record_normalized)):
                # Make sure the match is meaningful (at least 3 characters)
                if len(normalized_code) >= 3 or len(record_normalized) >= 3:
                    matched_rows.append(record)
                    qty = record.get("quantity", 0)
                    if isinstance(qty, str):
                        qty = parse_quantity(qty)
                    elif not isinstance(qty, (int, float)):
                        qty = 0.0
                    total += float(qty)
    
    return total, matched_rows

