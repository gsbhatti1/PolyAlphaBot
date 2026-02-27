# Windows PowerShell Setup Guide

Run the Polymarket Alpha Scanner on your local Windows machine.

---

## 1. Prerequisites

### Install Python 3.11+

1. Download from https://www.python.org/downloads/
2. **IMPORTANT:** During install, check ✅ "Add Python to PATH"
3. Verify in PowerShell:

```powershell
python --version
# Should show Python 3.11.x or higher
```

If `python` doesn't work, try `python3` or `py`.

### Install SQLite Viewer (Optional, for inspecting results)

- Download DB Browser for SQLite: https://sqlitebrowser.org/dl/

---

## 2. Download and Set Up the Project

Open PowerShell and run:

```powershell
# Navigate to where you want the project
cd ~\Documents

# Create project folder (if copying files manually)
mkdir poly_alpha
cd poly_alpha
```

If you downloaded the files as a zip, extract them into this folder. The structure should be:

```
poly_alpha\
├── config.py
├── db.py
├── poly_api.py
├── scoring.py
├── alerts.py
├── paper_trader.py
├── poly_alpha_scanner.py
├── poly_alpha_monitor.py
└── requirements.txt
```

---

## 3. Create Virtual Environment and Install Dependencies

```powershell
cd ~\Documents\poly_alpha

# Create virtual environment
python -m venv venv

# Activate it
.\venv\Scripts\Activate.ps1

# If you get an "execution policy" error, run this first:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Install dependencies
pip install -r requirements.txt

# Verify
python -c "import config, db, poly_api, scoring, alerts, paper_trader; print('All OK')"
```

**Note:** Every time you open a new PowerShell window, you need to activate the venv again:

```powershell
cd ~\Documents\poly_alpha
.\venv\Scripts\Activate.ps1
```

You'll see `(venv)` at the start of your prompt when it's active.

---

## 4. Run the Scanner

```powershell
# Make sure venv is activated (you should see (venv) in prompt)
cd ~\Documents\poly_alpha

# Default scan
python poly_alpha_scanner.py

# Deeper scan (takes longer)
python poly_alpha_scanner.py --limit 500

# Adjusted thresholds
python poly_alpha_scanner.py --min-pnl 200 --min-winrate 0.55
```

This will:
- Pull wallets from the Polymarket leaderboard
- Deep-scan each wallet's trades and profile
- Score, filter, and rank them
- Save results to `hidden_alpha_wallets.json` and `poly_alpha.db`

Wait for it to finish (5-15 min depending on `--limit`).

---

## 5. View Scanner Results

```powershell
# Pretty-print the JSON output
Get-Content hidden_alpha_wallets.json | python -m json.tool | Select-Object -First 80

# Or open it in any text editor
notepad hidden_alpha_wallets.json

# Query the database (if you have sqlite3 on PATH)
sqlite3 poly_alpha.db "SELECT address, username, alpha_score, pnl, win_rate FROM wallets ORDER BY alpha_score DESC LIMIT 10;"
```

Or open `poly_alpha.db` in DB Browser for SQLite for a visual view.

---

## 6. Run the Monitor (Paper Trading)

```powershell
# Make sure scanner has run first (so the DB has wallets)
python poly_alpha_monitor.py

# Customize
python poly_alpha_monitor.py --max-wallets 10 --interval 15

# Add a specific wallet to watch
python poly_alpha_monitor.py --wallet 0xABC123...

# Only alert on big trades
python poly_alpha_monitor.py --min-size 200

# Custom bankroll
python poly_alpha_monitor.py --bankroll 50000

# Watch-only mode (no paper trading)
python poly_alpha_monitor.py --no-paper
```

The monitor shows a live terminal dashboard. Press `Ctrl+C` to stop it gracefully.

---

## 7. Set Up Alerts (Optional)

### Telegram

```powershell
# Set for current session
$env:TELEGRAM_BOT_TOKEN = "123456:ABC-DEF..."
$env:TELEGRAM_CHAT_ID = "987654321"
python poly_alpha_monitor.py
```

To make permanent, add to your PowerShell profile:

```powershell
# Open profile
notepad $PROFILE

# Add these lines:
$env:TELEGRAM_BOT_TOKEN = "123456:ABC-DEF..."
$env:TELEGRAM_CHAT_ID = "987654321"
```

### Discord

```powershell
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python poly_alpha_monitor.py
```

---

## 8. Run in Background (Keep Running After Closing Terminal)

### Option A — Start-Process (Simple)

```powershell
cd ~\Documents\poly_alpha
Start-Process -NoNewWindow -FilePath ".\venv\Scripts\python.exe" -ArgumentList "poly_alpha_monitor.py" -RedirectStandardOutput "monitor.log" -RedirectStandardError "monitor_err.log"
```

To stop it:

```powershell
Get-Process python | Where-Object { $_.Path -like "*poly_alpha*" } | Stop-Process
# Or just:
Get-Process python | Stop-Process
```

### Option B — Task Scheduler (Recommended for Unattended)

1. Open **Task Scheduler** (search in Start menu)
2. Click **Create Basic Task**
3. Name: `Poly Alpha Monitor`
4. Trigger: **When the computer starts** (or daily)
5. Action: **Start a program**
   - Program: `C:\Users\YOUR_NAME\Documents\poly_alpha\venv\Scripts\python.exe`
   - Arguments: `poly_alpha_monitor.py`
   - Start in: `C:\Users\YOUR_NAME\Documents\poly_alpha`
6. Finish

### Option C — Scheduled Re-Scans

Create a scheduled task for the scanner too:

1. Task Scheduler → Create Basic Task
2. Name: `Poly Alpha Scanner`
3. Trigger: **Daily** at 3:00 AM
4. Action: **Start a program**
   - Program: `C:\Users\YOUR_NAME\Documents\poly_alpha\venv\Scripts\python.exe`
   - Arguments: `poly_alpha_scanner.py --limit 300`
   - Start in: `C:\Users\YOUR_NAME\Documents\poly_alpha`

---

## 9. Check Paper Trading Performance

```powershell
cd ~\Documents\poly_alpha

# Summary from DB
sqlite3 poly_alpha.db "SELECT * FROM paper_ledger ORDER BY id DESC LIMIT 10;"

# Open positions
sqlite3 poly_alpha.db "SELECT wallet_address, market_question, side, entry_price, size_usd FROM paper_positions WHERE status='open';"

# Closed trades with P&L
sqlite3 poly_alpha.db "SELECT market_question, side, entry_price, exit_price, pnl FROM paper_positions WHERE status='closed' ORDER BY closed_at DESC LIMIT 20;"

# Overall stats
sqlite3 poly_alpha.db "SELECT SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, SUM(CASE WHEN pnl<=0 THEN 1 ELSE 0 END) as losses, ROUND(SUM(pnl),2) as total_pnl FROM paper_positions WHERE status='closed';"
```

---

## 10. Common PowerShell Issues

| Problem | Fix |
|---|---|
| `python` not recognized | Reinstall Python with "Add to PATH" checked, or use `py` instead |
| Execution policy error on venv activate | Run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `pip` not recognized | Use `python -m pip install -r requirements.txt` |
| `sqlite3` not recognized | Install from https://www.sqlite.org/download.html or use DB Browser instead |
| Rich tables look garbled | Use Windows Terminal (not old cmd.exe). Install from Microsoft Store |
| Monitor crashes on startup | Make sure you ran the scanner first so the DB has wallets |
| Empty scan results | API may be rate-limited. Open `config.py` and set `REQUEST_DELAY = 1.0` |
| Encoding errors in output | Add to top of PowerShell: `[Console]::OutputEncoding = [Text.Encoding]::UTF8` |

---

## Quick Reference

```powershell
# Always start with:
cd ~\Documents\poly_alpha
.\venv\Scripts\Activate.ps1

# Scan for wallets
python poly_alpha_scanner.py

# Monitor + paper trade
python poly_alpha_monitor.py

# Stop monitor
# Press Ctrl+C

# Check results
sqlite3 poly_alpha.db "SELECT * FROM paper_ledger ORDER BY id DESC LIMIT 5;"
```
