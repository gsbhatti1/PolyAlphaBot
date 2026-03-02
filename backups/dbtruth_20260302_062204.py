import sqlite3

def build_portfolio_snapshot_dbtruth(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row

    cap = conn.execute(
        "SELECT starting_capital, COALESCE(current_capital, starting_capital) AS current_capital "
        "FROM capital_account WHERE id=1"
    ).fetchone()

    starting = float(cap["starting_capital"]) if cap else 0.0
    current = float(cap["current_capital"]) if cap else starting
    pnl = current - starting

    open_count = 0
    exposure = 0.0

    # best-effort scan common position tables, never crash
    for tbl in ("paper_positions", "positions", "open_positions"):
        try:
            rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
        except Exception:
            continue

        for r in rows:
            d = dict(r)
            qty = float(d.get("qty", d.get("quantity", 0)) or 0)
            side = str(d.get("side", d.get("position_side", "")) or "").upper()
            if qty != 0 and side not in ("FLAT",""):
                open_count += 1
                px = float(d.get("mark_price", d.get("price", d.get("avg_entry", 0))) or 0)
                exposure += abs(qty * px)
        break

    return (
        "📊 Poly Alpha Portfolio Snapshot (DB TRUTH)\n\n"
        f"💰 Bankroll: ${current:,.2f}\n"
        f"🟢 PnL: ${pnl:,.2f}\n"
        f"📦 Open Positions: {open_count}\n"
        f"⚖️ Exposure: ${exposure:,.2f}\n"
    )
