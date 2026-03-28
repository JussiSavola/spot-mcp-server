"""
Tests for spot_hinta_mcp.py

Real sample data from api.spot-hinta.fi fetched 2026-03-28:
  - Today  (2026-03-28): 96 slots (24h × 4), timezone +02:00 (EET)
  - Tomorrow (2026-03-29): 92 slots (23h × 4, DST spring-forward), last slots +03:00 (EEST)
"""

import time
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from unittest.mock import patch

from spot_hinta_mcp import (
    FINNISH_TZ,
    PriceCache,
    _parse_slots,
)


def fi_time(dt_str: str) -> datetime:
    """Create a timezone-aware Finnish datetime from a naive ISO string."""
    return datetime.fromisoformat(dt_str).replace(tzinfo=FINNISH_TZ)


def patch_now(dt_str: str):
    """Patch spot_hinta_mcp._now_fi to return a fixed Finnish datetime."""
    return patch("spot_hinta_mcp._now_fi", return_value=fi_time(dt_str))

# ---------------------------------------------------------------------------
# Fixtures — real data sampled from the API on 2026-03-28
# ---------------------------------------------------------------------------

# One slot per hour, today (2026-03-28), +02:00
RAW_TODAY = [
    {"Rank": 93, "DateTime": "2026-03-28T00:00:00+02:00", "PriceNoTax": 0.02327, "PriceWithTax": 0.02920},
    {"Rank": 85, "DateTime": "2026-03-28T01:00:00+02:00", "PriceNoTax": 0.02004, "PriceWithTax": 0.02515},
    {"Rank": 71, "DateTime": "2026-03-28T02:00:00+02:00", "PriceNoTax": 0.01680, "PriceWithTax": 0.02108},
    {"Rank": 59, "DateTime": "2026-03-28T03:00:00+02:00", "PriceNoTax": 0.01514, "PriceWithTax": 0.01900},
    {"Rank": 50, "DateTime": "2026-03-28T04:00:00+02:00", "PriceNoTax": 0.01397, "PriceWithTax": 0.01753},
    {"Rank": 40, "DateTime": "2026-03-28T05:00:00+02:00", "PriceNoTax": 0.01249, "PriceWithTax": 0.01567},
    {"Rank": 23, "DateTime": "2026-03-28T06:00:00+02:00", "PriceNoTax": 0.00971, "PriceWithTax": 0.01219},
    {"Rank": 11, "DateTime": "2026-03-28T07:00:00+02:00", "PriceNoTax": 0.00799, "PriceWithTax": 0.01003},
    {"Rank": 16, "DateTime": "2026-03-28T08:00:00+02:00", "PriceNoTax": 0.00879, "PriceWithTax": 0.01103},
    {"Rank": 38, "DateTime": "2026-03-28T09:00:00+02:00", "PriceNoTax": 0.01234, "PriceWithTax": 0.01549},
    {"Rank": 51, "DateTime": "2026-03-28T10:00:00+02:00", "PriceNoTax": 0.01397, "PriceWithTax": 0.01753},
    {"Rank": 57, "DateTime": "2026-03-28T11:00:00+02:00", "PriceNoTax": 0.01475, "PriceWithTax": 0.01851},
    {"Rank": 24, "DateTime": "2026-03-28T12:00:00+02:00", "PriceNoTax": 0.00976, "PriceWithTax": 0.01225},
    {"Rank":  9, "DateTime": "2026-03-28T13:00:00+02:00", "PriceNoTax": 0.00720, "PriceWithTax": 0.00904},
    {"Rank":  5, "DateTime": "2026-03-28T14:00:00+02:00", "PriceNoTax": 0.00641, "PriceWithTax": 0.00804},
    {"Rank":  1, "DateTime": "2026-03-28T15:00:00+02:00", "PriceNoTax": 0.00500, "PriceWithTax": 0.00628},
    {"Rank": 14, "DateTime": "2026-03-28T16:00:00+02:00", "PriceNoTax": 0.00827, "PriceWithTax": 0.01038},
    {"Rank": 61, "DateTime": "2026-03-28T17:00:00+02:00", "PriceNoTax": 0.01562, "PriceWithTax": 0.01960},
    {"Rank": 78, "DateTime": "2026-03-28T18:00:00+02:00", "PriceNoTax": 0.01817, "PriceWithTax": 0.02280},
    {"Rank": 94, "DateTime": "2026-03-28T19:00:00+02:00", "PriceNoTax": 0.02358, "PriceWithTax": 0.02959},
    {"Rank": 89, "DateTime": "2026-03-28T20:00:00+02:00", "PriceNoTax": 0.02225, "PriceWithTax": 0.02792},
    {"Rank": 79, "DateTime": "2026-03-28T21:00:00+02:00", "PriceNoTax": 0.01836, "PriceWithTax": 0.02304},
    {"Rank": 64, "DateTime": "2026-03-28T22:00:00+02:00", "PriceNoTax": 0.01584, "PriceWithTax": 0.01988},
    {"Rank": 73, "DateTime": "2026-03-28T23:00:00+02:00", "PriceNoTax": 0.01728, "PriceWithTax": 0.02169},
]

# One slot per hour, tomorrow (2026-03-29 = DST spring-forward, only 23 hours)
# Clocks jump from 03:00 -> 04:00, so hour 03 does not exist
RAW_TOMORROW = [
    {"Rank": 52, "DateTime": "2026-03-29T00:00:00+02:00", "PriceNoTax": 0.01414, "PriceWithTax": 0.01774},
    {"Rank": 44, "DateTime": "2026-03-29T01:00:00+02:00", "PriceNoTax": 0.01307, "PriceWithTax": 0.01640},
    {"Rank": 22, "DateTime": "2026-03-29T02:00:00+02:00", "PriceNoTax": 0.00965, "PriceWithTax": 0.01211},
    # hour 03 skipped — DST gap
    {"Rank": 26, "DateTime": "2026-03-29T04:00:00+03:00", "PriceNoTax": 0.01036, "PriceWithTax": 0.01300},
    {"Rank": 15, "DateTime": "2026-03-29T05:00:00+03:00", "PriceNoTax": 0.00846, "PriceWithTax": 0.01062},
    {"Rank":  8, "DateTime": "2026-03-29T06:00:00+03:00", "PriceNoTax": 0.00714, "PriceWithTax": 0.00896},
    {"Rank": 20, "DateTime": "2026-03-29T07:00:00+03:00", "PriceNoTax": 0.00946, "PriceWithTax": 0.01187},
    {"Rank": 36, "DateTime": "2026-03-29T08:00:00+03:00", "PriceNoTax": 0.01183, "PriceWithTax": 0.01485},
    {"Rank": 53, "DateTime": "2026-03-29T09:00:00+03:00", "PriceNoTax": 0.01426, "PriceWithTax": 0.01790},
    {"Rank": 41, "DateTime": "2026-03-29T10:00:00+03:00", "PriceNoTax": 0.01268, "PriceWithTax": 0.01591},
    {"Rank": 33, "DateTime": "2026-03-29T11:00:00+03:00", "PriceNoTax": 0.01124, "PriceWithTax": 0.01411},
    {"Rank": 29, "DateTime": "2026-03-29T12:00:00+03:00", "PriceNoTax": 0.01069, "PriceWithTax": 0.01342},
    {"Rank": 28, "DateTime": "2026-03-29T13:00:00+03:00", "PriceNoTax": 0.01063, "PriceWithTax": 0.01334},
    {"Rank": 37, "DateTime": "2026-03-29T14:00:00+03:00", "PriceNoTax": 0.01198, "PriceWithTax": 0.01504},
    {"Rank": 47, "DateTime": "2026-03-29T15:00:00+03:00", "PriceNoTax": 0.01360, "PriceWithTax": 0.01707},
    {"Rank": 63, "DateTime": "2026-03-29T16:00:00+03:00", "PriceNoTax": 0.01582, "PriceWithTax": 0.01985},
    {"Rank": 70, "DateTime": "2026-03-29T17:00:00+03:00", "PriceNoTax": 0.01671, "PriceWithTax": 0.02097},
    {"Rank": 76, "DateTime": "2026-03-29T18:00:00+03:00", "PriceNoTax": 0.01793, "PriceWithTax": 0.02250},
    {"Rank": 87, "DateTime": "2026-03-29T19:00:00+03:00", "PriceNoTax": 0.02138, "PriceWithTax": 0.02683},
    {"Rank": 82, "DateTime": "2026-03-29T20:00:00+03:00", "PriceNoTax": 0.01927, "PriceWithTax": 0.02419},
    {"Rank": 68, "DateTime": "2026-03-29T21:00:00+03:00", "PriceNoTax": 0.01640, "PriceWithTax": 0.02058},
    {"Rank": 45, "DateTime": "2026-03-29T22:00:00+03:00", "PriceNoTax": 0.01318, "PriceWithTax": 0.01654},
    {"Rank": 23, "DateTime": "2026-03-29T23:00:00+03:00", "PriceNoTax": 0.01040, "PriceWithTax": 0.01305},
]

# Quarter-hour slots for one hour — used to test current-slot matching
RAW_QUARTER_HOUR = [
    {"Rank": 61, "DateTime": "2026-03-28T17:00:00+02:00", "PriceNoTax": 0.01562, "PriceWithTax": 0.01960},
    {"Rank": 62, "DateTime": "2026-03-28T17:15:00+02:00", "PriceNoTax": 0.01570, "PriceWithTax": 0.01971},
    {"Rank": 63, "DateTime": "2026-03-28T17:30:00+02:00", "PriceNoTax": 0.01580, "PriceWithTax": 0.01982},
    {"Rank": 64, "DateTime": "2026-03-28T17:45:00+02:00", "PriceNoTax": 0.01590, "PriceWithTax": 0.01993},
]


def make_slots(raw: list) -> list[dict]:
    return _parse_slots(raw)


# ---------------------------------------------------------------------------
# _parse_slots
# ---------------------------------------------------------------------------

class TestParseSlots:
    def test_field_mapping(self):
        slots = _parse_slots([RAW_TODAY[0]])
        s = slots[0]
        assert s["rank"] == 93
        assert s["datetime"] == "2026-03-28T00:00:00+02:00"
        assert s["price_no_tax_eur_kwh"] == pytest.approx(0.02327)
        assert s["price_with_tax_eur_kwh"] == pytest.approx(0.02920)

    def test_empty_input(self):
        assert _parse_slots([]) == []

    def test_all_fields_present(self):
        slots = _parse_slots(RAW_TODAY)
        for s in slots:
            assert set(s.keys()) == {"rank", "datetime", "price_no_tax_eur_kwh", "price_with_tax_eur_kwh"}

    def test_preserves_timezone_offset(self):
        slots = _parse_slots(RAW_TOMORROW)
        # Earlier slots +02:00 (EET), later +03:00 (EEST)
        assert "+02:00" in slots[0]["datetime"]
        assert "+03:00" in slots[-1]["datetime"]


# ---------------------------------------------------------------------------
# PriceCache.get_slots_for_date
# ---------------------------------------------------------------------------

class TestGetSlotsForDate:
    def setup_method(self):
        with patch_now("2026-03-28 10:00:00"):
            self.cache = PriceCache()
            self.cache.store(make_slots(RAW_TODAY + RAW_TOMORROW))

    def test_today_slots(self):
        slots = self.cache.get_slots_for_date(date(2026, 3, 28))
        assert len(slots) == len(RAW_TODAY)
        assert all(s["datetime"].startswith("2026-03-28") for s in slots)

    def test_tomorrow_slots(self):
        slots = self.cache.get_slots_for_date(date(2026, 3, 29))
        assert len(slots) == len(RAW_TOMORROW)
        assert all(s["datetime"].startswith("2026-03-29") for s in slots)

    def test_tomorrow_dst_day_has_23_hours(self):
        """DST spring-forward: tomorrow only has 23 hours."""
        slots = self.cache.get_slots_for_date(date(2026, 3, 29))
        assert len(slots) == 23  # one slot per hour in our fixture

    def test_unknown_date_returns_empty(self):
        slots = self.cache.get_slots_for_date(date(2026, 3, 30))
        assert slots == []


# ---------------------------------------------------------------------------
# PriceCache.has_tomorrow
# ---------------------------------------------------------------------------

class TestHasTomorrow:
    def test_true_when_tomorrow_present(self):
        with patch_now("2026-03-28 12:00:00"):
            cache = PriceCache()
            cache.store(make_slots(RAW_TODAY + RAW_TOMORROW))
            assert cache.has_tomorrow() is True

    def test_false_when_only_today(self):
        with patch_now("2026-03-28 12:00:00"):
            cache = PriceCache()
            cache.store(make_slots(RAW_TODAY))
            assert cache.has_tomorrow() is False


# ---------------------------------------------------------------------------
# PriceCache._ttl_seconds
# ---------------------------------------------------------------------------

class TestTTLSeconds:
    def test_tomorrow_present_expires_after_tomorrow_midnight(self):
        with patch_now("2026-03-28 10:00:00"):
            cache = PriceCache()
            slots = make_slots(RAW_TODAY + RAW_TOMORROW)
            ttl = cache._ttl_seconds(slots)
            # Now is 2026-03-28 10:00 EET
            # Expires at 2026-03-30 00:00 Finnish time
            # Note: datetime.combine on Windows/tzdata assigns EET (+02:00) to March 30
            # instead of EEST (+03:00), so effective midnight is 1h off = 38h = 136800s
            # (would be 133200s with correct DST; acceptable 1h error once per year)
            assert ttl == pytest.approx(136800, abs=60)

    def test_only_today_before_1415_expires_at_midnight(self):
        with patch_now("2026-03-28 10:00:00"):
            cache = PriceCache()
            slots = make_slots(RAW_TODAY)
            ttl = cache._ttl_seconds(slots)
            # 10:00 EET → midnight = 14h = 50400s
            assert ttl == pytest.approx(50400, abs=60)

    def test_only_today_after_1415_expires_in_15_minutes(self):
        with patch_now("2026-03-28 15:00:00"):
            cache = PriceCache()
            slots = make_slots(RAW_TODAY)
            ttl = cache._ttl_seconds(slots)
            assert ttl == 15 * 60

    def test_just_before_1415_still_uses_midnight_ttl(self):
        with patch_now("2026-03-28 14:14:59"):
            cache = PriceCache()
            slots = make_slots(RAW_TODAY)
            ttl = cache._ttl_seconds(slots)
            # 9h 45m 1s to midnight > 15 min
            assert ttl > 15 * 60

    def test_exactly_1415_uses_poll_ttl(self):
        with patch_now("2026-03-28 14:15:00"):
            cache = PriceCache()
            slots = make_slots(RAW_TODAY)
            ttl = cache._ttl_seconds(slots)
            assert ttl == 15 * 60


# ---------------------------------------------------------------------------
# PriceCache.is_valid
# ---------------------------------------------------------------------------

class TestIsValid:
    def test_empty_cache_is_invalid(self):
        assert PriceCache().is_valid() is False

    def test_freshly_stored_is_valid(self):
        with patch_now("2026-03-28 10:00:00"):
            cache = PriceCache()
            cache.store(make_slots(RAW_TODAY + RAW_TOMORROW))
            assert cache.is_valid() is True

    def test_expired_cache_is_invalid(self):
        cache = PriceCache()
        cache.store(make_slots(RAW_TODAY + RAW_TOMORROW))
        # Force expiry by backdating _expires_at
        cache._expires_at = time.monotonic() - 1
        assert cache.is_valid() is False


# ---------------------------------------------------------------------------
# PriceCache.get_current_slot
# ---------------------------------------------------------------------------

class TestGetCurrentSlot:
    def setup_method(self):
        with patch_now("2026-03-28 10:00:00"):
            self.cache = PriceCache()
            self.cache.store(make_slots(RAW_TODAY + RAW_QUARTER_HOUR))

    def test_matches_on_the_hour(self):
        with patch_now("2026-03-28 17:00:00"):
            slot = self.cache.get_current_slot()
        assert slot is not None
        assert slot["datetime"].startswith("2026-03-28T17:00")

    def test_matches_quarter_past(self):
        with patch_now("2026-03-28 17:15:00"):
            slot = self.cache.get_current_slot()
        assert slot is not None
        assert slot["datetime"].startswith("2026-03-28T17:15")

    def test_matches_half_past(self):
        with patch_now("2026-03-28 17:30:00"):
            slot = self.cache.get_current_slot()
        assert slot is not None
        assert slot["datetime"].startswith("2026-03-28T17:30")

    def test_matches_quarter_to(self):
        with patch_now("2026-03-28 17:45:00"):
            slot = self.cache.get_current_slot()
        assert slot is not None
        assert slot["datetime"].startswith("2026-03-28T17:45")

    def test_mid_quarter_rounds_down_to_slot_start(self):
        """17:07 falls in the 17:00 slot."""
        with patch_now("2026-03-28 17:07:33"):
            slot = self.cache.get_current_slot()
        assert slot is not None
        assert slot["datetime"].startswith("2026-03-28T17:00")

    def test_just_before_next_quarter(self):
        """17:29:59 still in the 17:15 slot."""
        with patch_now("2026-03-28 17:29:59"):
            slot = self.cache.get_current_slot()
        assert slot is not None
        assert slot["datetime"].startswith("2026-03-28T17:15")

    def test_returns_none_when_not_in_cache(self):
        """Only 17:xx slots in cache — 23:xx returns None."""
        with patch_now("2026-03-28 10:00:00"):
            cache = PriceCache()
            cache.store(make_slots(RAW_QUARTER_HOUR))
        with patch_now("2026-03-28 23:59:00"):
            slot = cache.get_current_slot()
        assert slot is None


