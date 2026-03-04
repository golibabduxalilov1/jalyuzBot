import asyncio
import logging
import time
from typing import Dict, List, Optional

import aiohttp
import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_SHEET_ID, SERVICE_ACCOUNT_FILE, BASE_DIR, GOOGLE_WORKSHEET_NAME, DEFAULT_IMAGE_URL
from services.product_utils import normalize_code, generate_fuzzy_code_variants
from services.lru_cache import LRUCache

logger = logging.getLogger(__name__)

# ==================== GLOBAL IN-MEMORY CACHE ====================
# Cache structure: { "sheets1": [...], "sheets2": {...}, "sheets2_full": [...], "sheets3": [...], "sheets4": [...], "sheets5": [...], "sheets6": [...], "collection_index": {...} }
CACHE: Dict[str, any] = {
    "sheets1": [],
    "sheets2": {},  # Dict[str, str] - {normalize(code): image_url} - backward compatibility
    "sheets2_full": [],  # List[Dict] - Full sheets2 data with all columns (rang, tur, naqsh, etc.)
    "sheets3": [],
    "sheets4": [],
    "sheets5": [],  # List[Dict] - Tayyor razmerlar: code, razmer, manzil, nomi, kolleksiya, image_url
    "sheets6": [],  # List[Dict] - Tayyor razmerlar: code, razmer, model_nomi, kolleksiya, image_url
    "image_map": LRUCache(max_size=10000),  # LRU Cache - {normalize(code): file_id} - faqat file_id saqlanadi, max 10k items
    "collection_index": {}  # Dict[str, List[Dict]] - {collection_name: [products]} - Fast collection lookup
}


# ==================== CACHE MANAGEMENT FUNCTIONS ====================

def _rebuild_collection_index_from_sheets1() -> None:
    """
    Rebuild collection_index from CACHE["sheets1"].

    Astatka "Kolleksiya bo'yicha bilish" reads from this index, so it must be
    refreshed every time sheets1 is reloaded.
    """
    collection_index: Dict[str, List[Dict]] = {}
    for product in CACHE.get("sheets1", []):
        coll = str(product.get("collection", "")).strip()
        if not coll:
            continue
        if coll not in collection_index:
            collection_index[coll] = []
        collection_index[coll].append(product)

    CACHE["collection_index"] = collection_index
    logger.info(f"Created collection index with {len(collection_index)} collections")

async def _load_sheets1_direct(sheet_service: 'GoogleSheetService') -> List[Dict]:
    """Load sheets1 directly from Google Sheets (without cache check)"""
    await sheet_service._ensure_client()
    
    def _fetch():
        spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
        worksheet = None
        target_name = (sheet_service.products_sheet_name or "").strip()
        if target_name:
            try:
                worksheet = spreadsheet.worksheet(target_name)
            except Exception:
                worksheet = spreadsheet.sheet1
        else:
            worksheet = spreadsheet.sheet1
        
        raw_values = worksheet.get_all_values()
        if not raw_values:
            return []
        
        headers = raw_values[0]
        data_rows = raw_values[1:]
        normalized_records = []
        
        for row_values in data_rows:
            if len(row_values) < len(headers):
                row_values = row_values + [""] * (len(headers) - len(row_values))
            elif len(row_values) > len(headers):
                row_values = row_values[:len(headers)]
            
            record = {headers[idx]: row_values[idx] for idx in range(len(headers))}
            normalized_records.append(sheet_service._normalize_record(record))
        
        return normalized_records
    
    records = await asyncio.to_thread(_fetch)
    # Process products format
    products = []
    for row in records:
        qty_original = row.get("quantity")
        qty_str = "" if qty_original is None else str(qty_original).strip()
        row_copy = row.copy()
        row_copy["quantity"] = qty_str
        products.append(row_copy)
    
    return products


async def _load_sheets2_direct(sheet_service: 'GoogleSheetService') -> Dict[str, str]:
    """Load sheets2 directly from Google Sheets (without cache check) - backward compatibility"""
    await sheet_service._ensure_client()
    
    def _fetch():
        spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
        try:
            worksheet = spreadsheet.worksheet(sheet_service.images_sheet_name)
        except Exception:
            return {}
        
        raw_values = worksheet.get_all_values()
        if not raw_values or len(raw_values) < 2:
            return {}
        
        headers = raw_values[0]
        code_idx = None
        image_idx = None
        
        for idx, header in enumerate(headers):
            header_lower = (header or "").strip().lower()
            if header_lower == "code":
                code_idx = idx
            elif header_lower in ["image_url", "imageurl", "image url", "image"]:
                image_idx = idx
        
        if code_idx is None:
            return {}
        
        image_map: Dict[str, str] = {}
        for row in raw_values[1:]:
            if code_idx >= len(row):
                continue
            code_value = row[code_idx] if code_idx < len(row) else ""
            if not code_value:
                continue
            
            image_value = ""
            if image_idx is not None and image_idx < len(row):
                image_value = (row[image_idx] or "").strip()
            
            code_norm = normalize_code(code_value)
            if code_norm:
                image_map[code_norm] = image_value
        
        return image_map
    
    return await asyncio.to_thread(_fetch)


async def _load_sheets2_full_direct(sheet_service: 'GoogleSheetService') -> List[Dict]:
    """
    Load sheets2 full data with all columns ONCE.
    Returns list of dicts with: code, image_url, color/rang, type/turi, collection/kolleksiya, material, etc.
    This replaces the need for separate image map loading.
    """
    await sheet_service._ensure_client()
    
    def _fetch():
        spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
        try:
            worksheet = spreadsheet.worksheet(sheet_service.images_sheet_name)
        except Exception:
            return []
        
        raw_values = worksheet.get_all_values()
        if not raw_values or len(raw_values) < 2:
            return []
        
        headers = [h.strip().lower() if h else "" for h in raw_values[0]]
        
        # Find column indices
        code_idx = None
        image_idx = None
        for idx, header in enumerate(headers):
            if header == "code":
                code_idx = idx
            elif header in ["image_url", "imageurl", "image url", "image"]:
                image_idx = idx
        
        if code_idx is None:
            return []
        
        records = []
        image_map = {}  # For backward compatibility
        
        for row in raw_values[1:]:
            # Pad row to match headers length
            while len(row) < len(headers):
                row.append("")
            
            code_value = (row[code_idx] if code_idx < len(row) else "").strip()
            if not code_value:
                continue
            
            record = {}
            # Store all columns with lowercase header names
            for idx, header in enumerate(headers):
                value = (row[idx] if idx < len(row) else "").strip()
                record[header] = value
            
            # Ensure code exists and normalize
            code_normalized = normalize_code(code_value)
            if code_normalized:
                # Add normalized code for easy lookup
                record["_code_normalized"] = code_normalized
                records.append(record)
                
                # Also populate image_map for backward compatibility
                image_url = ""
                if image_idx is not None and image_idx < len(row):
                    image_url = (row[image_idx] or "").strip()
                image_map[code_normalized] = image_url
        
        # Store image_map in records for backward compatibility access
        # We'll use records directly, but keep image_map for any legacy code
        logger.info(f"Sheets2 loaded once: {len(records)} records with all fields")
        return records
    
    return await asyncio.to_thread(_fetch)


async def _load_sheets3_direct(sheet_service: 'GoogleSheetService') -> List[Dict]:
    """Load sheets3 directly from Google Sheets (without cache check)"""
    await sheet_service._ensure_client()
    
    def _fetch():
        spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
        try:
            worksheet = spreadsheet.worksheet("sheets3")
        except Exception:
            return []
        
        raw_values = worksheet.get_all_values()
        if not raw_values or len(raw_values) < 2:
            return []
        
        headers = raw_values[0]
        data_rows = raw_values[1:]
        
        def normalize_header(header):
            if not header:
                return ""
            return str(header).strip().lower().replace(" ", "").replace("_", "")
        
        code_idx = None
        model_name_idx = None
        asosiy_idx = None
        mini_idx = None
        kasetniy_idx = None
        asosiy_qimmat_idx = None
        mini_qimmat_idx = None
        kasetniy_qimmat_idx = None
        izoh_idx = None
        collection_idx = None
        
        for idx, header in enumerate(headers):
            header_norm = normalize_header(header)
            header_original = str(header).strip() if header else ""
            header_original_lower = header_original.lower()
            
            if header_norm in ["code", "kod", "код"]:
                code_idx = idx
            elif header_norm in ["modelnomi", "model_name", "modelname", "модель", "nomi", "madelnomi", "madel_nomi"] or header_original_lower == "madel nomi" or "madel" in header_original_lower and "nomi" in header_original_lower:
                model_name_idx = idx
            elif header_norm in ["asosiy", "основной"]:
                asosiy_idx = idx
            elif header_norm in ["mini", "мини"]:
                mini_idx = idx
            elif header_norm in ["kasetniy", "kasetni", "кассетный", "kaset"]:
                kasetniy_idx = idx
            elif header_norm in ["asosiy qimmat", "asosiyqimmat", "asosiy_qimmat", "основной дорогой"]:
                asosiy_qimmat_idx = idx
            elif header_norm in ["mini qimmat", "miniqimmat", "mini_qimmat", "мини дорогой"]:
                mini_qimmat_idx = idx
            elif header_norm in ["kasetniy qimmat", "kasetniyqimmat", "kasetniy_qimmat", "кассетный дорогой"]:
                kasetniy_qimmat_idx = idx
            elif header_norm in ["izoh", "izox", "примечание", "комментарий", "comment"]:
                izoh_idx = idx
            elif header_norm in ["collection", "kolleksiya", "коллекция", "коллекция"]:
                collection_idx = idx
        
        if code_idx is None:
            return []
        
        records = []
        for row in data_rows:
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            elif len(row) > len(headers):
                row = row[:len(headers)]
            
            code = str(row[code_idx]).strip() if code_idx is not None and code_idx < len(row) else ""
            model_name = str(row[model_name_idx]).strip() if model_name_idx is not None and model_name_idx < len(row) else ""
            asosiy_price = str(row[asosiy_idx]).strip() if asosiy_idx is not None and asosiy_idx < len(row) else ""
            mini_price = str(row[mini_idx]).strip() if mini_idx is not None and mini_idx < len(row) else ""
            kasetniy_price = str(row[kasetniy_idx]).strip() if kasetniy_idx is not None and kasetniy_idx < len(row) else ""
            asosiy_qimmat = str(row[asosiy_qimmat_idx]).strip() if asosiy_qimmat_idx is not None and asosiy_qimmat_idx < len(row) else ""
            mini_qimmat = str(row[mini_qimmat_idx]).strip() if mini_qimmat_idx is not None and mini_qimmat_idx < len(row) else ""
            kasetniy_qimmat = str(row[kasetniy_qimmat_idx]).strip() if kasetniy_qimmat_idx is not None and kasetniy_qimmat_idx < len(row) else ""
            izoh = str(row[izoh_idx]).strip() if izoh_idx is not None and izoh_idx < len(row) else ""
            collection = str(row[collection_idx]).strip() if collection_idx is not None and collection_idx < len(row) else ""
            
            if code:
                records.append({
                    "code": code,
                    "code_normalized": normalize_code(code),
                    "collection": collection,
                    "model_name": model_name,
                    "asosiy_price": asosiy_price,
                    "mini_price": mini_price,
                    "kasetniy_price": kasetniy_price,
                    "izoh": izoh,
                    "asosiy_qimmat": asosiy_qimmat,
                    "mini_qimmat": mini_qimmat,
                    "kasetniy_qimmat": kasetniy_qimmat
                })
        
        return records
    
    return await asyncio.to_thread(_fetch)


async def _load_sheets4_direct(sheet_service: 'GoogleSheetService') -> List[Dict]:
    """Load sheets4 directly from Google Sheets (without cache check)"""
    await sheet_service._ensure_client()
    
    def _fetch():
        spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
        try:
            worksheet = spreadsheet.worksheet("sheets4")
        except Exception:
            return []
        
        raw_values = worksheet.get_all_values()
        if not raw_values or len(raw_values) < 2:
            return []
        
        data_rows = raw_values[1:]
        records: List[Dict] = []
        
        for row in data_rows:
            # Sheets4 da faqat 5 ta ustun bor: A, B, C, D, E
            while len(row) < 5:
                row.append("")
            
            code = str(row[0]).strip() if len(row) > 0 else ""              # A: code
            quantity = str(row[1]).strip() if len(row) > 1 else ""          # B: quantity (ishlatilmaydi, Sheets1 dan olinadi)
            collection = str(row[2]).strip() if len(row) > 2 else ""        # C: collection (ishlatilmaydi, Sheets1 dan olinadi)
            model_name = str(row[3]).strip() if len(row) > 3 else ""        # D: Madel nomi
            image_url = str(row[4]).strip() if len(row) > 4 else ""         # E: image_url
            
            if code:
                records.append({
                    "code": code,
                    "code_normalized": normalize_code(code),
                    "model_name": model_name,
                    "image_url": image_url,
                })
        
        return records
    
    return await asyncio.to_thread(_fetch)


async def _load_sheets5_direct(sheet_service: 'GoogleSheetService') -> List[Dict]:
    """Load sheets5 directly from Google Sheets (without cache check)"""
    await sheet_service._ensure_client()
    
    def _fetch():
        spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
        try:
            worksheet = spreadsheet.worksheet("sheets5")
        except Exception:
            return []
        
        raw_values = worksheet.get_all_values()
        if not raw_values or len(raw_values) < 2:
            return []
        
        # Headers ni topish (case-insensitive)
        headers = raw_values[0]
        header_map = {}
        for idx, header in enumerate(headers):
            header_lower = (header or "").strip().lower()
            # Bo'sh joylarni olib tashlash va underscore/slash bilan ishlash
            header_normalized = header_lower.replace(" ", "_").replace("/", "_")
            header_map[header_normalized] = idx
            header_map[header_lower] = idx  # Asl variantni ham saqlash
        
        # Ustunlar: code, razmer, magazin, mahsulot_turi, nomi (yoki model_nomi), kolleksiya, image_url
        code_idx = header_map.get("code") or header_map.get("код") or 0
        razmer_idx = header_map.get("razmer") or header_map.get("размер") or 1
        magazin_idx = header_map.get("magazin") or header_map.get("магазин") or 2
        mahsulot_turi_idx = header_map.get("mahsulot_turi") or header_map.get("mahsulot turi") or header_map.get("тип") or 3
        # Model nomi uchun turli variantlar
        nomi_idx = (header_map.get("nomi") or header_map.get("model_nomi") or 
                   header_map.get("model nomi") or header_map.get("model") or 
                   header_map.get("название") or header_map.get("модель") or 4)
        kolleksiya_idx = header_map.get("kolleksiya") or header_map.get("коллекция") or 5
        image_url_idx = header_map.get("image_url") or header_map.get("imageurl") or header_map.get("image url") or header_map.get("image") or 6
        
        data_rows = raw_values[1:]
        records: List[Dict] = []
        
        for row in data_rows:
            # Ustunlar sonini tekshirish
            max_idx = max(code_idx, razmer_idx, magazin_idx, mahsulot_turi_idx, nomi_idx, kolleksiya_idx, image_url_idx)
            while len(row) <= max_idx:
                row.append("")
            
            code = str(row[code_idx]).strip() if code_idx < len(row) else ""
            razmer = str(row[razmer_idx]).strip() if razmer_idx < len(row) else ""
            magazin = str(row[magazin_idx]).strip() if magazin_idx < len(row) else ""
            mahsulot_turi = str(row[mahsulot_turi_idx]).strip() if mahsulot_turi_idx < len(row) else ""
            nomi = str(row[nomi_idx]).strip() if nomi_idx < len(row) else ""
            kolleksiya = str(row[kolleksiya_idx]).strip() if kolleksiya_idx < len(row) else ""
            image_url = str(row[image_url_idx]).strip() if image_url_idx < len(row) else ""
            
            # Majburiy maydonlar: code, razmer, magazin, nomi
            if code:
                records.append({
                    "code": code,
                    "code_normalized": normalize_code(code),
                    "razmer": razmer if razmer else "noma'lum",
                    "magazin": magazin if magazin else "noma'lum",
                    "mahsulot_turi": mahsulot_turi if mahsulot_turi else "noma'lum",
                    "nomi": nomi if nomi else "noma'lum",
                    "kolleksiya": kolleksiya if kolleksiya else "noma'lum",
                    "image_url": image_url,  # Faqat ichki logika uchun, userga chiqmaydi
                })
        
        return records
    
    return await asyncio.to_thread(_fetch)


async def _load_sheets6_direct(sheet_service: 'GoogleSheetService') -> List[Dict]:
    """Load sheets6 directly from Google Sheets (without cache check)"""
    await sheet_service._ensure_client()
    
    def _fetch():
        spreadsheet = sheet_service.client.open_by_key(sheet_service.sheet_id)
        try:
            worksheet = spreadsheet.worksheet("sheets6")
        except Exception:
            return []
        
        raw_values = worksheet.get_all_values()
        if not raw_values or len(raw_values) < 2:
            return []
        
        # Headers ni topish (case-insensitive)
        headers = raw_values[0]
        header_map = {}
        for idx, header in enumerate(headers):
            header_lower = (header or "").strip().lower()
            # Bo'sh joylarni olib tashlash va underscore/slash bilan ishlash
            header_normalized = header_lower.replace(" ", "_").replace("/", "_")
            header_map[header_normalized] = idx
            header_map[header_lower] = idx  # Asl variantni ham saqlash
        
        # Ustunlar: code, razmer, shtuk, model_nomi, kolleksiya, image_url
        code_idx = header_map.get("code") or header_map.get("код") or 0
        razmer_idx = header_map.get("razmer") or header_map.get("размер") or 1
        shtuk_idx = header_map.get("shtuk") or header_map.get("штук") or header_map.get("soni") or header_map.get("miqdor") or 2
        model_nomi_idx = (header_map.get("model_nomi") or header_map.get("model nomi") or 
                         header_map.get("model") or header_map.get("nomi") or 
                         header_map.get("название") or header_map.get("модель") or 3)
        kolleksiya_idx = header_map.get("kolleksiya") or header_map.get("коллекция") or 4
        image_url_idx = header_map.get("image_url") or header_map.get("imageurl") or header_map.get("image url") or header_map.get("image") or 5
        
        data_rows = raw_values[1:]
        records: List[Dict] = []
        
        for row in data_rows:
            # Ustunlar sonini tekshirish
            max_idx = max(code_idx, razmer_idx, shtuk_idx, model_nomi_idx, kolleksiya_idx, image_url_idx)
            while len(row) <= max_idx:
                row.append("")
            
            code = str(row[code_idx]).strip() if code_idx < len(row) else ""
            razmer = str(row[razmer_idx]).strip() if razmer_idx < len(row) else ""
            shtuk = str(row[shtuk_idx]).strip() if shtuk_idx < len(row) else ""
            model_nomi = str(row[model_nomi_idx]).strip() if model_nomi_idx < len(row) else ""
            kolleksiya = str(row[kolleksiya_idx]).strip() if kolleksiya_idx < len(row) else ""
            image_url = str(row[image_url_idx]).strip() if image_url_idx < len(row) else ""
            
            # Majburiy maydonlar: code
            if code:
                records.append({
                    "code": code,
                    "code_normalized": normalize_code(code),
                    "razmer": razmer if razmer else "",
                    "shtuk": shtuk if shtuk else "",
                    "model_nomi": model_nomi if model_nomi else "",
                    "kolleksiya": kolleksiya if kolleksiya else "",
                    "image_url": image_url,
                })
        
        return records
    
    return await asyncio.to_thread(_fetch)


async def load_all_sheets_to_cache():
    """Load all sheets into global CACHE on bot startup"""
    logger.info("Loading all sheets into cache...")
    sheet_service = GoogleSheetService()
    
    try:
        # Load sheets1
        CACHE["sheets1"] = await _load_sheets1_direct(sheet_service)
        logger.info(f"Loaded {len(CACHE['sheets1'])} records from sheets1")
        
        # Build collection index for fast lookups
        _rebuild_collection_index_from_sheets1()
        
        # Load sheets2 ONCE with all fields
        sheets2_full = await _load_sheets2_full_direct(sheet_service)
        CACHE["sheets2_full"] = sheets2_full
        logger.info(f"Sheets2 loaded once: {len(sheets2_full)} records with all fields")
        
        # Create backward-compatible image map from sheets2_full
        image_map = {}
        for record in sheets2_full:
            code_norm = record.get("_code_normalized", "")
            if code_norm:
                # Get image_url from various possible column names
                image_url = (record.get("image_url") or record.get("imageurl") or 
                           record.get("image url") or record.get("image") or "")
                if image_url and image_url.strip():
                    # Convert Google Drive link to direct image URL format
                    image_url_clean = image_url.strip()
                    if 'drive.google.com' in image_url_clean:
                        # Extract file ID and convert to direct link format
                        file_id = None
                        if '/file/d/' in image_url_clean:
                            try:
                                file_id = image_url_clean.split('/file/d/')[1].split('/')[0]
                            except Exception:
                                pass
                        elif 'id=' in image_url_clean:
                            try:
                                file_id = image_url_clean.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
                            except Exception:
                                pass
                        if file_id:
                            image_url_clean = f"https://drive.google.com/uc?export=view&id={file_id}"
                    image_map[code_norm] = image_url_clean
        
        # Load sheets3
        CACHE["sheets3"] = await _load_sheets3_direct(sheet_service)
        logger.info(f"Loaded {len(CACHE['sheets3'])} records from sheets3")
        
        # Load sheets4
        CACHE["sheets4"] = await _load_sheets4_direct(sheet_service)
        logger.info(f"Loaded {len(CACHE['sheets4'])} records from sheets4")
        
        # Add sheets4 image_url to image_map (only if code not already exists)
        sheets4_added = 0
        for record in CACHE["sheets4"]:
            code_norm = record.get("code_normalized", "")
            if code_norm and code_norm not in image_map:  # Don't overwrite existing entries
                image_url = record.get("image_url", "").strip()
                if image_url:
                    # Convert Google Drive link to direct image URL format
                    if 'drive.google.com' in image_url:
                        file_id = None
                        if '/file/d/' in image_url:
                            try:
                                file_id = image_url.split('/file/d/')[1].split('/')[0]
                            except Exception:
                                pass
                        elif 'id=' in image_url:
                            try:
                                file_id = image_url.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
                            except Exception:
                                pass
                        if file_id:
                            image_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                    image_map[code_norm] = image_url
                    sheets4_added += 1
        logger.info(f"Added {sheets4_added} image URLs from sheets4 to image_map")
        
        # Load sheets5
        CACHE["sheets5"] = await _load_sheets5_direct(sheet_service)
        logger.info(f"Loaded {len(CACHE['sheets5'])} records from sheets5")
        
        # Load sheets6
        CACHE["sheets6"] = await _load_sheets6_direct(sheet_service)
        logger.info(f"Loaded {len(CACHE['sheets6'])} records from sheets6")
        
        # Add sheets5 image_url to image_map (only if code not already exists)
        sheets5_added = 0
        for record in CACHE["sheets5"]:
            code_norm = record.get("code_normalized", "")
            if code_norm and code_norm not in image_map:  # Don't overwrite existing entries
                image_url = record.get("image_url", "").strip()
                if image_url:
                    # Convert Google Drive link to direct image URL format
                    if 'drive.google.com' in image_url:
                        file_id = None
                        if '/file/d/' in image_url:
                            try:
                                file_id = image_url.split('/file/d/')[1].split('/')[0]
                            except Exception:
                                pass
                        elif 'id=' in image_url:
                            try:
                                file_id = image_url.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
                            except Exception:
                                pass
                        if file_id:
                            image_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                    image_map[code_norm] = image_url
                    sheets5_added += 1
        logger.info(f"Added {sheets5_added} image URLs from sheets5 to image_map")
        
        # Save final image_map to cache (backward compatibility - URL lar)
        CACHE["sheets2"] = image_map
        
        # Initialize new image_map structure if not exists (faqat file_id saqlanadi)
        if "image_map" not in CACHE or not isinstance(CACHE["image_map"], LRUCache):
            CACHE["image_map"] = LRUCache(max_size=10000)
        
        # image_map faqat file_id saqlaydi, URL lar saqlanmaydi
        # URL lar faqat sheets2/sheets4/sheets5 dan olinadi (fallback)
        
        logger.info(f"Created image map: {len(image_map)} entries (sheets2: {len(sheets2_full)}, sheets4: +{sheets4_added}, sheets5: +{sheets5_added})")
        
        logger.info("✅ All sheets loaded into cache successfully")
    except Exception as e:
        logger.error(f"Error loading sheets into cache: {e}")
        raise


async def load_sheets1_to_cache():
    """Load only sheets1 from Google Sheets and update CACHE"""
    logger.info("Loading sheets1 into cache...")
    sheet_service = GoogleSheetService()
    try:
        CACHE["sheets1"] = await _load_sheets1_direct(sheet_service)
        _rebuild_collection_index_from_sheets1()
        logger.info(f"✅ Sheets1 loaded: {len(CACHE['sheets1'])} records")
    except Exception as e:
        logger.error(f"Error loading sheets1: {e}")
        raise


async def load_sheets2_to_cache():
    """Load only sheets2 from Google Sheets and update CACHE - OPTIMIZED: loads once"""
    logger.info("Loading sheets2 into cache (optimized - single load)...")
    sheet_service = GoogleSheetService()
    try:
        # Load sheets2 ONCE with all fields
        sheets2_full = await _load_sheets2_full_direct(sheet_service)
        CACHE["sheets2_full"] = sheets2_full
        logger.info(f"✅ Sheets2 loaded once: {len(sheets2_full)} records with all fields")
        
        # Create backward-compatible image map from sheets2_full
        image_map = {}
        for record in sheets2_full:
            code_norm = record.get("_code_normalized", "")
            if code_norm:
                # Get image_url from various possible column names
                image_url = (record.get("image_url") or record.get("imageurl") or 
                           record.get("image url") or record.get("image") or "")
                if image_url and image_url.strip():
                    # Convert Google Drive link to direct image URL format
                    image_url_clean = image_url.strip()
                    if 'drive.google.com' in image_url_clean:
                        file_id = None
                        if '/file/d/' in image_url_clean:
                            try:
                                file_id = image_url_clean.split('/file/d/')[1].split('/')[0]
                            except Exception:
                                pass
                        elif 'id=' in image_url_clean:
                            try:
                                file_id = image_url_clean.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
                            except Exception:
                                pass
                        if file_id:
                            image_url_clean = f"https://drive.google.com/uc?export=view&id={file_id}"
                    image_map[code_norm] = image_url_clean
        
        # Add sheets4 image_url from cache (if available, don't overwrite existing)
        sheets4_added = 0
        if CACHE.get("sheets4"):
            for record in CACHE["sheets4"]:
                code_norm = record.get("code_normalized", "")
                if code_norm and code_norm not in image_map:
                    image_url = record.get("image_url", "").strip()
                    if image_url:
                        if 'drive.google.com' in image_url:
                            file_id = None
                            if '/file/d/' in image_url:
                                try:
                                    file_id = image_url.split('/file/d/')[1].split('/')[0]
                                except Exception:
                                    pass
                            elif 'id=' in image_url:
                                try:
                                    file_id = image_url.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
                                except Exception:
                                    pass
                            if file_id:
                                image_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                        image_map[code_norm] = image_url
                        sheets4_added += 1
        
        # Add sheets5 image_url from cache (if available, don't overwrite existing)
        sheets5_added = 0
        if CACHE.get("sheets5"):
            for record in CACHE["sheets5"]:
                code_norm = record.get("code_normalized", "")
                if code_norm and code_norm not in image_map:
                    image_url = record.get("image_url", "").strip()
                    if image_url:
                        if 'drive.google.com' in image_url:
                            file_id = None
                            if '/file/d/' in image_url:
                                try:
                                    file_id = image_url.split('/file/d/')[1].split('/')[0]
                                except Exception:
                                    pass
                            elif 'id=' in image_url:
                                try:
                                    file_id = image_url.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
                                except Exception:
                                    pass
                            if file_id:
                                image_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                        image_map[code_norm] = image_url
                        sheets5_added += 1
        
        CACHE["sheets2"] = image_map
        
        # Initialize new image_map structure if not exists (faqat file_id saqlanadi)
        if "image_map" not in CACHE or not isinstance(CACHE["image_map"], LRUCache):
            CACHE["image_map"] = LRUCache(max_size=10000)
        
        # image_map faqat file_id saqlaydi, URL lar saqlanmaydi
        # URL lar faqat sheets2/sheets4/sheets5 dan olinadi (fallback)
        
        logger.info(f"✅ Created image map: {len(image_map)} entries (sheets2: {len(sheets2_full)}, sheets4: +{sheets4_added}, sheets5: +{sheets5_added})")
    except Exception as e:
        logger.error(f"Error loading sheets2: {e}")
        raise


async def load_sheets3_to_cache():
    """Load only sheets3 from Google Sheets and update CACHE"""
    logger.info("Loading sheets3 into cache...")
    sheet_service = GoogleSheetService()
    try:
        CACHE["sheets3"] = await _load_sheets3_direct(sheet_service)
        logger.info(f"✅ Sheets3 loaded: {len(CACHE['sheets3'])} records")
    except Exception as e:
        logger.error(f"Error loading sheets3: {e}")
        raise


async def load_sheets4_to_cache():
    """Load only sheets4 from Google Sheets and update CACHE"""
    logger.info("Loading sheets4 into cache...")
    sheet_service = GoogleSheetService()
    try:
        CACHE["sheets4"] = await _load_sheets4_direct(sheet_service)
        logger.info(f"✅ Sheets4 loaded: {len(CACHE['sheets4'])} records")
    except Exception as e:
        logger.error(f"Error loading sheets4: {e}")
        raise


async def load_sheets5_to_cache():
    """Load only sheets5 from Google Sheets and update CACHE"""
    logger.info("Loading sheets5 into cache...")
    sheet_service = GoogleSheetService()
    try:
        CACHE["sheets5"] = await _load_sheets5_direct(sheet_service)
        logger.info(f"✅ Sheets5 loaded: {len(CACHE['sheets5'])} records")
    except Exception as e:
        logger.error(f"Error loading sheets5: {e}")
        raise


async def load_sheets6_to_cache():
    """Load only sheets6 from Google Sheets and update CACHE"""
    logger.info("Loading sheets6 into cache...")
    sheet_service = GoogleSheetService()
    try:
        CACHE["sheets6"] = await _load_sheets6_direct(sheet_service)
        logger.info(f"Sheets6 loaded: {len(CACHE['sheets6'])} records")
    except Exception as e:
        logger.error(f"Error loading sheets6: {e}")
        raise


async def reload_cache():
    """Reload all sheets from Google Sheets and update CACHE"""
    logger.info("Reloading all cache from Google Sheets...")
    try:
        # Reload all sheets sequentially
        # Note: sheets2 must be loaded AFTER sheets4 and sheets5, 
        # because load_sheets2_to_cache() adds image URLs from sheets4 and sheets5 cache
        await load_sheets1_to_cache()
        await load_sheets3_to_cache()
        await load_sheets4_to_cache()
        await load_sheets5_to_cache()
        await load_sheets6_to_cache()
        await load_sheets2_to_cache()  # Load sheets2 last to include sheets4/sheets5 images
        logger.info(f"Sheets6 reloaded: {len(CACHE['sheets6'])} records")
        logger.info(f"✅ All cache reloaded: sheets1={len(CACHE['sheets1'])}, sheets2={len(CACHE['sheets2'])}, sheets2_full={len(CACHE['sheets2_full'])}, sheets3={len(CACHE['sheets3'])}, sheets4={len(CACHE['sheets4'])}, sheets5={len(CACHE['sheets5'])}")
    except Exception as e:
        logger.error(f"Error reloading cache: {e}")
        raise


# ==================== IMAGE RESOLVER - FILE_ID PRIORITY ====================

def get_file_id_for_code(product_code: str) -> Optional[str]:
    """
    Faqat file_id ni qaytaradi (eng tez variant).
    image_map faqat file_id saqlaydi: {code: file_id}
    
    Returns:
        file_id string yoki None
    """
    if not product_code:
        return None
    
    code_norm = normalize_code(product_code)
    if not code_norm:
        return None
    
    # Initialize image_map in CACHE if not exists
    if "image_map" not in CACHE:
        CACHE["image_map"] = {}
    
    image_map = CACHE["image_map"]
    
    # Check image_map for file_id
    if code_norm in image_map:
        file_id = image_map[code_norm]
        # Agar dict format bo'lsa (backward compatibility - eski format)
        if isinstance(file_id, dict):
            file_id = file_id.get("file_id")
            # Eski formatni yangi formatga o'tkazish
            if file_id:
                image_map[code_norm] = file_id
        # Agar string bo'lsa va bo'sh bo'lmasa (yangi format)
        if file_id and isinstance(file_id, str) and file_id.strip():
            return file_id
    
    return None


def get_image_url_for_code(product_code: str) -> Optional[str]:
    """
    URL ni qaytaradi (fallback uchun).
    image_map dan emas, balki sheets2/sheets4/sheets5 dan oladi.
    
    Returns:
        image_url string yoki None
    """
    if not product_code:
        return None
    
    code_norm = normalize_code(product_code)
    if not code_norm:
        return None
    
    # Check sheets2 (backward compatibility)
    sheets2_map = CACHE.get("sheets2", {})
    if code_norm in sheets2_map:
        url = sheets2_map[code_norm]
        if url and url.strip():
            return url
    
    # Check sheets2_full, sheets4, sheets5
    sheets_to_check = [
        ("sheets2_full", "sheets2"),
        ("sheets4", "sheets4"),
        ("sheets5", "sheets5")
    ]
    
    for cache_key, _ in sheets_to_check:
        records = CACHE.get(cache_key, [])
        if not records:
            continue
        
        for record in records:
            record_code_norm = record.get("code_normalized") or record.get("_code_normalized", "")
            if record_code_norm == code_norm:
                # Get image_url from various possible column names
                image_url = (record.get("image_url") or record.get("imageurl") or 
                           record.get("image url") or record.get("image") or "")
                if image_url and image_url.strip():
                    return image_url.strip()
    
    return None


def get_cache_stats() -> Dict:
    """
    Get statistics about the cache.
    
    Returns:
        Dictionary with cache statistics:
            - sheets1_size: Number of records in sheets1
            - sheets2_size: Number of records in sheets2
            - sheets3_size: Number of records in sheets3
            - sheets4_size: Number of records in sheets4
            - sheets5_size: Number of records in sheets5
            - sheets6_size: Number of records in sheets6
            - image_map_stats: Statistics from image_map LRU cache
            - collection_index_size: Number of collections in index
    """
    image_map = CACHE.get("image_map")
    image_map_stats = {}
    
    if isinstance(image_map, LRUCache):
        image_map_stats = image_map.get_stats()
    
    return {
        "sheets1_size": len(CACHE.get("sheets1", [])),
        "sheets2_size": len(CACHE.get("sheets2", {})),
        "sheets2_full_size": len(CACHE.get("sheets2_full", [])),
        "sheets3_size": len(CACHE.get("sheets3", [])),
        "sheets4_size": len(CACHE.get("sheets4", [])),
        "sheets5_size": len(CACHE.get("sheets5", [])),
        "sheets6_size": len(CACHE.get("sheets6", [])),
        "image_map_stats": image_map_stats,
        "collection_index_size": len(CACHE.get("collection_index", {}))
    }


class GoogleSheetService:
    """Service for interacting with Google Sheets"""

    def __init__(self):
        self.sheet_id = GOOGLE_SHEET_ID
        self.service_account_file = BASE_DIR / SERVICE_ACCOUNT_FILE
        self.worksheet_name = GOOGLE_WORKSHEET_NAME
        self.client = None
        self._connect_lock = None
        self._records_cache: Optional[List[Dict]] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl = 300  # seconds
        self._images_cache: Optional[Dict[str, str]] = None
        self._images_cache_timestamp: float = 0.0
        self.products_sheet_name = "sheets1"
        self.images_sheet_name = "sheets2"
        # images_by_code: {normalize(code): image_url}
        self.images_by_code = {}
        # image_map: {normalize(code): image_url} - sync yuklash uchun
        self.image_map = {}

    async def _ensure_client(self):
        """Ensure Google Sheets client is initialized off the event loop."""
        if self.client:
            return
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self.client:
                return
            await asyncio.to_thread(self._connect)

    def _convert_google_drive_link(self, url: str) -> str:
        """
        Convert Google Drive sharing link to direct image link
        
        Args:
            url: Google Drive link or direct image URL
            
        Returns:
            Direct image URL
        """
        if not url or not isinstance(url, str):
            return url
        
        url = url.strip()
        
        # If it's already a direct image URL, return as is
        if url.startswith('http://') or url.startswith('https://'):
            # Check if it's a Google Drive link
            if 'drive.google.com' in url:
                file_id = None
                
                # Format 1: https://drive.google.com/file/d/FILE_ID/view
                # Format 2: https://drive.google.com/file/d/FILE_ID/edit
                if '/file/d/' in url:
                    try:
                        file_id = url.split('/file/d/')[1].split('/')[0]
                        logger.info(f"Extracted file ID from /file/d/ format: {file_id}")
                    except Exception as e:
                        logger.error(f"Error extracting file ID from /file/d/ format: {e}")
                
                # Format 3: https://drive.google.com/open?id=FILE_ID
                # Format 4: https://drive.google.com/uc?id=FILE_ID
                elif 'id=' in url:
                    try:
                        # Extract file ID from id= parameter
                        file_id = url.split('id=')[1].split('&')[0].split('#')[0].split('?')[0]
                        logger.info(f"Extracted file ID from id= format: {file_id}")
                    except Exception as e:
                        logger.error(f"Error extracting file ID from id= format: {e}")
                
                # Format 5: https://drive.google.com/file/d/FILE_ID (without /view or /edit)
                elif '/file/d/' in url:
                    try:
                        parts = url.split('/file/d/')
                        if len(parts) > 1:
                            file_id = parts[1].split('/')[0].split('?')[0].split('#')[0]
                            logger.info(f"Extracted file ID from /file/d/ (no suffix) format: {file_id}")
                    except Exception as e:
                        logger.error(f"Error extracting file ID: {e}")
                
                # If file ID was extracted, convert to direct image link
                if file_id:
                    direct_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                    logger.info(f"Converted Google Drive link: {url[:50]}... -> {direct_url}")
                    return direct_url
                else:
                    logger.warning(f"Could not extract file ID from Google Drive link: {url}")
                    return url
            else:
                # Not a Google Drive link, return as is
                return url
        else:
            # Not a valid URL, return as is
            return url

    def _normalize_record(self, record: Dict) -> Dict:
        """
        Normalize record from sheets1.
        Sheets1 contains only: code, quantity, collection, date
        
        Column normalization:
        - code ustuni: code, kod, model
        - quantity ustuni: quantity, qoldiq, qty, soni, kv
        """
        # Normalize keys: lowercase, remove spaces, underscores, hyphens
        normalized_keys = {}
        original_keys = {}
        for k, v in record.items():
            key_str = str(k or "").strip().lower()
            # Remove spaces, underscores, hyphens
            key_normalized = key_str.replace(" ", "").replace("_", "").replace("-", "")
            normalized_keys[key_normalized] = v
            original_keys[key_normalized] = k
        
        # Find code column (code, kod, model)
        code_value = ""
        for possible_code_key in ["code", "kod", "model"]:
            if possible_code_key in normalized_keys:
                code_value = normalized_keys[possible_code_key] or ""
                break
        
        # Find quantity column (quantity, qoldiq, qty, soni, kv)
        quantity_raw = ""
        for possible_qty_key in ["quantity", "qoldiq", "qty", "soni", "kv"]:
            if possible_qty_key in normalized_keys:
                quantity_raw = normalized_keys[possible_qty_key] or ""
                break
        
        # Sheets1 faqat: code, quantity, collection, date
        return {
            "code": code_value,
            "code_normalized": normalize_code(code_value),
            "quantity": quantity_raw,  # Will be processed in read_products()
            "collection": normalized_keys.get("collection") or normalized_keys.get("kolleksiya") or "",
            "date": normalized_keys.get("date") or normalized_keys.get("sana") or "",
        }

    def _connect(self):
        """Connect to Google Sheets API"""
        try:
            if not self.sheet_id:
                raise ValueError("GOOGLE_SHEET_ID is not set in config")

            if not self.service_account_file.exists():
                raise FileNotFoundError(
                    f"Service account file not found: {self.service_account_file}\n"
                    "Please create service_account.json file with Google Sheets credentials."
                )

            # Define the scope
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive'
            ]

            # Authenticate and create the client
            creds = Credentials.from_service_account_file(
                str(self.service_account_file),
                scopes=scope
            )
            self.client = gspread.authorize(creds)

            logger.info("Successfully connected to Google Sheets")

        except Exception as e:
            logger.error(f"Error connecting to Google Sheets: {e}")
            raise

    def _get_images_records(self) -> Dict[str, str]:
        """
        Fetch image records from sheets2 synchronously.
        Sheets2 contains only: code, image_url
        Returns: {normalize(code): image_url}
        """
        try:
            # Agar client bo'lmasa, bo'sh dict qaytar
            if not self.client:
                # Client ni yaratishga urinish
                try:
                    self._connect()
                except Exception as e:
                    logger.warning(f"Could not connect to Google Sheets in _get_images_records: {e}")
                    return {}
            
            spreadsheet = self.client.open_by_key(self.sheet_id)
            try:
                worksheet = spreadsheet.worksheet(self.images_sheet_name)
            except Exception as exc:
                logger.error(f"Worksheet '{self.images_sheet_name}' not found: {exc}")
                return {}

            raw_values = worksheet.get_all_values()
            if not raw_values or len(raw_values) < 2:
                return {}

            headers = raw_values[0]
            code_idx = None
            image_idx = None
            
            # Headers ni tekshirish - case-insensitive
            for idx, header in enumerate(headers):
                header_lower = (header or "").strip().lower()
                if header_lower == "code":
                    code_idx = idx
                elif header_lower in ["image_url", "imageurl", "image url", "image"]:
                    image_idx = idx

            if code_idx is None:
                logger.warning("'code' column not found in sheets2")
                return {}
            
            if image_idx is None:
                logger.warning("'image_url' column not found in sheets2")
                # image_idx None bo'lsa ham davom etamiz, lekin image_url bo'sh qoladi

            # Sheets2 faqat: code, image_url
            image_map: Dict[str, str] = {}
            for row in raw_values[1:]:
                if code_idx >= len(row):
                    continue
                code_value = row[code_idx] if code_idx < len(row) else ""
                if not code_value:
                    continue
                
                image_value = ""
                if image_idx is not None and image_idx < len(row):
                    image_value = (row[image_idx] or "").strip()
                
                # Har qanday link formatini qabul qilish (bo'sh bo'lmasa)
                if image_value:
                    # Faqat bo'sh bo'lmagan qiymatni saqlash
                    code_norm = normalize_code(code_value)
                    if code_norm:
                        image_map[code_norm] = image_value
                        logger.debug(f"Mapped code '{code_norm}' to image URL: {image_value[:50]}...")

            logger.info(f"Loaded {len(image_map)} image records from sheets2")
            return image_map

        except Exception as e:
            logger.error(f"Error fetching image records: {e}")
            return {}

    async def _get_records(self) -> List[Dict]:
        """Fetch and cache worksheet records."""
        now = time.time()
        if self._records_cache and (now - self._cache_timestamp) < self._cache_ttl:
            return self._records_cache

        await self._ensure_client()

        def _fetch_records():
            spreadsheet = self.client.open_by_key(self.sheet_id)

            worksheet = None
            target_name = (self.products_sheet_name or "").strip()
            if target_name:
                try:
                    worksheet = spreadsheet.worksheet(target_name)
                    logger.info(f"Using worksheet '{target_name}'")
                except Exception as exc:
                    logger.warning(
                        f"Worksheet '{target_name}' not found: {exc}. Falling back to first sheet."
                    )

            if worksheet is None:
                worksheet = spreadsheet.sheet1
                logger.info(f"Defaulting to worksheet '{worksheet.title}'")

            raw_values = worksheet.get_all_values()
            if not raw_values:
                logger.info("Google Sheets returned no data")
                return []

            headers = raw_values[0]
            data_rows = raw_values[1:]
            normalized_records = []

            for row_values in data_rows:
                # Ensure row has same length as headers
                if len(row_values) < len(headers):
                    row_values = row_values + [""] * (len(headers) - len(row_values))
                elif len(row_values) > len(headers):
                    row_values = row_values[:len(headers)]

                record = {headers[idx]: row_values[idx] for idx in range(len(headers))}
                normalized_records.append(self._normalize_record(record))

            if normalized_records:
                logger.info(f"Available columns in Google Sheets: {list(normalized_records[0].keys())}")

            return normalized_records

        records = await asyncio.to_thread(_fetch_records)
        self._records_cache = records
        self._cache_timestamp = now
        return records

    async def _get_image_records(self) -> Dict[str, str]:
        """
        Get image records from CACHE (sheets2).
        Sheets2 contains only: code, image_url
        """
        # Return from cache directly
        image_map = CACHE["sheets2"].copy() if CACHE["sheets2"] else {}
        # Update images_by_code and image_map for compatibility
        self.images_by_code = image_map.copy()
        self.image_map = image_map.copy()
        return image_map

    async def read_products(self) -> List[Dict]:
        """
        Read products from CACHE (sheets1).
        Returns direct reference - DO NOT MODIFY returned list!
        No numeric conversions or formatting changes are applied here.
        """
        # Return from cache directly (no .copy() for performance)
        return CACHE["sheets1"] if CACHE["sheets1"] else []

    async def get_product_data(self, product_code: str) -> Optional[Dict]:
        """
        Get product data from Google Sheets by product code.
        Supports universal search: exact match, startswith, endswith, or contains.
        
        Args:
            product_code: Product code to search for (e.g., MRC1221, 3209-8)
            
        Returns:
            Dictionary with product data (quantity, collection, date, image_url)
            or None if not found
        """
        # 1) Foydalanuvchi kiritgan kodni normalizatsiya qilish
        user_code_norm = normalize_code(product_code or "")
        if not user_code_norm:
            return None

        try:
            products = await self.read_products()
            
            # Generate fuzzy variants for user code (e.g., "13401" -> ["13401", "134001", "1340001"])
            user_code_variants = generate_fuzzy_code_variants(user_code_norm)

            matched_rows = []
            for row in products:
                # 2) Google Sheets'dagi har bir row["code"] maydonini normalizatsiya qilish
                row_code_original = row.get("code", "")
                if not row_code_original:
                    continue
                    
                # Sheet'dagi kodni normalizatsiya qilish
                sheet_code_norm = row.get("code_normalized", "")
                if not sheet_code_norm:
                    sheet_code_norm = normalize_code(str(row_code_original))
                
                # Generate fuzzy variants for sheet code
                sheet_code_variants = generate_fuzzy_code_variants(sheet_code_norm)
                
                # 3) Universal qidiruv qoidasi - 5 ta shartdan biri bajarilsa kifoya
                matches = False
                
                # Shart 1: To'liq mos kelish
                if sheet_code_norm == user_code_norm:
                    matches = True
                # Shart 2: sheet_code_norm.startswith(user_code_norm)
                elif sheet_code_norm.startswith(user_code_norm):
                    matches = True
                # Shart 3: sheet_code_norm.endswith(user_code_norm)
                elif sheet_code_norm.endswith(user_code_norm):
                    matches = True
                # Shart 4: user_code_norm sheet_code_norm ichida mavjud bo'lsa
                elif user_code_norm in sheet_code_norm:
                    matches = True
                # Shart 5 (YANGI): Fuzzy matching - variant mos kelishi
                # Masalan: user="13401" sheet="134001" -> user_variants=["13401","134001","1340001"]
                # sheet_code_norm "134001" user_variants da mavjud
                elif (sheet_code_norm in user_code_variants or 
                      user_code_norm in sheet_code_variants or
                      any(v in sheet_code_variants for v in user_code_variants)):
                    matches = True
                # Shart 6 (YANGI): Prefiks bilan fuzzy matching
                # Agar sheet kodida harf prefiksi bor (masalan: MRC134001) va user kodi raqamlardan iborat bo'lsa
                # sheet kodidan faqat raqamli qismni ajratib olib, fuzzy matching qilish
                else:
                    # Extract numeric part from sheet code (e.g., "MRC134001" -> "134001")
                    import re
                    sheet_code_numeric = re.sub(r'^[A-Z]+', '', sheet_code_norm)
                    
                    # Agar sheet kodida prefiks bor va raqamli qism mavjud bo'lsa
                    if sheet_code_numeric and sheet_code_numeric != sheet_code_norm:
                        # Generate fuzzy variants for numeric part only
                        sheet_numeric_variants = generate_fuzzy_code_variants(sheet_code_numeric)
                        
                        # Check if user code matches with numeric part
                        if (sheet_code_numeric == user_code_norm or
                            sheet_code_numeric in user_code_variants or
                            user_code_norm in sheet_numeric_variants or
                            any(v in sheet_numeric_variants for v in user_code_variants)):
                            matches = True
                
                if matches:
                    qty_value = row.get("quantity")
                    qty_str = ""
                    if qty_value is not None:
                        qty_str = str(qty_value).strip()
                    # Row nusxasini yaratish
                    row_copy = row.copy()
                    row_copy['quantity'] = qty_str
                    row_copy['code_original'] = row_code_original  # Original code ni saqlash
                    matched_rows.append(row_copy)

            # 4) Agar matched_rows bo'sh bo'lsa
            if not matched_rows:
                return None

            # 5) Client ni ta'minlash va image_map ni yuklash
            await self._ensure_client()
            if not self.image_map:
                self.image_map = self._get_images_records()

            # 6) Bir xil kodli qatorlar uchun: collection, date
            # Agar bir xil bo'lsa birinchisi, har xil bo'lsa "N/A"
            def collect_value(key: str) -> str:
                values = [p.get(key) for p in matched_rows if p.get(key)]
                if not values:
                    return "N/A"
                unique_values = {v for v in values}
                if len(unique_values) == 1:
                    return values[0]
                return "N/A"

            collection = collect_value("collection")
            date = collect_value("date")

            # Original code ni olish (sheets'dagi kabi)
            original_code = matched_rows[0].get("code_original") or matched_rows[0].get("code") or product_code
            
            # Rasm ulashish - matched_rows tayyor bo'lgandan keyin, RETURN dan oldin
            # image_map ni yangilash (agar bo'sh bo'lsa)
            if not self.image_map:
                self.image_map = self._get_images_records()
                logger.info(f"Reloaded image_map with {len(self.image_map)} records")
            
            for item in matched_rows:
                code = item.get("code_normalized", "")
                image_url = self.image_map.get(code, "")
                item["image_url"] = image_url
                if image_url:
                    logger.debug(f"Found image_url for code '{code}': {image_url[:50]}...")
                else:
                    logger.debug(f"No image_url found for code '{code}'")
            
            return {
                "code": user_code_norm,
                "original_code": original_code,
                "collection": collection,
                "date": date,
                "matched_rows": matched_rows,
            }

        except Exception as e:
            logger.error(f"Error fetching product data: {e}")
            raise

    async def get_product_data_by_collection(self, product_code: str, collection: str) -> Optional[Dict]:
        """
        Get product data from Google Sheets by product code filtered by collection.
        Supports universal search: exact match, startswith, endswith, or contains.
        Only returns products that match the specified collection.
        
        Args:
            product_code: Product code to search for (e.g., MRC1221, 3209-8)
            collection: Collection name to filter by (e.g., "0-start", "1-stage")
            
        Returns:
            Dictionary with product data (quantity, collection, date)
            or None if not found
        """
        # 1) Foydalanuvchi kiritgan kodni normalizatsiya qilish
        user_code_norm = normalize_code(product_code or "")
        if not user_code_norm:
            return None

        try:
            products = await self.read_products()

            matched_rows = []
            for row in products:
                # 2) Collection filter - faqat mos kolleksiyadagi mahsulotlar
                row_collection = row.get("collection", "")
                row_collection_clean = str(row_collection).replace("\u00A0", " ").strip()
                collection_clean = str(collection).replace("\u00A0", " ").strip()
                if row_collection_clean != collection_clean:
                    continue
                
                # 3) Google Sheets'dagi har bir row["code"] maydonini normalizatsiya qilish
                row_code_original = row.get("code", "")
                if not row_code_original:
                    continue
                    
                # Sheet'dagi kodni normalizatsiya qilish
                sheet_code_norm = row.get("code_normalized", "")
                if not sheet_code_norm:
                    sheet_code_norm = normalize_code(str(row_code_original))
                
                # 4) Universal qidiruv qoidasi - 4 ta shartdan biri bajarilsa kifoya
                matches = False
                
                # Shart 1: sheet_code_norm == user_code_norm
                if sheet_code_norm == user_code_norm:
                    matches = True
                # Shart 2: sheet_code_norm.startswith(user_code_norm)
                elif sheet_code_norm.startswith(user_code_norm):
                    matches = True
                # Shart 3: sheet_code_norm.endswith(user_code_norm)
                elif sheet_code_norm.endswith(user_code_norm):
                    matches = True
                # Shart 4: user_code_norm sheet_code_norm ichida mavjud bo'lsa
                elif user_code_norm in sheet_code_norm:
                    matches = True
                
                if matches:
                    qty_value = row.get("quantity")
                    qty_str = ""
                    if qty_value is not None:
                        qty_str = str(qty_value).strip()
                    # Row nusxasini yaratish
                    row_copy = row.copy()
                    row_copy['quantity'] = qty_str
                    row_copy['code_original'] = row_code_original  # Original code ni saqlash
                    matched_rows.append(row_copy)

            # 5) Agar matched_rows bo'sh bo'lsa
            if not matched_rows:
                return None

            # 6) Bir xil kodli qatorlar uchun: collection, date
            # Agar bir xil bo'lsa birinchisi, har xil bo'lsa "N/A"
            def collect_value(key: str) -> str:
                values = [p.get(key) for p in matched_rows if p.get(key)]
                if not values:
                    return "N/A"
                unique_values = {v for v in values}
                if len(unique_values) == 1:
                    return values[0]
                return "N/A"

            collection_value = collect_value("collection")
            date = collect_value("date")

            # Original code ni olish (sheets'dagi kabi)
            original_code = matched_rows[0].get("code_original") or matched_rows[0].get("code") or product_code
            
            return {
                "code": user_code_norm,
                "original_code": original_code,
                "collection": collection_value,
                "date": date,
                "matched_rows": matched_rows,
            }

        except Exception as e:
            logger.error(f"Error fetching product data by collection: {e}")
            raise

    async def get_all_products_by_collection(self, collection: str) -> List[Dict]:
        """
        Get all products from Google Sheets filtered by collection name.
        Case-insensitive comparison.
        
        Args:
            collection: Collection name to filter by (e.g., "0-start", "1-stage")
            
        Returns:
            List of product dictionaries with code, quantity, collection, date
            Empty list if not found
        """
        try:
            products = await self.read_products()
            
            matched_rows = []
            for row in products:
                # Collection filter
                row_collection = row.get("collection", "")
                row_collection_clean = str(row_collection).replace("\u00A0", " ").strip()
                collection_clean = str(collection).replace("\u00A0", " ").strip()
                if row_collection_clean != collection_clean:
                    continue
                
                # Row nusxasini yaratish
                row_code_original = row.get("code", "")
                qty_value = row.get("quantity")
                qty_str = ""
                if qty_value is not None:
                    qty_str = str(qty_value).strip()
                
                row_copy = {
                    "code": row_code_original,
                    "code_original": row_code_original,
                    "quantity": qty_str,
                    "collection": row_collection_clean,
                    "date": row.get("date", ""),
                }
                matched_rows.append(row_copy)
            
            return matched_rows

        except Exception as e:
            logger.error(f"Error fetching all products by collection: {e}")
            return []

    async def download_image(self, url: str) -> Optional[bytes]:
        """
        Download an image from a given URL.

        Args:
            url: Image URL (Google Drive links should already be converted)

        Returns:
            Bytes of the downloaded image or None if download fails
        """
        if not url:
            return None

        try:
            converted_url = self._convert_google_drive_link(url)
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            }

            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(converted_url) as response:
                    if response.status != 200:
                        logger.error(f"Failed to download image {converted_url}: HTTP {response.status}")
                        return None

                    content_type = response.headers.get("Content-Type", "")
                    if "image" not in content_type:
                        logger.error(f"URL does not point to an image: {converted_url} ({content_type})")
                        return None

                    image_bytes = await response.read()
                    if not image_bytes:
                        logger.error(f"Downloaded image is empty: {converted_url}")
                        return None

                    return image_bytes
        except Exception as e:
            logger.error(f"Error downloading image {url}: {e}")
            return None

    async def update_image_url(self, product_code: str, image_url: str) -> bool:
        """
        Update image_url in sheets2 for a given product code.
        
        Args:
            product_code: Product code to update (will be normalized)
            image_url: Image URL to set
            
        Returns:
            True if update successful, False otherwise
        """
        try:
            await self._ensure_client()
            product_code_norm = normalize_code(product_code)
            
            if not product_code_norm:
                logger.error(f"Invalid product code: {product_code}")
                return False
            
            def _update_image_url():
                spreadsheet = self.client.open_by_key(self.sheet_id)
                try:
                    worksheet = spreadsheet.worksheet(self.images_sheet_name)
                except Exception as exc:
                    logger.error(f"Worksheet '{self.images_sheet_name}' not found: {exc}")
                    return False
                
                raw_values = worksheet.get_all_values()
                if not raw_values or len(raw_values) < 2:
                    logger.warning("sheets2 is empty or has no data rows")
                    return False
                
                headers = raw_values[0]
                code_idx = None
                image_idx = None
                
                # Find column indices
                for idx, header in enumerate(headers):
                    header_lower = (header or "").strip().lower()
                    if header_lower == "code":
                        code_idx = idx
                    elif header_lower in ["image_url", "imageurl", "image url", "image"]:
                        image_idx = idx
                
                if code_idx is None:
                    logger.error("'code' column not found in sheets2")
                    return False
                
                if image_idx is None:
                    logger.error("'image_url' column not found in sheets2")
                    return False
                
                # Find the row with matching code
                row_to_update = None
                for row_idx, row in enumerate(raw_values[1:], start=2):  # Start from row 2 (1-indexed)
                    if code_idx >= len(row):
                        continue
                    code_value = row[code_idx] if code_idx < len(row) else ""
                    if not code_value:
                        continue
                    
                    code_norm = normalize_code(code_value)
                    if code_norm == product_code_norm:
                        row_to_update = row_idx
                        break
                
                if row_to_update is None:
                    logger.warning(f"Code '{product_code}' not found in sheets2")
                    return False
                
                # Ensure row has enough columns
                current_row = raw_values[row_to_update - 1]  # Convert to 0-indexed
                while len(current_row) <= image_idx:
                    current_row.append("")
                
                # Update the image_url
                current_row[image_idx] = image_url
                
                # Update the row in the sheet
                range_name = f"{self.images_sheet_name}!{row_to_update}:{row_to_update}"
                worksheet.update(range_name, [current_row], value_input_option='RAW')
                
                logger.info(f"Updated image_url for code '{product_code}' in sheets2")
                
                # Invalidate cache
                self._images_cache = None
                self._images_cache_timestamp = 0.0
                
                return True
            
            return await asyncio.to_thread(_update_image_url)
            
        except Exception as e:
            logger.error(f"Error updating image_url in sheets2: {e}")
            return False

    async def read_prices_from_sheets3(self) -> List[Dict]:
        """
        Read price data from CACHE (sheets3).
        
        Returns:
            List of dictionaries with keys: code, collection, model_name, asosiy_price, mini_price, kasetniy_price, izoh, asosiy_qimmat, mini_qimmat, kasetniy_qimmat
        """
        # Return from cache directly
        return CACHE["sheets3"].copy() if CACHE["sheets3"] else []

    async def read_discount_prices_from_sheets4(self) -> List[Dict]:
        """
        Read discount price data from CACHE (sheets4).

        Returns:
            List of dictionaries with keys:
            code, code_normalized, quantity, collection, date,
            model_name, old_price, price, mini_price, kasetniy_price, image_url
        """
        # Return from cache directly
        return CACHE["sheets4"].copy() if CACHE["sheets4"] else []
