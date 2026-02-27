# Polymarket Hidden Alpha Scanner

Find profitable Polymarket wallets that aren't being copy-traded yet — and paper-trade alongside them to validate the edge before risking real capital.

## Architecture

```
┌──────────────────────────┐     ┌──────────────────────────┐
│  poly_alpha_scanner.py    │────▶│  hidden_alpha_wallets    │
│  (Discovery)              │     │  .json                   │
│                           │     └───────────┬──────────────┘
│  Phase 1: Leaderboard     │                 │
│  Phase 2: Deep Scan       │     ┌───────────▼──────────────┐
│  Phase 3: Score + Filter  │     │  poly_alpha_monitor.py    │
│  Phase 4: Output          │     │  (Real-time Watch)        │
└──────────────────────────┘     │                           │
                                  │  Poll wallets → Detect    │
         ┌──────────────┐        │  Paper trade → Alert      │
         │ poly_alpha.db │◀───────│                           │
         │ (SQLite)      │        └───────────────────────────┘
         └──────────────┘
                │
    ┌───────────┴──────────────┐
    │  Paper Trading Engine     │
    │  Kelly sizing · P&L track │
    │  Resolution checking      │
    └──────────────────────────┘
```

## Project Structure

```
poly_alpha/
├── config.py               # All configuration and defaults
├── db.py                   # SQLite persistence layer
├── poly_api.py             # Polymarket API client (async, rate-limited)
├── scoring.py              # Alpha scoring engine + filters
├── alerts.py               # Telegram / Discord dispatch
├── paper_trader.py         # Paper trading with Kelly criterion
├── poly_alpha_scanner.py   # Main scanner CLI
├── poly_alpha_monitor.py   # Real-time monitor CLI
└── requirements.txt
```

## Quick Start

```bash
pip install -r requirements.txt

# Step 1: Discover hidden alpha wallets
python poly_alpha_scanner.py

# Step 2: Monitor and paper-trade in real-time
python poly_alpha_monitor.py
```

## Scanner Options

```bash
# Scan more wallets (slower, deeper)
python poly_alpha_scanner.py --limit 500

# Lower PnL threshold
python poly_alpha_scanner.py --min-pnl 200

# Tighter win rate
python poly_alpha_scanner.py --min-winrate 0.60

# Custom output
python poly_alpha_scanner.py --output my_wallets.json --db my_scan.db
```

## Monitor Options

```bash
# Watch top 10, poll every 15s
python poly_alpha_monitor.py --max-wallets 10 --interval 15

# Add a specific wallet
python poly_alpha_monitor.py --wallet 0xABC123...

# Only alert on trades > $200
python poly_alpha_monitor.py --min-size 200

# Custom bankroll
python poly_alpha_monitor.py --bankroll 50000

# Disable paper trading (alerts only)
python poly_alpha_monitor.py --no-paper
```

## Telegram / Discord Alerts

```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

python poly_alpha_monitor.py
```

## Alpha Score Breakdown

| Component          | Weight | What it measures                         |
|--------------------|--------|------------------------------------------|
| PnL Magnitude      | 20%    | How much profit (log-scaled)             |
| Win Rate           | 20%    | % of markets profitable                  |
| Profit Factor      | 15%    | Gross wins / gross losses                |
| Sharpe Ratio       | 15%    | Risk-adjusted return (adapted for binary)|
| Consistency        | 10%    | Win streak patterns + return variance    |
| Recency            | 10%    | Exponential decay, 14-day half-life      |
| Inverse Visibility | 10%    | Lower visibility = higher score          |

## Filters

| Filter                  | Default     | Purpose                             |
|-------------------------|-------------|-------------------------------------|
| Min PnL                 | $500        | No noise from micro-bettors         |
| Min trades              | 15          | Filters lucky one-shots             |
| Win rate                | > 52%       | Better than coin flip               |
| Exclude top 500 volume  | On          | Too visible / already being copied  |
| Market maker detection  | On          | Removes spread-capture bots         |
| Max avg bet             | $50k        | Filters whale-sized wallets         |
| Min active days         | 14          | Requires sustained track record     |

## Paper Trading

The monitor includes a paper trading engine that:

1. **Mirrors detected trades** from watched wallets
2. **Sizes positions with Kelly criterion** (quarter-Kelly for safety)
3. **Caps exposure** at 10% of bankroll per trade
4. **Checks market resolutions** periodically to close positions
5. **Tracks full P&L** with bankroll snapshots in SQLite

This lets you validate whether the "hidden alpha" actually translates to profit
before committing real capital.

## SQLite Database

```bash
sqlite3 poly_alpha.db

-- Top wallets by alpha score
SELECT address, username, alpha_score, pnl, win_rate
FROM wallets ORDER BY alpha_score DESC LIMIT 20;

-- Scan history
SELECT * FROM scan_log ORDER BY id DESC LIMIT 5;

-- Paper trading performance
SELECT * FROM paper_ledger ORDER BY id DESC LIMIT 10;

-- Open paper positions
SELECT * FROM paper_positions WHERE status='open';

-- Closed trades with P&L
SELECT wallet_address, market_question, side, entry_price,
       exit_price, pnl, status
FROM paper_positions WHERE status='closed'
ORDER BY closed_at DESC LIMIT 20;
```

## Important Caveats

- **Alpha decays fast.** The more people copy a wallet, the less edge remains.
- **Survivorship bias.** 55% win rate over 20 trades can be luck. Paper trade first.
- **API rate limits.** The scanner throttles requests (0.4s delay). Scanning 500 wallets takes ~10 min.
- **Binary Sharpe.** The Sharpe ratio is adapted for binary outcomes but isn't a perfect analog to traditional Sharpe.
- **Not financial advice.** This is a research tool. Paper trade extensively before considering real capital.
# auto-push test Fri Feb 27 09:09:44 PM UTC 2026
