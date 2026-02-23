"""
Base Repository - Database access uchun base class.

Repository pattern:
- Data access logic ni business logic dan ajratish
- Testability ni oshirish
- Code reusability
"""

import logging
from typing import Optional, List, Dict, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseRepository(ABC):
    """
    Base repository class for data access.
    
    All repositories should inherit from this class and implement
    the required methods.
    """
    
    def __init__(self):
        """Initialize repository."""
        self._cache = {}
    
    @abstractmethod
    async def find_by_id(self, id: Any) -> Optional[Dict]:
        """
        Find record by ID.
        
        Args:
            id: Record ID
            
        Returns:
            Record dictionary or None
        """
        pass
    
    @abstractmethod
    async def find_all(self) -> List[Dict]:
        """
        Find all records.
        
        Returns:
            List of record dictionaries
        """
        pass
    
    @abstractmethod
    async def find_by_criteria(self, criteria: Dict[str, Any]) -> List[Dict]:
        """
        Find records by criteria.
        
        Args:
            criteria: Search criteria dictionary
            
        Returns:
            List of matching record dictionaries
        """
        pass
    
    async def count(self) -> int:
        """
        Count total records.
        
        Returns:
            Total number of records
        """
        records = await self.find_all()
        return len(records)
    
    async def exists(self, id: Any) -> bool:
        """
        Check if record exists.
        
        Args:
            id: Record ID
            
        Returns:
            True if exists, False otherwise
        """
        record = await self.find_by_id(id)
        return record is not None
    
    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        return self._cache.get(key)
    
    def _set_to_cache(self, key: str, value: Any):
        """Set value to cache."""
        self._cache[key] = value
    
    def _clear_cache(self):
        """Clear all cache."""
        self._cache.clear()

