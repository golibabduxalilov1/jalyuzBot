"""
Admin Section Status Handler
Provides detailed status reports for all bot sections
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import Counter

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from services.admin_utils import is_any_admin
from services.google_sheet import CACHE
from services.product_utils import normalize_code
from services.ai_service import get_ai_stats

logger = logging.getLogger(__name__)

router = Router()

# ==================== CACHE FOR STATUS MODULE ====================
# This cache stores metadata about section status checks
STATUS_CACHE: Dict[str, any] = {
    "last_check_times": {},  # {section_name: timestamp}
    "last_errors": {},  # {section_name: error_message}
    "check_durations": {},  # {section_name: duration_ms}
}


# ==================== HELPER FUNCTIONS ====================

def format_timestamp(timestamp: Optional[float]) -> str:
    """Format timestamp to readable date/time"""
    if not timestamp:
        return "topilmadi"
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        return "xato"


def get_last_sheet_date(records: List[Dict]) -> str:
    """Extract last date from sheet records"""
    if not records:
        return "topilmadi"
    
    dates = []
    for record in records:
        date_str = record.get("date") or record.get("sana", "")
        if date_str and date_str.strip():
            dates.append(date_str.strip())
    
    if not dates:
        return "topilmadi"
    
    # Return the last non-empty date (assuming data is ordered)
    return dates[-1] if dates else "topilmadi"


def detect_quantity_issues(records: List[Dict]) -> Dict[str, int]:
    """Detect quantity-related issues in records"""
    issues = {
        "missing_code": 0,
        "missing_quantity": 0,
        "zero_quantity": 0,
        "low_stock": 0,  # quantity < 50
        "invalid_quantity": 0,  # quantity is not a number
    }
    
    for record in records:
        code = record.get("code", "").strip()
        if not code:
            issues["missing_code"] += 1
            continue
        
        qty_str = str(record.get("quantity", "")).strip()
        if not qty_str or qty_str == " ":
            issues["missing_quantity"] += 1
        else:
            try:
                # MUHIM: Vergulni nuqtaga almashtirish (kasr sonlar uchun)
                # Misollar: "476,25" -> "476.25", "103,5" -> "103.5"
                qty_normalized = qty_str.replace(",", ".")
                qty = float(qty_normalized)
                
                if qty < 0:
                    issues["invalid_quantity"] += 1
                elif qty == 0:
                    issues["zero_quantity"] += 1
                elif 0 < qty < 50:
                    issues["low_stock"] += 1
            except ValueError:
                # Faqat haqiqiy xatolar (harf, maxsus belgilar va h.k.)
                issues["invalid_quantity"] += 1
    
    return issues


def detect_image_issues(records: List[Dict]) -> Dict[str, int]:
    """Detect image-related issues"""
    issues = {
        "missing_image": 0,
        "invalid_url": 0,
    }
    
    for record in records:
        image_url = record.get("image_url", "").strip()
        if not image_url:
            issues["missing_image"] += 1
        elif not (image_url.startswith("http://") or image_url.startswith("https://")):
            issues["invalid_url"] += 1
    
    return issues


def detect_catalog_issues(records: List[Dict], image_map: Dict) -> Dict[str, int]:
    """Detect catalog-related issues (image_url only, file_id is not a problem)"""
    issues = {
        "missing_image": 0,
        "invalid_url": 0,
        "drive_file_id_not_found": 0,
    }
    
    for record in records:
        # Get image_url from multiple possible column names
        image_url = (
            record.get("image_url") or 
            record.get("imageurl") or 
            record.get("image url") or 
            record.get("image") or 
            ""
        ).strip()
        
        # Check if image_url is missing
        if not image_url:
            issues["missing_image"] += 1
        else:
            # Check if URL format is valid (http/https)
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                issues["invalid_url"] += 1
            # Check if it's a drive link
            elif "drive.google.com" in image_url or "docs.google.com" in image_url:
                # It's a drive link - check if file_id can be extracted
                # Try to find file ID pattern in URL
                import re
                # Common patterns: /d/FILE_ID/, /file/d/FILE_ID/, ?id=FILE_ID
                file_id_patterns = [
                    r'/d/([a-zA-Z0-9_-]{20,})',
                    r'/file/d/([a-zA-Z0-9_-]{20,})',
                    r'[?&]id=([a-zA-Z0-9_-]{20,})'
                ]
                file_id_found = False
                for pattern in file_id_patterns:
                    match = re.search(pattern, image_url)
                    if match and len(match.group(1)) >= 20:
                        file_id_found = True
                        break
                
                if not file_id_found:
                    issues["drive_file_id_not_found"] += 1
            else:
                # Not a drive link - invalid URL
                issues["invalid_url"] += 1
    
    return issues


def normalize_model_name(name: str) -> str:
    """Normalize model name for comparison (remove spaces, hyphens, lowercase)"""
    if not name:
        return ""
    return name.strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def analyze_discount_models() -> Dict[str, any]:
    """
    Analyze discount models (Sheets4) against inventory (Sheets1).
    Only checks 3 things:
    1) Low stock (quantity > 0 and < 50)
    2) Out of stock (quantity = 0)
    3) Image issues (no image_url or invalid URL in Sheets4)
    
    NO comparison with Sheets2 catalog.
    """
    sheets4 = CACHE.get("sheets4", [])
    sheets1 = CACHE.get("sheets1", [])
    
    # Create inventory lookup map
    inventory_map = {}  # {normalized_code: quantity_string}
    for record in sheets1:
        code_norm = record.get("code_normalized", "")
        if code_norm:
            inventory_map[code_norm] = str(record.get("quantity", "")).strip()
    
    results = {
        "total_discount": len(sheets4),
        "low_stock_count": 0,
        "out_of_stock_count": 0,
        "quantity_not_found_count": 0,
        "image_issues_count": 0,
        "low_stock_list": [],
        "out_of_stock_list": [],
        "quantity_not_found_list": [],
        "image_issues_list": [],
    }
    
    for idx, discount_item in enumerate(sheets4):
        code_norm = discount_item.get("code_normalized", "")
        code_original = discount_item.get("code", "").strip()
        model_name = discount_item.get("model_name", "")
        row_number = idx + 2  # Google Sheets row number (header is row 1)
        
        # ========== Check quantity from Sheets1 ==========
        if code_norm in inventory_map:
            qty_str = inventory_map[code_norm]
            if qty_str:
                try:
                    # Handle comma as decimal separator
                    qty_normalized = qty_str.replace(",", ".")
                    qty = float(qty_normalized)
                    
                    if qty == 0:
                        # Out of stock
                        results["out_of_stock_count"] += 1
                        results["out_of_stock_list"].append({
                            "row_number": row_number,
                            "code": code_original or code_norm,
                            "model_name": model_name,
                            "quantity": qty_str,
                            "reason": "Tugagan (quantity = 0)"
                        })
                    elif 0 < qty < 50:
                        # Low stock
                        results["low_stock_count"] += 1
                        results["low_stock_list"].append({
                            "row_number": row_number,
                            "code": code_original or code_norm,
                            "model_name": model_name,
                            "quantity": qty_str,
                            "reason": f"Kam qoldi (quantity = {qty_str})"
                        })
                except ValueError:
                    # Invalid quantity format
                    results["quantity_not_found_count"] += 1
                    results["quantity_not_found_list"].append({
                        "row_number": row_number,
                        "code": code_original or code_norm,
                        "model_name": model_name,
                        "quantity": qty_str,
                        "reason": "Sheets1: quantity noto'g'ri format"
                    })
            else:
                # Empty quantity
                results["quantity_not_found_count"] += 1
                results["quantity_not_found_list"].append({
                    "row_number": row_number,
                    "code": code_original or code_norm,
                    "model_name": model_name,
                    "quantity": "bo'sh",
                    "reason": "Sheets1: quantity bo'sh"
                })
        else:
            # Not found in Sheets1
            results["quantity_not_found_count"] += 1
            results["quantity_not_found_list"].append({
                "row_number": row_number,
                "code": code_original or code_norm,
                "model_name": model_name,
                "quantity": "topilmadi",
                "reason": "Sheets1 da topilmadi"
            })
        
        # ========== Check image issues from Sheets4 ==========
        image_url = discount_item.get("image_url", "").strip()
        
        if not image_url:
            # No image URL
            results["image_issues_count"] += 1
            results["image_issues_list"].append({
                "row_number": row_number,
                "code": code_original or code_norm,
                "model_name": model_name,
                "image_url": "",
                "reason": "Rasm yo'q"
            })
        elif not (image_url.startswith("http://") or image_url.startswith("https://")):
            # Invalid URL format
            results["image_issues_count"] += 1
            results["image_issues_list"].append({
                "row_number": row_number,
                "code": code_original or code_norm,
                "model_name": model_name,
                "image_url": image_url,
                "reason": "Rasm URL noto'g'ri"
            })
    
    return results


# ==================== STATUS REPORT GENERATORS ====================

def generate_astatka_status() -> str:
    """Generate status report for Astatka section (sheet1)"""
    start_time = time.time()
    
    try:
        sheets1 = CACHE.get("sheets1", [])
        total_records = len(sheets1)
        
        # Check if sheet1 is loaded (ERROR if not loaded or empty)
        if total_records == 0:
            STATUS_CACHE["last_errors"]["astatka"] = "Sheet1 yuklenmagan yoki bo'sh"
            return f"""🔎 <b>Astatka Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheet1 yuklenmagan yoki bo'sh

<b>⏱ Tekshirish vaqti:</b> {int((time.time() - start_time) * 1000)} ms"""
        
        # Check if columns are found (ERROR if code or quantity column not found)
        # Sample first record to check if columns exist
        first_record = sheets1[0] if sheets1 else {}
        has_code_column = "code" in first_record or "code_normalized" in first_record
        has_quantity_column = "quantity" in first_record
        
        if not has_code_column or not has_quantity_column:
            STATUS_CACHE["last_errors"]["astatka"] = "Sheet1 ustunlari noto'g'ri: quantity yoki code topilmadi"
            return f"""🔎 <b>Astatka Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheet1 ustunlari noto'g'ri: quantity yoki code topilmadi

<b>⏱ Tekshirish vaqti:</b> {int((time.time() - start_time) * 1000)} ms"""
        
        # Detect issues
        issues = detect_quantity_issues(sheets1)
        
        # Get last sheet date
        last_sheet_date = get_last_sheet_date(sheets1)
        
        # Get last cache load time (from STATUS_CACHE)
        last_load_time = STATUS_CACHE["last_check_times"].get("sheets1")
        last_load_str = format_timestamp(last_load_time)
        
        # Calculate status based on new rules:
        # ✅ OK -> if sheet1 is loaded and no problems
        # ⚠️ WARNING -> if sheet1 is loaded but has low stock issues only
        # ❌ ERROR -> if missing code, missing quantity, invalid quantity, or zero quantity
        error_count = (issues['missing_code'] + issues['missing_quantity'] + 
                      issues['invalid_quantity'] + issues['zero_quantity'])
        
        if error_count > 0:
            status_icon = "❌ ERROR"
        elif issues['low_stock'] > 0:
            status_icon = "⚠️ WARNING"
        else:
            status_icon = "✅ OK"
        
        # Get last error
        last_error = STATUS_CACHE["last_errors"].get("astatka", "Oxirgi xatolar topilmadi")
        
        # Format report
        report = f"""🔎 <b>Astatka Bo'limi</b>

<b>Status:</b> {status_icon}

<b>📋 Umumiy ma'lumot:</b>
• Google Sheet: sheets1
• Yuklangan yozuvlar: {total_records} ta
• 📌 RAMga yuklangan vaqt: {last_load_str}
• 📌 Sheetdagi oxirgi sana: {last_sheet_date}

<b>❌ Xatolar (ERROR):</b>
• Code yo'q: {issues['missing_code']} ta
• Quantity yo'q: {issues['missing_quantity']} ta
• Quantity noto'g'ri: {issues['invalid_quantity']} ta
• Quantity = 0: {issues['zero_quantity']} ta
• <b>Jami xatolar:</b> {error_count} ta

<b>⚠️ Ogohlantirishlar (WARNING):</b>
• Kam qoldiq (&lt;50): {issues['low_stock']} ta

<b>📝 Oxirgi xatolar:</b>
{last_error}

<b>⏱ Tekshirish vaqti:</b> {int((time.time() - start_time) * 1000)} ms"""
        
        # Store metrics
        STATUS_CACHE["check_durations"]["astatka"] = int((time.time() - start_time) * 1000)
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating astatka status: {e}")
        STATUS_CACHE["last_errors"]["astatka"] = f"Xato: {str(e)}"
        return f"""🔎 <b>Astatka Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato yuz berdi:</b>
{str(e)}"""


def generate_ai_generate_status() -> str:
    """Generate status report for AI generation section"""
    return """🔎 <b>AI generatsiya Bo'limi</b>

<b>Status:</b> ⚠️ WARNING

Bu bo'lim hozircha ishga tushirilmagan.

Kelajakda AI orqali rasm generatsiya qilish funksiyasi qo'shiladi."""


def generate_help_status() -> str:
    """Generate status report for Help/AI Assistant section"""
    start_time = time.time()
    
    try:
        # Get AI stats
        ai_stats = get_ai_stats()
        
        # Determine status based on errors
        last_error = ai_stats.get("last_error", "")
        if not last_error or last_error == "None":
            status_icon = "✅ OK"
            error_msg = "Oxirgi xatolar topilmadi"
        elif "Unable to reach the model provider" in last_error:
            status_icon = "❌ ERROR"
            error_msg = f"AI provider ga ulanib bo'lmadi:\n{last_error}"
        else:
            status_icon = "⚠️ WARNING"
            error_msg = f"Oxirgi xato:\n{last_error}"
        
        # Get AI service info
        total_requests = ai_stats.get("total_requests", 0)
        total_errors = ai_stats.get("total_errors", 0)
        
        report = f"""🔎 <b>Yordam (AI Assistant) Bo'limi</b>

<b>Status:</b> {status_icon}

<b>📋 Umumiy ma'lumot:</b>
• AI Model: OpenAI GPT
• Jami so'rovlar: {total_requests} ta
• Xatolar soni: {total_errors} ta
• 📌 RAMga yuklangan vaqt: Statik xizmat
• 📌 Sheetdagi oxirgi sana: Sheet ishlatilmaydi

<b>⚠️ AI provider holati:</b>
{error_msg}

<b>⏱ Tekshirish vaqti:</b> {int((time.time() - start_time) * 1000)} ms"""
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating help status: {e}")
        return f"""🔎 <b>Yordam (AI Assistant) Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato yuz berdi:</b>
{str(e)}"""


def generate_catalog_status() -> str:
    """Generate status report for Model Catalog section (sheet2)"""
    start_time = time.time()
    
    try:
        sheets2_full = CACHE.get("sheets2_full", [])
        image_map = CACHE.get("image_map", {})
        
        total_records = len(sheets2_full)
        
        # Check if sheet2 is loaded (ERROR if not loaded or empty)
        if total_records == 0:
            STATUS_CACHE["last_errors"]["catalog"] = "Sheets2 yuklenmagan yoki bo'sh"
            duration_ms = int((time.time() - start_time) * 1000)
            return f"""🔎 <b>Modellar Katalogi Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheets2 yuklenmagan yoki bo'sh

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""
        
        # Check if image_url column exists (ERROR if not found)
        first_record = sheets2_full[0] if sheets2_full else {}
        has_image_url_column = any(
            key in first_record 
            for key in ["image_url", "imageurl", "image url", "image"]
        )
        
        if not has_image_url_column:
            STATUS_CACHE["last_errors"]["catalog"] = "Sheets2 ustunlari noto'g'ri: image_url topilmadi"
            duration_ms = int((time.time() - start_time) * 1000)
            return f"""🔎 <b>Modellar Katalogi Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheets2 ustunlari noto'g'ri: image_url topilmadi

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""
        
        # Detect image issues (file_id is not counted as a problem)
        issues = detect_catalog_issues(sheets2_full, image_map)
        
        # Get last sheet date
        last_sheet_date = get_last_sheet_date(sheets2_full)
        
        # Get last cache load time
        last_load_time = STATUS_CACHE["last_check_times"].get("sheets2")
        last_load_str = format_timestamp(last_load_time)
        
        # Calculate status based on new rules:
        # ✅ OK -> sheets2 loaded and no problems
        # ⚠️ WARNING -> sheets2 loaded but has url issues
        # ❌ ERROR -> sheets2 cannot be opened or columns not found (handled above)
        total_issues = issues["missing_image"] + issues["invalid_url"] + issues["drive_file_id_not_found"]
        if total_issues == 0:
            status_icon = "✅ OK"
        else:
            status_icon = "⚠️ WARNING"
        
        # Get last error
        last_error = STATUS_CACHE["last_errors"].get("catalog", "Oxirgi xatolar topilmadi")
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Count file_id not generated yet (informational only)
        file_id_not_generated = 0
        for record in sheets2_full:
            code_norm = record.get("_code_normalized", "")
            if code_norm:
                file_id = image_map.get(code_norm, "")
                if not file_id:
                    file_id_not_generated += 1
        
        # Check Sheets1 vs Sheets2 compatibility
        missing_in_catalog = get_catalog_missing_in_sheets2()
        missing_count = len(missing_in_catalog)
        
        report = f"""🔎 <b>Modellar Katalogi Bo'limi</b>

<b>Status:</b> {status_icon}

<b>📋 Umumiy ma'lumot:</b>
• Google Sheet: sheets2
• Yuklangan modellar: {total_records} ta
• file_id cache: {len(image_map)} ta
• 📌 RAMga yuklangan vaqt: {last_load_str}
• 📌 Sheetdagi oxirgi sana: {last_sheet_date}

<b>⚠️ Aniqlangan muammolar:</b>
• Rasm URL yo'q: {issues['missing_image']} ta
• URL noto'g'ri format: {issues['invalid_url']} ta
• Drive link, lekin file_id ajratilmadi: {issues['drive_file_id_not_found']} ta
• <b>Jami muammolar:</b> {total_issues} ta

<b>🔄 Sheets1 bilan moslik:</b>
• Sheets1 da bor, Sheets2 da yo'q/rasm yo'q: {missing_count} ta

<b>ℹ️ Ma'lumot:</b>
• file_id hali generatsiya qilinmagan: {file_id_not_generated} ta (bu muammo emas)

<b>📝 Oxirgi xatolar:</b>
{last_error}

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""
        
        STATUS_CACHE["check_durations"]["catalog"] = duration_ms
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating catalog status: {e}")
        STATUS_CACHE["last_errors"]["catalog"] = f"Xato: {str(e)}"
        duration_ms = int((time.time() - start_time) * 1000)
        return f"""🔎 <b>Modellar Katalogi Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato yuz berdi:</b>
{str(e)}

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""


def is_order_confirmed(record: Dict) -> bool:
    """
    Check if an order is confirmed based on various possible status indicators.
    
    Confirmed order criteria:
    - Has status column with "tasdiq", "confirmed", "done", "approved"
    - Has tasdiqlangan_time / confirmed_date field
    - Has boolean "tasdiqlangan" flag
    """
    if not isinstance(record, dict):
        return False
    
    # Check status column (case-insensitive)
    status_fields = ["status", "holat", "tasdiqlangan_holat"]
    for field in status_fields:
        for key, value in record.items():
            if key.lower().strip() == field.lower():
                value_str = str(value).lower().strip()
                if value_str in ["tasdiq", "confirmed", "done", "approved", "tasdiqlangan"]:
                    return True
    
    # Check for confirmed date/time fields
    confirmed_time_fields = ["tasdiqlangan_time", "confirmed_date", "confirmed_time", "tasdiq_vaqti"]
    for field in confirmed_time_fields:
        for key, value in record.items():
            if key.lower().strip() == field.lower():
                if value and str(value).strip():
                    return True
    
    # Check for boolean flag
    boolean_fields = ["tasdiqlangan", "confirmed", "is_confirmed"]
    for field in boolean_fields:
        for key, value in record.items():
            if key.lower().strip() == field.lower():
                # Check if it's truthy (True, "true", "1", "yes", etc.)
                if value in [True, 1, "1", "true", "True", "TRUE", "yes", "Yes", "YES"]:
                    return True
    
    return False


def generate_ready_sizes_status() -> str:
    """Generate status report for Ready Sizes section (sheet6) with real-time event monitoring"""
    start_time = time.time()
    
    try:
        from services.ready_sizes_events import (
            get_total_event_counts,
            get_events_today_count,
            get_confirmed_events,
        )
        from services.cart_service import get_cart_items_for_admin_view
        from services.order_service import get_order_items_for_admin_view
        
        sheets6 = CACHE.get("sheets6", [])
        total_records = len(sheets6)
        
        # Check if sheet6 is loaded (ERROR if not loaded or empty)
        if total_records == 0:
            STATUS_CACHE["last_errors"]["ready_sizes"] = "Sheets6 yuklenmagan yoki bo'sh"
            duration_ms = int((time.time() - start_time) * 1000)
            return f"""🔎 <b>Tayyor Razmerlar Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheets6 yuklenmagan yoki bo'sh

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""
        
        # Check if required columns exist
        first_record = sheets6[0] if sheets6 else {}
        has_model_column = "model_nomi" in first_record or "model" in first_record or "code" in first_record
        
        if not has_model_column:
            STATUS_CACHE["last_errors"]["ready_sizes"] = "Sheets6 ustunlari noto'g'ri: model_nomi yoki code topilmadi"
            duration_ms = int((time.time() - start_time) * 1000)
            return f"""🔎 <b>Tayyor Razmerlar Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheets6 ustunlari noto'g'ri: model_nomi yoki code topilmadi

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""
        
        # Get real-time event statistics
        total_event_counts = get_total_event_counts()
        today_event_counts = get_events_today_count()
        
        # Get cart and order counts (real-time from RAM)
        cart_items = get_cart_items_for_admin_view()
        order_items = get_order_items_for_admin_view()
        cart_count = len(cart_items)
        
        # Get last sheet date
        last_sheet_date = get_last_sheet_date(sheets6)
        
        # Get last cache load time
        last_load_time = STATUS_CACHE["last_check_times"].get("sheets6")
        last_load_str = format_timestamp(last_load_time)
        
        # Calculate status based on event counts:
        # ✅ OK -> no confirmed orders today
        # ⚠️ WARNING -> has confirmed orders or cart items
        # ❌ ERROR -> sheets6 cannot be opened or columns not found (handled above)
        if today_event_counts["confirmed_count"] == 0 and cart_count == 0:
            status_icon = "✅ OK"
        else:
            status_icon = "⚠️ WARNING"
        
        # Get last error
        last_error = STATUS_CACHE["last_errors"].get("ready_sizes", "Oxirgi xatolar topilmadi")
        
        # Get last 5 confirmed orders for display
        recent_confirmed = get_confirmed_events(limit=5)
        if recent_confirmed:
            confirmed_section = "<b>📌 Oxirgi tasdiqlangan buyurtmalar (5 ta):</b>\n"
            for idx, event in enumerate(recent_confirmed, 1):
                # Convert UTC time to Uzbekistan time (UTC+5)
                uz_time = event.timestamp + timedelta(hours=5)
                time_str = uz_time.strftime("%d.%m.%Y %H:%M")
                model_display = event.model_nomi or event.code
                size_display = f" | {event.razmer}" if event.razmer else ""
                qty_display = f" | {event.qty} ta" if event.qty > 1 else ""
                
                confirmed_section += f"{idx}. Model: {model_display}{size_display}{qty_display}\n"
                
                # Show buyer (oluvchi)
                if event.role == "Hamkor" and event.seller_name:
                    # Partner with seller
                    confirmed_section += f"   Hamkor: {event.user_name} (ID: {event.user_id})\n"
                    confirmed_section += f"   Sotuvchi: {event.seller_name}\n"
                elif event.role == "Hamkor":
                    # Partner without seller
                    confirmed_section += f"   Hamkor: {event.user_name} (ID: {event.user_id})\n"
                else:
                    # Regular user or other roles
                    confirmed_section += f"   Oluvchi ({event.role}): {event.user_name} (ID: {event.user_id})\n"
                
                # Show confirmer (tasdiqlagan)
                if event.confirmer_id and event.confirmer_name:
                    confirmer_role = event.confirmer_role if event.confirmer_role else "Admin"
                    confirmed_section += f"   Tasdiqlagan ({confirmer_role}): {event.confirmer_name} (ID: {event.confirmer_id})\n"
                else:
                    confirmed_section += f"   Tasdiqlagan: noma'lum\n"
                
                confirmed_section += f"   Vaqt: {time_str} (UZ)\n\n"
        else:
            confirmed_section = "<b>📌 Tasdiqlangan buyurtmalar:</b>\nHozircha tasdiqlangan buyurtmalar yo'q."
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        report = f"""🔎 <b>Tayyor Razmerlar Bo'limi</b>

<b>Status:</b> {status_icon}

<b>📌 Umumiy:</b>
• Google Sheet: sheets6
• Jami buyurtmalar: {total_records} ta
• Karzinkada: {cart_count} ta
• Tasdiqlangan (sotilgan): {total_event_counts['confirmed_count']} ta
• O'chirilgan/qaytarilgan: {total_event_counts['deleted_count']} ta
• 📌 RAMga yuklangan vaqt: {last_load_str}
• 📌 Sheetdagi oxirgi sana: {last_sheet_date}

<b>📊 Bugun:</b>
• Karzinkaga qo'shilganlar: {today_event_counts['cart_count']} ta
• Tasdiqlanganlar: {today_event_counts['confirmed_count']} ta
• O'chirilganlar: {today_event_counts['deleted_count']} ta

{confirmed_section}

<b>⚠️ Muhim:</b>
Event log orqali barcha tasdiqlanganlar ro'yxatini doim ko'rish mumkin.
Pastdagi tugmalardan foydalanib, to'liq ma'lumotlarni ko'ring.

<b>📝 Oxirgi xatolar:</b>
{last_error}

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""
        
        STATUS_CACHE["check_durations"]["ready_sizes"] = duration_ms
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating ready sizes status: {e}")
        STATUS_CACHE["last_errors"]["ready_sizes"] = f"Xato: {str(e)}"
        duration_ms = int((time.time() - start_time) * 1000)
        return f"""🔎 <b>Tayyor Razmerlar Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato yuz berdi:</b>
{str(e)}

<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"""


def generate_discount_status() -> str:
    """
    Generate status report for Discount Models section (Sheets4 + Sheets1).
    
    No comparison with Sheets2 catalog. Only checks:
    1) Low stock (quantity < 5)
    2) Out of stock (quantity = 0)
    3) Image issues in Sheets4
    """
    start_time = time.time()
    
    try:
        sheets4 = CACHE.get("sheets4", [])
        sheets1 = CACHE.get("sheets1", [])
        
        # Check if sheets are loaded (ERROR if not loaded or empty)
        if len(sheets4) == 0:
            STATUS_CACHE["last_errors"]["discount"] = "Sheets4 yuklenmagan yoki bo'sh"
            return f"""🔎 <b>Chegirmadagi Modellar Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheets4 yuklenmagan yoki bo'sh

<b>⏱ Tekshirish vaqti:</b> {int((time.time() - start_time) * 1000)} ms"""
        
        if len(sheets1) == 0:
            STATUS_CACHE["last_errors"]["discount"] = "Sheets1 yuklenmagan yoki bo'sh"
            return f"""🔎 <b>Chegirmadagi Modellar Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato:</b> Sheets1 yuklenmagan yoki bo'sh

<b>⏱ Tekshirish vaqti:</b> {int((time.time() - start_time) * 1000)} ms"""
        
        # Analyze discount models
        analysis = analyze_discount_models()
        
        # Get last sheet date
        last_sheet_date = get_last_sheet_date(sheets4)
        
        # Get last cache load time for sheets4
        last_load_time = STATUS_CACHE["last_check_times"].get("sheets4")
        last_load_str = format_timestamp(last_load_time)
        
        # Calculate status:
        # ✅ OK -> all counts are 0
        # ⚠️ WARNING -> any count > 0
        # ❌ ERROR -> sheets not loaded or exception (handled above/below)
        total_issues = (analysis["out_of_stock_count"] + 
                       analysis["low_stock_count"] + 
                       analysis["image_issues_count"])
        
        if total_issues == 0 and analysis["quantity_not_found_count"] == 0:
            status_icon = "✅ OK"
        else:
            status_icon = "⚠️ WARNING"
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        report = f"""🔎 <b>Chegirmadagi Modellar Bo'limi</b>

<b>Status:</b> {status_icon}

<b>📌 Umumiy:</b>
• Google Sheets: sheets4 (chegirma) + sheets1 (qoldiq)
• Jami chegirmali modellar: {analysis['total_discount']} ta
• 📌 RAMga yuklangan vaqt: {last_load_str}
• 📌 Sheetdagi oxirgi sana: {last_sheet_date}

<b>📌 Natija:</b>
• Kam qolgan (&lt;5): {analysis['low_stock_count']} ta
• Tugagan (=0): {analysis['out_of_stock_count']} ta
• Qoldiq topilmadi: {analysis['quantity_not_found_count']} ta
• Rasm muammosi: {analysis['image_issues_count']} ta"""

        # Mahsulot kodlarini qo'shish (har bir bo'limdan faqat 5 ta)
        if analysis['low_stock_count'] > 0:
            report += "\n\n<b>🟡 Kam qolgan mahsulotlar:</b>"
            for item in analysis['low_stock_list'][:5]:
                # Debug log
                logger.info(f"[DISCOUNT_REPORT] Low stock item: {item}")
                
                code = str(item.get('code', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                model = str(item.get('model_name', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                qty = str(item.get('quantity', ''))
                
                # Debug log
                logger.info(f"[DISCOUNT_REPORT] Displaying: code={code}, model={model}, qty={qty}")
                
                report += f"\n  • {code}"
                if model:
                    report += f" ({model})"
                report += f" - Qoldiq: {qty}"
            if analysis['low_stock_count'] > 5:
                report += f"\n  ... va yana {analysis['low_stock_count'] - 5} ta"
        
        if analysis['out_of_stock_count'] > 0:
            report += "\n\n<b>🔴 Tugagan mahsulotlar:</b>"
            for item in analysis['out_of_stock_list'][:5]:
                code = str(item.get('code', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                model = str(item.get('model_name', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                report += f"\n  • {code}"
                if model:
                    report += f" ({model})"
            if analysis['out_of_stock_count'] > 5:
                report += f"\n  ... va yana {analysis['out_of_stock_count'] - 5} ta"
        
        if analysis['quantity_not_found_count'] > 0:
            report += "\n\n<b>⚠️ Qoldiq topilmagan mahsulotlar:</b>"
            for item in analysis['quantity_not_found_list'][:5]:
                code = str(item.get('code', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                model = str(item.get('model_name', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                report += f"\n  • {code}"
                if model:
                    report += f" ({model})"
            if analysis['quantity_not_found_count'] > 5:
                report += f"\n  ... va yana {analysis['quantity_not_found_count'] - 5} ta"
        
        if analysis['image_issues_count'] > 0:
            report += "\n\n<b>📸 Rasm muammolari:</b>"
            for item in analysis['image_issues_list'][:5]:
                code = str(item.get('code', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                model = str(item.get('model_name', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                report += f"\n  • {code}"
                if model:
                    report += f" ({model})"
            if analysis['image_issues_count'] > 5:
                report += f"\n  ... va yana {analysis['image_issues_count'] - 5} ta"
        
        report += f"\n\n<b>⏱ Tekshirish vaqti:</b> {duration_ms} ms"
        
        STATUS_CACHE["check_durations"]["discount"] = duration_ms
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating discount status: {e}")
        STATUS_CACHE["last_errors"]["discount"] = f"Xato: {str(e)}"
        return f"""🔎 <b>Chegirmadagi Modellar Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato yuz berdi:</b>
{str(e)}"""


def generate_prices_status() -> str:
    """Generate status report for Prices section"""
    start_time = time.time()
    
    try:
        from services.settings import get_api_users
        
        sheets3 = CACHE.get("sheets3", [])
        total_records = len(sheets3)
        
        # Get API access users
        api_users = get_api_users()
        api_enabled = len(api_users) > 0
        
        # Get last sheet date
        last_sheet_date = get_last_sheet_date(sheets3)
        
        # Get last cache load time
        last_load_time = STATUS_CACHE["last_check_times"].get("sheets3")
        last_load_str = format_timestamp(last_load_time)
        
        # Count missing prices
        missing_prices = 0
        for record in sheets3:
            asosiy = record.get("asosiy_price", "").strip()
            mini = record.get("mini_price", "").strip()
            kasetniy = record.get("kasetniy_price", "").strip()
            if not asosiy and not mini and not kasetniy:
                missing_prices += 1
        
        # Calculate status
        if api_enabled and missing_prices < 10:
            status_icon = "✅ OK"
        elif not api_enabled:
            status_icon = "⚠️ WARNING"
        else:
            status_icon = "❌ ERROR"
        
        # Get last error
        last_error = STATUS_CACHE["last_errors"].get("prices", "Oxirgi xatolar topilmadi")
        
        report = f"""🔎 <b>Narxlar Bo'limi</b>

<b>Status:</b> {status_icon}

<b>📋 Umumiy ma'lumot:</b>
• Google Sheet: sheets3
• Jami narx yozuvlari: {total_records} ta
• API ruxsati: {"✅ Yoqilgan" if api_enabled else "❌ O'chirilgan"}
• API foydalanuvchilari: {len(api_users)} ta
• 📌 RAMga yuklangan vaqt: {last_load_str}
• 📌 Sheetdagi oxirgi sana: {last_sheet_date}

<b>⚠️ Aniqlangan muammolar:</b>
• Narx yo'q (barcha turlar): {missing_prices} ta

<b>📝 Oxirgi xatolar:</b>
{last_error}

<b>⏱ Tekshirish vaqti:</b> {int((time.time() - start_time) * 1000)} ms"""
        
        STATUS_CACHE["check_durations"]["prices"] = int((time.time() - start_time) * 1000)
        
        return report
        
    except Exception as e:
        logger.error(f"Error generating prices status: {e}")
        STATUS_CACHE["last_errors"]["prices"] = f"Xato: {str(e)}"
        return f"""🔎 <b>Narxlar Bo'limi</b>

<b>Status:</b> ❌ ERROR

<b>Xato yuz berdi:</b>
{str(e)}"""


# ==================== HANDLERS ====================

@router.callback_query(F.data == "section_status")
async def section_status_menu(callback_query: CallbackQuery):
    """Show section status menu"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Update last check times for all sheets
    STATUS_CACHE["last_check_times"]["sheets1"] = time.time()
    STATUS_CACHE["last_check_times"]["sheets2"] = time.time()
    STATUS_CACHE["last_check_times"]["sheets3"] = time.time()
    STATUS_CACHE["last_check_times"]["sheets4"] = time.time()
    STATUS_CACHE["last_check_times"]["sheets5"] = time.time()
    STATUS_CACHE["last_check_times"]["sheets6"] = time.time()
    
    text = "🔎 <b>Bo'limlar holati</b>\n\nQuyidagi bo'limlardan birini tanlang:"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Astatka", callback_data="section_status_astatka")],
            [InlineKeyboardButton(text="🤖 AI generatsiya", callback_data="section_status_ai_generate")],
            [InlineKeyboardButton(text="🆘 Yordam", callback_data="section_status_help")],
            [InlineKeyboardButton(text="📚 Modellar katalogi", callback_data="section_status_catalog")],
            [InlineKeyboardButton(text="📐 Tayyor razmerlar", callback_data="section_status_ready_sizes")],
            [InlineKeyboardButton(text="🔥 Chegirmadagi modellar", callback_data="section_status_discount")],
            [InlineKeyboardButton(text="💰 Narxlar", callback_data="section_status_prices")],
            [InlineKeyboardButton(text="🧹 Cache tozalash", callback_data="section_status_clear_cache")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel")],
        ]
    )
    
    try:
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Error showing section status menu: {e}")
        await callback_query.answer("Xatolik yuz berdi", show_alert=True)


@router.callback_query(F.data == "section_status_astatka")
async def section_status_astatka_handler(callback_query: CallbackQuery):
    """Show Astatka section status"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    report = generate_astatka_status()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Muammoli ro'yxat", callback_data="astatka_problematic_list:0")],
            [InlineKeyboardButton(text="🟡 Kam qolgan modellar (<5)", callback_data="astatka_low_stock_list:0")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
        ]
    )
    
    try:
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing astatka status: {e}")


@router.callback_query(F.data == "section_status_ai_generate")
async def section_status_ai_generate_handler(callback_query: CallbackQuery):
    """Show AI Generate section status"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    report = generate_ai_generate_status()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
        ]
    )
    
    try:
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing AI generate status: {e}")


@router.callback_query(F.data == "section_status_help")
async def section_status_help_handler(callback_query: CallbackQuery):
    """Show Help section status"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    report = generate_help_status()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
        ]
    )
    
    try:
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing help status: {e}")


@router.callback_query(F.data == "section_status_catalog")
async def section_status_catalog_handler(callback_query: CallbackQuery):
    """Show Catalog section status"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    report = generate_catalog_status()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Muammoli ro'yxat", callback_data="catalog_problematic_list:0")],
            [InlineKeyboardButton(text="📊 Sheets1 da bor, Sheets2 da yo'q", callback_data="catalog_missing_in_sheets2:0")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
        ]
    )
    
    try:
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing catalog status: {e}")


@router.callback_query(F.data == "section_status_ready_sizes")
async def section_status_ready_sizes_handler(callback_query: CallbackQuery):
    """Show Ready Sizes section status with real-time event monitoring"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    report = generate_ready_sizes_status()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Karzinkaga olinganlar ro'yxati", callback_data="ready_sizes_cart_events:0")],
            [InlineKeyboardButton(text="✅ Tasdiqlangan (sotilgan) buyurtmalar", callback_data="ready_sizes_confirmed_events:0")],
            [InlineKeyboardButton(text="🗑 O'chirilgan/qaytarilgan buyurtmalar", callback_data="ready_sizes_deleted_events:0")],
            [InlineKeyboardButton(text="🧹 Event loglarni tozalash", callback_data="ready_sizes_clear_events")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
        ]
    )
    
    try:
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing ready sizes status: {e}")


@router.callback_query(F.data == "section_status_discount")
async def section_status_discount_handler(callback_query: CallbackQuery):
    """Show Discount section status"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    report = generate_discount_status()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟠 Kam qolgan modellar", callback_data="discount_low_stock:0")],
            [InlineKeyboardButton(text="🔴 Tugagan modellar", callback_data="discount_out_of_stock:0")],
            [InlineKeyboardButton(text="📉 Qoldiq topilmagan modellar", callback_data="discount_quantity_not_found:0")],
            [InlineKeyboardButton(text="🖼 Rasm muammosi bo'lgan modellar", callback_data="discount_image_issues:0")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
        ]
    )
    
    try:
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing discount status: {e}")


@router.callback_query(F.data == "section_status_prices")
async def section_status_prices_handler(callback_query: CallbackQuery):
    """Show Prices section status"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    report = generate_prices_status()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
        ]
    )
    
    try:
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing prices status: {e}")


@router.callback_query(F.data == "section_status_clear_cache")
async def section_status_clear_cache_handler(callback_query: CallbackQuery):
    """Clear section status cache"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    try:
        # Clear only status module cache (not main bot cache)
        STATUS_CACHE["last_check_times"].clear()
        STATUS_CACHE["last_errors"].clear()
        STATUS_CACHE["check_durations"].clear()
        
        logger.info(f"Section status cache cleared by admin {user_id}")
        
        await callback_query.answer("✅ Cache tozalandi", show_alert=True)
        
        # Return to section status menu
        await section_status_menu(callback_query)
        
    except Exception as e:
        logger.error(f"Error clearing section status cache: {e}")
        await callback_query.answer("Xatolik yuz berdi", show_alert=True)


# ==================== ASTATKA PROBLEMATIC LIST ====================

def get_problematic_rows() -> List[Dict]:
    """
    Get all problematic rows from sheet1 with details.
    
    Returns list of dicts with:
    - row_number: Real row number in sheet (starting from 2 if header is row 1)
    - code: Code value
    - quantity: Raw quantity value
    - reason: Reason why it's problematic
    """
    sheets1 = CACHE.get("sheets1", [])
    problematic = []
    
    for idx, record in enumerate(sheets1):
        row_number = idx + 2  # Row 1 is header, so data starts from row 2
        code = record.get("code", "").strip()
        qty_raw = record.get("quantity", "")
        qty_str = str(qty_raw).strip() if qty_raw is not None else ""
        
        reasons = []
        
        # Check code issues
        if not code:
            reasons.append("code bo'sh")
        
        # Check quantity issues
        if not qty_str or qty_str == " ":
            reasons.append("quantity bo'sh")
        else:
            try:
                # MUHIM: Vergulni nuqtaga almashtirish (kasr sonlar uchun)
                # Misollar: "476,25" -> "476.25", "103,5" -> "103.5"
                qty_normalized = qty_str.replace(",", ".")
                qty = float(qty_normalized)
                if qty < 0:
                    reasons.append("quantity manfiy")
                elif qty == 0:
                    reasons.append("quantity = 0")
            except ValueError:
                # Faqat haqiqiy xatolar (harf, maxsus belgilar va h.k.)
                reasons.append("quantity raqam emas")
        
        # If there are any issues, add to problematic list
        if reasons:
            problematic.append({
                "row_number": row_number,
                "code": code if code else '""',
                "quantity": f'"{qty_str}"' if qty_str else '""',
                "reason": ", ".join(reasons)
            })
    
    return problematic


def get_low_stock_rows() -> List[Dict]:
    """
    Get all low stock rows from sheet1 (quantity > 0 and < 50).
    
    Returns list of dicts with:
    - row_number: Real row number in sheet (starting from 2 if header is row 1)
    - code: Code value
    - quantity: Quantity value
    - date: Date value (if exists)
    """
    sheets1 = CACHE.get("sheets1", [])
    low_stock = []
    
    for idx, record in enumerate(sheets1):
        row_number = idx + 2  # Row 1 is header, so data starts from row 2
        code = record.get("code", "").strip()
        qty_raw = record.get("quantity", "")
        qty_str = str(qty_raw).strip() if qty_raw is not None else ""
        
        # Skip if code is missing (this is an error, not low stock)
        if not code:
            continue
        
        # Check if quantity is low stock (0 < qty < 50)
        if qty_str and qty_str != " ":
            try:
                # MUHIM: Vergulni nuqtaga almashtirish (kasr sonlar uchun)
                qty_normalized = qty_str.replace(",", ".")
                qty = float(qty_normalized)
                
                # Only include if 0 < qty < 50
                if 0 < qty < 50:
                    # Get date if available
                    date_str = record.get("date", "").strip() or record.get("sana", "").strip()
                    
                    low_stock.append({
                        "row_number": row_number,
                        "code": code,
                        "quantity": qty_str,
                        "date": date_str if date_str else None
                    })
            except ValueError:
                # Invalid quantity format - skip (this is an error, not low stock)
                continue
    
    return low_stock


@router.callback_query(F.data.startswith("astatka_problematic_list:"))
async def astatka_problematic_list_handler(callback_query: CallbackQuery):
    """Show problematic list with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all problematic rows from CACHE
        problematic_rows = get_problematic_rows()
        total_problems = len(problematic_rows)
        
        # Pagination settings
        items_per_page = 20
        total_pages = (total_problems + items_per_page - 1) // items_per_page if total_problems > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_problems)
        page_items = problematic_rows[start_idx:end_idx]
        
        # Build report text
        if total_problems == 0:
            report = """📄 <b>Astatka bo'limi - muammoli ro'yxat</b>

✅ Muammoli qatorlar topilmadi!

Barcha qatorlar to'g'ri."""
        else:
            lines = [f"📄 <b>Astatka bo'limi - muammoli ro'yxat</b>\n"]
            lines.append(f"Jami muammoli qatorlar: {total_problems} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) Qator: {item['row_number']}")
                lines.append(f"   Code: {item['code']}")
                lines.append(f"   Quantity: {item['quantity']}")
                lines.append(f"   Sabab: {item['reason']}\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"astatka_problematic_list:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"astatka_problematic_list:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_astatka")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing problematic list: {e}")


@router.callback_query(F.data.startswith("astatka_low_stock_list:"))
async def astatka_low_stock_list_handler(callback_query: CallbackQuery):
    """Show low stock list with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all low stock rows from CACHE
        low_stock_rows = get_low_stock_rows()
        total_low_stock = len(low_stock_rows)
        
        # Pagination settings
        items_per_page = 15
        total_pages = (total_low_stock + items_per_page - 1) // items_per_page if total_low_stock > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_low_stock)
        page_items = low_stock_rows[start_idx:end_idx]
        
        # Build report text
        if total_low_stock == 0:
            report = """📉 <b>Kam qolgan modellar ro'yxati</b>

✅ Kam qolgan modellar topilmadi!

Barcha modellarning qoldiqlari yetarli (≥50) yoki 0."""
        else:
            lines = [f"📉 <b>Kam qolgan modellar ro'yxati</b>\n"]
            lines.append(f"Jami kam qolgan modellar: {total_low_stock} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) Qator: {item['row_number']}")
                lines.append(f"   Code: {item['code']}")
                lines.append(f"   Quantity: {item['quantity']}")
                if item['date']:
                    lines.append(f"   Sana: {item['date']}")
                lines.append("")  # Empty line between items
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"astatka_low_stock_list:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"astatka_low_stock_list:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_astatka")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing low stock list: {e}")


# ==================== CATALOG PROBLEMATIC LIST ====================

def get_catalog_missing_in_sheets2() -> List[Dict]:
    """
    Sheets1 da bor, lekin Sheets2 da yo'q yoki rasmi yo'q bo'lgan mahsulotlar.
    
    Returns list of dicts with:
    - code: Code from Sheets1
    - quantity: Quantity from Sheets1
    - found_in_sheets2: True/False
    - has_image: True/False (if found in Sheets2)
    - reason: Reason why it's problematic
    """
    sheets1 = CACHE.get("sheets1", [])
    sheets2_full = CACHE.get("sheets2_full", [])
    
    # Build Sheets2 lookup map: {code_normalized: record}
    sheets2_map = {}
    for record in sheets2_full:
        code_norm = record.get("_code_normalized", "").strip()
        if code_norm:
            sheets2_map[code_norm] = record
    
    missing_list = []
    
    for idx, item in enumerate(sheets1):
        code_norm = item.get("code_normalized", "").strip()
        code_original = item.get("code", "").strip()
        quantity = item.get("quantity", "")
        
        # Skip if no code
        if not code_norm or not code_original:
            continue
        
        # Skip if quantity is 0 or empty
        try:
            qty_str = str(quantity).strip().replace(",", ".")
            if not qty_str:
                continue
            qty = float(qty_str)
            if qty <= 0:
                continue
        except (ValueError, TypeError):
            continue
        
        # Check if exists in Sheets2
        if code_norm not in sheets2_map:
            # Not found in Sheets2 at all
            missing_list.append({
                "code": code_original,
                "quantity": str(quantity),
                "found_in_sheets2": False,
                "has_image": False,
                "reason": "Sheets2 da topilmadi"
            })
        else:
            # Found in Sheets2, check if has image
            sheets2_record = sheets2_map[code_norm]
            image_url = (
                sheets2_record.get("image_url") or 
                sheets2_record.get("imageurl") or 
                sheets2_record.get("image url") or 
                sheets2_record.get("image") or 
                ""
            ).strip()
            
            if not image_url:
                # Found but no image
                missing_list.append({
                    "code": code_original,
                    "quantity": str(quantity),
                    "found_in_sheets2": True,
                    "has_image": False,
                    "reason": "Sheets2 da bor, lekin rasm yo'q"
                })
            elif not (image_url.startswith("http://") or image_url.startswith("https://")):
                # Found but invalid image URL
                missing_list.append({
                    "code": code_original,
                    "quantity": str(quantity),
                    "found_in_sheets2": True,
                    "has_image": False,
                    "reason": "Sheets2 da bor, lekin rasm URL noto'g'ri"
                })
    
    return missing_list


def get_catalog_problematic_rows() -> List[Dict]:
    """
    Get all problematic rows from sheets2 with details.
    
    Only counts image_url related problems (file_id is informational, not a problem).
    
    Returns list of dicts with:
    - row_number: Real row number in sheet (starting from 2 if header is row 1)
    - code: Code value
    - model_name: Model name
    - image_url: Raw image_url value
    - reason: Reason why it's problematic
    """
    sheets2_full = CACHE.get("sheets2_full", [])
    problematic = []
    
    for idx, record in enumerate(sheets2_full):
        row_number = idx + 2  # Row 1 is header, so data starts from row 2
        
        # Get code and model_name
        code = record.get("code", "").strip()
        model_name = (
            record.get("model_name") or 
            record.get("nomi") or 
            record.get("model") or 
            ""
        ).strip()
        
        # Get image_url from multiple possible column names
        image_url = (
            record.get("image_url") or 
            record.get("imageurl") or 
            record.get("image url") or 
            record.get("image") or 
            ""
        ).strip()
        
        reasons = []
        
        # A) Check image_url missing
        if not image_url:
            reasons.append("Rasm URL yo'q")
        else:
            # B) Check URL format issues
            if not (image_url.startswith("http://") or image_url.startswith("https://")):
                reasons.append("URL noto'g'ri format")
            # C) Check if it's a drive link
            elif "drive.google.com" in image_url or "docs.google.com" in image_url:
                # It's a drive link - check if file_id can be extracted
                import re
                # Common patterns: /d/FILE_ID/, /file/d/FILE_ID/, ?id=FILE_ID
                file_id_patterns = [
                    r'/d/([a-zA-Z0-9_-]{20,})',
                    r'/file/d/([a-zA-Z0-9_-]{20,})',
                    r'[?&]id=([a-zA-Z0-9_-]{20,})'
                ]
                file_id_found = False
                for pattern in file_id_patterns:
                    match = re.search(pattern, image_url)
                    if match and len(match.group(1)) >= 20:
                        file_id_found = True
                        break
                
                if not file_id_found:
                    reasons.append("Drive file_id ajratib bo'lmadi")
            else:
                # Not a drive link - invalid URL
                reasons.append("URL noto'g'ri format")
        
        # If there are any issues, add to problematic list
        if reasons:
            problematic.append({
                "row_number": row_number,
                "code": code if code else "-",
                "model_name": model_name if model_name else "-",
                "image_url": image_url if image_url else "(bo'sh)",
                "reason": ", ".join(reasons)
            })
    
    return problematic


@router.callback_query(F.data.startswith("catalog_problematic_list:"))
async def catalog_problematic_list_handler(callback_query: CallbackQuery):
    """Show catalog problematic list with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all problematic rows from CACHE
        problematic_rows = get_catalog_problematic_rows()
        total_problems = len(problematic_rows)
        
        # Pagination settings (10-15 items per page as requested)
        items_per_page = 12
        total_pages = (total_problems + items_per_page - 1) // items_per_page if total_problems > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_problems)
        page_items = problematic_rows[start_idx:end_idx]
        
        # Build report text
        if total_problems == 0:
            report = """📂 <b>Modellar katalogi - muammoli ro'yxat</b>

✅ Muammoli qatorlar topilmadi!

Barcha qatorlar to'g'ri."""
        else:
            lines = [f"📂 <b>Modellar katalogi - muammoli ro'yxat</b>\n"]
            lines.append(f"Jami muammolar: {total_problems} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) <b>Qator:</b> {item['row_number']}")
                if item['code'] != "-":
                    lines.append(f"   <b>Model code:</b> {item['code']}")
                if item['model_name'] != "-":
                    lines.append(f"   <b>Model nomi:</b> {item['model_name']}")
                if item['image_url'] != "(bo'sh)":
                    # Truncate long URLs for display
                    display_url = item['image_url'] if len(item['image_url']) <= 50 else item['image_url'][:47] + "..."
                    lines.append(f"   <b>image_url:</b> {display_url}")
                else:
                    lines.append(f"   <b>image_url:</b> (bo'sh)")
                lines.append(f"   <b>Sabab:</b> {item['reason']}\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"catalog_problematic_list:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"catalog_problematic_list:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button - return to catalog section status
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_catalog")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing catalog problematic list: {e}")


@router.callback_query(F.data.startswith("catalog_missing_in_sheets2:"))
async def catalog_missing_in_sheets2_handler(callback_query: CallbackQuery):
    """Show list of products in Sheets1 but missing/no image in Sheets2 with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all missing/problematic items from CACHE
        missing_items = get_catalog_missing_in_sheets2()
        total_items = len(missing_items)
        
        # Pagination settings
        items_per_page = 15
        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        page_items = missing_items[start_idx:end_idx]
        
        # Build report text
        if total_items == 0:
            report = """📊 <b>Sheets1 da bor, Sheets2 da yo'q yoki rasm yo'q</b>

✅ Barcha mahsulotlar mos!

Sheets1 dagi barcha mahsulotlar Sheets2 da mavjud va rasmli."""
        else:
            lines = [f"📊 <b>Sheets1 da bor, Sheets2 da yo'q yoki rasm yo'q</b>\n"]
            lines.append(f"Jami: {total_items} ta mahsulot")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) <b>Code:</b> {item['code']}")
                lines.append(f"   <b>Quantity:</b> {item['quantity']}")
                lines.append(f"   <b>Sabab:</b> {item['reason']}\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"catalog_missing_in_sheets2:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"catalog_missing_in_sheets2:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_catalog")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing catalog missing in sheets2: {e}")


# ==================== READY SIZES CONFIRMED ORDERS LIST ====================

def get_confirmed_orders() -> List[Dict]:
    """
    Get all confirmed orders from sheets6 with details.
    
    Returns list of dicts with:
    - row_number: Real row number in sheet (starting from 2 if header is row 1)
    - model_code: Model code
    - collection: Collection name
    - date: Order date if available
    - confirmed_by: Who confirmed (admin/username/user_id) if available
    - confirmed_time: When confirmed if available
    """
    sheets6 = CACHE.get("sheets6", [])
    confirmed_orders = []
    
    for idx, record in enumerate(sheets6):
        if not is_order_confirmed(record):
            continue
        
        row_number = idx + 2  # Row 1 is header, so data starts from row 2
        
        # Get model code (try multiple possible column names)
        model_code = ""
        for key in ["model_nomi", "model", "code", "model_code"]:
            if key in record:
                model_code = str(record.get(key, "")).strip()
                if model_code:
                    break
        
        # Get collection (try multiple possible column names)
        collection = ""
        for key in ["kolleksiya", "collection", "kolleksiya_nomi", "mahsulot_turi"]:
            if key in record:
                collection = str(record.get(key, "")).strip()
                if collection:
                    break
        
        # Get date (try multiple possible column names)
        order_date = ""
        for key in ["sana", "date", "buyurtma_sana", "order_date"]:
            if key in record:
                order_date = str(record.get(key, "")).strip()
                if order_date:
                    break
        
        # Get who confirmed (try multiple possible column names)
        confirmed_by = ""
        for key in ["tasdiqlagan", "confirmed_by", "admin", "username", "user_id"]:
            if key in record:
                confirmed_by = str(record.get(key, "")).strip()
                if confirmed_by:
                    break
        
        # Get confirmation time (try multiple possible column names)
        confirmed_time = ""
        for key in ["tasdiqlangan_time", "confirmed_time", "confirmed_date", "tasdiq_vaqti"]:
            if key in record:
                confirmed_time = str(record.get(key, "")).strip()
                if confirmed_time:
                    break
        
        confirmed_orders.append({
            "row_number": row_number,
            "model_code": model_code if model_code else "-",
            "collection": collection if collection else "-",
            "date": order_date if order_date else "-",
            "confirmed_by": confirmed_by if confirmed_by else "-",
            "confirmed_time": confirmed_time if confirmed_time else "-",
        })
    
    return confirmed_orders


@router.callback_query(F.data.startswith("ready_sizes_confirmed_list:"))
async def ready_sizes_confirmed_list_handler(callback_query: CallbackQuery):
    """Show confirmed orders list with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all confirmed orders
        confirmed_orders = get_confirmed_orders()
        total_confirmed = len(confirmed_orders)
        
        # Pagination settings
        items_per_page = 20
        total_pages = (total_confirmed + items_per_page - 1) // items_per_page if total_confirmed > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_confirmed)
        page_items = confirmed_orders[start_idx:end_idx]
        
        # Build report text
        if total_confirmed == 0:
            report = """📄 <b>Tasdiqlangan buyurtmalar ro'yxati</b>

Hozircha tasdiqlangan buyurtmalar yo'q."""
        else:
            lines = [f"📄 <b>Tasdiqlangan buyurtmalar ro'yxati</b>\n"]
            lines.append(f"Jami: {total_confirmed} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, order in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) Qator: {order['row_number']}")
                lines.append(f"   Model: {order['model_code']}")
                lines.append(f"   Kolleksiya: {order['collection']}")
                
                if order['date'] != "-":
                    lines.append(f"   Sana: {order['date']}")
                
                if order['confirmed_by'] != "-":
                    # Format username with @ if it doesn't start with @
                    confirmed_by = order['confirmed_by']
                    if confirmed_by and not confirmed_by.startswith('@') and not confirmed_by.isdigit():
                        confirmed_by = f"@{confirmed_by}"
                    lines.append(f"   Tasdiqlagan: {confirmed_by}")
                
                if order['confirmed_time'] != "-":
                    lines.append(f"   Tasdiqlangan vaqt: {order['confirmed_time']}")
                
                lines.append("")  # Empty line between orders
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"ready_sizes_confirmed_list:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"ready_sizes_confirmed_list:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button - return to ready sizes section status
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_ready_sizes")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing confirmed orders list: {e}")


# ==================== READY SIZES EVENT LISTS ====================

@router.callback_query(F.data == "ready_sizes_clear_events")
async def ready_sizes_clear_events_handler(callback_query: CallbackQuery):
    """Clear ready sizes event logs"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    try:
        from services.ready_sizes_events import clear_all_events
        
        # Clear all ready sizes event logs
        clear_all_events()
        
        logger.info(f"Ready sizes event logs cleared by admin {user_id}")
        
        await callback_query.answer("✅ Event loglari tozalandi", show_alert=True)
        
        # Refresh the status page
        report = generate_ready_sizes_status()
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛒 Karzinkaga olinganlar ro'yxati", callback_data="ready_sizes_cart_events:0")],
                [InlineKeyboardButton(text="✅ Tasdiqlangan (sotilgan) buyurtmalar", callback_data="ready_sizes_confirmed_events:0")],
                [InlineKeyboardButton(text="🗑 O'chirilgan/qaytarilgan buyurtmalar", callback_data="ready_sizes_deleted_events:0")],
                [InlineKeyboardButton(text="🧹 Event loglarni tozalash", callback_data="ready_sizes_clear_events")],
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status")]
            ]
        )
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error clearing ready sizes event logs: {e}")
        await callback_query.answer("Xatolik yuz berdi", show_alert=True)


@router.callback_query(F.data.startswith("ready_sizes_cart_events:"))
async def ready_sizes_cart_events_handler(callback_query: CallbackQuery):
    """Show cart events list with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        from services.ready_sizes_events import get_cart_events
        
        # Get all cart events (newest first)
        cart_events = get_cart_events()
        total_events = len(cart_events)
        
        # Pagination settings
        items_per_page = 10
        total_pages = (total_events + items_per_page - 1) // items_per_page if total_events > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_events)
        page_items = cart_events[start_idx:end_idx]
        
        # Build report text
        if total_events == 0:
            report = """🛒 <b>Karzinkaga olinganlar ro'yxati</b>

Hozircha hech kim karzinkaga mahsulot olmagan."""
        else:
            lines = [f"🛒 <b>Karzinkaga olinganlar ro'yxati</b>\n"]
            lines.append(f"Jami: {total_events} ta event")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, event in enumerate(page_items, start=start_idx + 1):
                # Convert UTC time to Uzbekistan time (UTC+5)
                uz_time = event.timestamp + timedelta(hours=5)
                time_str = uz_time.strftime("%d.%m.%Y %H:%M")
                
                lines.append(f"{i}. <b>Mahsulot kodi:</b> {event.code}")
                if event.model_nomi:
                    lines.append(f"   <b>Model nomi:</b> {event.model_nomi}")
                lines.append(f"   <b>Razmer:</b> {event.razmer if event.razmer else '-'}")
                lines.append(f"   <b>Miqdor:</b> {event.qty} ta")
                
                # Show who added to cart
                if event.role == "Hamkor" and event.seller_name:
                    # Partner with seller
                    lines.append(f"   <b>Hamkor:</b> {event.user_name} (ID: {event.user_id})")
                    lines.append(f"   <b>Sotuvchi:</b> {event.seller_name}")
                elif event.role == "Hamkor":
                    # Partner without seller
                    lines.append(f"   <b>Hamkor:</b> {event.user_name} (ID: {event.user_id})")
                else:
                    # Regular user or other roles
                    lines.append(f"   <b>Olgan ({event.role}):</b> {event.user_name} (ID: {event.user_id})")
                
                lines.append(f"   <b>Vaqt:</b> {time_str} (UZ)\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"ready_sizes_cart_events:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"ready_sizes_cart_events:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_ready_sizes")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error showing cart events list: {e}")


@router.callback_query(F.data.startswith("ready_sizes_confirmed_events:"))
async def ready_sizes_confirmed_events_handler(callback_query: CallbackQuery):
    """Show confirmed events list with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        from services.ready_sizes_events import get_confirmed_events
        
        # Get all confirmed events (newest first)
        confirmed_events = get_confirmed_events()
        total_events = len(confirmed_events)
        
        # Pagination settings
        items_per_page = 10
        total_pages = (total_events + items_per_page - 1) // items_per_page if total_events > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_events)
        page_items = confirmed_events[start_idx:end_idx]
        
        # Build report text
        if total_events == 0:
            report = """✅ <b>Tasdiqlangan (sotilgan) buyurtmalar</b>

Hozircha tasdiqlangan buyurtmalar yo'q."""
        else:
            lines = [f"✅ <b>Tasdiqlangan (sotilgan) buyurtmalar</b>\n"]
            lines.append(f"Jami: {total_events} ta event")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, event in enumerate(page_items, start=start_idx + 1):
                # Convert UTC time to Uzbekistan time (UTC+5)
                uz_time = event.timestamp + timedelta(hours=5)
                time_str = uz_time.strftime("%d.%m.%Y %H:%M")
                
                lines.append(f"\n{i}. 📌 <b>Mahsulot kodi:</b> {event.code}")
                if event.model_nomi:
                    lines.append(f"📌 <b>Model nomi:</b> {event.model_nomi}")
                lines.append(f"📌 <b>Razmer:</b> {event.razmer if event.razmer else '-'}")
                lines.append(f"📌 <b>Miqdor:</b> {event.qty} ta")
                
                # Show buyer (oluvchi) - hamkor or foydalanuvchi
                if event.role == "Hamkor" and event.seller_name:
                    # Partner with seller
                    lines.append(f"👤 <b>Hamkor:</b> {event.user_name} (ID: {event.user_id})")
                    lines.append(f"🏪 <b>Sotuvchi:</b> {event.seller_name}")
                elif event.role == "Hamkor":
                    # Partner without seller
                    lines.append(f"👤 <b>Hamkor:</b> {event.user_name} (ID: {event.user_id})")
                else:
                    # Regular user or other roles
                    lines.append(f"👤 <b>Oluvchi ({event.role}):</b> {event.user_name} (ID: {event.user_id})")
                
                # Show confirmer (tasdiqlagan) - admin or ofis
                if event.confirmer_id and event.confirmer_name:
                    confirmer_role_display = event.confirmer_role if event.confirmer_role else "Admin"
                    lines.append(f"🛡 <b>Tasdiqlagan ({confirmer_role_display}):</b> {event.confirmer_name} (ID: {event.confirmer_id})")
                else:
                    lines.append(f"🛡 <b>Tasdiqlagan:</b> noma'lum")
                
                lines.append(f"🕒 <b>Vaqt:</b> {time_str} (UZ)\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"ready_sizes_confirmed_events:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"ready_sizes_confirmed_events:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_ready_sizes")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing confirmed events list: {e}")


@router.callback_query(F.data.startswith("ready_sizes_deleted_events:"))
async def ready_sizes_deleted_events_handler(callback_query: CallbackQuery):
    """Show deleted events list with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        from services.ready_sizes_events import get_deleted_events
        
        # Get all deleted events (newest first)
        deleted_events = get_deleted_events()
        total_events = len(deleted_events)
        
        # Pagination settings
        items_per_page = 10
        total_pages = (total_events + items_per_page - 1) // items_per_page if total_events > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_events)
        page_items = deleted_events[start_idx:end_idx]
        
        # Build report text
        if total_events == 0:
            report = """🗑 <b>O'chirilgan/qaytarilgan buyurtmalar</b>

Hozircha o'chirilgan buyurtmalar yo'q."""
        else:
            lines = [f"🗑 <b>O'chirilgan/qaytarilgan buyurtmalar</b>\n"]
            lines.append(f"Jami: {total_events} ta event")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, event in enumerate(page_items, start=start_idx + 1):
                # Convert UTC time to Uzbekistan time (UTC+5)
                uz_time = event.timestamp + timedelta(hours=5)
                time_str = uz_time.strftime("%d.%m.%Y %H:%M")
                
                lines.append(f"{i}. <b>Mahsulot kodi:</b> {event.code}")
                if event.model_nomi:
                    lines.append(f"   <b>Model nomi:</b> {event.model_nomi}")
                lines.append(f"   <b>Razmer:</b> {event.razmer if event.razmer else '-'}")
                lines.append(f"   <b>Miqdor:</b> {event.qty} ta")
                
                # Show who deleted
                if event.role == "Hamkor" and event.seller_name:
                    # Partner with seller
                    lines.append(f"   <b>Hamkor:</b> {event.user_name} (ID: {event.user_id})")
                    lines.append(f"   <b>Sotuvchi:</b> {event.seller_name}")
                elif event.role == "Hamkor":
                    # Partner without seller
                    lines.append(f"   <b>Hamkor:</b> {event.user_name} (ID: {event.user_id})")
                else:
                    # Regular user or other roles
                    lines.append(f"   <b>O'chirdi ({event.role}):</b> {event.user_name} (ID: {event.user_id})")
                
                lines.append(f"   <b>Vaqt:</b> {time_str} (UZ)\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"ready_sizes_deleted_events:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"ready_sizes_deleted_events:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_ready_sizes")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing deleted events list: {e}")


# ==================== DISCOUNT MODELS DETAIL LISTS ====================

def get_discount_low_stock_list() -> List[Dict]:
    """Get list of discount models with low stock (0 < quantity < 50)"""
    analysis = analyze_discount_models()
    return analysis.get("low_stock_list", [])


def get_discount_out_of_stock_list() -> List[Dict]:
    """Get list of discount models that are out of stock (quantity = 0)"""
    analysis = analyze_discount_models()
    return analysis.get("out_of_stock_list", [])


def get_discount_quantity_not_found_list() -> List[Dict]:
    """Get list of discount models where quantity was not found in Sheets1"""
    analysis = analyze_discount_models()
    return analysis.get("quantity_not_found_list", [])


def get_discount_image_issues_list() -> List[Dict]:
    """Get list of discount models with image issues in Sheets4"""
    analysis = analyze_discount_models()
    return analysis.get("image_issues_list", [])


@router.callback_query(F.data.startswith("discount_low_stock:"))
async def discount_low_stock_handler(callback_query: CallbackQuery):
    """Show list of discount models with low stock (0 < quantity < 50) with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all low stock models from CACHE
        low_stock = get_discount_low_stock_list()
        total_items = len(low_stock)
        
        # Pagination settings (10-15 per page as requested)
        items_per_page = 15
        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        page_items = low_stock[start_idx:end_idx]
        
        # Build report text
        if total_items == 0:
            report = """🟠 <b>Kam qolgan modellar</b>

✅ Kam qolgan modellar yo'q!

Barcha modellarning qoldiqlari yetarli."""
        else:
            lines = [f"🟠 <b>Kam qolgan modellar</b>\n"]
            lines.append(f"Jami: {total_items} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) <b>Mahsulot kodi:</b> {item.get('code', 'N/A')}")
                lines.append(f"   <b>Model nomi:</b> {item['model_name']}")
                lines.append(f"   <b>Sheets4 qator:</b> {item['row_number']}")
                lines.append(f"   <b>Sheets1 dagi quantity:</b> {item['quantity']}")
                lines.append(f"   <b>Sabab:</b> {item['reason']}\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"discount_low_stock:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"discount_low_stock:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_discount")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing discount low stock list: {e}")


@router.callback_query(F.data.startswith("discount_out_of_stock:"))
async def discount_out_of_stock_handler(callback_query: CallbackQuery):
    """Show list of discount models that are out of stock (quantity = 0) with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all out of stock models from CACHE
        out_of_stock = get_discount_out_of_stock_list()
        total_items = len(out_of_stock)
        
        # Pagination settings (10-15 per page as requested)
        items_per_page = 15
        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        page_items = out_of_stock[start_idx:end_idx]
        
        # Build report text
        if total_items == 0:
            report = """🔴 <b>Tugagan modellar</b>

✅ Tugagan modellar yo'q!

Barcha modellar qoldiqda mavjud."""
        else:
            lines = [f"🔴 <b>Tugagan modellar</b>\n"]
            lines.append(f"Jami: {total_items} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) <b>Mahsulot kodi:</b> {item.get('code', 'N/A')}")
                lines.append(f"   <b>Model nomi:</b> {item['model_name']}")
                lines.append(f"   <b>Sheets4 qator:</b> {item['row_number']}")
                lines.append(f"   <b>Sheets1 dagi quantity:</b> {item['quantity']}")
                lines.append(f"   <b>Sabab:</b> {item['reason']}\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"discount_out_of_stock:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"discount_out_of_stock:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_discount")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing discount out of stock list: {e}")


@router.callback_query(F.data.startswith("discount_quantity_not_found:"))
async def discount_quantity_not_found_handler(callback_query: CallbackQuery):
    """Show list of discount models where quantity was not found in Sheets1 with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all models with quantity not found from CACHE
        quantity_not_found = get_discount_quantity_not_found_list()
        total_items = len(quantity_not_found)
        
        # Pagination settings (10-15 per page as requested)
        items_per_page = 15
        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        page_items = quantity_not_found[start_idx:end_idx]
        
        # Build report text
        if total_items == 0:
            report = """📉 <b>Qoldiq topilmagan modellar</b>

✅ Barcha modellar uchun qoldiq topildi!

Chegirmadagi barcha modellar Sheets1 da mavjud."""
        else:
            lines = [f"📉 <b>Qoldiq topilmagan modellar</b>\n"]
            lines.append(f"Jami: {total_items} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) <b>Mahsulot kodi:</b> {item.get('code', 'N/A')}")
                lines.append(f"   <b>Model nomi:</b> {item['model_name']}")
                lines.append(f"   <b>Sheets4 qator:</b> {item['row_number']}")
                lines.append(f"   <b>Sheets1 dagi quantity:</b> {item['quantity']}")
                lines.append(f"   <b>Sabab:</b> {item['reason']}\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"discount_quantity_not_found:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"discount_quantity_not_found:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_discount")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing discount quantity not found list: {e}")


@router.callback_query(F.data.startswith("discount_image_issues:"))
async def discount_image_issues_handler(callback_query: CallbackQuery):
    """Show list of discount models with image issues with pagination"""
    user_id = callback_query.from_user.id
    
    if not is_any_admin(user_id):
        await callback_query.answer("⛔️ Sizda admin huquqlari yo'q", show_alert=True)
        return
    
    # Answer immediately to prevent bot freezing
    await callback_query.answer()
    
    try:
        # Parse page number from callback data
        page = int(callback_query.data.split(":")[-1])
    except (ValueError, IndexError):
        page = 0
    
    try:
        # Get all models with image issues from CACHE
        image_issues = get_discount_image_issues_list()
        total_items = len(image_issues)
        
        # Pagination settings (10-15 per page as requested)
        items_per_page = 15
        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
        page = max(0, min(page, total_pages - 1))  # Clamp page number
        
        start_idx = page * items_per_page
        end_idx = min(start_idx + items_per_page, total_items)
        page_items = image_issues[start_idx:end_idx]
        
        # Build report text
        if total_items == 0:
            report = """🖼 <b>Rasm muammosi bo'lgan modellar</b>

✅ Rasm muammolari yo'q!

Barcha modellarning rasmlari to'g'ri."""
        else:
            lines = [f"🖼 <b>Rasm muammosi bo'lgan modellar</b>\n"]
            lines.append(f"Jami: {total_items} ta")
            lines.append(f"Sahifa: {page + 1}/{total_pages}\n")
            
            for i, item in enumerate(page_items, start=start_idx + 1):
                lines.append(f"{i}) <b>Mahsulot kodi:</b> {item.get('code', 'N/A')}")
                lines.append(f"   <b>Model nomi:</b> {item['model_name']}")
                lines.append(f"   <b>Sheets4 qator:</b> {item['row_number']}")
                
                # Show image URL if exists (truncated)
                if item['image_url']:
                    display_url = item['image_url'] if len(item['image_url']) <= 50 else item['image_url'][:47] + "..."
                    lines.append(f"   <b>image_url:</b> {display_url}")
                
                lines.append(f"   <b>Sabab:</b> {item['reason']}\n")
            
            report = "\n".join(lines)
        
        # Build keyboard with pagination
        keyboard_buttons = []
        
        # Pagination buttons
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"discount_image_issues:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"discount_image_issues:{page + 1}"))
            if nav_row:
                keyboard_buttons.append(nav_row)
        
        # Back button
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="section_status_discount")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback_query.message.edit_text(report, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing discount image issues list: {e}")

