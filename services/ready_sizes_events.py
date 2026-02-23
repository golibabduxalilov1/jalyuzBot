"""
Ready Sizes Event Logging System

This module provides real-time event tracking for Tayyor Razmerlar (Ready Sizes) section.
Events are stored in RAM and provide monitoring for:
- Cart events (items added to cart)
- Confirmed events (items sold/confirmed)
- Deleted events (items removed/returned)

Each event stores: timestamp, model, size, quantity, user info, role, partner info.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import Counter

# ==================== EVENT DATA CLASSES ====================

@dataclass
class ReadySizeEvent:
    """Base event class for Ready Sizes tracking"""
    timestamp: datetime
    model_nomi: str
    code: str
    razmer: str
    qty: int
    kolleksiya: str
    order_id: Optional[str]  # row ID or unique identifier
    # Buyer information (who placed the order)
    user_id: int
    user_name: str
    role: str  # "admin" / "hamkor" / "sotuvchi" / "oddiy foydalanuvchi"
    seller_name: Optional[str]  # if hamkor, which seller
    # Confirmer information (who confirmed/approved the order) - for confirmed events only
    confirmer_id: Optional[int] = None
    confirmer_name: Optional[str] = None
    confirmer_role: Optional[str] = None
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> "ReadySizeEvent":
        d = dict(data)
        timestamp_raw = d.get("timestamp")
        if isinstance(timestamp_raw, str):
            try:
                d["timestamp"] = datetime.fromisoformat(timestamp_raw)
            except Exception:
                d["timestamp"] = datetime.utcnow()
        elif isinstance(timestamp_raw, datetime):
            d["timestamp"] = timestamp_raw
        else:
            d["timestamp"] = datetime.utcnow()
        # Set default values for new fields if not present (backward compatibility)
        d.setdefault("confirmer_id", None)
        d.setdefault("confirmer_name", None)
        d.setdefault("confirmer_role", None)
        return cls(**d)


# ==================== EVENT STORAGE ====================

# RAM storage for events (each list stores chronological events)
_CART_EVENTS: List[ReadySizeEvent] = []
_CONFIRMED_EVENTS: List[ReadySizeEvent] = []
_DELETED_EVENTS: List[ReadySizeEvent] = []

# Event TTL: 7 days (events older than 7 days are auto-cleaned)
_EVENT_TTL = timedelta(days=7)


# ==================== HELPER FUNCTIONS ====================

def _cleanup_old_events() -> None:
    """Remove events older than 7 days from all event logs"""
    global _CART_EVENTS, _CONFIRMED_EVENTS, _DELETED_EVENTS
    
    now = datetime.utcnow()
    cutoff_time = now - _EVENT_TTL
    
    _CART_EVENTS = [e for e in _CART_EVENTS if e.timestamp > cutoff_time]
    _CONFIRMED_EVENTS = [e for e in _CONFIRMED_EVENTS if e.timestamp > cutoff_time]
    _DELETED_EVENTS = [e for e in _DELETED_EVENTS if e.timestamp > cutoff_time]


def _determine_user_role(user_id: int, is_partner: bool = False) -> str:
    """
    Determine user role based on user_id and context.
    
    Returns one of: "Super admin", "Yordamchi admin", "Sotuvchi", "Hamkor", "Oddiy foydalanuvchi"
    """
    from services.admin_utils import is_admin, is_super_admin
    from services.admin_storage import is_seller
    
    if is_super_admin(user_id):
        return "Super admin"
    elif is_admin(user_id):
        return "Yordamchi admin"
    elif is_seller(user_id):
        return "Sotuvchi"
    elif is_partner:
        return "Hamkor"
    else:
        return "Oddiy foydalanuvchi"


# ==================== EVENT LOGGING FUNCTIONS ====================

def log_cart_event(
    model_nomi: str,
    code: str,
    razmer: str,
    qty: int,
    kolleksiya: str,
    user_id: int,
    user_name: str,
    is_partner: bool = False,
    seller_name: Optional[str] = None,
    order_id: Optional[str] = None,
) -> None:
    """
    Log an event when an item is added to cart.
    
    Args:
        model_nomi: Model name
        code: Product code
        razmer: Size
        qty: Quantity added
        kolleksiya: Collection name
        user_id: User ID who added to cart
        user_name: User display name
        is_partner: Whether user is a partner
        seller_name: Partner's seller name (if applicable)
        order_id: Optional order/row identifier
    """
    _cleanup_old_events()
    
    role = _determine_user_role(user_id, is_partner)
    
    event = ReadySizeEvent(
        timestamp=datetime.utcnow(),
        model_nomi=model_nomi,
        code=code,
        razmer=razmer,
        qty=qty,
        kolleksiya=kolleksiya,
        order_id=order_id,
        user_id=user_id,
        user_name=user_name,
        role=role,
        seller_name=seller_name,
    )
    
    _CART_EVENTS.append(event)


def log_confirmed_event(
    model_nomi: str,
    code: str,
    razmer: str,
    qty: int,
    kolleksiya: str,
    user_id: int,
    user_name: str,
    is_partner: bool = False,
    seller_name: Optional[str] = None,
    order_id: Optional[str] = None,
    confirmer_id: Optional[int] = None,
    confirmer_name: Optional[str] = None,
) -> None:
    """
    Log an event when an order is confirmed (sold).
    
    Args:
        model_nomi: Model name
        code: Product code
        razmer: Size
        qty: Quantity confirmed
        kolleksiya: Collection name
        user_id: User ID of buyer (who placed the order)
        user_name: Buyer display name
        is_partner: Whether buyer is a partner
        seller_name: Partner's seller name (if buyer is partner)
        order_id: Optional order/row identifier
        confirmer_id: User ID of admin who confirmed the order
        confirmer_name: Display name of admin who confirmed
    """
    _cleanup_old_events()
    
    # Buyer role
    buyer_role = _determine_user_role(user_id, is_partner)
    
    # Confirmer role
    confirmer_role = None
    if confirmer_id is not None:
        confirmer_role = _determine_user_role(confirmer_id, is_partner=False)
    
    event = ReadySizeEvent(
        timestamp=datetime.utcnow(),
        model_nomi=model_nomi,
        code=code,
        razmer=razmer,
        qty=qty,
        kolleksiya=kolleksiya,
        order_id=order_id,
        user_id=user_id,
        user_name=user_name,
        role=buyer_role,
        seller_name=seller_name,
        confirmer_id=confirmer_id,
        confirmer_name=confirmer_name,
        confirmer_role=confirmer_role,
    )
    
    _CONFIRMED_EVENTS.append(event)


def log_deleted_event(
    model_nomi: str,
    code: str,
    razmer: str,
    qty: int,
    kolleksiya: str,
    user_id: int,
    user_name: str,
    is_partner: bool = False,
    seller_name: Optional[str] = None,
    order_id: Optional[str] = None,
) -> None:
    """
    Log an event when an item is deleted/removed/returned.
    
    Args:
        model_nomi: Model name
        code: Product code
        razmer: Size
        qty: Quantity deleted
        kolleksiya: Collection name
        user_id: User ID who deleted the item
        user_name: User display name
        is_partner: Whether user is a partner
        seller_name: Partner's seller name (if applicable)
        order_id: Optional order/row identifier
    """
    _cleanup_old_events()
    
    role = _determine_user_role(user_id, is_partner)
    
    event = ReadySizeEvent(
        timestamp=datetime.utcnow(),
        model_nomi=model_nomi,
        code=code,
        razmer=razmer,
        qty=qty,
        kolleksiya=kolleksiya,
        order_id=order_id,
        user_id=user_id,
        user_name=user_name,
        role=role,
        seller_name=seller_name,
    )
    
    _DELETED_EVENTS.append(event)


# ==================== EVENT RETRIEVAL FUNCTIONS ====================

def get_cart_events(limit: Optional[int] = None) -> List[ReadySizeEvent]:
    """
    Get all cart events (most recent first).
    
    Args:
        limit: Optional limit on number of events to return
    
    Returns:
        List of cart events, sorted by timestamp (newest first)
    """
    _cleanup_old_events()
    events = sorted(_CART_EVENTS, key=lambda e: e.timestamp, reverse=True)
    if limit:
        return events[:limit]
    return events


def get_confirmed_events(limit: Optional[int] = None) -> List[ReadySizeEvent]:
    """
    Get all confirmed events (most recent first).
    
    Args:
        limit: Optional limit on number of events to return
    
    Returns:
        List of confirmed events, sorted by timestamp (newest first)
    """
    _cleanup_old_events()
    events = sorted(_CONFIRMED_EVENTS, key=lambda e: e.timestamp, reverse=True)
    if limit:
        return events[:limit]
    return events


def get_deleted_events(limit: Optional[int] = None) -> List[ReadySizeEvent]:
    """
    Get all deleted events (most recent first).
    
    Args:
        limit: Optional limit on number of events to return
    
    Returns:
        List of deleted events, sorted by timestamp (newest first)
    """
    _cleanup_old_events()
    events = sorted(_DELETED_EVENTS, key=lambda e: e.timestamp, reverse=True)
    if limit:
        return events[:limit]
    return events


def get_user_events(
    user_id: int,
    is_admin: bool = False,
    is_seller: bool = False,
    seller_name: Optional[str] = None
) -> Dict[str, List[ReadySizeEvent]]:
    """
    Get events filtered by user permissions.
    
    Args:
        user_id: Current user ID
        is_admin: Whether user is admin (sees all events)
        is_seller: Whether user is seller (sees own + linked partners)
        seller_name: Seller's name (for filtering partner events)
    
    Returns:
        Dict with 'cart', 'confirmed', 'deleted' event lists (newest first)
    """
    _cleanup_old_events()
    
    def filter_events(events: List[ReadySizeEvent]) -> List[ReadySizeEvent]:
        if is_admin:
            # Admin sees all events
            return events
        elif is_seller and seller_name:
            # Seller sees own events + events from partners linked to them
            return [e for e in events if e.user_id == user_id or e.seller_name == seller_name]
        else:
            # Regular user/partner sees only their own events
            return [e for e in events if e.user_id == user_id]
    
    # Filter and sort each event type
    cart_filtered = filter_events(_CART_EVENTS)
    confirmed_filtered = filter_events(_CONFIRMED_EVENTS)
    deleted_filtered = filter_events(_DELETED_EVENTS)
    
    return {
        "cart": sorted(cart_filtered, key=lambda e: e.timestamp, reverse=True),
        "confirmed": sorted(confirmed_filtered, key=lambda e: e.timestamp, reverse=True),
        "deleted": sorted(deleted_filtered, key=lambda e: e.timestamp, reverse=True)
    }


def get_events_today_count() -> Dict[str, int]:
    """
    Get count of events that happened today.
    
    Returns:
        Dict with keys: cart_count, confirmed_count, deleted_count
    """
    _cleanup_old_events()
    
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    
    cart_today = sum(1 for e in _CART_EVENTS if e.timestamp >= today_start)
    confirmed_today = sum(1 for e in _CONFIRMED_EVENTS if e.timestamp >= today_start)
    deleted_today = sum(1 for e in _DELETED_EVENTS if e.timestamp >= today_start)
    
    return {
        "cart_count": cart_today,
        "confirmed_count": confirmed_today,
        "deleted_count": deleted_today,
    }


def get_total_event_counts() -> Dict[str, int]:
    """
    Get total count of all events (within TTL).
    
    Returns:
        Dict with keys: cart_count, confirmed_count, deleted_count
    """
    _cleanup_old_events()
    
    return {
        "cart_count": len(_CART_EVENTS),
        "confirmed_count": len(_CONFIRMED_EVENTS),
        "deleted_count": len(_DELETED_EVENTS),
    }


def get_top_confirmed_models(limit: int = 10) -> List[tuple]:
    """
    Get top N most confirmed (sold) models.
    
    Args:
        limit: Number of top models to return
    
    Returns:
        List of tuples: [(model_nomi, count), ...]
    """
    _cleanup_old_events()
    
    model_counter = Counter()
    for event in _CONFIRMED_EVENTS:
        if event.model_nomi:
            model_counter[event.model_nomi] += event.qty
    
    return model_counter.most_common(limit)


def clear_all_events() -> None:
    """Clear all events (admin function for testing or maintenance)"""
    global _CART_EVENTS, _CONFIRMED_EVENTS, _DELETED_EVENTS
    _CART_EVENTS.clear()
    _CONFIRMED_EVENTS.clear()
    _DELETED_EVENTS.clear()

