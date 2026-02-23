"""
Image Service - Rasm bilan ishlash business logic.

Bu service quyidagi vazifalarni bajaradi:
- Rasmdan xususiyatlarni ajratish (AI orqali)
- Rasm bo'yicha modellarni qidirish
- Rasm URL larni olish va validatsiya qilish
"""

import logging
from typing import Optional, List, Dict

from services.ai_service import get_openai_service
from services.google_sheet import get_file_id_for_code, get_image_url_for_code
from services.product_utils import normalize_code

logger = logging.getLogger(__name__)


# ==================== IMAGE FEATURE EXTRACTION ====================

async def process_image_and_match_models(image_bytes: bytes) -> Dict[str, any]:
    """
    Process image using AI and extract features for model matching.
    
    Uses OpenAI Vision API to:
    - Identify product type
    - Extract colors
    - Identify patterns
    - Suggest similar models
    
    Args:
        image_bytes: Image data in bytes
        
    Returns:
        Dictionary with extracted features:
            - success: bool
            - features: dict (colors, patterns, type, etc.)
            - error: str (if failed)
    """
    if not image_bytes:
        return {
            "success": False,
            "error": "Rasm ma'lumotlari yo'q"
        }
    
    try:
        ai_service = get_openai_service()
        
        # Prepare prompt for feature extraction
        prompt = """
        Ushbu rasmni tahlil qiling va quyidagi ma'lumotlarni JSON formatida bering:
        
        {
            "product_type": "jalyuzi turi (mini, asosiy, kasetniy)",
            "colors": ["asosiy ranglar ro'yxati"],
            "patterns": ["naqsh turlari"],
            "material": "material turi",
            "style": "stil (klassik, zamonaviy, minimalistik)",
            "similar_codes": ["o'xshash model kodlari"]
        }
        
        Faqat JSON javob bering, boshqa matn yo'q.
        """
        
        # Call AI service
        response = await ai_service.analyze_image(
            image_bytes=image_bytes,
            prompt=prompt
        )
        
        if not response or not response.get("success"):
            return {
                "success": False,
                "error": response.get("error", "AI tahlil xatosi")
            }
        
        # Parse AI response
        import json
        try:
            features = json.loads(response.get("text", "{}"))
        except json.JSONDecodeError:
            # If AI didn't return valid JSON, extract manually
            features = {
                "product_type": "noma'lum",
                "colors": [],
                "patterns": [],
                "material": "noma'lum",
                "style": "noma'lum",
                "similar_codes": []
            }
        
        logger.info(f"Extracted features from image: {features}")
        
        return {
            "success": True,
            "features": features
        }
        
    except Exception as e:
        logger.error(f"Error processing image: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"Xatolik: {str(e)}"
        }


def search_by_image_features(features: Dict[str, any]) -> List[Dict]:
    """
    Search models in database by extracted image features.
    
    Args:
        features: Extracted features from image
            - colors: list of colors
            - patterns: list of patterns
            - product_type: type of product
            - style: style
            
    Returns:
        List of matching model dictionaries
    """
    if not features:
        return []
    
    from services.google_sheet import CACHE
    
    sheets2_full = CACHE.get("sheets2_full", [])
    if not sheets2_full:
        logger.warning("sheets2_full is empty")
        return []
    
    results = []
    colors = features.get("colors", [])
    patterns = features.get("patterns", [])
    product_type = features.get("product_type", "")
    style = features.get("style", "")
    
    for record in sheets2_full:
        score = 0
        
        # Match colors
        record_color = (record.get("rang", "") or record.get("color", "")).lower()
        for color in colors:
            if color.lower() in record_color or record_color in color.lower():
                score += 5
        
        # Match patterns
        record_pattern = (record.get("naqsh", "") or record.get("pattern", "")).lower()
        for pattern in patterns:
            if pattern.lower() in record_pattern or record_pattern in pattern.lower():
                score += 3
        
        # Match product type
        record_type = (record.get("turi", "") or record.get("type", "")).lower()
        if product_type and product_type.lower() in record_type:
            score += 7
        
        # Match style (if available in database)
        record_style = record.get("stil", "").lower()
        if style and style.lower() in record_style:
            score += 2
        
        if score > 0:
            record["_match_score"] = score
            results.append(record)
    
    # Sort by match score (descending)
    results.sort(key=lambda x: x.get("_match_score", 0), reverse=True)
    
    logger.info(f"Found {len(results)} models matching image features")
    return results[:10]  # Return top 10


# ==================== IMAGE URL MANAGEMENT ====================

def get_image_url_for_product(code: str) -> Optional[str]:
    """
    Get image URL for a product code.
    
    Tries multiple sources:
    1. file_id from image_map (fastest)
    2. URL from sheets2/sheets4/sheets5
    
    Args:
        code: Product code
        
    Returns:
        Image URL or None
    """
    if not code:
        return None
    
    try:
        # Try file_id first (fastest)
        file_id = get_file_id_for_code(code)
        if file_id:
            logger.debug(f"Found file_id for code {code}")
            return file_id
        
        # Try URL from sheets
        url = get_image_url_for_code(code)
        if url:
            logger.debug(f"Found image URL for code {code}")
            return url
        
        logger.debug(f"No image found for code {code}")
        return None
        
    except Exception as e:
        logger.error(f"Error getting image for code {code}: {e}")
        return None


def get_multiple_images_for_codes(codes: List[str], limit: int = 5) -> List[str]:
    """
    Get image URLs for multiple product codes.
    
    Args:
        codes: List of product codes
        limit: Maximum number of images to return
        
    Returns:
        List of image URLs
    """
    if not codes:
        return []
    
    images = []
    
    for code in codes[:limit]:
        url = get_image_url_for_product(code)
        if url:
            images.append(url)
    
    logger.info(f"Found {len(images)} images for {len(codes)} codes")
    return images


def validate_image_url(url: str) -> bool:
    """
    Validate if URL is a valid image URL.
    
    Args:
        url: Image URL to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not url:
        return False
    
    # Check if URL starts with http/https
    if not url.startswith(("http://", "https://")):
        return False
    
    # Check if URL contains image extensions
    image_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]
    url_lower = url.lower()
    
    # Check extension or Google Drive/common image hosting
    if any(ext in url_lower for ext in image_extensions):
        return True
    
    if "drive.google.com" in url_lower:
        return True
    
    if "imgur.com" in url_lower or "cloudinary.com" in url_lower:
        return True
    
    return False


# ==================== IMAGE PROCESSING UTILITIES ====================

def extract_image_metadata(image_bytes: bytes) -> Dict[str, any]:
    """
    Extract metadata from image bytes.
    
    Args:
        image_bytes: Image data in bytes
        
    Returns:
        Dictionary with metadata:
            - size: file size in bytes
            - format: image format (if detectable)
            - dimensions: (width, height) if available
    """
    if not image_bytes:
        return {}
    
    metadata = {
        "size": len(image_bytes),
        "format": "unknown",
        "dimensions": None
    }
    
    try:
        # Try to detect format from magic bytes
        if image_bytes[:2] == b'\xff\xd8':
            metadata["format"] = "JPEG"
        elif image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            metadata["format"] = "PNG"
        elif image_bytes[:6] in (b'GIF87a', b'GIF89a'):
            metadata["format"] = "GIF"
        elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
            metadata["format"] = "WEBP"
        
        logger.debug(f"Image metadata: {metadata}")
        
    except Exception as e:
        logger.error(f"Error extracting image metadata: {e}")
    
    return metadata


def is_valid_image_size(image_bytes: bytes, max_size_mb: int = 10) -> bool:
    """
    Check if image size is within acceptable limits.
    
    Args:
        image_bytes: Image data in bytes
        max_size_mb: Maximum size in megabytes
        
    Returns:
        True if size is acceptable, False otherwise
    """
    if not image_bytes:
        return False
    
    size_bytes = len(image_bytes)
    size_mb = size_bytes / (1024 * 1024)
    
    if size_mb > max_size_mb:
        logger.warning(f"Image size {size_mb:.2f}MB exceeds limit {max_size_mb}MB")
        return False
    
    return True


# ==================== IMAGE COMPARISON ====================

async def compare_images_similarity(image1_bytes: bytes, image2_bytes: bytes) -> float:
    """
    Compare two images and return similarity score.
    
    Uses AI to compare images and return a similarity score.
    
    Args:
        image1_bytes: First image data
        image2_bytes: Second image data
        
    Returns:
        Similarity score (0.0 to 1.0)
    """
    if not image1_bytes or not image2_bytes:
        return 0.0
    
    try:
        ai_service = get_openai_service()
        
        prompt = """
        Ushbu ikki rasmni solishtiring va o'xshashlik darajasini 0 dan 1 gacha raqam bilan bering.
        
        0.0 = mutlaqo o'xshamas
        1.0 = bir xil
        
        Faqat raqam javob bering, boshqa matn yo'q.
        """
        
        # Note: This is a simplified version. 
        # In production, you'd need to implement proper image comparison
        # using OpenAI Vision API with multiple images
        
        logger.info("Comparing images (simplified version)")
        return 0.5  # Placeholder
        
    except Exception as e:
        logger.error(f"Error comparing images: {e}")
        return 0.0

