# spot-mcp-server

MCP server for Finnish electricity spot prices, powered by [api.spot-hinta.fi](https://api.spot-hinta.fi).

Exposes real-time and today's quarter-hour spot price data as MCP tools, making electricity prices available to AI agents and LLM-based automations without requiring them to call external APIs directly.

## Background

Finnish electricity prices change every 15 minutes (96 slots per day). Knowing the current price rank and upcoming price windows is essential for smart home automations — pre-heating during cheap hours, deferring loads during expensive ones, and letting an AI agent reason about trade-offs between comfort and cost.

This server fills the gap: a lightweight MCP endpoint that any MCP-compatible AI client (Claude, etc.) can query to get structured, reasoned price data.

## Tools

| Tool | Description |
|------|-------------|
| `get_current_price` | Current quarter-hour: rank, price with/without tax. Cached 60 s. |
| `get_today_prices` | All 96 quarter-hour slots for today. Cache refreshes at each :00/:15/:30/:45 boundary. |
| `get_prices_for_hours(hour_from, hour_to)` | Filtered time window with min/max/avg summary and cheapest/most expensive slot. |
| `get_cheapest_remaining_slots(n)` | Top N cheapest slots remaining today, sorted by price. |
| `get_today_summary` | Current price context: rank, assessment (very cheap → very expensive), position in today's range. |

Prices are in EUR/kWh. **Rank** is a 1–96 percentile within today's prices (1 = cheapest quarter-hour, 96 = most expensive).

## Requirements

- Python 3.10+
- `fastmcp`
- `httpx`

## Installation

```bash
git clone https://github.com/JussiSavola/spot-mcp-server.git
cd spot-mcp-server
pip install -r requirements.txt
```

## Running

```bash
python spot_hinta_mcp.py
```

Default port: **8765**. Override with environment variable:

```bash
SPOT_HINTA_PORT=9000 python spot_hinta_mcp.py
```

MCP endpoint (Streamable HTTP transport):
```
http://localhost:8765/mcp
```

Or via `run.sh`:
```bash
bash run.sh
```

## Connecting to Claude

Add to your Claude MCP configuration:

```json
{
  "mcpServers": {
    "spot-hinta": {
      "type": "url",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

## Data source

[api.spot-hinta.fi](https://api.spot-hinta.fi) — free Finnish electricity spot price API.  
Endpoints used: `/JustNow`, `/Today`.

## License

MIT
