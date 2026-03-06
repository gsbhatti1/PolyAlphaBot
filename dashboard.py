import sqlite3
conn = sqlite3.connect('/home/polymarket/poly_alpha_bot/poly_alpha.db')
conn.row_factory = sqlite3.Row

print('=' * 65)
print('  POLY ALPHA BOT — LIVE DASHBOARD')
print('=' * 65)

ledger = conn.execute('SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1').fetchone()
bankroll = ledger['bankroll'] if ledger else 1000.0
print(f'  Bankroll:       ${bankroll:,.2f}')

exp = conn.execute("SELECT COUNT(*) as n, COALESCE(SUM(size_usd),0) as locked FROM paper_positions WHERE status='open'").fetchone()
print(f'  Open:           {exp["n"]} positions  (${exp["locked"]:.2f} locked)')

stats = conn.execute("SELECT COUNT(*) as n, COALESCE(SUM(pnl),0) as pnl, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, SUM(CASE WHEN pnl<0 THEN 1 ELSE 0 END) as losses FROM paper_positions WHERE status='closed'").fetchone()
closed = stats['n'] or 0
wins   = stats['wins'] or 0
losses = stats['losses'] or 0
pnl    = stats['pnl'] or 0
wr     = (wins/closed*100) if closed else 0
print(f'  Realised PnL:   ${pnl:+,.2f}')
print(f'  Closed trades:  {closed}  (W:{wins} L:{losses}  WR:{wr:.1f}%)')

print()
print('  LAST 10 CLOSED TRADES')
print('  ' + '-'*63)
rows = conn.execute('''
    SELECT market_slug, side, outcome, entry_price, exit_price, pnl, size_usd,
           datetime(closed_at, "unixepoch") as closed
    FROM paper_positions WHERE status="closed"
    ORDER BY closed_at DESC LIMIT 10
''').fetchall()
if rows:
    for r in rows:
        print(f'  {r["market_slug"][:28]:<28} {r["side"]:<4} {r["outcome"][:10]:<10} entry={r["entry_price"]:.3f} exit={r["exit_price"]:.3f} pnl=${r["pnl"]:+.2f}')
else:
    print('  No closed trades yet')

print()
print('  CURRENT OPEN POSITIONS')
print('  ' + '-'*63)
rows2 = conn.execute('''
    SELECT market_slug, side, outcome, entry_price, size_usd,
           datetime(opened_at, "unixepoch") as opened
    FROM paper_positions WHERE status="open"
    ORDER BY opened_at DESC
''').fetchall()
if rows2:
    for r in rows2:
        print(f'  {r["market_slug"][:28]:<28} {r["side"]:<4} {r["outcome"][:10]:<10} entry={r["entry_price"]:.3f} size=${r["size_usd"]:.2f}  {r["opened"]}')
else:
    print('  No open positions yet')

print()
print('  LAST 15 OUTBOX ACTIONS')
print('  ' + '-'*63)
rows3 = conn.execute('''
    SELECT ts, market_slug, side, outcome, size_usd, status, error
    FROM order_outbox
    ORDER BY ts DESC LIMIT 15
''').fetchall()
import datetime
for r in rows3:
    ts  = datetime.datetime.fromtimestamp(r["ts"]).strftime("%H:%M:%S")
    err = str(r["error"] or "")[:35]
    print(f'  {ts} {r["status"]:<8} {r["market_slug"][:22]:<22} {r["side"]:<4} {err}')

print('=' * 65)
