"""
Input validation functions for bot security.
Validates user input to prevent injection attacks and ensure data integrity.
"""
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ==================== PRODUCT CODE VALIDATION ====================

def validate_product_code(code: str) -> Tuple[bool, Optional[str]]:
    """
    Validate product code format.
    
    Args:
        code: User input product code
        
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
        
    Rules:
        - Length: 1-50 characters
        - Allowed: letters, digits, spaces, dash, dot, slash, underscore
        - Not allowed: special characters, SQL injection patterns
    
    Examples:
        >>> validate_product_code("ABC-123")
        (True, None)
        >>> validate_product_code("SMF 02")
        (True, None)
        >>> validate_product_code("")
        (False, "Kod bo'sh bo'lishi mumkin emas")
        >>> validate_product_code("A" * 100)
        (False, "Kod juda uzun (maksimum 50 belgi)")
    """
    if not code:
        return False, "Kod bo'sh bo'lishi mumkin emas"
    
    if not isinstance(code, str):
        return False, "Kod matn formatida bo'lishi kerak"
    
    # Strip whitespace
    code = code.strip()
    
    if not code:
        return False, "Kod bo'sh bo'lishi mumkin emas"
    
    # Check length
    if len(code) > 50:
        return False, "Kod juda uzun (maksimum 50 belgi)"
    
    if len(code) < 1:
        return False, "Kod juda qisqa (minimum 1 belgi)"
    
    # Check allowed characters: letters, digits, spaces, -, ., /, _
    # Also allow Cyrillic letters for Russian/Uzbek codes
    allowed_pattern = r'^[a-zA-Z0-9а-яА-ЯёЁ\s\-\./_]+$'
    if not re.match(allowed_pattern, code):
        return False, "Kod faqat harf, raqam, bo'sh joy, -, ., /, _ belgilardan iborat bo'lishi mumkin"
    
    # Check for SQL injection patterns (basic)
    sql_patterns = [
        r'[\'\";]',  # Quotes and semicolon
        r'--',       # SQL comment
        r'/\*',      # SQL comment
        r'\*/',      # SQL comment
        r'union\s+select',  # SQL injection
        r'drop\s+table',    # SQL injection
        r'delete\s+from',   # SQL injection
    ]
    
    code_lower = code.lower()
    for pattern in sql_patterns:
        if re.search(pattern, code_lower):
            return False, "Kod noto'g'ri belgilar yoki so'zlarni o'z ichiga oladi"
    
    return True, None


# ==================== TEXT INPUT VALIDATION ====================

def validate_text_input(text: str, max_length: int = 1000, field_name: str = "Matn") -> Tuple[bool, Optional[str]]:
    """
    Validate text input (messages, descriptions, etc).
    
    Args:
        text: User input text
        max_length: Maximum allowed length
        field_name: Field name for error messages
        
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
        
    Rules:
        - Length: 1-max_length characters
        - Not allowed: excessive whitespace, control characters
    
    Examples:
        >>> validate_text_input("Normal text")
        (True, None)
        >>> validate_text_input("")
        (False, "Matn bo'sh bo'lishi mumkin emas")
        >>> validate_text_input("A" * 2000)
        (False, "Matn juda uzun (maksimum 1000 belgi)")
    """
    if not text:
        return False, f"{field_name} bo'sh bo'lishi mumkin emas"
    
    if not isinstance(text, str):
        return False, f"{field_name} matn formatida bo'lishi kerak"
    
    # Strip whitespace
    text = text.strip()
    
    if not text:
        return False, f"{field_name} bo'sh bo'lishi mumkin emas"
    
    # Check length
    if len(text) > max_length:
        return False, f"{field_name} juda uzun (maksimum {max_length} belgi)"
    
    # Check for control characters (except newline, tab, carriage return)
    control_chars = [chr(i) for i in range(32) if i not in [9, 10, 13]]
    for char in control_chars:
        if char in text:
            return False, f"{field_name} noto'g'ri belgilarni o'z ichiga oladi"
    
    return True, None


# ==================== USER ID VALIDATION ====================

def validate_user_id(user_id: any) -> Tuple[bool, Optional[str]]:
    """
    Validate Telegram user ID.
    
    Args:
        user_id: User ID (can be int or str)
        
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
        
    Rules:
        - Must be positive integer
        - Range: 1 to 2147483647 (Telegram limit)
    
    Examples:
        >>> validate_user_id(123456789)
        (True, None)
        >>> validate_user_id("123456789")
        (True, None)
        >>> validate_user_id(-1)
        (False, "User ID musbat son bo'lishi kerak")
        >>> validate_user_id("abc")
        (False, "User ID raqam bo'lishi kerak")
    """
    if user_id is None:
        return False, "User ID bo'sh bo'lishi mumkin emas"
    
    # Convert to int
    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        return False, "User ID raqam bo'lishi kerak"
    
    # Check range
    if user_id_int < 1:
        return False, "User ID musbat son bo'lishi kerak"
    
    # Telegram user ID limit: 2^31 - 1
    if user_id_int > 2147483647:
        return False, "User ID juda katta"
    
    return True, None


# ==================== QUANTITY VALIDATION ====================

def validate_quantity(quantity: any) -> Tuple[bool, Optional[str]]:
    """
    Validate product quantity.
    
    Args:
        quantity: Quantity value (can be int, float, or str)
        
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
        
    Rules:
        - Must be non-negative number
        - Can be integer or float
        - Range: 0 to 1000000
    
    Examples:
        >>> validate_quantity(10)
        (True, None)
        >>> validate_quantity("10.5")
        (True, None)
        >>> validate_quantity(-1)
        (False, "Qoldiq manfiy bo'lishi mumkin emas")
        >>> validate_quantity("abc")
        (False, "Qoldiq raqam bo'lishi kerak")
    """
    if quantity is None or quantity == "":
        # Empty quantity is allowed (means out of stock)
        return True, None
    
    # Convert to float
    try:
        quantity_float = float(str(quantity).replace(",", "."))
    except (ValueError, TypeError):
        return False, "Qoldiq raqam bo'lishi kerak"
    
    # Check range
    if quantity_float < 0:
        return False, "Qoldiq manfiy bo'lishi mumkin emas"
    
    if quantity_float > 1000000:
        return False, "Qoldiq juda katta"
    
    return True, None


# ==================== URL VALIDATION ====================

def validate_url(url: str, field_name: str = "URL") -> Tuple[bool, Optional[str]]:
    """
    Validate URL format (basic validation).
    
    Args:
        url: URL string
        field_name: Field name for error messages
        
    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
        
    Rules:
        - Must start with http:// or https://
        - Length: 10-2000 characters
        - No whitespace
    
    Examples:
        >>> validate_url("https://example.com/image.jpg")
        (True, None)
        >>> validate_url("http://example.com")
        (True, None)
        >>> validate_url("not a url")
        (False, "URL http:// yoki https:// bilan boshlanishi kerak")
    """
    if not url:
        # Empty URL is allowed (optional field)
        return True, None
    
    if not isinstance(url, str):
        return False, f"{field_name} matn formatida bo'lishi kerak"
    
    # Strip whitespace
    url = url.strip()
    
    if not url:
        return True, None
    
    # Check length
    if len(url) < 10:
        return False, f"{field_name} juda qisqa"
    
    if len(url) > 2000:
        return False, f"{field_name} juda uzun (maksimum 2000 belgi)"
    
    # Check for whitespace
    if ' ' in url:
        return False, f"{field_name} bo'sh joy o'z ichiga olmaydi"
    
    # Check protocol
    if not (url.startswith('http://') or url.startswith('https://')):
        return False, f"{field_name} http:// yoki https:// bilan boshlanishi kerak"
    
    return True, None


# ==================== SANITIZATION FUNCTIONS ====================

def sanitize_html(text: str) -> str:
    """
    Remove HTML tags from text to prevent XSS.
    
    Args:
        text: Input text
        
    Returns:
        str: Text without HTML tags
        
    Examples:
        >>> sanitize_html("<script>alert('xss')</script>Hello")
        "Hello"
        >>> sanitize_html("Normal text")
        "Normal text"
    """
    if not text:
        return ""
    
    # Remove HTML tags
    clean_text = re.sub(r'<[^>]+>', '', text)
    return clean_text.strip()


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent directory traversal.
    
    Args:
        filename: Input filename
        
    Returns:
        str: Safe filename
        
    Examples:
        >>> sanitize_filename("../../etc/passwd")
        "etcpasswd"
        >>> sanitize_filename("normal_file.txt")
        "normal_file.txt"
    """
    if not filename:
        return "unnamed"
    
    # Remove directory separators
    safe_name = filename.replace('/', '').replace('\\', '').replace('..', '')
    
    # Remove special characters
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '', safe_name)
    
    # Limit length
    if len(safe_name) > 255:
        safe_name = safe_name[:255]
    
    return safe_name or "unnamed"


# ==================== LOGGING ====================

def log_validation_error(field_name: str, value: any, error_message: str):
    """Log validation error for security monitoring."""
    logger.warning(
        f"Validation failed - Field: {field_name}, "
        f"Value: {str(value)[:50]}, Error: {error_message}"
    )


# ==================== HELPER FUNCTIONS ====================

def is_valid_telegram_username(username: str) -> bool:
    """
    Check if string is valid Telegram username format.
    
    Args:
        username: Username string (with or without @)
        
    Returns:
        bool: True if valid format
        
    Rules:
        - 5-32 characters
        - Alphanumeric and underscores only
        - Can't start with digit
    
    Examples:
        >>> is_valid_telegram_username("@username")
        True
        >>> is_valid_telegram_username("user_name")
        True
        >>> is_valid_telegram_username("123user")
        False
    """
    if not username:
        return False
    
    # Remove @ if present
    username = username.lstrip('@')
    
    # Check length
    if len(username) < 5 or len(username) > 32:
        return False
    
    # Check format: alphanumeric and underscore, can't start with digit
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', username):
        return False
    
    return True

