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

# ── Aggressive Mode Toggle ────────────────────────────────────────────────
AGGRESSIVE_MODE = True

# ── Scanner Defaults ───────────────────────────────────────────────────────
DEFAULT_SCAN_LIMIT = 1000         # more wallets
MIN_PNL = 0                       # don't filter by pnl initially
MIN_TRADES = 1                    # include new wallets
MIN_WIN_RATE = 0.49               # basically allow everyone
MAX_AVG_BET = 500_000             # allow whales for now
MIN_ACTIVE_DAYS = 1               # allow recent
TOP_VOLUME_EXCLUDE = 0            # include visible wallets for action

# ── Monitor Defaults ───────────────────────────────────────────────────────
POLL_INTERVAL = 5                 # faster polling
MAX_WATCH_WALLETS = 150           # monitor more wallets
MIN_TRADE_SIZE = 5                # catch small trades

# ── Paper Trader Defaults ──────────────────────────────────────────────────
STARTING_BANKROLL = 10_000
DEFAULT_KELLY_FRACTION = 1.0      # full Kelly (paper only)
MAX_POSITION_PCT = 0.35           # up to 35% per trade (paper only)

# ── Rate Limiting ──────────────────────────────────────────────────────────
REQUEST_DELAY = 0.1
MAX_RETRIES = 5

# ── Alerts ─────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── Database ───────────────────────────────────────────────────────────────
DB_PATH = "poly_alpha.db"

# ── Rate Limiting ──────────────────────────────────────────────────────────
REQUEST_DELAY = 0.5               # seconds between API calls
MAX_RETRIES = 3
