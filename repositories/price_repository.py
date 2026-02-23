"""
Price Repository - Narx ma'lumotlari bilan ishlash.
"""

import logging
from typing import Optional, List, Dict, Any

from repositories.base_repository import BaseRepository
from services.google_sheet import CACHE
from services.product_utils import normalize_code

logger = logging.getLogger(__name__)


class PriceRepository(BaseRepository):
    """
    Repository for price data (sheets3).
    
    Provides access to:
    - Model codes
    - Prices (asosiy, mini, kasetniy)
    - Expensive variants (qimmat)
    - Comments (izoh)
    """
    
    def __init__(self):
        super().__init__()
        self._sheet_name = "sheets3"
    
    async def find_by_id(self, code: str) -> Optional[Dict]:
        """
        Find price by code.
        
        Args:
            code: Model code
            
        Returns:
            Price dictionary or None
        """
        if not code:
            return None
        
        code_norm = normalize_code(code)
        prices = await self.find_all()
        
        for price in prices:
            price_code_norm = price.get("code_normalized", "")
            if not price_code_norm:
                price_code_norm = normalize_code(price.get("code", ""))
            
            if price_code_norm == code_norm:
                return price
        
        return None
    
    async def find_all(self) -> List[Dict]:
        """
        Find all prices.
        
        Returns:
            List of price dictionaries
        """
        prices = CACHE.get(self._sheet_name, [])
        return prices.copy() if prices else []
    
    async def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict]:
        """
        Find prices by criteria.
        
        Supported criteria:
        - code: Model code (partial match)
        - collection: Collection name (exact match)
        - price_min: Minimum price (asosiy)
        - price_max: Maximum price (asosiy)
        
        Args:
            criteria: Search criteria
            
        Returns:
            List of matching prices
        """
        prices = await self.find_all()
        results = []
        
        for price in prices:
            match = True
            
            # Check code
            if "code" in criteria:
                code_norm = normalize_code(criteria["code"])
                price_code_norm = price.get("code_normalized", "")
                if not price_code_norm:
                    price_code_norm = normalize_code(price.get("code", ""))
                
                if code_norm not in price_code_norm and price_code_norm not in code_norm:
                    match = False
            
            # Check collection
            if "collection" in criteria and match:
                price_collection = price.get("collection", "").strip()
                criteria_collection = criteria["collection"].strip()
                if price_collection != criteria_collection:
                    match = False
            
            # Check price range
            if match and ("price_min" in criteria or "price_max" in criteria):
                try:
                    asosiy_price_str = price.get("asosiy_price", "0")
                    asosiy_price = float(asosiy_price_str) if asosiy_price_str else 0
                    
                    if "price_min" in criteria and asosiy_price < criteria["price_min"]:
                        match = False
                    
                    if "price_max" in criteria and asosiy_price > criteria["price_max"]:
                        match = False
                except (ValueError, TypeError):
                    match = False
            
            if match:
                results.append(price)
        
        logger.info(f"Found {len(results)} prices matching criteria")
        return results
    
    async def find_by_collection(self, collection: str) -> List[Dict]:
        """
        Find prices by collection.
        
        Args:
            collection: Collection name
            
        Returns:
            List of prices in collection
        """
        return await self.find_by_criteria({"collection": collection})
    
    async def get_price_info(self, code: str) -> Optional[Dict[str, str]]:
        """
        Get formatted price information for a code.
        
        Args:
            code: Model code
            
        Returns:
            Dictionary with price info or None
        """
        price = await self.find_by_id(code)
        if not price:
            return None
        
        return {
            "asosiy": price.get("asosiy_price", "N/A"),
            "mini": price.get("mini_price", "N/A"),
            "kasetniy": price.get("kasetniy_price", "N/A"),
            "asosiy_qimmat": price.get("asosiy_qimmat", "N/A"),
            "mini_qimmat": price.get("mini_qimmat", "N/A"),
            "kasetniy_qimmat": price.get("kasetniy_qimmat", "N/A"),
            "izoh": price.get("izoh", "")
        }
    
    async def find_in_price_range(self, min_price: float, max_price: float) -> List[Dict]:
        """
        Find models in a price range.
        
        Args:
            min_price: Minimum price
            max_price: Maximum price
            
        Returns:
            List of models in price range
        """
        return await self.find_by_criteria({
            "price_min": min_price,
            "price_max": max_price
        })
    
    async def get_average_price(self, collection: Optional[str] = None) -> float:
        """
        Get average price for all models or a specific collection.
        
        Args:
            collection: Collection name (optional)
            
        Returns:
            Average price
        """
        if collection:
            prices = await self.find_by_collection(collection)
        else:
            prices = await self.find_all()
        
        if not prices:
            return 0.0
        
        total = 0.0
        count = 0
        
        for price in prices:
            try:
                asosiy_price_str = price.get("asosiy_price", "0")
                asosiy_price = float(asosiy_price_str) if asosiy_price_str else 0
                if asosiy_price > 0:
                    total += asosiy_price
                    count += 1
            except (ValueError, TypeError):
                continue
        
        return total / count if count > 0 else 0.0
    
    async def get_price_statistics(self) -> Dict[str, Any]:
        """
        Get price statistics.
        
        Returns:
            Dictionary with statistics:
                - total_models: Total number of models with prices
                - average_price: Average price
                - min_price: Minimum price
                - max_price: Maximum price
        """
        prices = await self.find_all()
        
        if not prices:
            return {
                "total_models": 0,
                "average_price": 0.0,
                "min_price": 0.0,
                "max_price": 0.0
            }
        
        price_values = []
        
        for price in prices:
            try:
                asosiy_price_str = price.get("asosiy_price", "0")
                asosiy_price = float(asosiy_price_str) if asosiy_price_str else 0
                if asosiy_price > 0:
                    price_values.append(asosiy_price)
            except (ValueError, TypeError):
                continue
        
        if not price_values:
            return {
                "total_models": len(prices),
                "average_price": 0.0,
                "min_price": 0.0,
                "max_price": 0.0
            }
        
        return {
            "total_models": len(prices),
            "average_price": sum(price_values) / len(price_values),
            "min_price": min(price_values),
            "max_price": max(price_values)
        }

