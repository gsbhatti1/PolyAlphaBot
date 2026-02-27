# VPS Setup Guide (Ubuntu/Debian)

Run the Polymarket Alpha Scanner 24/7 on a cheap VPS.

---

## 1. Requirements

- Ubuntu 22.04+ or Debian 12+ (any provider: Hetzner, DigitalOcean, Vultr, etc.)
- Minimum spec: 1 vCPU, 512MB RAM, 5GB disk
- Python 3.11+
- Cost: ~$4-6/month is plenty

---

## 2. Initial Server Setup

SSH into your VPS and run:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and essentials
sudo apt install -y python3 python3-pip python3-venv git screen

# Verify Python version (need 3.11+)
python3 --version
```

---

## 3. Upload the Project

**Option A — SCP from your local machine:**

```bash
# Run this on YOUR machine, not the VPS
scp -r poly_alpha/ user@YOUR_VPS_IP:~/poly_alpha/
```

**Option B — Git clone (if you pushed to a repo):**

```bash
cd ~
git clone https://github.com/YOUR_USER/poly_alpha.git
```

**Option C — Copy-paste files manually:**

```bash
mkdir -p ~/poly_alpha
cd ~/poly_alpha
# Then create each file with nano or vim
```

---

## 4. Set Up Python Environment

```bash
cd ~/poly_alpha

# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Verify everything works
python -c "import config, db, poly_api, scoring, alerts, paper_trader; print('All OK')"
```

---

## 5. Run the Scanner (One-Time Discovery)

```bash
cd ~/poly_alpha
source venv/bin/activate

# Default scan (200 wallets)
python poly_alpha_scanner.py

# Deeper scan
python poly_alpha_scanner.py --limit 500 --min-pnl 300
```

This takes 5-15 minutes depending on `--limit`. Results go into:
- `hidden_alpha_wallets.json` — human-readable output
- `poly_alpha.db` — SQLite database

---

## 6. Run the Monitor (Long-Running)

### Option A — Screen Session (Recommended for Beginners)

```bash
# Create a named screen session
screen -S alpha

# Activate env and start monitor
cd ~/poly_alpha
source venv/bin/activate
python poly_alpha_monitor.py --interval 30 --max-wallets 20

# Detach from screen: press Ctrl+A, then D
# Reattach later:
screen -r alpha
```

### Option B — Systemd Service (Recommended for Production)

Create the service file:

```bash
sudo nano /etc/systemd/system/poly-alpha.service
```

Paste this (change `your_user` to your actual username):

```ini
[Unit]
Description=Polymarket Alpha Monitor
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/home/your_user/poly_alpha
ExecStart=/home/your_user/poly_alpha/venv/bin/python poly_alpha_monitor.py --interval 30 --max-wallets 20
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

# Optional: Telegram/Discord alerts
# Environment=TELEGRAM_BOT_TOKEN=your_token
# Environment=TELEGRAM_CHAT_ID=your_chat_id
# Environment=DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable poly-alpha
sudo systemctl start poly-alpha

# Check status
sudo systemctl status poly-alpha

# View logs
sudo journalctl -u poly-alpha -f

# Restart after changes
sudo systemctl restart poly-alpha
```

### Option C — tmux (Alternative to Screen)

```bash
sudo apt install -y tmux

tmux new -s alpha
cd ~/poly_alpha && source venv/bin/activate
python poly_alpha_monitor.py

# Detach: Ctrl+B, then D
# Reattach: tmux attach -t alpha
```

---

## 7. Set Up Alerts (Optional)

### Telegram

1. Message `@BotFather` on Telegram → `/newbot` → save the token
2. Message your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat ID
3. Set environment variables:

```bash
# Add to ~/.bashrc or the systemd service file
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export TELEGRAM_CHAT_ID="987654321"
```

### Discord

1. Server Settings → Integrations → Webhooks → New Webhook
2. Copy the webhook URL
3. Set:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

---

## 8. Schedule Re-Scans with Cron

The scanner should run periodically to discover new wallets:

```bash
crontab -e
```

Add this line to re-scan daily at 3 AM:

```cron
0 3 * * * cd /home/your_user/poly_alpha && /home/your_user/poly_alpha/venv/bin/python poly_alpha_scanner.py --limit 300 >> /home/your_user/poly_alpha/scan.log 2>&1
```

---

## 9. Check Results

```bash
cd ~/poly_alpha

# View discovered wallets
cat hidden_alpha_wallets.json | python3 -m json.tool | head -60

# Query the database
sqlite3 poly_alpha.db "SELECT address, username, alpha_score, pnl, win_rate FROM wallets ORDER BY alpha_score DESC LIMIT 10;"

# Paper trading performance
sqlite3 poly_alpha.db "SELECT * FROM paper_ledger ORDER BY id DESC LIMIT 10;"

# Open positions
sqlite3 poly_alpha.db "SELECT * FROM paper_positions WHERE status='open';"
```

---

## 10. Updating

```bash
cd ~/poly_alpha
source venv/bin/activate

# If using git
git pull

# If using SCP, re-upload changed files then:
sudo systemctl restart poly-alpha
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Make sure venv is activated: `source venv/bin/activate` |
| Empty scan results | Polymarket API may be rate-limiting. Increase `REQUEST_DELAY` in `config.py` to 1.0 |
| Monitor shows no trades | Normal if wallets are inactive. Wait or add active wallets with `--wallet` |
| `Permission denied` on port | Not needed — this project doesn't open any ports |
| Systemd won't start | Check `journalctl -u poly-alpha -n 50` for the actual error |
| SQLite locked | Only run one instance of the monitor at a time |
