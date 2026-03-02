import sqlite3

def _cols(conn: sqlite3.Connection, table: str):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()

def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    try:
        r = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return bool(r)
    except Exception:
        return False

def build_portfolio_snapshot_dbtruth(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row

    starting = 0.0
    current = None

    # capital_account: always trust starting_capital; current_capital is optional
    if _has_table(conn, "capital_account"):
        cols = _cols(conn, "capital_account")
        if "starting_capital" in cols:
            row = conn.execute("SELECT starting_capital FROM capital_account WHERE id=1").fetchone()
            if row and row[0] is not None:
                starting = float(row[0])
        if "current_capital" in cols:
            row = conn.execute("SELECT current_capital FROM capital_account WHERE id=1").fetchone()
            if row and row[0] is not None:
                current = float(row[0])

    # fallback: paper_ledger bankroll if present
    if current is None and _has_table(conn, "paper_ledger"):
        cols = _cols(conn, "paper_ledger")
        if "bankroll" in cols:
            row = conn.execute("SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1").fetchone()
            if row and row[0] is not None:
                current = float(row[0])

    # final fallback
    if current is None:
        current = starting

    pnl = current - starting

    open_cnt = 0
    exposure = 0.0

    # paper_positions open exposure (schema-flex)
    if _has_table(conn, "paper_positions"):
        cols = _cols(conn, "paper_positions")
        status_col = "status" if "status" in cols else None
        size_col = "size_usd" if "size_usd" in cols else None

        try:
            if status_col:
                row = conn.execute(
                    "SELECT COUNT(*) AS c, COALESCE(SUM(COALESCE(size_usd,0)),0) AS s "
                    "FROM paper_positions WHERE lower(status)='open'"
                ).fetchone() if size_col else conn.execute(
                    "SELECT COUNT(*) AS c FROM paper_positions WHERE lower(status)='open'"
                ).fetchone()
                open_cnt = int(row[0] or 0)
                if size_col and len(row) > 1:
                    exposure = float(row[1] or 0.0)
            else:
                # no status column: best effort count all
                row = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()
                open_cnt = int(row[0] or 0)
        except Exception:
            pass

    return (
        "📊 Poly Alpha Portfolio Snapshot (DB TRUTH)\n\n"
        f"💰 Bankroll: ${current:,.2f}\n"
        f"🟢 PnL: ${pnl:,.2f}\n"
        f"📦 Open Positions: {open_cnt}\n"
        f"⚖️ Exposure: ${exposure:,.2f}\n"
        "Controlled. Adaptive. Institutional."
    )
