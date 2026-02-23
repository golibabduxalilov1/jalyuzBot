"""
Image Repository - Rasm ma'lumotlari bilan ishlash.
"""

import logging
from typing import Optional, List, Dict, Any

from repositories.base_repository import BaseRepository
from services.google_sheet import CACHE
from services.product_utils import normalize_code

logger = logging.getLogger(__name__)


class ImageRepository(BaseRepository):
    """
    Repository for image/model data (sheets2_full).
    
    Provides access to:
    - Model codes
    - Image URLs
    - Colors (rang)
    - Types (turi)
    - Patterns (naqsh)
    - Collections (kolleksiya)
    """
    
    def __init__(self):
        super().__init__()
        self._sheet_name = "sheets2_full"
    
    async def find_by_id(self, code: str) -> Optional[Dict]:
        """
        Find model by code.
        
        Args:
            code: Model code
            
        Returns:
            Model dictionary or None
        """
        if not code:
            return None
        
        code_norm = normalize_code(code)
        models = await self.find_all()
        
        for model in models:
            model_code_norm = model.get("_code_normalized", "")
            if not model_code_norm:
                model_code_norm = normalize_code(model.get("code", ""))
            
            if model_code_norm == code_norm:
                return model
        
        return None
    
    async def find_all(self) -> List[Dict]:
        """
        Find all models.
        
        Returns:
            List of model dictionaries
        """
        models = CACHE.get(self._sheet_name, [])
        return models.copy() if models else []
    
    async def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict]:
        """
        Find models by criteria.
        
        Supported criteria:
        - code: Model code (partial match)
        - color: Color name (partial match)
        - type: Type name (partial match)
        - pattern: Pattern name (partial match)
        - collection: Collection name (partial match)
        
        Args:
            criteria: Search criteria
            
        Returns:
            List of matching models
        """
        models = await self.find_all()
        results = []
        
        for model in models:
            match = True
            
            # Check code
            if "code" in criteria:
                code_norm = normalize_code(criteria["code"])
                model_code_norm = model.get("_code_normalized", "")
                if not model_code_norm:
                    model_code_norm = normalize_code(model.get("code", ""))
                
                if code_norm not in model_code_norm and model_code_norm not in code_norm:
                    match = False
            
            # Check color
            if "color" in criteria and match:
                model_color = (model.get("rang", "") or model.get("color", "")).lower()
                criteria_color = criteria["color"].lower()
                if criteria_color not in model_color:
                    match = False
            
            # Check type
            if "type" in criteria and match:
                model_type = (model.get("turi", "") or model.get("type", "")).lower()
                criteria_type = criteria["type"].lower()
                if criteria_type not in model_type:
                    match = False
            
            # Check pattern
            if "pattern" in criteria and match:
                model_pattern = (model.get("naqsh", "") or model.get("pattern", "")).lower()
                criteria_pattern = criteria["pattern"].lower()
                if criteria_pattern not in model_pattern:
                    match = False
            
            # Check collection
            if "collection" in criteria and match:
                model_collection = (model.get("kolleksiya", "") or model.get("collection", "")).lower()
                criteria_collection = criteria["collection"].lower()
                if criteria_collection not in model_collection:
                    match = False
            
            if match:
                results.append(model)
        
        logger.info(f"Found {len(results)} models matching criteria")
        return results
    
    async def find_by_color(self, color: str) -> List[Dict]:
        """
        Find models by color.
        
        Args:
            color: Color name
            
        Returns:
            List of models with matching color
        """
        return await self.find_by_criteria({"color": color})
    
    async def find_by_type(self, type_name: str) -> List[Dict]:
        """
        Find models by type.
        
        Args:
            type_name: Type name
            
        Returns:
            List of models with matching type
        """
        return await self.find_by_criteria({"type": type_name})
    
    async def find_by_collection(self, collection: str) -> List[Dict]:
        """
        Find models by collection.
        
        Args:
            collection: Collection name
            
        Returns:
            List of models in collection
        """
        return await self.find_by_criteria({"collection": collection})
    
    async def get_image_url(self, code: str) -> Optional[str]:
        """
        Get image URL for a model code.
        
        Args:
            code: Model code
            
        Returns:
            Image URL or None
        """
        model = await self.find_by_id(code)
        if not model:
            return None
        
        return model.get("image_url", "") or model.get("imageurl", "") or None
    
    async def get_all_colors(self) -> List[str]:
        """
        Get list of all unique colors.
        
        Returns:
            List of color names
        """
        models = await self.find_all()
        colors = set()
        
        for model in models:
            color = (model.get("rang", "") or model.get("color", "")).strip()
            if color:
                colors.add(color)
        
        return sorted(list(colors))
    
    async def get_all_types(self) -> List[str]:
        """
        Get list of all unique types.
        
        Returns:
            List of type names
        """
        models = await self.find_all()
        types = set()
        
        for model in models:
            type_name = (model.get("turi", "") or model.get("type", "")).strip()
            if type_name:
                types.add(type_name)
        
        return sorted(list(types))
    
    async def get_all_collections(self) -> List[str]:
        """
        Get list of all unique collections.
        
        Returns:
            List of collection names
        """
        models = await self.find_all()
        collections = set()
        
        for model in models:
            collection = (model.get("kolleksiya", "") or model.get("collection", "")).strip()
            if collection:
                collections.add(collection)
        
        return sorted(list(collections))

