"""
Standalone hourly Telegram reporter.
Reads DB every hour, sends pro-format report.
Runs as a separate systemd service.
"""
import time, sqlite3, os, sys
sys.path.insert(0, '/home/polymarket/poly_alpha_bot')
import config, alerts

DB = config.DB_PATH
START = config.STARTING_BANKROLL

def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def run():
    alerts.send_startup(START)
    last_report = 0
    while True:
        now = time.time()
        if now - last_report >= 3600:
            try:
                conn = get_conn()
                # bankroll
                row = conn.execute("SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1").fetchone()
                bankroll = float(row["bankroll"]) if row else START
                # open positions
                opens = conn.execute("SELECT * FROM paper_positions WHERE status='open'").fetchall()
                open_count = len(opens)
                locked = sum(float(r["size_usd"]) for r in opens)
                # closed stats
                stats = conn.execute("""
                    SELECT
                        COALESCE(SUM(pnl),0) as total_pnl,
                        SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN pnl<0 THEN 1 ELSE 0 END) as losses
                    FROM paper_positions WHERE status='closed'
                """).fetchone()
                pnl = float(stats["total_pnl"] or 0)
                wins = int(stats["wins"] or 0)
                losses = int(stats["losses"] or 0)
                open_list = [dict(r) for r in opens[:5]]
                conn.close()
                alerts.send_hourly_report(
                    bankroll=bankroll,
                    starting_bankroll=START,
                    pnl_today=pnl,
                    open_count=open_count,
                    locked_usd=locked,
                    wins=wins,
                    losses=losses,
                    open_positions=open_list
                )
                last_report = now
            except Exception as e:
                print(f"Report error: {e}")
        time.sleep(60)

if __name__ == "__main__":
    run()
