"""
Configuration for Polymarket Hidden Alpha Scanner + Monitor (Aggressive Preset).
Start aggressive, then tone down after confirming execution.
"""
import os

# ── API Endpoints ──────────────────────────────────────────────────────────
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ── Mode Toggles ───────────────────────────────────────────────────────────
AGGRESSIVE_MODE = True

# DEBUG: guarantees you see paper positions open while tuning
FORCE_PAPER_EXECUTION = True
FORCE_MIN_TRADE_USD = 1
FORCE_MAX_PER_HOUR = 10

# ── Scanner Defaults (who to follow) ───────────────────────────────────────
DEFAULT_SCAN_LIMIT = 1000         # pull more wallets
MIN_PNL = 0                       # include all
MIN_TRADES = 1                    # include new wallets
MIN_WIN_RATE = 0.49               # very permissive
MAX_AVG_BET = 500_000             # allow whales for now
MIN_ACTIVE_DAYS = 1               # allow recent
TOP_VOLUME_EXCLUDE = 0            # include visible wallets for action

# ── Monitor Defaults (how often + what to alert/act on) ────────────────────
POLL_INTERVAL = 5                 # seconds between polls (aggressive)
MAX_WATCH_WALLETS = 150           # monitor more wallets
MIN_TRADE_SIZE = 5                # minimum detected trade size (USD)

# ── Paper Trader Defaults (sizing) ─────────────────────────────────────────
STARTING_BANKROLL = 10_000
DEFAULT_KELLY_FRACTION = 1.0      # full Kelly (paper only)
MAX_POSITION_PCT = 0.35           # up to 35% of bankroll per trade (paper only)

# Safety rails so “aggressive” doesn’t go insane
MAX_TRADES_PER_HOUR = 60
MAX_POSITIONS_OPEN = 30
COOLDOWN_SECONDS_PER_MARKET = 20

# ── Alerts ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── Database ───────────────────────────────────────────────────────────────
# Absolute/env-driven is safer under systemd
DB_PATH = os.getenv("DB_PATH", "/home/polymarket/poly_alpha_bot/poly_alpha.db")

# ── Rate Limiting ──────────────────────────────────────────────────────────
REQUEST_DELAY = 0.1               # seconds between API calls
MAX_RETRIES = 5
