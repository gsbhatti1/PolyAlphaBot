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

# ── Execution Mode (Paper ≈ Live) ──────────────────────────────────────────
EXECUTION_MODE = "PAPER"          # PAPER / SHADOW / LIVE
LIVE_CAPITAL_FRACTION = 0.01      # 1% when LIVE

# Market-order realism (paper sim)
SIM_LATENCY_MS_MIN = 250
SIM_LATENCY_MS_MAX = 1200
SIM_SLIP_BPS_BASE = 8
SIM_SLIP_BPS_PER_100USD = 4
SIM_FEE_BPS = 10                  # adjust to real later

# --- Risk caps (paper) ---
MAX_MARKET_EXPOSURE_USD = 100     # cap per (market_slug,outcome,side)
# Optional: cap count too (safety)
MAX_MARKET_POSITIONS = 5          # max open positions per (market_slug,outcome,side)

# --- throttle controls (paper/live parity: keep but tune) ---
CAP_THROTTLE_SEC = 1              # was defaulting to 60; keep tiny for speed
THROTTLE_LOG_EVERY_SEC = 15       # log throttle at most every 10s (avoid spam)

# ── Scanner Defaults (who to follow) ───────────────────────────────────────
DEFAULT_SCAN_LIMIT = 1000         # pull more wallets
MIN_PNL = 0                       # include all
MIN_TRADES = 1                    # include new wallets
MIN_WIN_RATE = 0.49               # very permissive
MAX_AVG_BET = 500_000             # allow whales for now
MIN_ACTIVE_DAYS = 1               # allow recent
TOP_VOLUME_EXCLUDE = 0            # include visible wallets for action

# ── Monitor Defaults ───────────────────────────────────────
POLL_INTERVAL = 3                 # faster loop (was 5)
MAX_WATCH_WALLETS = 150
MIN_TRADE_SIZE = 5

# ── Paper Trader Defaults (sizing) ──────────────────────────
STARTING_BANKROLL = 1000
DEFAULT_KELLY_FRACTION = 0.35     # full kelly is explosive; 0.35 is aggressive but survivable
MAX_PAPER_TRADE_USD = 25
MAX_POSITION_PCT = 0.10           # 35% is too chunky for high-frequency; use 10%

# ── Safety rails (prevent blowups + keep flow) ──────────────
MAX_TRADES_PER_HOUR = 300         # raise throughput ceiling
MAX_OPEN_POSITIONS = 30           # single source of truth (remove MAX_POSITIONS_OPEN)
MAX_OPEN_EXPOSURE_USD = 1000      # matches higher open count
MAX_MARKET_EXPOSURE_USD = 150     # per (market,outcome,side)
MAX_MARKET_POSITIONS = 5
COOLDOWN_SECONDS_PER_MARKET = 10   # faster recycle (was 20)
WALLET_COOLDOWN_SEC = 180         # 2 minutes (was 1800!)

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

# ── Autonomous wallet maintenance ────────────────────────────────────────────
DEAD_WALLET_DAYS = 5                 # prune wallets if no trades for N days
MAINTENANCE_EVERY_SEC = 3600         # run maintenance every hour
REFILL_FROM_INACTIVE = True          # refill from inactive wallets already in DB

# ── Scanner auto-refresh ─────────────────────────────────────────────────────
SCAN_REFRESH_EVERY_SEC = 21600   # 6 hours
SCAN_LIMIT = 1000                # leaderboard entries to scan
SCANNER_SCRIPT = "poly_alpha_scanner.py"

MAX_PAPER_TRADE_USD = 25
MIRROR_ALWAYS = True
MAX_OPEN_POSITIONS = 30
MAX_OPEN_EXPOSURE_USD = 1000

WALLET_COOLDOWN_SEC = 1800

TELEGRAM_PORTFOLIO_EVERY_SEC = 600

TELEGRAM_PORTFOLIO_LAST_N = 5

TELEGRAM_CMD_POLL_SEC = 5

REPORT_DELTA_BANKROLL = 25

REPORT_DELTA_PNL = 10

REPORT_DELTA_OPEN_POS = 1

REPORT_DELTA_EXPOSURE = 50

REPORT_THROTTLE_SEC = 30

IGNORE_OLD_TRADES_ON_BOOT_SEC = 300

MAX_TELEGRAM_ALERTS_PER_MIN = 12

ALERTS_SUPPRESS_SUMMARY = True

# --- Paper position auto-close (prevents cap deadlock) ---
AUTO_CLOSE_SEC = 60              # 15 minutes
AUTO_CLOSE_PRICE_MODE = "mid"
