"""
Tests for Google Sheet cache reload behavior.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services import google_sheet


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_sheets1_to_cache_rebuilds_collection_index(sample_products):
    """Sheets1 reload should rebuild collection index used by astatka collection search."""
    original_cache = google_sheet.CACHE.copy()

    try:
        # Simulate stale index from previous cache state
        google_sheet.CACHE["collection_index"] = {"stale-collection": [{"code": "OLD-1"}]}

        with patch("services.google_sheet.GoogleSheetService"), patch(
            "services.google_sheet._load_sheets1_direct",
            new=AsyncMock(return_value=sample_products),
        ):
            await google_sheet.load_sheets1_to_cache()

        assert set(google_sheet.CACHE["collection_index"].keys()) == {"0-start", "1-stage"}
        assert len(google_sheet.CACHE["collection_index"]["0-start"]) == 2
        assert google_sheet.CACHE["collection_index"]["0-start"][0]["date"] == "2026-01-01"
    finally:
        google_sheet.CACHE.clear()
        google_sheet.CACHE.update(original_cache)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_sheets1_to_cache_clears_stale_collection_index():
    """Sheets1 reload should clear old collection index entries when new data has no collection values."""
    original_cache = google_sheet.CACHE.copy()

    try:
        google_sheet.CACHE["collection_index"] = {"old": [{"code": "OLD-1"}]}
        sheets1_without_collection = [{"code": "A-1", "quantity": "3", "collection": "", "date": "2026-03-04"}]

        with patch("services.google_sheet.GoogleSheetService"), patch(
            "services.google_sheet._load_sheets1_direct",
            new=AsyncMock(return_value=sheets1_without_collection),
        ):
            await google_sheet.load_sheets1_to_cache()

        assert google_sheet.CACHE["collection_index"] == {}
    finally:
        google_sheet.CACHE.clear()
        google_sheet.CACHE.update(original_cache)
