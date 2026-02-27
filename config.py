"""
Configuration for Polymarket Hidden Alpha Scanner.
"""
import os

# ── API Endpoints ──────────────────────────────────────────────────────────
# Gamma API — market metadata, profiles, search
GAMMA_API = "https://gamma-api.polymarket.com"
# Data API — positions, activity, trades, leaderboard
DATA_API = "https://data-api.polymarket.com"
# CLOB API — order book (not used in scanner)
CLOB_API = "https://clob.polymarket.com"

# ── Scanner Defaults ───────────────────────────────────────────────────────
DEFAULT_SCAN_LIMIT = 200          # wallets to pull from leaderboard
MIN_PNL = 500                     # minimum PnL in USD
MIN_TRADES = 15                   # minimum number of resolved markets
MIN_WIN_RATE = 0.52               # better than coin flip
MAX_AVG_BET = 50_000              # filter out whale wallets
MIN_ACTIVE_DAYS = 14              # minimum days between first and last trade
TOP_VOLUME_EXCLUDE = 500          # exclude top N by volume (too visible)

# ── Alpha Score Weights ────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "pnl_magnitude":     0.20,
    "win_rate":          0.20,
    "profit_factor":     0.15,
    "sharpe_ratio":      0.15,
    "consistency":       0.10,
    "recency":           0.10,
    "inverse_visibility": 0.10,
}

# ── Monitor Defaults ───────────────────────────────────────────────────────
POLL_INTERVAL = 30                # seconds between polls
MAX_WATCH_WALLETS = 20            # max wallets to monitor
MIN_TRADE_SIZE = 50               # minimum trade size to alert on (USD)

# ── Paper Trader Defaults ──────────────────────────────────────────────────
STARTING_BANKROLL = 10_000        # paper trading bankroll
DEFAULT_KELLY_FRACTION = 0.25     # quarter-Kelly for safety
MAX_POSITION_PCT = 0.10           # max 10% of bankroll per trade

# ── Alerts ─────────────────────────────────────────────────────────────────
# ── Alerts ─────────────────────────────────────────────────────────────────
# Alerts

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── Database ───────────────────────────────────────────────────────────────
DB_PATH = "poly_alpha.db"

# ── Rate Limiting ──────────────────────────────────────────────────────────
REQUEST_DELAY = 0.5               # seconds between API calls
MAX_RETRIES = 3
