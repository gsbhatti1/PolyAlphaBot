"""
Live order router for Polymarket CLOB.

Credentials are read from environment variables — never from a secrets file.
Required env vars (set in systemd unit or .env):
    POLY_PRIVATE_KEY      — wallet private key (hex, with or without 0x)
    POLY_API_KEY          — CLOB L2 API key
    POLY_API_SECRET       — CLOB L2 API secret
    POLY_API_PASSPHRASE   — CLOB L2 API passphrase
    POLY_CHAIN_ID         — 137 for Polygon mainnet (default), 80002 for Amoy testnet

py_clob_client docs: https://github.com/Polymarket/py-clob-client
"""
import logging
import os
from typing import Any, Dict, Optional

import config
from config import LIVE_CAPITAL_FRACTION, MAX_PAPER_TRADE_USD

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.constants import POLYGON
except ImportError:
    ClobClient = None
    OrderArgs = None
    OrderType = None
    POLYGON = 137

log = logging.getLogger("polymarket-bot")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _clob_host() -> str:
    return getattr(config, "CLOB_API", "https://clob.polymarket.com")


def _chain_id() -> int:
    try:
        return int(_env("POLY_CHAIN_ID", str(POLYGON)))
    except ValueError:
        return POLYGON


class LiveTrader:
    """
    Routes real orders to the Polymarket CLOB.
    Raises RuntimeError if py_clob_client is missing or POLY_PRIVATE_KEY is unset.
    """

    def __init__(self):
        if ClobClient is None:
            raise RuntimeError(
                "py_clob_client is not installed. "
                "Run: pip install py-clob-client"
            )

        private_key = _env("POLY_PRIVATE_KEY")
        if not private_key:
            raise RuntimeError(
                "POLY_PRIVATE_KEY env var is not set. "
                "Cannot initialise LiveTrader without a wallet key."
            )

        chain = _chain_id()
        host  = _clob_host()

        api_key        = _env("POLY_API_KEY")        or None
        api_secret     = _env("POLY_API_SECRET")     or None
        api_passphrase = _env("POLY_API_PASSPHRASE") or None

        if api_key and api_secret and api_passphrase:
            # L2 auth (preferred — no on-chain signing per order)
            self.client = ClobClient(
                host,
                chain_id=chain,
                key=private_key,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
        else:
            log.warning(
                "POLY_API_KEY/SECRET/PASSPHRASE not set — using L1 auth only. "
                "Run client.derive_api_key() once to get L2 credentials."
            )
            self.client = ClobClient(host, chain_id=chain, key=private_key)

        log.info("LiveTrader initialised (host=%s chain=%d L2=%s)",
                 host, chain, bool(api_key))

    def open_position(
        self,
        market_id: str,
        token_id: str,
        side: str,
        size_usd: float,
        price: float,
        time_in_force: str = "GTC",
    ) -> Optional[Dict[str, Any]]:
        """
        Place a real order on the Polymarket CLOB.
        Returns {order_id, status, filled_size, avg_fill_price} or None on failure.
        """
        if size_usd <= 0:
            log.warning("Refusing live order: non-positive size_usd=%.2f", size_usd)
            return None

        # Apply LIVE_CAPITAL_FRACTION scaling
        scaled = round(float(size_usd) * float(LIVE_CAPITAL_FRACTION), 2)
        if scaled <= 0:
            log.warning("Post-fraction size is zero (size=%.2f fraction=%.4f)", size_usd, LIVE_CAPITAL_FRACTION)
            return None

        # Hard cap
        if scaled > float(MAX_PAPER_TRADE_USD):
            log.warning("Clamping live order %.2f → %.2f (MAX_PAPER_TRADE_USD)", scaled, MAX_PAPER_TRADE_USD)
            scaled = float(MAX_PAPER_TRADE_USD)

        side = side.upper()
        if side not in ("BUY", "SELL"):
            log.error("Invalid side for live order: %s", side)
            return None

        # Polymarket CLOB orders are in token shares, not USD
        # shares = usd_size / price  (price is 0–1 cents-per-share)
        if not (0 < price < 1):
            log.error("Invalid price %.6f (must be 0 < p < 1)", price)
            return None
        shares = round(scaled / price, 4)
        if shares <= 0:
            log.error("Computed shares <= 0: size=%.2f price=%.6f", scaled, price)
            return None

        order_type = OrderType.GTC if time_in_force == "GTC" else OrderType.FOK

        try:
            order_args = OrderArgs(
                token_id=str(token_id),
                price=round(float(price), 4),
                size=shares,
                side=side,
            )
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, order_type)
        except Exception as e:
            log.exception("Error placing live order on Polymarket CLOB: %s", e)
            return None

        if resp is None:
            log.error("CLOB returned None (market=%s token=%s)", market_id, token_id)
            return None

        order_id  = resp.get("orderID") or resp.get("id") or resp.get("orderId")
        status    = resp.get("status", "unknown")
        error_msg = resp.get("errorMsg") or resp.get("error")

        if error_msg:
            log.error("CLOB rejected order: %s (market=%s token=%s)", error_msg, market_id, token_id)
            return None

        if not order_id:
            log.error("CLOB response missing orderID: %s", resp)
            return None

        result = {
            "order_id":       order_id,
            "status":         status,
            "filled_size":    float(resp.get("filledSize", 0) or 0),
            "avg_fill_price": float(resp.get("avgPrice", price) or price),
        }
        log.info(
            "LIVE ORDER placed OK  market=%s token=%s side=%s "
            "usd=%.2f shares=%.4f price=%.4f id=%s status=%s",
            market_id, token_id, side, scaled, shares, price, order_id, status,
        )
        return result
