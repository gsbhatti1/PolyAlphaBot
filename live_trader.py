import logging
from typing import Optional, Dict, Any

from config import MAX_PAPER_TRADE_USD, LIVE_CAPITAL_FRACTION
import config_secrets  # local-only, not tracked

# Example using py_clob_client (Polymarket official client)
try:
    from py_clob_client.client import ClobClient
except ImportError:
    ClobClient = None

log = logging.getLogger(__name__)


class LiveTrader:
    def __init__(self):
        if ClobClient is None:
            raise RuntimeError("py_clob_client not installed; cannot trade live")

        # You can configure base_url if you want testnet vs mainnet
        self.client = ClobClient(
            api_key=getattr(config_secrets, "POLY_API_KEY", None),
            api_secret=getattr(config_secrets, "POLY_API_SECRET", None),
            api_passphrase=getattr(config_secrets, "POLY_API_PASSPHRASE", None),
        )

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
        Place a real order on Polymarket CLOB.

        Returns a dict with order_id, status, filled_size, avg_fill_price, or None on failure.
        """

        # Hard safety caps
        if size_usd <= 0:
            log.warning("Refusing live order: non-positive size_usd=%.2f", size_usd)
            return None
        if size_usd > MAX_PAPER_TRADE_USD:
            log.warning(
                "Clamping live order size from %.2f to MAX_PAPER_TRADE_USD=%.2f",
                size_usd,
                MAX_PAPER_TRADE_USD,
            )
            size_usd = float(MAX_PAPER_TRADE_USD)

        side = side.upper()
        if side not in ("BUY", "SELL"):
            log.error("Invalid side for live order: %s", side)
            return None

        try:
            # py_clob_client commonly uses quote/size in token units; here we assume USD size
            # and rely on the client conversion or you can compute size from price & shares.
            order = self.client.place_order(
                market=market_id,
                outcome=token_id,
                side=side.lower(),  # client often expects "buy"/"sell"
                price=price,
                size=size_usd,
                time_in_force=time_in_force,
            )
        except Exception as e:
            log.exception("Error placing live order on Polymarket: %s", e)
            return None

        # Normalize result
        result = {
            "order_id": order.get("id") or order.get("orderId"),
            "status": order.get("status"),
            "filled_size": order.get("filledSize", 0),
            "avg_fill_price": order.get("avgPrice", price),
        }
        log.info(
            "LIVE ORDER placed: market=%s token=%s side=%s size=%.2f price=%.4f id=%s status=%s",
            market_id,
            token_id,
            side,
            size_usd,
            price,
            result["order_id"],
            result["status"],
        )
        return result
