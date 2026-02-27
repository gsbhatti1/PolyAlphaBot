"""
Alert dispatch — Telegram, Discord, and console.
"""
import httpx
import logging
import os
import httpx
import config

logger = logging.getLogger(__name__)


async def send_telegram(message: str, client: httpx.AsyncClient | None = None):
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured")
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    managed_client = client is None
    c = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await c.post(url, json=payload)
        response.raise_for_status()
        logger.info("Telegram alert sent successfully")
    except httpx.HTTPStatusError as e:
        logger.error(f"Telegram HTTP error {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Telegram request error: {e}")
    except Exception as e:
        logger.error(f"Unexpected Telegram error: {e}")
    finally:
        if managed_client:
            await c.aclose()


async def send_discord(message: str, client: httpx.AsyncClient | None = None):
    if not config.DISCORD_WEBHOOK_URL:
        logger.debug("Discord not configured")
        return
    managed_client = client is None
    c = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await c.post(
            config.DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10,
        )
        response.raise_for_status()
        logger.info("Discord alert sent successfully")
    except httpx.HTTPStatusError as e:
        logger.error(f"Discord HTTP error {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"Discord request error: {e}")
    except Exception as e:
        logger.error(f"Unexpected Discord error: {e}")
    finally:
        if managed_client:
            await c.aclose()


async def send_alert(message: str, client: httpx.AsyncClient | None = None):
    """Send to all configured channels."""
    await send_telegram(message, client)
    await send_discord(message, client)


def format_new_trade_alert(wallet: dict, trade: dict, paper_action: dict | None = None) -> str:
    """Format a trade detection into an alert message."""
    addr = wallet.get("address", "?")[:10]
    name = wallet.get("username", addr)
    score = wallet.get("alpha_score", 0)

    market = trade.get("market_question", trade.get("market", "?"))
    outcome = trade.get("outcome", "?")
    side = trade.get("side", "?")
    size = trade.get("size_usd", trade.get("size", 0))
    price = trade.get("price", 0)

    lines = [
        f"🔔 *New Trade Detected*",
        f"Wallet: `{name}` (α {score:.2f})",
        f"Market: {market[:80]}",
        f"Side: {side} {outcome} @ {float(price):.2f}",
        f"Size: ${float(size):,.0f}",
    ]

    if paper_action:
        lines.append("")
        lines.append(f"📝 *Paper Trade*")
        lines.append(f"Size: ${paper_action['size_usd']:,.0f} "
                      f"(Kelly: {paper_action['kelly_fraction']:.1%})")
        lines.append(f"Bankroll: ${paper_action['bankroll']:,.0f}")

    return "\n".join(lines)
def send_telegram_sync(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
    }

    with httpx.Client(timeout=15.0) as client:
        r = client.post(url, data=data)
        if r.status_code != 200:
            raise RuntimeError(
                f"Telegram HTTP {r.status_code}: {r.text[:300]}"
            )
