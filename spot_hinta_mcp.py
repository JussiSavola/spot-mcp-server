"""
spot-hinta MCP server
Fetches Finnish electricity spot prices from api.spot-hinta.fi
and serves them via FastMCP (Streamable HTTP transport).

Endpoint used:
  GET /TodayAndDayForward  -> all available slots for today and, if published, tomorrow

Cache strategy:
  - A single /TodayAndDayForward call populates all data.
  - Prices are static once published; cache expires only when new data can appear:
      * Tomorrow's data present  -> expire at midnight after tomorrow
      * Only today, time < 14:15 -> expire at 14:15 today (when tomorrow's prices first appear)
      * Only today, time >= 14:15 -> expire in 15 min (lazy poll until tomorrow published)
  - Current price is derived from cache by matching the current quarter-hour slot.
    No /JustNow calls are made.

Run:
  python spot_hinta_mcp.py

Default port: 8765  (set env SPOT_HINTA_PORT to override)
MCP endpoint: http://localhost:8765/mcp  (Streamable HTTP)
"""

import os
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://api.spot-hinta.fi"
PORT = int(os.environ.get("SPOT_HINTA_PORT", 8765))
FINNISH_TZ = ZoneInfo("Europe/Helsinki")

# Tomorrow's prices are published around 14:15, at latest ~16:00
TOMORROW_POLL_AFTER_HOUR = 14    # )
TOMORROW_POLL_AFTER_MINUTE = 15  # ) prices published after ~14:15
TOMORROW_POLL_INTERVAL_S = 15 * 60  # re-check every 15 min after 14:xx

# ---------------------------------------------------------------------------
# Price cache
# ---------------------------------------------------------------------------

class PriceCache:
    def __init__(self):
        self._slots: list[dict] = []
        self._expires_at: float = 0.0  # monotonic

    def is_valid(self) -> bool:
        return bool(self._slots) and time.monotonic() < self._expires_at

    def store(self, slots: list[dict]) -> None:
        self._slots = slots
        self._expires_at = time.monotonic() + self._ttl_seconds(slots)

    def _ttl_seconds(self, slots: list[dict]) -> float:
        now_fi = _now_fi()
        today = now_fi.date()
        tomorrow = today + timedelta(days=1)

        dates_in_data = {datetime.fromisoformat(s["datetime"]).date() for s in slots}

        if tomorrow in dates_in_data:
            # Have tomorrow's data — valid until midnight after tomorrow
            midnight = datetime.combine(tomorrow + timedelta(days=1), datetime.min.time(), tzinfo=FINNISH_TZ)
        elif (now_fi.hour, now_fi.minute) >= (TOMORROW_POLL_AFTER_HOUR, TOMORROW_POLL_AFTER_MINUTE):
            # After 14:xx — poll every 15 min waiting for tomorrow's prices
            return float(TOMORROW_POLL_INTERVAL_S)
        else:
            # Before 14:xx — cache until exactly 14:15 today, the earliest tomorrow's
            # prices can appear; no point expiring later since data won't change before then
            midnight = now_fi.replace(
                hour=TOMORROW_POLL_AFTER_HOUR, minute=TOMORROW_POLL_AFTER_MINUTE,
                second=0, microsecond=0,
            )

        return max((midnight - now_fi).total_seconds(), 0.0)

    def get_slots_for_date(self, d: date) -> list[dict]:
        return [s for s in self._slots if datetime.fromisoformat(s["datetime"]).date() == d]

    def get_current_slot(self) -> Optional[dict]:
        now_fi = _now_fi()
        # Build prefix matching current quarter-hour: "YYYY-MM-DDTHH:MM"
        minute_q = (now_fi.minute // 15) * 15
        prefix = now_fi.strftime(f"%Y-%m-%dT%H:{minute_q:02d}")
        for s in self._slots:
            if s["datetime"].startswith(prefix):
                return s
        return None

    def has_tomorrow(self) -> bool:
        tomorrow = (_now_fi() + timedelta(days=1)).date()
        return any(datetime.fromisoformat(s["datetime"]).date() == tomorrow for s in self._slots)


price_cache = PriceCache()

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _now_fi() -> datetime:
    """Return current Finnish local time. Extracted for testability."""
    return datetime.now(FINNISH_TZ)


async def fetch_json(path: str) -> object:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BASE_URL}{path}")
        resp.raise_for_status()
        return resp.json()


def _parse_slots(raw: list) -> list[dict]:
    return [
        {
            "rank": entry["Rank"],
            "datetime": entry["DateTime"],
            "price_no_tax_eur_kwh": entry["PriceNoTax"],
            "price_with_tax_eur_kwh": entry["PriceWithTax"],
        }
        for entry in raw
    ]


async def ensure_cache() -> None:
    """Populate the cache if empty or expired."""
    if price_cache.is_valid():
        return
    raw = await fetch_json("/TodayAndDayForward")
    price_cache.store(_parse_slots(raw))


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="spot-hinta",
    instructions=(
        "Provides Finnish electricity spot prices from api.spot-hinta.fi. "
        "Prices are in EUR/kWh. Rank is a 1-96 percentile within today's prices "
        "(1 = cheapest quarter-hour, 96 = most expensive). "
        "All timestamps are in Finnish local time (EET/EEST, UTC+2/UTC+3). "
        "Days may have 23, 24, or 25 hours due to DST transitions."
    ),
)


@mcp.tool(description=(
    "Get the current electricity spot price (this quarter-hour). "
    "Returns rank (1=cheapest, 96=most expensive), datetime, "
    "price_no_tax_eur_kwh and price_with_tax_eur_kwh. Served from local cache."
))
async def get_current_price() -> dict:
    await ensure_cache()
    slot = price_cache.get_current_slot()
    if slot is None:
        return {"error": "Current quarter-hour slot not found in cached data"}
    return slot


@mcp.tool(description=(
    "Get all electricity spot prices for today as a list of quarter-hour slots. "
    "Each entry has rank, datetime, price_no_tax_eur_kwh, price_with_tax_eur_kwh. "
    "Useful for planning: finding cheap/expensive windows, computing averages, "
    "identifying best hours for pre-heating etc."
))
async def get_today_prices() -> dict:
    await ensure_cache()
    today = _now_fi().date()
    slots = price_cache.get_slots_for_date(today)
    return {"slots": slots, "count": len(slots)}


@mcp.tool(description=(
    "Get all electricity spot prices for tomorrow. "
    "Prices are published by Nord Pool around 14:15 Finnish time, at latest 16:00. "
    "Returns slots with rank, datetime, price_no_tax_eur_kwh, price_with_tax_eur_kwh, "
    "or available=false if tomorrow's prices are not yet published."
))
async def get_tomorrow_prices() -> dict:
    await ensure_cache()
    tomorrow = (_now_fi() + timedelta(days=1)).date()
    slots = price_cache.get_slots_for_date(tomorrow)
    if not slots:
        return {
            "available": False,
            "message": (
                "Tomorrow's prices are not yet published. "
                "They are typically available after 14:15 Finnish time."
            ),
        }
    return {"available": True, "slots": slots, "count": len(slots)}


@mcp.tool(description=(
    "Get spot prices for a specific time window today. "
    "Provide hour_from and hour_to (0-23, inclusive). "
    "Returns matching quarter-hour slots sorted by time, "
    "plus summary stats (min, max, average price with tax, cheapest slot). "
    "Example: hour_from=18, hour_to=23 for tonight's evening prices."
))
async def get_prices_for_hours(hour_from: int, hour_to: int) -> dict:
    if not (0 <= hour_from <= 23 and 0 <= hour_to <= 23):
        return {"error": "hour_from and hour_to must be 0-23"}
    if hour_from > hour_to:
        return {"error": "hour_from must be <= hour_to"}

    await ensure_cache()
    today = _now_fi().date()
    slots = price_cache.get_slots_for_date(today)

    filtered = [
        s for s in slots
        if hour_from <= datetime.fromisoformat(s["datetime"]).hour <= hour_to
    ]
    if not filtered:
        return {"slots": [], "count": 0, "summary": None}

    prices = [s["price_with_tax_eur_kwh"] for s in filtered]
    cheapest = min(filtered, key=lambda s: s["price_with_tax_eur_kwh"])
    most_expensive = max(filtered, key=lambda s: s["price_with_tax_eur_kwh"])

    return {
        "slots": filtered,
        "count": len(filtered),
        "summary": {
            "min_price_with_tax": round(min(prices), 5),
            "max_price_with_tax": round(max(prices), 5),
            "avg_price_with_tax": round(sum(prices) / len(prices), 5),
            "cheapest_slot": cheapest,
            "most_expensive_slot": most_expensive,
        },
    }


@mcp.tool(description=(
    "Get the N cheapest quarter-hour slots remaining today from now onwards. "
    "Useful for scheduling: 'find the 4 cheapest slots left today for pre-heating'. "
    "Returns slots sorted by price ascending, with their datetimes."
))
async def get_cheapest_remaining_slots(n: int = 4) -> dict:
    await ensure_cache()
    today = _now_fi().date()
    slots = price_cache.get_slots_for_date(today)

    now_fi = _now_fi()
    minute_q = (now_fi.minute // 15) * 15
    now_prefix = now_fi.strftime(f"%Y-%m-%dT%H:{minute_q:02d}")

    remaining = [s for s in slots if s["datetime"][:16] >= now_prefix]
    sorted_slots = sorted(remaining, key=lambda s: s["price_with_tax_eur_kwh"])

    return {
        "requested_n": n,
        "available_remaining_slots": len(remaining),
        "cheapest_slots": sorted_slots[:n],
    }


@mcp.tool(description=(
    "Summarise today's price distribution: cheapest slot, most expensive slot, "
    "average price, and how the current price compares to today's range. "
    "Also indicates whether tomorrow's prices are already available. "
    "Good for a quick sanity check or agent reasoning context."
))
async def get_today_summary() -> dict:
    await ensure_cache()
    today = _now_fi().date()
    slots = price_cache.get_slots_for_date(today)
    current = price_cache.get_current_slot()

    if not slots or current is None:
        return {"error": "Price data unavailable"}

    prices = [s["price_with_tax_eur_kwh"] for s in slots]
    cheapest = min(slots, key=lambda s: s["price_with_tax_eur_kwh"])
    most_expensive = max(slots, key=lambda s: s["price_with_tax_eur_kwh"])
    avg = sum(prices) / len(prices)

    current_price = current["price_with_tax_eur_kwh"]
    price_range = most_expensive["price_with_tax_eur_kwh"] - cheapest["price_with_tax_eur_kwh"]
    pct_in_range = (
        round((current_price - cheapest["price_with_tax_eur_kwh"]) / price_range * 100, 1)
        if price_range > 0 else 0.0
    )

    return {
        "current": {
            "rank": current["rank"],
            "price_with_tax_eur_kwh": current_price,
            "pct_of_todays_range": pct_in_range,
            "assessment": (
                "very cheap" if current["rank"] <= 10 else
                "cheap" if current["rank"] <= 30 else
                "average" if current["rank"] <= 70 else
                "expensive" if current["rank"] <= 90 else
                "very expensive"
            ),
        },
        "today": {
            "avg_price_with_tax": round(avg, 5),
            "min_price_with_tax": round(min(prices), 5),
            "max_price_with_tax": round(max(prices), 5),
            "cheapest_slot": cheapest,
            "most_expensive_slot": most_expensive,
            "total_slots": len(slots),
            "tomorrow_prices_available": price_cache.has_tomorrow(),
        },
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Starting spot-hinta MCP server on port {PORT}")
    print(f"MCP endpoint: http://localhost:{PORT}/mcp")
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=PORT,
        path="/mcp",
        log_level="debug",
    )
