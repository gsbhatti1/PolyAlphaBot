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
LIVE_CAPITAL_FRACTION = 0.01     # 1% of computed size deployed when LIVE (safety ramp)

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
MAX_AVG_BET = 5000                      # ignore whales betting over 5k avg
MIN_ACTIVE_DAYS = 1               # allow recent
TOP_VOLUME_EXCLUDE = 0            # include visible wallets for action

# ── Monitor Defaults ───────────────────────────────────────
POLL_INTERVAL = 3                 # faster loop (was 5)
MAX_WATCH_WALLETS = 150
MIN_TRADE_SIZE = 5                      # min $5

# ── Paper Trader Defaults (sizing) ──────────────────────────
STARTING_BANKROLL = 1000                # reset
MAX_PAPER_TRADE_USD = 50                # HARD cap 0 per trade

# ── Trade quality filters ─────────────────────────────────────────────────
# Only copy trades in the 35%-85% probability window — real edge lives here
MIN_COPY_PRICE   = 0.35   # skip longshots below 35% (alpha wallets use as hedges)
MAX_COPY_PRICE   = 0.85   # skip near-certain outcomes (tiny upside, capital locked)
SKIP_SELL_BELOW  = 0.40   # never SELL outcome priced <40% (max gain tiny, max loss huge)

# ── Bot market blocklist ──────────────────────────────────────────────────
# These are HFT bot markets — uncopyable edge, guaranteed spread loss
BOT_MARKET_KEYWORDS = [
    "updown", "15m-", "5m-", "1h-", "30m-",
    "btc-up", "eth-up", "sol-up", "btc-down", "eth-down",
    "-15m", "-5m", "-1h", "-30m"
]

# ── Consensus filter ──────────────────────────────────────────────────────
# Only open a trade when this many watched wallets are in same market+outcome
# 1 = copy everyone (current, noisy), 2 = require confirmation (recommended)
MIN_CONSENSUS_WALLETS = 2

# ── Wallet quality filter ─────────────────────────────────────────────────
MIN_WALLET_TRADES     = 30      # lower threshold — catch more human traders
MIN_WALLET_DAYS       = 90      # ignore wallets active less than 90 days

# ── Market volume filter ──────────────────────────────────────────────────
MIN_MARKET_VOLUME_USD = 50_000  # skip markets with less than $50k total volume
                                 # prevents slippage in illiquid markets

# ── Kelly sizing ──────────────────────────────────────────────────────────
# Real Kelly: f = (win_rate - market_price) / (1 - market_price) * quarter_kelly
# This is the formula every profitable Polymarket bot uses
DEFAULT_KELLY_FRACTION = 0.25   # quarter Kelly — reduces max drawdown to 4%
MAX_POSITION_PCT       = 0.15   # never risk more than 15% bankroll on one trade

# ── Weekly wallet rescan ──────────────────────────────────────────────────
WALLET_RESCAN_SEC          = 604_800  # rescan every 7 days (7 * 24 * 3600)
WALLET_MIN_RECENT_WIN_RATE = 0.55     # drop wallets below 55% recent win rate
WALLET_MIN_RECENT_DAYS     = 30       # look at last 30 days of activity

# ── Safety rails (prevent blowups + keep flow) ──────────────
MAX_TRADES_PER_HOUR = 300         # raise throughput ceiling
MAX_OPEN_POSITIONS = 10                 # max 10 open positions
MAX_OPEN_EXPOSURE_USD = 500             # max $500 locked total
MAX_MARKET_EXPOSURE_USD = 150     # per (market,outcome,side)
MAX_MARKET_POSITIONS = 5
COOLDOWN_SECONDS_PER_MARKET = 10   # faster recycle (was 20)
WALLET_COOLDOWN_SEC = 0               # disabled — consensus filter handles throttling

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

MIRROR_ALWAYS = True


TELEGRAM_PORTFOLIO_EVERY_SEC = 600

TELEGRAM_PORTFOLIO_LAST_N = 5

TELEGRAM_CMD_POLL_SEC = 5

REPORT_DELTA_BANKROLL = 50       # only report when bankroll moves $50+
REPORT_DELTA_PNL = 20            # only report when PnL moves $20+
REPORT_DELTA_OPEN_POS = 3        # only report when 3+ positions open/close (not every single one)
REPORT_DELTA_EXPOSURE = 100      # only report when exposure moves $100+
REPORT_THROTTLE_SEC = 300        # max one portfolio report per 5 minutes

IGNORE_OLD_TRADES_ON_BOOT_SEC = 86400  # ignore trades older than 24h on boot

MAX_TELEGRAM_ALERTS_PER_MIN = 12

ALERTS_SUPPRESS_SUMMARY = True

# --- Paper position auto-close (prevents cap deadlock) ---
AUTO_CLOSE_SEC = 0               # DISABLED — hold until resolution or SL/TP fires
AUTO_CLOSE_PRICE_MODE = "mid"    # used when AUTO_CLOSE_SEC > 0

# ── Stop-loss and Take-profit ─────────────────────────────────────────────
# Positions held until market resolves, UNLESS one of these fires first
# This prevents the 2.4% round-trip spread cost on every 15-min cycle
STOP_LOSS_PCT   = 0.30           # close if position loses 30% of entry value
TAKE_PROFIT_PCT = 0.50           # close if position gains 50% of entry value
TELEGRAM_NOTIFY_DETECTIONS = False
TELEGRAM_NOTIFY_EXECUTIONS = True
