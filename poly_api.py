"""
Polymarket API client.

Matches the official Polymarket API docs (2025):
  - Data API:  https://data-api.polymarket.com
    - GET /v1/leaderboard     (timePeriod, orderBy, limit<=50, offset)
    - GET /activity           (user, limit, offset)
    - GET /positions          (user, sizeThreshold)
    - GET /trades             (user, limit, offset)
  - Gamma API: https://gamma-api.polymarket.com
    - GET /markets            (slug, active, closed, limit)
    - GET /profiles/{address}
    - GET /search             (query)
"""
import asyncio
import time
from typing import Any

import httpx

import config

# CLOB base URL (public read endpoints: orderbook/prices/spreads)
CLOB_API = getattr(config, "CLOB_API", None) or getattr(config, "CLOB_API_URL", None) or getattr(config, "CLOB_BASE_URL", None) or "https://clob.polymarket.com"

_last_request = 0.0


async def _throttle():
    global _last_request
    elapsed = time.monotonic() - _last_request
    if elapsed < config.REQUEST_DELAY:
        await asyncio.sleep(config.REQUEST_DELAY - elapsed)
    _last_request = time.monotonic()


async def _get(client: httpx.AsyncClient, url: str,
               params: dict | None = None) -> Any:
    """GET with throttle + retry."""
    for attempt in range(config.MAX_RETRIES):
        await _throttle()
        try:
            r = await client.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                await asyncio.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPStatusError, httpx.ReadTimeout) as e:
            if attempt == config.MAX_RETRIES - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    return None


# ── Leaderboard ────────────────────────────────────────────────────────────
# Endpoint: GET https://data-api.polymarket.com/v1/leaderboard
# Params:   timePeriod (DAY|WEEK|MONTH|ALL), orderBy (PNL|VOL),
#           limit (1-50), offset (0-1000), category (OVERALL|...)
# Response: [ { rank, proxyWallet, userName, vol, pnl, profileImage,
#               xUsername, verifiedBadge } ]

async def fetch_leaderboard(client: httpx.AsyncClient,
                            limit: int = 50,
                            offset: int = 0,
                            time_period: str = "ALL",
                            order_by: str = "PNL") -> list[dict]:
    """Fetch profit leaderboard. Max 50 per page."""
    data = await _get(
        client,
        f"{config.DATA_API}/v1/leaderboard",
        params={
            "timePeriod": time_period,
            "orderBy": order_by,
            "limit": min(limit, 50),
            "offset": offset,
        },
    )
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return data.get("results", data.get("leaderboard", []))


async def fetch_leaderboard_paginated(client: httpx.AsyncClient,
                                       total: int = 200,
                                       time_period: str = "ALL",
                                       order_by: str = "PNL") -> list[dict]:
    """Page through leaderboard (50 per page) to get `total` entries."""
    results = []
    page_size = 50  # API max is 50
    for offset in range(0, total, page_size):
        batch = await fetch_leaderboard(
            client, limit=page_size, offset=offset,
            time_period=time_period, order_by=order_by,
        )
        if not batch:
            break
        results.extend(batch)
        if len(batch) < page_size:
            break
    return results[:total]


# ── Volume Leaderboard (for exclusion list) ────────────────────────────────

async def fetch_top_volume_addresses(client: httpx.AsyncClient,
                                      top_n: int = 500) -> set[str]:
    """Get addresses of top-N wallets by volume (to exclude)."""
    results = await fetch_leaderboard_paginated(
        client, total=top_n, order_by="VOL",
    )
    return {
        w.get("proxyWallet", "").lower()
        for w in results if w.get("proxyWallet")
    }


# ── Profile (Gamma API) ───────────────────────────────────────────────────
# Endpoint: GET https://gamma-api.polymarket.com/profiles/{address}

async def fetch_profile(client: httpx.AsyncClient,
                        address: str) -> dict | None:
    """Fetch user profile from Gamma API."""
    try:
        return await _get(client, f"{config.GAMMA_API}/profiles/{address}")
    except Exception:
        return None


# ── Trading Activity (Data API) ────────────────────────────────────────────
# Endpoint: GET https://data-api.polymarket.com/activity
# Params:   user (address, required), limit, offset, type, conditionId, side
# Response: [ { proxyWallet, timestamp, conditionId, type, size, usdcSize,
#               transactionHash, price, asset, side, outcomeIndex,
#               title, slug, icon, eventSlug, outcome,
#               name, pseudonym, bio, profileImage } ]

async def fetch_activity(client: httpx.AsyncClient,
                         address: str,
                         limit: int = 100,
                         offset: int = 0) -> list[dict]:
    """Fetch trade activity for a wallet."""
    try:
        data = await _get(
            client,
            f"{config.DATA_API}/activity",
            params={"user": address, "limit": limit, "offset": offset},
        )
    except Exception:
        data = None

    if data is None:
        return []
    if isinstance(data, list):
        return data
    return data.get("history", data.get("activity", data.get("results", [])))


async def fetch_all_activity(client: httpx.AsyncClient,
                              address: str,
                              max_pages: int = 5) -> list[dict]:
    """Page through all activity for a wallet."""
    all_trades = []
    page_size = 100
    for page in range(max_pages):
        batch = await fetch_activity(
            client, address, limit=page_size, offset=page * page_size
        )
        if not batch:
            break
        all_trades.extend(batch)
        if len(batch) < page_size:
            break
    return all_trades


# ── Trades (Data API) ─────────────────────────────────────────────────────
# Endpoint: GET https://data-api.polymarket.com/trades
# Params:   user, market (conditionId), limit, offset

async def fetch_trades(client: httpx.AsyncClient,
                       address: str,
                       limit: int = 100,
                       offset: int = 0) -> list[dict]:
    """Fetch trades for a wallet."""
    try:
        data = await _get(
            client,
            f"{config.DATA_API}/trades",
            params={"user": address, "limit": limit, "offset": offset},
        )
    except Exception:
        data = None

    if data is None:
        return []
    if isinstance(data, list):
        return data
    return data.get("trades", data.get("results", []))


# ── Positions (Data API) ──────────────────────────────────────────────────
# Endpoint: GET https://data-api.polymarket.com/positions
# Params:   user (required), market (conditionId), sizeThreshold

async def fetch_positions(client: httpx.AsyncClient,
                           address: str) -> list[dict]:
    """Fetch current open positions for a wallet."""
    try:
        data = await _get(
            client,
            f"{config.DATA_API}/positions",
            params={"user": address},
        )
        if data is None:
            return []
        if isinstance(data, list):
            return data
        return data.get("positions", data.get("results", []))
    except Exception:
        return []


# ── Market Info (Gamma API) ────────────────────────────────────────────────
# Endpoint: GET https://gamma-api.polymarket.com/markets
# Endpoint: GET https://gamma-api.polymarket.com/markets/{conditionId}

async def fetch_market(client: httpx.AsyncClient,
                       condition_id: str) -> dict | None:
    """Fetch single market details by condition ID."""
    try:
        return await _get(
            client, f"{config.GAMMA_API}/markets/{condition_id}"
        )
    except Exception:
        return None


async def fetch_market_by_slug(client: httpx.AsyncClient,
                                slug: str) -> dict | None:
    """Fetch market by its URL slug."""
    try:
        data = await _get(
            client, f"{config.GAMMA_API}/markets",
            params={"slug": slug, "limit": 1},
        )
        if isinstance(data, list) and data:
            return data[0]
        return data
    except Exception:
        return None


async def fetch_active_markets(client: httpx.AsyncClient,
                                limit: int = 50) -> list[dict]:
    """Fetch currently active / open markets."""
    try:
        data = await _get(
            client, f"{config.GAMMA_API}/markets",
            params={"active": True, "closed": False, "limit": limit,
                    "order": "volume", "ascending": False},
        )
        if isinstance(data, list):
            return data
        return data.get("markets", data.get("results", []))
    except Exception:
        return []


def _gamma_market_by_slug(http, slug: str):
    """Fetch market metadata from Gamma by slug. Returns dict or None."""
    try:
        # Gamma often supports filtering by slug; if not, it will return empty and we fall back.
        r = http.get(f"{config.GAMMA_API}/markets", params={"slug": slug, "limit": 1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                mkts = data.get("markets") or data.get("results") or data.get("data") or []
                if mkts:
                    return mkts[0]
        # fallback: try search endpoint style
        r = http.get(f"{config.GAMMA_API}/markets", params={"active": True, "closed": False, "limit": 200}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            mkts = data if isinstance(data, list) else (data.get("markets") or data.get("results") or [])
            for m in mkts:
                if str(m.get("slug") or "") == slug:
                    return m
    except Exception:
        return None
    return None


def _extract_token_id(market: dict, outcome: str | None):
    """Extract token_id for an outcome from Gamma market structure.
    Handles Gamma returning JSON strings for outcomes/clobTokenIds.
    """
    import json

    if not isinstance(market, dict):
        return None

    outcome_norm = (outcome or "").strip().lower()

    # 1) Preferred: clobTokenIds (can be dict/list or JSON string)
    ct = market.get("clobTokenIds") or market.get("clob_token_ids")

    # If JSON-encoded string, decode it
    if isinstance(ct, str):
        try:
            ct = json.loads(ct)
        except Exception:
            ct = None

    # outcomes may be JSON string too
    outs = market.get("outcomes")
    if isinstance(outs, str):
        try:
            outs = json.loads(outs)
        except Exception:
            outs = None

    # Case A: ct is dict like {"Yes": "...", "No": "..."}
    if isinstance(ct, dict):
        if outcome_norm:
            for k, v in ct.items():
                if str(k).strip().lower() == outcome_norm:
                    return str(v)
        # fallback: first value
        try:
            return str(next(iter(ct.values())))
        except Exception:
            return None

    # Case B: ct is list [yes_token, no_token] (common)
    if isinstance(ct, list) and ct:
        # If outcomes list exists, align by index
        if isinstance(outs, list) and outs and outcome_norm:
            for i, name in enumerate(outs):
                if i < len(ct) and str(name).strip().lower() == outcome_norm:
                    return str(ct[i])
        # canonical Yes/No mapping if we only have two
        if len(ct) >= 2 and outcome_norm in ("yes", "no"):
            return str(ct[0] if outcome_norm == "yes" else ct[1])
        # fallback: first
        return str(ct[0])

    # 2) Other possible structures (kept as fallback)
    candidates = []

    outs2 = market.get("outcomes")
    if isinstance(outs2, list):
        for o in outs2:
            if isinstance(o, dict):
                name = str(o.get("name") or o.get("outcome") or "").strip()
                tid = o.get("token_id") or o.get("tokenId") or o.get("clobTokenId") or o.get("id")
                if tid is not None:
                    candidates.append((name, str(tid)))

    toks = market.get("tokens")
    if isinstance(toks, list):
        for t in toks:
            if isinstance(t, dict):
                name = str(t.get("outcome") or t.get("name") or t.get("label") or "").strip()
                tid = t.get("token_id") or t.get("tokenId") or t.get("clobTokenId") or t.get("id")
                if tid is not None:
                    candidates.append((name, str(tid)))

    if not candidates:
        return None

    if not outcome_norm:
        return candidates[0][1]

    for name, tid in candidates:
        if name.strip().lower() == outcome_norm:
            return tid

    if outcome_norm in ("yes", "no"):
        for name, tid in candidates:
            if name.strip().lower() == outcome_norm:
                return tid

    for name, tid in candidates:
        if outcome_norm and outcome_norm in name.strip().lower():
            return tid

    return None

    outcome_norm = (outcome or "").strip().lower()

    # Common Gamma structures: market["outcomes"] or market["tokens"]
    candidates = []

    outs = market.get("outcomes")
    if isinstance(outs, list):
        # outcomes might be dicts with {name, token_id} or similar
        for o in outs:
            if isinstance(o, dict):
                name = str(o.get("name") or o.get("outcome") or "").strip()
                tid = o.get("token_id") or o.get("tokenId") or o.get("clobTokenId") or o.get("id")
                if tid is not None:
                    candidates.append((name, str(tid)))

    toks = market.get("tokens")
    if isinstance(toks, list):
        for t in toks:
            if isinstance(t, dict):
                name = str(t.get("outcome") or t.get("name") or t.get("label") or "").strip()
                tid = t.get("token_id") or t.get("tokenId") or t.get("clobTokenId") or t.get("id")
                if tid is not None:
                    candidates.append((name, str(tid)))

    # Sometimes token ids live under market["clobTokenIds"] or similar
    ct = market.get("clobTokenIds") or market.get("clob_token_ids")
    if isinstance(ct, dict):
        for name, tid in ct.items():
            candidates.append((str(name), str(tid)))

    if not candidates:
        return None

    if not outcome_norm:
        # If no outcome specified, return the first token id
        return candidates[0][1]

    # Try exact match
    for name, tid in candidates:
        if name.strip().lower() == outcome_norm:
            return tid

    # Handle YES/NO canonicalization
    if outcome_norm in ("yes", "no"):
        for name, tid in candidates:
            if name.strip().lower() in ("yes", "no") and name.strip().lower() == outcome_norm:
                return tid

    # Fuzzy contains
    for name, tid in candidates:
        if outcome_norm and outcome_norm in name.strip().lower():
            return tid

    return None


def _clob_book_by_token(http, token_id: str):
    """Try known CLOB orderbook endpoints for a token id. Returns json or None."""
    paths = [
        (f"{CLOB_API}/book", {"token_id": token_id}),
        (f"{CLOB_API}/orderbook/{token_id}", None),
        (f"{CLOB_API}/markets/{token_id}/book", None),
    ]
    for url, params in paths:
        try:
            r = http.get(url, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None



def get_quote(http, market_slug, outcome: str | None = None):
    """Return best bid/ask quote for a given market slug + outcome (token)."""
    try:
        m = _gamma_market_by_slug(http, market_slug)
        if not m:
            return None

        token_id = _extract_token_id(m, outcome)
        if not token_id:
            return None

        book = _clob_book_by_token(http, token_id)
        if not isinstance(book, dict):
            return None

        bids = book.get("bids") or book.get("buy") or []
        asks = book.get("asks") or book.get("sell") or []

        def _best_price(side_list, is_bid: bool):
            if not side_list or not isinstance(side_list, list):
                return None
            prices = []
            for lvl in side_list:
                try:
                    if isinstance(lvl, dict):
                        p = lvl.get("price") or lvl.get("p") or lvl.get("rate")
                    elif isinstance(lvl, (list, tuple)) and len(lvl) >= 1:
                        p = lvl[0]
                    else:
                        continue
                    if p is None:
                        continue
                    prices.append(float(p))
                except Exception:
                    continue
            if not prices:
                return None
            return max(prices) if is_bid else min(prices)

        bid = _best_price(bids, True)
        ask = _best_price(asks, False)

        if bid is None or ask is None:
            # some endpoints return bestBid/bestAsk
            if bid is None:
                raw = book.get("bestBid") or book.get("bid")
                bid = float(raw) if raw is not None else None
            if ask is None:
                raw = book.get("bestAsk") or book.get("ask")
                ask = float(raw) if raw is not None else None

        if bid is None or ask is None:
            return None
        bid = float(bid); ask = float(ask)
        if bid <= 0 or ask <= 0 or ask < bid:
            return None

        mid = (bid + ask) / 2.0
        spread = ask - bid
        return {"bid": bid, "ask": ask, "mid": mid, "spread": spread, "source": f"clob_token:{token_id}"}
    except Exception:
        return None

