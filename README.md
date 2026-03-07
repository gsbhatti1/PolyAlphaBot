# PolyAlphaBot

**Automated Polymarket copy-trading bot** — discovers profitable wallets, watches them in real-time, and mirrors their trades using Kelly criterion sizing.

Currently running in **paper trading mode**. Architecture is built for a clean switch to live execution via the Polymarket CLOB API.

---

## How It Works (Plain English)

1. **Scanner** crawls the Polymarket leaderboard, scores every wallet on profitability, consistency, and how "hidden" they are
2. **Monitor** watches the top-scored wallets every 3 seconds for new trades
3. When a watched wallet trades, the **Paper Trader** decides whether to copy it — checking price quality, market volume, wallet quality, anomaly/insider signals, and consensus across wallets
4. If it passes all filters, the trade is sized using the **Kelly criterion** (quarter-Kelly for safety) and opened as a paper position
5. Positions close when the market resolves (win/loss) or when stop-loss / take-profit triggers fire
6. Everything is tracked in SQLite and reported to Telegram

---

## Architecture

```
                          ┌─────────────────────────────────────────────┐
                          │           POLYMARKET APIs                   │
                          │                                             │
                          │  Data API ──── Leaderboard, Activity,       │
                          │                Trades, Positions            │
                          │  Gamma API ─── Market metadata, Profiles    │
                          │  CLOB API ──── Orderbook, Quotes,           │
                          │                Live orders (LIVE mode)      │
                          └──────┬──────────────┬──────────────┬────────┘
                                 │              │              │
                    ┌────────────▼──┐   ┌───────▼───────┐  ┌──▼──────────┐
                    │   SCANNER     │   │   MONITOR     │  │  CLOB       │
                    │               │   │               │  │  QUOTES     │
                    │ Leaderboard   │   │ Poll wallets  │  │             │
                    │ Deep scan     │   │ every 3s      │  │ bid/ask/mid │
                    │ Score+filter  │   │               │  │ per token   │
                    │               │   │ Detect new    │  │             │
                    │ Outputs:      │   │ trades        │  └──────┬──────┘
                    │ wallets.json  │   │               │         │
                    │ → DB wallets  │   └───────┬───────┘         │
                    └───────┬───────┘           │                 │
                            │                   │                 │
                            ▼                   ▼                 ▼
                    ┌───────────────────────────────────────────────────┐
                    │                  PAPER TRADER                      │
                    │                                                   │
                    │  ┌─────────────────────────────────────────────┐  │
                    │  │            FILTER PIPELINE                  │  │
                    │  │                                             │  │
                    │  │  1. Bot market filter (HFT keywords)       │  │
                    │  │  2. Wallet quality (min trades/days)       │  │
                    │  │  3. Price quality (0.30–0.92 window)       │  │
                    │  │  4. Duplicate position guard               │  │
                    │  │  5. Volume filter ($10k+ markets)          │  │
                    │  │  6. CLOB quote validation (bid/ask >0)     │  │
                    │  │  7. Risk caps (max positions/exposure)     │  │
                    │  └─────────────────────────────────────────────┘  │
                    │                                                   │
                    │  ┌─────────────────────────────────────────────┐  │
                    │  │            SIZING ENGINE                    │  │
                    │  │                                             │  │
                    │  │  Real Kelly: f = (p - m) / (1 - m)         │  │
                    │  │  p = wallet win rate, m = market price     │  │
                    │  │  Quarter-Kelly × PnL tier multiplier       │  │
                    │  │  Capped at MAX_POSITION_PCT of bankroll    │  │
                    │  └─────────────────────────────────────────────┘  │
                    │                                                   │
                    │  ┌─────────────────────────────────────────────┐  │
                    │  │            EXECUTION                        │  │
                    │  │                                             │  │
                    │  │  PAPER: simulated fill (slip + fee + lat)  │  │
                    │  │  LIVE:  real CLOB order via py-clob-client │  │
                    │  │                                             │  │
                    │  │  outbox: attempt → opened → filled          │  │
                    │  └─────────────────────────────────────────────┘  │
                    │                                                   │
                    │  ┌─────────────────────────────────────────────┐  │
                    │  │            EXIT MANAGEMENT                  │  │
                    │  │                                             │  │
                    │  │  • Market resolution (win=1.0 / loss=0.0)  │  │
                    │  │  • Stop-loss at -30% of entry              │  │
                    │  │  • Take-profit at +50% of entry            │  │
                    │  └─────────────────────────────────────────────┘  │
                    └───────────────────┬───────────────────────────────┘
                                        │
                                        ▼
                    ┌───────────────────────────────────────────────────┐
                    │                   SQLite DB                        │
                    │                                                   │
                    │  wallets          — scored wallet pool             │
                    │  trades           — every detected whale trade     │
                    │  paper_positions  — open/closed positions + PnL   │
                    │  paper_ledger     — bankroll snapshots over time  │
                    │  order_outbox     — execution audit trail          │
                    │  fills            — simulated/real fill records    │
                    │  quotes           — CLOB bid/ask snapshots        │
                    │  trade_features   — ML feature store per trade    │
                    │  scan_log         — scanner run history            │
                    └───────────────────┬───────────────────────────────┘
                                        │
                                        ▼
                    ┌───────────────────────────────────────────────────┐
                    │                  TELEGRAM                         │
                    │                                                   │
                    │  • Trade opened/closed alerts                     │
                    │  • Insider/anomaly signals                        │
                    │  • Portfolio reports (/status, /report)           │
                    │  • Heartbeat pings every 20 polls                │
                    │  • Wallet maintenance summaries                   │
                    └───────────────────────────────────────────────────┘
```

---

## File Map

| File | Purpose | Key Functions |
|---|---|---|
| `config.py` | All tunables in one place | Kelly fractions, risk caps, filter thresholds, API URLs |
| `poly_api.py` | Polymarket API client (async + sync) | `fetch_activity()`, `fetch_leaderboard()`, `get_quote()`, `fetch_market_by_slug()` |
| `scoring.py` | Alpha scoring engine | `score_pnl()`, `score_win_rate()`, `score_consistency()`, composite `alpha_score` |
| `poly_alpha_scanner.py` | Wallet discovery CLI | Crawls leaderboard → deep scans → scores → saves to DB |
| `poly_alpha_scanner_aggressive.py` | Wider-net scanner variant | Lower thresholds for more wallet coverage |
| `poly_alpha_monitor.py` | Main bot loop (runs 24/7) | `poll_wallet()`, `check_resolutions()`, `run_maintenance()`, Telegram commands |
| `paper_trader.py` | Trade execution engine | `size_trade()` (Kelly), `open_position()`, `close_position()`, `auto_close_positions()` (SL/TP) |
| `live_trader.py` | Real CLOB order router | `open_position()` → `py-clob-client` → Polymarket CLOB |
| `db.py` | SQLite persistence | All table schemas, `init_db()`, position CRUD, wallet CRUD, ledger snapshots |
| `alerts.py` | Telegram / Discord dispatch | `send_telegram_sync()`, `format_new_trade_alert()`, `format_close_alert()` |
| `telegram_commands.py` | Bot command handlers | `/status`, `/report`, `/positions` |
| `v2_engine/` | Experimental v2 pipeline (WIP) | `edge_engine`, `risk_engine`, `execution_simulator` |

---

## Data Flow: Life of a Trade

```
Whale wallet buys "Will X happen?" at $0.45
        │
        ▼
[1] poll_wallet() detects new activity via Data API
        │
        ▼
[2] Trade record created: slug, outcome, side, price, size, tx_hash
        │  Inserted into `trades` table (dedup by tx_hash)
        │  Features snapshot saved to `trade_features`
        ▼
[3] Anomaly scoring (0–10 scale)
        │  Factors: wallet age, trade size vs average, market concentration
        │  ≥7.0 = SUPER INSIDER (bypass all filters, 3x size)
        │  ≥5.0 = INSIDER (bypass consensus + volume)
        │  ≥3.0 = SUSPICIOUS (bypass consensus only)
        ▼
[4] Consensus filter (if MIN_CONSENSUS_WALLETS > 1)
        │  Skip unless N wallets agree on same market+outcome+side
        │  Insiders (≥3.0) bypass this
        ▼
[5] Signal freshness check
        │  Skip if signal >2 hours old or price drifted >25%
        │  Scale down size if drift 10–25%
        ▼
[6] Wallet decay detection
        │  Skip if lifetime WR >60% but recent 20 trades <45% (lost edge)
        │  Halve size if moderate decay detected
        ▼
[7] size_trade() — Kelly criterion
        │  Real Kelly: f = (win_rate - market_price) / (1 - market_price)
        │  × quarter_kelly (0.25) × PnL tier multiplier
        │  Floor: MIN_TRADE_SIZE ($5), Cap: MAX_PAPER_TRADE_USD ($50)
        ▼
[8] open_position() — filter pipeline
        │  Bot market filter → wallet quality → price window →
        │  duplicate guard → volume filter → CLOB quote → risk caps
        ▼
[9] Execution
        │  PAPER: simulated fill (latency + slippage + fees)
        │  LIVE: real CLOB order via py-clob-client
        │
        │  Atomic transaction: INSERT position + INSERT fill
        │  Bankroll deducted ONLY after commit succeeds
        ▼
[10] Position management
        │  • check_resolutions() every 10 polls — checks if market resolved
        │  • auto_close_positions() every poll — SL/TP monitoring
        │  • On close: bankroll += principal + PnL, ledger snapshot written
        ▼
[11] Telegram alert sent
```

---

## Database Schema

```sql
-- Discovered and scored wallets
wallets (address PK, username, alpha_score, pnl, win_rate, num_trades,
         profit_factor, sharpe_ratio, consistency, recency_score,
         visibility, avg_bet_size, markets_traded, first_seen,
         last_updated, last_trade_ts, is_active, meta)

-- Every detected whale trade (dedup by tx_hash)
trades (id, wallet_address FK, market_slug, market_question, outcome,
        side, size_usd, price, timestamp, tx_hash UNIQUE)

-- Paper/live positions with full PnL tracking
paper_positions (id, wallet_address FK, market_slug, market_question,
                 outcome, side, entry_price, size_usd, kelly_fraction,
                 opened_at, closed_at, exit_price, pnl, status)

-- Bankroll snapshots over time (for equity curve)
paper_ledger (id, timestamp, bankroll, open_positions, total_pnl,
              num_trades, win_rate)

-- Execution audit trail (attempt → opened → filled / skipped / error)
order_outbox (id, ts, updated_ts, mode, market_slug, side, outcome,
              order_type, size_usd, payload, status, error)

-- Simulated/real fill records tied to outbox
fills (id, ts, order_id, outbox_id, market_slug, side, outcome,
       size_usd, bid, ask, fill_price, slip_bps, fee_usd, notes)

-- CLOB bid/ask snapshots for analysis
quotes (id, market_slug, ts, bid, ask, mid, spread, source)

-- ML feature store — every trade with wallet snapshot at detection time
trade_features (id, tx_hash UNIQUE, detected_at, wallet_address,
                market_slug, outcome, side, size_usd, price, timestamp,
                features JSON, resolved_at, result, pnl)

-- Scanner run history
scan_log (id, timestamp, wallets_scanned, wallets_passed, top_score, params)
```

---

## Key Configuration (config.py)

| Setting | Current Value | What It Controls |
|---|---|---|
| `EXECUTION_MODE` | `"PAPER"` | `PAPER` / `LIVE` — the master switch |
| `LIVE_CAPITAL_FRACTION` | `0.01` | When LIVE, deploy only 1% of computed size (safety ramp) |
| `STARTING_BANKROLL` | `$1,000` | Paper bankroll starting point |
| `MAX_PAPER_TRADE_USD` | `$50` | Hard cap per trade |
| `MAX_OPEN_POSITIONS` | `10` | Max concurrent positions |
| `MAX_OPEN_EXPOSURE_USD` | `$600` | Max total capital locked |
| `DEFAULT_KELLY_FRACTION` | `0.25` | Quarter-Kelly for drawdown control |
| `MAX_POSITION_PCT` | `0.15` | Never risk >15% bankroll on one trade |
| `MIN_COPY_PRICE` | `0.30` | Skip longshots below 30% |
| `MAX_COPY_PRICE` | `0.92` | Skip near-certain outcomes |
| `MIN_MARKET_VOLUME_USD` | `$10,000` | Skip illiquid markets |
| `MIN_CONSENSUS_WALLETS` | `1` | Wallets required to agree (1 = copy everyone) |
| `STOP_LOSS_PCT` | `0.30` | Close if position loses 30% |
| `TAKE_PROFIT_PCT` | `0.50` | Close if position gains 50% |
| `POLL_INTERVAL` | `3s` | How often to check wallets |
| `MAX_WATCH_WALLETS` | `150` | Wallet pool size |

---

## Deployment (VPS)

```bash
# Clone and install
git clone https://github.com/gsbhatti1/PolyAlphaBot.git
cd PolyAlphaBot
pip install -r requirements.txt

# Set Telegram alerts
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"

# Step 1: Discover wallets
python poly_alpha_scanner.py --limit 1000

# Step 2: Run the bot (systemd recommended)
python poly_alpha_monitor.py
```

**systemd service** (`/etc/systemd/system/polymarket-bot.service`):

```ini
[Unit]
Description=PolyAlphaBot Monitor
After=network.target

[Service]
Type=simple
User=polymarket
WorkingDirectory=/home/polymarket/poly_alpha_bot
Environment=TELEGRAM_BOT_TOKEN=your_token
Environment=TELEGRAM_CHAT_ID=your_chat_id
Environment=DB_PATH=/home/polymarket/poly_alpha_bot/poly_alpha.db
ExecStart=/usr/bin/python3 poly_alpha_monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Telegram commands** (send to your bot):
- `/status` or `/report` — full portfolio snapshot
- `/positions` — list open positions

---

## Roadmap: Paper → Live

### Phase 1 — Paper Validation (CURRENT)
- [x] Wallet discovery and scoring
- [x] Real-time trade detection
- [x] Kelly criterion sizing
- [x] Anomaly / insider detection
- [x] Paper execution with realistic sim (slippage, fees, latency)
- [x] Stop-loss and take-profit
- [x] Telegram alerts and commands
- [x] Autonomous wallet maintenance
- [ ] **Milestone: 200+ paper trades with positive PnL and >52% win rate**

### Phase 2 — Pre-Live Hardening
- [ ] Backtest Kelly parameters against `trade_features` history
- [ ] Tune `MIN_CONSENSUS_WALLETS` to 2 (reduce noise, confirm edge)
- [ ] Add max drawdown circuit breaker (pause trading if bankroll drops >20%)
- [ ] Add per-market cooldown tracking to prevent over-concentration
- [ ] Fix Telegram parse mode (HTML everywhere) for clean reporting
- [ ] Clean up dead code in `poly_api.py` (`_extract_token_id` duplicate block)
- [ ] Load-test with `POLL_INTERVAL=1` to verify API rate limits hold

### Phase 3 — Live (Small Capital)
- [ ] Install `py-clob-client`: `pip install py-clob-client`
- [ ] Set up Polymarket CLOB API credentials:
  ```bash
  export POLY_PRIVATE_KEY="your_wallet_private_key"
  export POLY_API_KEY="your_L2_api_key"
  export POLY_API_SECRET="your_L2_api_secret"
  export POLY_API_PASSPHRASE="your_L2_api_passphrase"
  ```
- [ ] Set `EXECUTION_MODE = "LIVE"` in config.py
- [ ] Keep `LIVE_CAPITAL_FRACTION = 0.01` (1% of paper size — $0.50 trades)
- [ ] Run for 1 week, compare live fills vs paper fills
- [ ] Verify: order placement, fill confirmation, PnL accuracy
- [ ] **Milestone: 50+ live trades, fills match paper expectations**

### Phase 4 — Live (Scale Up)
- [ ] Gradually increase `LIVE_CAPITAL_FRACTION`: 0.01 → 0.05 → 0.10 → 0.25 → 1.0
- [ ] Increase `MAX_PAPER_TRADE_USD` (rename to `MAX_TRADE_USD`)
- [ ] Increase `STARTING_BANKROLL` to match real wallet balance
- [ ] Add real balance checking from on-chain USDC
- [ ] Add order status polling (check if CLOB orders actually filled)
- [ ] Add partial fill handling
- [ ] **Milestone: consistent profit at 10%+ capital fraction**

### Phase 5 — Advanced
- [ ] Train ML model on `trade_features` table (features → PnL outcome)
- [ ] Replace static Kelly with model-predicted win probability
- [ ] Multi-wallet execution (spread across wallets to avoid detection)
- [ ] WebSocket feed instead of polling (lower latency)
- [ ] Dashboard UI (equity curve, open positions, wallet leaderboard)
- [ ] v2 engine integration (`v2_engine/`) with edge engine and risk engine

---

## How LIVE Mode Works

The switch from paper to live is a **one-line config change**. The same `open_position()` function in `paper_trader.py` handles both modes:

```
EXECUTION_MODE = "PAPER"
  └─► Simulated fill (random latency + slippage model)
      └─► Position saved to DB with simulated entry price

EXECUTION_MODE = "LIVE"
  └─► live_trader.py → py-clob-client → Polymarket CLOB
      └─► Real order placed, real fill price returned
      └─► Position saved to DB with actual fill price
      └─► Size scaled down by LIVE_CAPITAL_FRACTION (safety)
```

**Required env vars for LIVE:**

| Variable | Description |
|---|---|
| `POLY_PRIVATE_KEY` | Wallet private key (hex) |
| `POLY_API_KEY` | CLOB L2 API key |
| `POLY_API_SECRET` | CLOB L2 API secret |
| `POLY_API_PASSPHRASE` | CLOB L2 API passphrase |
| `POLY_CHAIN_ID` | `137` (Polygon mainnet) or `80002` (Amoy testnet) |

Generate L2 credentials by running `client.derive_api_key()` once with your private key.

---

## Useful Queries

```sql
-- Current portfolio state
SELECT COUNT(*) as positions,
       SUM(size_usd) as exposure,
       (SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1) as bankroll
FROM paper_positions WHERE status='open';

-- Win rate and PnL
SELECT COUNT(*) as total,
       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
       SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
       ROUND(SUM(pnl), 2) as total_pnl
FROM paper_positions WHERE status='closed';

-- Best performing wallets (by copy-trade PnL)
SELECT wallet_address, COUNT(*) as trades,
       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
       ROUND(SUM(pnl), 2) as total_pnl
FROM paper_positions WHERE status='closed'
GROUP BY wallet_address ORDER BY total_pnl DESC LIMIT 10;

-- Why trades were skipped (debug)
SELECT error, COUNT(*) as n
FROM order_outbox WHERE status='skipped'
GROUP BY error ORDER BY n DESC LIMIT 15;

-- Equity curve data
SELECT timestamp, bankroll FROM paper_ledger ORDER BY timestamp;

-- Insider signals detected
SELECT market_slug, error FROM order_outbox
WHERE error LIKE '%INSIDER%' OR error LIKE '%ANOMALY%'
ORDER BY ts DESC LIMIT 20;
```

---

## Important Notes

- **Alpha decays.** The more people copy a wallet, the less edge remains. The scanner re-runs every 6 hours and prunes underperformers weekly.
- **Paper trade first.** 55% win rate over 20 trades can be luck. Validate over 200+ trades before going live.
- **API rate limits.** The bot throttles at `REQUEST_DELAY=0.1s`. Scanning 1000 wallets takes ~10 min.
- **Not financial advice.** This is a research and trading tool. Use at your own risk.
