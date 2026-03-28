"""
spot-hinta MCP server
Fetches Finnish electricity spot prices from api.spot-hinta.fi
and serves them via FastMCP (Streamable HTTP transport).

Endpoints used:
  GET /JustNow        -> current quarter-hour: Rank, DateTime, PriceNoTax, PriceWithTax
  GET /Today          -> all 96 quarter-hour slots for today (same fields)

Cache strategy:
  - JustNow: cached for 60 seconds
  - Today:   cached until next quarter-hour boundary (refreshed at :00, :15, :30, :45)
  - tomorrow's prices available from ~13:00 EET — fetched on demand, cached for 1 hour

Run:
  python spot_hinta_mcp.py

Default port: 8765  (set env SPOT_HINTA_PORT to override)
MCP endpoint: http://localhost:8765/mcp  (Streamable HTTP)
"""

import asyncio
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://api.spot-hinta.fi"
PORT = int(os.environ.get("SPOT_HINTA_PORT", 8765))

# ---------------------------------------------------------------------------
# Simple in-memory cache
# ---------------------------------------------------------------------------

class Cache:
    def __init__(self):
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str, max_age_s: float) -> Optional[object]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > max_age_s:
            return None
        return value

    def set(self, key: str, value: object):
        self._store[key] = (time.monotonic(), value)

    def invalidate(self, key: str):
        self._store.pop(key, None)


cache = Cache()

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def fetch_json(path: str) -> object:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BASE_URL}{path}")
        resp.raise_for_status()
        return resp.json()


def seconds_until_next_quarter() -> float:
    """Seconds until the next :00, :15, :30, or :45 boundary."""
    now = datetime.now()
    minutes = now.minute % 15
    seconds = now.second
    return (15 - minutes) * 60 - seconds


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="spot-hinta",
    instructions=(
        "Provides Finnish electricity spot prices from api.spot-hinta.fi. "
        "Prices are in EUR/kWh. Rank is a 1-96 percentile within today's prices "
        "(1 = cheapest quarter-hour, 96 = most expensive). "
        "All timestamps are in Finnish local time (EET/EEST, UTC+2/UTC+3)."
    ),
)


@mcp.tool(description=(
    "Get the current electricity spot price (this quarter-hour). "
    "Returns Rank (1=cheapest, 96=most expensive), DateTime, "
    "PriceNoTax and PriceWithTax in EUR/kWh. Cached for 60 seconds."
))
async def get_current_price() -> dict:
    cached = cache.get("justnow", max_age_s=60)
    if cached is not None:
        return cached

    data = await fetch_json("/JustNow")
    result = {
        "rank": data["Rank"],
        "datetime": data["DateTime"],
        "price_no_tax_eur_kwh": data["PriceNoTax"],
        "price_with_tax_eur_kwh": data["PriceWithTax"],
        "cached": False,
    }
    cache.set("justnow", result)
    result = dict(result)  # copy so cached version stays clean
    return result


@mcp.tool(description=(
    "Get all electricity spot prices for today as a list of 96 quarter-hour slots. "
    "Each entry has rank, datetime, price_no_tax_eur_kwh, price_with_tax_eur_kwh. "
    "Useful for planning: finding cheap/expensive windows, computing averages, "
    "identifying best hours for pre-heating etc. "
    "Cache refreshes at each quarter-hour boundary."
))
async def get_today_prices() -> dict:
    ttl = seconds_until_next_quarter()
    cached = cache.get("today", max_age_s=ttl)
    if cached is not None:
        return {"slots": cached, "cached": True, "count": len(cached)}

    raw = await fetch_json("/Today")
    slots = [
        {
            "rank": entry["Rank"],
            "datetime": entry["DateTime"],
            "price_no_tax_eur_kwh": entry["PriceNoTax"],
            "price_with_tax_eur_kwh": entry["PriceWithTax"],
        }
        for entry in raw
    ]
    cache.set("today", slots)
    return {"slots": slots, "cached": False, "count": len(slots)}


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

    all_data = await get_today_prices()
    slots = all_data["slots"]

    filtered = []
    for s in slots:
        dt = datetime.fromisoformat(s["datetime"])
        if hour_from <= dt.hour <= hour_to:
            filtered.append(s)

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
    all_data = await get_today_prices()
    slots = all_data["slots"]

    now_iso = datetime.now().astimezone().isoformat()
    # Filter to slots in the future (or current quarter)
    remaining = [
        s for s in slots
        if s["datetime"] >= now_iso[:16]  # compare YYYY-MM-DDTHH:MM prefix
    ]

    sorted_slots = sorted(remaining, key=lambda s: s["price_with_tax_eur_kwh"])
    top_n = sorted_slots[:n]

    return {
        "requested_n": n,
        "available_remaining_slots": len(remaining),
        "cheapest_slots": top_n,
    }


@mcp.tool(description=(
    "Summarise today's price distribution: cheapest hour, most expensive hour, "
    "average price, and how the current price compares to today's range. "
    "Good for a quick sanity check or agent reasoning context."
))
async def get_today_summary() -> dict:
    current = await get_current_price()
    all_data = await get_today_prices()
    slots = all_data["slots"]

    prices = [s["price_with_tax_eur_kwh"] for s in slots]
    cheapest = min(slots, key=lambda s: s["price_with_tax_eur_kwh"])
    most_expensive = max(slots, key=lambda s: s["price_with_tax_eur_kwh"])
    avg = sum(prices) / len(prices)

    current_price = current["price_with_tax_eur_kwh"]
    price_range = most_expensive["price_with_tax_eur_kwh"] - cheapest["price_with_tax_eur_kwh"]
    pct_in_range = (
        round((current_price - cheapest["price_with_tax_eur_kwh"]) / price_range * 100, 1)
        if price_range > 0 else 0
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
        },
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Starting spot-hinta MCP server on port {PORT}")
    print(f"MCP endpoint: http://localhost:{PORT}/mcp")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=PORT, path="/mcp")
