import sqlite3, datetime

DB = '/home/polymarket/poly_alpha_bot/poly_alpha.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# ── Bankroll ─────────────────────────────────────────────────────────────
ledger = conn.execute('SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1').fetchone()
pnl_row  = conn.execute("SELECT COALESCE(SUM(pnl),0) as p FROM paper_positions WHERE status='closed'").fetchone()
lock_row = conn.execute("SELECT COALESCE(SUM(size_usd),0) as l FROM paper_positions WHERE status='open'").fetchone()
total_pnl = float(pnl_row['p'])
locked    = float(lock_row['l'])

if ledger:
    bankroll = float(ledger['bankroll'])
else:
    bankroll = 1000.0 + total_pnl - locked

# ── Stats ─────────────────────────────────────────────────────────────────
stats = conn.execute("""
    SELECT COUNT(*) as n,
           SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins,
           SUM(CASE WHEN pnl<0 THEN 1 ELSE 0 END) as losses
    FROM paper_positions WHERE status='closed'
""").fetchone()
closed = int(stats['n'] or 0)
wins   = int(stats['wins'] or 0)
losses = int(stats['losses'] or 0)
wr     = (wins / closed * 100) if closed else 0.0
open_n = conn.execute("SELECT COUNT(*) as n FROM paper_positions WHERE status='open'").fetchone()['n']

print('=' * 65)
print('  POLY ALPHA BOT — LIVE DASHBOARD')
print('=' * 65)
print(f'  Bankroll:       ${bankroll:,.2f}   (started $1,000)')
print(f'  Open:           {open_n} positions  (${locked:.2f} locked)')
print(f'  Realised PnL:   ${total_pnl:+,.2f}')
print(f'  Closed trades:  {closed}  (W:{wins} L:{losses}  WR:{wr:.1f}%)')

# ── Last 10 Closed ────────────────────────────────────────────────────────
print()
print('  LAST 10 CLOSED TRADES')
print('  ' + '-' * 63)
rows = conn.execute("""
    SELECT market_slug, side, outcome, entry_price, exit_price, pnl, size_usd, closed_at
    FROM paper_positions
    WHERE status='closed'
    ORDER BY closed_at DESC
    LIMIT 10
""").fetchall()
if rows:
    for r in rows:
        ts = datetime.datetime.fromtimestamp(float(r['closed_at'])).strftime('%H:%M') if r['closed_at'] else '?'
        pnl_s = f"${float(r['pnl']):+.2f}"
        print(f'  {ts} {r["market_slug"][:24]:<24} {r["side"]:<4} {str(r["outcome"])[:8]:<8} '
              f'e={float(r["entry_price"]):.3f}→{float(r["exit_price"]):.3f} {pnl_s}')
else:
    print('  No closed trades yet')

# ── Open Positions ────────────────────────────────────────────────────────
print()
print('  CURRENT OPEN POSITIONS')
print('  ' + '-' * 63)
rows2 = conn.execute("""
    SELECT market_slug, side, outcome, entry_price, size_usd, opened_at
    FROM paper_positions WHERE status='open'
    ORDER BY opened_at DESC
""").fetchall()
if rows2:
    for r in rows2:
        ts = datetime.datetime.fromtimestamp(float(r['opened_at'])).strftime('%H:%M')
        print(f'  {ts} {r["market_slug"][:24]:<24} {r["side"]:<4} {str(r["outcome"])[:10]:<10} '
              f'entry={float(r["entry_price"]):.3f} size=${float(r["size_usd"]):.2f}')
else:
    print('  No open positions')

# ── Outbox ────────────────────────────────────────────────────────────────
print()
print('  LAST 15 OUTBOX ACTIONS')
print('  ' + '-' * 63)
rows3 = conn.execute("""
    SELECT ts, market_slug, side, outcome, size_usd, status, error
    FROM order_outbox
    ORDER BY ts DESC LIMIT 15
""").fetchall()
for r in rows3:
    ts  = datetime.datetime.fromtimestamp(float(r['ts'])).strftime('%H:%M:%S')
    err = str(r['error'] or '')[:35]
    print(f'  {ts} {r["status"]:<8} {r["market_slug"][:22]:<22} {r["side"]:<4} {err}')

print('=' * 65)
