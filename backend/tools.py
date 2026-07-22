"""
Live data tools for Zeal Co-Pilot.
Each function returns a plain string summary that gets fed back to the LLM.
All are wrapped in try/except so one failing API never crashes the chat.
"""

import os
from datetime import datetime, timezone, timedelta
import requests

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
NEWSDATA_KEY = os.environ.get("NEWSDATA_KEY", "")
SERPER_KEY = os.environ.get("SERPER_KEY", "")  # optional, better web search


def get_current_datetime() -> str:
    """Get today's date and current time. Use this for 'what is today', 'what's the date',
    'what time is it' with no specific city mentioned. Defaults to India (IST)."""
    try:
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist)
        return f"Current date and time (IST, India): {now.strftime('%A, %d %B %Y, %I:%M %p')}"
    except Exception as e:
        return f"Date/time lookup failed: {str(e)}"


def get_weather(location: str = "Delhi") -> str:
    """Get current weather for a location name (e.g. 'Delhi', 'New York')."""
    try:
        # Step 1: geocode the location name to lat/lon (free, no key)
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
            timeout=10,
        ).json()
        if not geo.get("results"):
            return f"Could not find location '{location}'."
        place = geo["results"][0]
        lat, lon = place["latitude"], place["longitude"]
        display_name = f"{place['name']}, {place.get('country', '')}"

        # Step 2: get weather for those coordinates
        w = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            },
            timeout=10,
        ).json()
        c = w.get("current", {})
        return (
            f"Weather in {display_name}: {c.get('temperature_2m')}°C, "
            f"humidity {c.get('relative_humidity_2m')}%, "
            f"wind {c.get('wind_speed_10m')} km/h."
        )
    except Exception as e:
        return f"Weather lookup failed: {str(e)}"


def get_time(location: str = "Asia/Kolkata") -> str:
    """Get current local time for a city/timezone (e.g. 'Asia/Kolkata', 'Tokyo')."""
    try:
        # Try treating input as a direct IANA timezone first
        tz = location if "/" in location else None
        if not tz:
            # crude city -> common timezone fallback list
            common = {
                "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "india": "Asia/Kolkata",
                "new york": "America/New_York", "london": "Europe/London",
                "tokyo": "Asia/Tokyo", "dubai": "Asia/Dubai",
                "singapore": "Asia/Singapore", "sydney": "Australia/Sydney",
            }
            tz = common.get(location.lower(), "Asia/Kolkata")

        r = requests.get(f"https://worldtimeapi.org/api/timezone/{tz}", timeout=10).json()
        if "datetime" not in r:
            # Fallback if WorldTimeAPI is down — at least give IST
            return get_current_datetime()
        return f"Current time in {tz}: {r.get('datetime', 'unavailable')}"
    except Exception:
        # Never fail outright — fall back to a safe default
        return get_current_datetime()


def get_stock_price(symbol: str) -> str:
    """Get latest stock price for a ticker symbol (e.g. 'AAPL', 'TCS.BSE')."""
    if not ALPHA_VANTAGE_KEY:
        return "Stock lookup is not configured (missing ALPHA_VANTAGE_KEY)."
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": ALPHA_VANTAGE_KEY,
            },
            timeout=10,
        ).json()
        quote = r.get("Global Quote", {})
        if not quote:
            return f"No data found for symbol '{symbol}'. Check the ticker format."
        return (
            f"{symbol}: price {quote.get('05. price')}, "
            f"change {quote.get('09. change')} ({quote.get('10. change percent')})"
        )
    except Exception as e:
        return f"Stock lookup failed: {str(e)}"


def get_currency_conversion(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert an amount between currencies (e.g. 100 USD to INR)."""
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest",
            params={"amount": amount, "from": from_currency.upper(), "to": to_currency.upper()},
            timeout=10,
        ).json()
        rate = r.get("rates", {}).get(to_currency.upper())
        if rate is None:
            return f"Could not convert {from_currency} to {to_currency}."
        return f"{amount} {from_currency.upper()} = {rate} {to_currency.upper()}"
    except Exception as e:
        return f"Currency conversion failed: {str(e)}"


def get_news(query: str = "", energy_only: bool = False) -> str:
    """Get recent news headlines, optionally filtered to energy/hydrogen sector."""
    if not NEWSDATA_KEY:
        return "News lookup is not configured (missing NEWSDATA_KEY)."
    try:
        search_query = query
        if energy_only:
            search_query = f"{query} energy hydrogen renewable".strip()
        r = requests.get(
            "https://newsdata.io/api/1/news",
            params={
                "apikey": NEWSDATA_KEY,
                "q": search_query or "top news",
                "language": "en",
            },
            timeout=10,
        ).json()
        articles = r.get("results", [])[:5]
        if not articles:
            return "No news articles found."
        lines = [f"- {a.get('title')} ({a.get('source_id')})" for a in articles]
        return "Recent headlines:\n" + "\n".join(lines)
    except Exception as e:
        return f"News lookup failed: {str(e)}"


def get_earthquake_alerts(min_magnitude: float = 4.5) -> str:
    """Get recent significant earthquakes worldwide (last 24 hours)."""
    try:
        r = requests.get(
            "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson",
            timeout=10,
        ).json()
        features = r.get("features", [])
        relevant = [f for f in features if f["properties"]["mag"] >= min_magnitude][:5]
        if not relevant:
            return "No significant earthquakes reported in the last 24 hours."
        lines = [
            f"- M{f['properties']['mag']} — {f['properties']['place']}"
            for f in relevant
        ]
        return "Recent significant earthquakes (last 24h):\n" + "\n".join(lines)
    except Exception as e:
        return f"Earthquake data lookup failed: {str(e)}"


def web_search(query: str) -> str:
    """General web search for facts, definitions, current events, or anything else."""
    if SERPER_KEY:
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_KEY},
                json={"q": query},
                timeout=10,
            ).json()
            results = r.get("organic", [])[:4]
            if not results:
                return "No web results found."
            lines = [f"- {res.get('title')}: {res.get('snippet')}" for res in results]
            return "Web search results:\n" + "\n".join(lines)
        except Exception as e:
            return f"Web search failed: {str(e)}"
    else:
        try:
            r = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
                timeout=10,
            ).json()
            abstract = r.get("AbstractText")
            if abstract:
                return f"{abstract} (source: {r.get('AbstractSource')})"
            related = r.get("RelatedTopics", [])[:3]
            if related:
                lines = [t.get("Text", "") for t in related if "Text" in t]
                return "Related info:\n" + "\n".join(lines)
            return "No quick answer found for that query."
        except Exception as e:
            return f"Web search failed: {str(e)}"


# ---- Tool schema definitions for the LLM (Groq function calling) ----

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": "Get today's date and current time in India (IST). Use this whenever the user asks 'what is today', 'what's the date', 'what time is it' without naming a specific city.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name"}},
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get current local time for a city or timezone.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name or IANA timezone"}},
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "Get latest stock/ticker price.",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "Stock ticker symbol"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_currency_conversion",
            "description": "Convert an amount from one currency to another.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "from_currency": {"type": "string", "description": "3-letter currency code, e.g. USD"},
                    "to_currency": {"type": "string", "description": "3-letter currency code, e.g. INR"},
                },
                "required": ["amount", "from_currency", "to_currency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "Get recent news headlines, optionally filtered to energy/hydrogen sector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic to search news for"},
                    "energy_only": {"type": "boolean", "description": "Filter to energy/hydrogen sector news"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_earthquake_alerts",
            "description": "Get recent significant earthquakes worldwide in the last 24 hours.",
            "parameters": {
                "type": "object",
                "properties": {
                    "min_magnitude": {"type": "number", "description": "Minimum magnitude to include, default 4.5"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "General web search for facts, definitions, meanings of terms, current events, or anything you'd normally look up on Google.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_current_datetime": lambda args: get_current_datetime(),
    "get_weather": lambda args: get_weather(args.get("location", "Delhi")),
    "get_time": lambda args: get_time(args.get("location", "Asia/Kolkata")),
    "get_stock_price": lambda args: get_stock_price(args.get("symbol", "")),
    "get_currency_conversion": lambda args: get_currency_conversion(
        args.get("amount", 1), args.get("from_currency", "USD"), args.get("to_currency", "INR")
    ),
    "get_news": lambda args: get_news(args.get("query", ""), args.get("energy_only", False)),
    "get_earthquake_alerts": lambda args: get_earthquake_alerts(args.get("min_magnitude", 4.5)),
    "web_search": lambda args: web_search(args.get("query", "")),
}
