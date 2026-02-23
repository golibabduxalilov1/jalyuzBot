"""
Product Repository - Mahsulot ma'lumotlari bilan ishlash.
"""

import logging
from typing import Optional, List, Dict, Any

from repositories.base_repository import BaseRepository
from services.google_sheet import CACHE
from services.product_utils import normalize_code

logger = logging.getLogger(__name__)


class ProductRepository(BaseRepository):
    """
    Repository for product data (sheets1).
    
    Provides access to:
    - Product codes
    - Quantities
    - Collections
    - Dates
    """
    
    def __init__(self):
        super().__init__()
        self._sheet_name = "sheets1"
    
    async def find_by_id(self, code: str) -> Optional[Dict]:
        """
        Find product by code.
        
        Args:
            code: Product code
            
        Returns:
            Product dictionary or None
        """
        if not code:
            return None
        
        code_norm = normalize_code(code)
        products = await self.find_all()
        
        for product in products:
            product_code_norm = product.get("code_normalized", "")
            if not product_code_norm:
                product_code_norm = normalize_code(product.get("code", ""))
            
            if product_code_norm == code_norm:
                return product
        
        return None
    
    async def find_all(self) -> List[Dict]:
        """
        Find all products.
        
        Returns:
            List of product dictionaries
        """
        products = CACHE.get(self._sheet_name, [])
        return products.copy() if products else []
    
    async def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict]:
        """
        Find products by criteria.
        
        Supported criteria:
        - code: Product code (partial match)
        - collection: Collection name (exact match)
        - quantity_min: Minimum quantity
        - quantity_max: Maximum quantity
        
        Args:
            criteria: Search criteria
            
        Returns:
            List of matching products
        """
        products = await self.find_all()
        results = []
        
        for product in products:
            match = True
            
            # Check code
            if "code" in criteria:
                code_norm = normalize_code(criteria["code"])
                product_code_norm = product.get("code_normalized", "")
                if not product_code_norm:
                    product_code_norm = normalize_code(product.get("code", ""))
                
                if code_norm not in product_code_norm and product_code_norm not in code_norm:
                    match = False
            
            # Check collection
            if "collection" in criteria and match:
                product_collection = product.get("collection", "").strip()
                criteria_collection = criteria["collection"].strip()
                if product_collection != criteria_collection:
                    match = False
            
            # Check quantity range
            if match and ("quantity_min" in criteria or "quantity_max" in criteria):
                try:
                    quantity_str = product.get("quantity", "0")
                    quantity = float(quantity_str) if quantity_str else 0
                    
                    if "quantity_min" in criteria and quantity < criteria["quantity_min"]:
                        match = False
                    
                    if "quantity_max" in criteria and quantity > criteria["quantity_max"]:
                        match = False
                except (ValueError, TypeError):
                    match = False
            
            if match:
                results.append(product)
        
        logger.info(f"Found {len(results)} products matching criteria")
        return results
    
    async def find_by_collection(self, collection: str) -> List[Dict]:
        """
        Find all products in a collection.
        
        Args:
            collection: Collection name
            
        Returns:
            List of products in collection
        """
        return await self.find_by_criteria({"collection": collection})
    
    async def find_available_products(self, min_quantity: float = 0.1) -> List[Dict]:
        """
        Find products with available quantity.
        
        Args:
            min_quantity: Minimum quantity threshold
            
        Returns:
            List of available products
        """
        return await self.find_by_criteria({"quantity_min": min_quantity})
    
    async def get_all_collections(self) -> List[str]:
        """
        Get list of all unique collections.
        
        Returns:
            List of collection names
        """
        products = await self.find_all()
        collections = set()
        
        for product in products:
            collection = product.get("collection", "").strip()
            if collection:
                collections.add(collection)
        
        return sorted(list(collections))
    
    async def count_by_collection(self, collection: str) -> int:
        """
        Count products in a collection.
        
        Args:
            collection: Collection name
            
        Returns:
            Number of products
        """
        products = await self.find_by_collection(collection)
        return len(products)

