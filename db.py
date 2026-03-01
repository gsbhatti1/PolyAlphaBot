"""
SQLite persistence for scanner results, paper trades, and scan history.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import config


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(conn: sqlite3.Connection):
    """Create all tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wallets (
            address         TEXT PRIMARY KEY,
            username        TEXT,
            alpha_score     REAL DEFAULT 0,
            pnl             REAL DEFAULT 0,
            win_rate        REAL DEFAULT 0,
            num_trades      INTEGER DEFAULT 0,
            profit_factor   REAL DEFAULT 0,
            sharpe_ratio    REAL DEFAULT 0,
            consistency     REAL DEFAULT 0,
            recency_score   REAL DEFAULT 0,
            visibility      REAL DEFAULT 0,
            avg_bet_size    REAL DEFAULT 0,
            markets_traded  INTEGER DEFAULT 0,
            first_seen      REAL,
            last_updated    REAL,
            is_active       INTEGER DEFAULT 1,
            meta            TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS scan_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       REAL,
            wallets_scanned INTEGER,
            wallets_passed  INTEGER,
            top_score       REAL,
            params          TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address  TEXT,
            market_slug     TEXT,
            market_question TEXT,
            outcome         TEXT,
            side            TEXT,
            size_usd        REAL,
            price           REAL,
            timestamp       REAL,
            tx_hash         TEXT UNIQUE,
            FOREIGN KEY (wallet_address) REFERENCES wallets(address)
        );

        CREATE TABLE IF NOT EXISTS paper_positions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address  TEXT,
            market_slug     TEXT,
            market_question TEXT,
            outcome         TEXT,
            side            TEXT,
            entry_price     REAL,
            size_usd        REAL,
            kelly_fraction  REAL,
            opened_at       REAL,
            closed_at       REAL,
            exit_price      REAL,
            pnl             REAL,
            status          TEXT DEFAULT 'open',
            FOREIGN KEY (wallet_address) REFERENCES wallets(address)
        );

        CREATE TABLE IF NOT EXISTS paper_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       REAL,
            bankroll        REAL,
            open_positions  INTEGER,
            total_pnl       REAL,
            num_trades      INTEGER,
            win_rate        REAL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_wallet ON trades(wallet_address);
        CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_positions(status);
    """)

    migrate_wallets(conn)



def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)

def migrate_wallets(conn: sqlite3.Connection) -> None:
    """
    Lightweight migrations for long-running bots.
    Adds columns safely if missing.
    """
    # Track real activity for dead-wallet pruning
    if not _has_column(conn, "wallets", "last_trade_ts"):
        conn.execute("ALTER TABLE wallets ADD COLUMN last_trade_ts REAL")

    # Helpful indexes for pruning/refill at scale
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wallets_active ON wallets(is_active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wallets_last_trade ON wallets(last_trade_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wallets_first_seen ON wallets(first_seen)")

def touch_wallet_activity(conn: sqlite3.Connection, address: str, trade_ts: float) -> None:
    """
    Mark a wallet as active + update last_trade_ts.
    Ensures wallet row exists.
    """
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO wallets(address, first_seen, last_updated, is_active, meta) "
        "VALUES (?, ?, ?, 1, '{}')",
        (address, now, now),
    )
    conn.execute(
        "UPDATE wallets SET last_trade_ts=?, last_updated=?, is_active=1 WHERE address=?",
        (float(trade_ts), now, address),
    )

def get_active_wallets(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM wallets WHERE is_active=1 ORDER BY alpha_score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]

def prune_dead_wallets(conn: sqlite3.Connection, dead_days: int) -> list[str]:
    cutoff = time.time() - float(dead_days) * 86400.0
    # If last_trade_ts is NULL, fall back to first_seen
    rows = conn.execute(
        "SELECT address FROM wallets "
        "WHERE is_active=1 AND COALESCE(last_trade_ts, first_seen, 0) < ?",
        (cutoff,),
    ).fetchall()
    dead = [r["address"] for r in rows]
    if dead:
        conn.executemany(
            "UPDATE wallets SET is_active=0 WHERE address=?",
            [(a,) for a in dead],
        )
    return dead

def activate_best_inactive(conn: sqlite3.Connection, n: int) -> list[str]:
    if n <= 0:
        return []
    rows = conn.execute(
        "SELECT address FROM wallets "
        "WHERE is_active=0 "
        "ORDER BY alpha_score DESC, last_updated DESC "
        "LIMIT ?",
        (n,),
    ).fetchall()
    addrs = [r["address"] for r in rows]
    if addrs:
        conn.executemany(
            "UPDATE wallets SET is_active=1 WHERE address=?",
            [(a,) for a in addrs],
        )
    return addrs


# ── Wallet Operations ──────────────────────────────────────────────────────

def upsert_wallet(conn: sqlite3.Connection, wallet: dict):
    conn.execute("""
        INSERT INTO wallets (
            address, username, alpha_score, pnl, win_rate, num_trades,
            profit_factor, sharpe_ratio, consistency, recency_score,
            visibility, avg_bet_size, markets_traded, first_seen, last_updated, meta
        ) VALUES (
            :address, :username, :alpha_score, :pnl, :win_rate, :num_trades,
            :profit_factor, :sharpe_ratio, :consistency, :recency_score,
            :visibility, :avg_bet_size, :markets_traded, :first_seen, :last_updated, :is_active, :meta
        ) ON CONFLICT(address) DO UPDATE SET
            alpha_score=:alpha_score, pnl=:pnl, win_rate=:win_rate,
            num_trades=:num_trades, profit_factor=:profit_factor,
            sharpe_ratio=:sharpe_ratio, consistency=:consistency,
            recency_score=:recency_score, visibility=:visibility,
            avg_bet_size=:avg_bet_size, markets_traded=:markets_traded,
            last_updated=:last_updated, meta=:meta
    """, {
        **wallet,
        "first_seen": wallet.get("first_seen", time.time()),
        "last_updated": time.time(),
        "meta": json.dumps(wallet.get("meta", {})),
        "is_active": int(wallet.get("is_active", 0)),
    })


def get_top_wallets(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM wallets WHERE is_active=1 ORDER BY alpha_score DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def log_scan(conn: sqlite3.Connection, scanned: int, passed: int,
             top_score: float, params: dict):
    conn.execute(
        "INSERT INTO scan_log (timestamp, wallets_scanned, wallets_passed, top_score, params) "
        "VALUES (?, ?, ?, ?, ?)",
        (time.time(), scanned, passed, top_score, json.dumps(params)),
    )


# ── Trade Operations ───────────────────────────────────────────────────────

def insert_trade(conn: sqlite3.Connection, trade: dict) -> bool:
    """Insert trade, return True if new (not duplicate)."""
    try:
        conn.execute("""
            INSERT INTO trades (
                wallet_address, market_slug, market_question, outcome,
                side, size_usd, price, timestamp, tx_hash
            ) VALUES (
                :wallet_address, :market_slug, :market_question, :outcome,
                :side, :size_usd, :price, :timestamp, :tx_hash
            )
        """, trade)
        touch_wallet_activity(conn, trade['wallet_address'], trade['timestamp'])
        return True
    except sqlite3.IntegrityError:
        return False


def get_latest_trade_ts(conn: sqlite3.Connection, address: str) -> float:
    row = conn.execute(
        "SELECT MAX(timestamp) as ts FROM trades WHERE wallet_address=?",
        (address,),
    ).fetchone()
    return row["ts"] if row and row["ts"] else 0.0


# ── Paper Trading Operations ──────────────────────────────────────────────

def open_paper_position(conn: sqlite3.Connection, pos: dict) -> int:
    cur = conn.execute("""
        INSERT INTO paper_positions (
            wallet_address, market_slug, market_question, outcome,
            side, entry_price, size_usd, kelly_fraction, opened_at
        ) VALUES (
            :wallet_address, :market_slug, :market_question, :outcome,
            :side, :entry_price, :size_usd, :kelly_fraction, :opened_at
        )
    """, pos)
    return cur.lastrowid


def close_paper_position(conn: sqlite3.Connection, pos_id: int,
                          exit_price: float, pnl: float):
    conn.execute("""
        UPDATE paper_positions
        SET status='closed', closed_at=?, exit_price=?, pnl=?
        WHERE id=?
    """, (time.time(), exit_price, pnl, pos_id))


def get_open_positions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM paper_positions WHERE status='open'"
    ).fetchall()
    return [dict(r) for r in rows]


def get_paper_stats(conn):
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total_trades,
          SUM(CASE WHEN status='closed' AND pnl > 0 THEN 1 ELSE 0 END) AS wins,
          SUM(CASE WHEN status='closed' AND pnl < 0 THEN 1 ELSE 0 END) AS losses,
          SUM(CASE WHEN status='closed' AND pnl = 0 THEN 1 ELSE 0 END) AS flats,
          COALESCE(SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END),0) AS total_pnl
        FROM paper_positions
        """
    ).fetchone()

    total = int(row["total_trades"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    flats = int(row["flats"] or 0)
    total_pnl = float(row["total_pnl"] or 0.0)

    closed = wins + losses + flats
    win_rate = (wins / closed) if closed > 0 else 0.0

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
    }


# --- ledger snapshot (required by order_outbox / paper execution pipeline) ---
def snapshot_ledger(conn, bankroll=None, timestamp=None, **_):
    """
    DB-truth ledger snapshot:
      cash_free (bankroll) comes from caller (paper engine cash)
      open_positions, realized_pnl, win_rate, closed_trades come from SQLite truth
      equity = cash_free + locked_usd (reported via total_pnl as realized only unless you add MTM)
    """
    import time as _t

    ts = float(timestamp) if timestamp is not None else _t.time()
    cash_free = float(bankroll) if bankroll is not None else 0.0

    # locked + open count
    row = conn.execute(
        "SELECT COUNT(*) as open_cnt, COALESCE(SUM(size_usd),0) as locked "
        "FROM paper_positions WHERE lower(status)='open'"
    ).fetchone()
    open_cnt = int(row[0] if row is not None else 0)
    locked = float(row[1] if row is not None else 0.0)

    # realized pnl + closed stats
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, "
        "SUM(CASE WHEN pnl<0 THEN 1 ELSE 0 END) as losses, "
        "COUNT(*) as closed_cnt, "
        "COALESCE(SUM(pnl),0) as realized "
        "FROM paper_positions WHERE lower(status)='closed'"
    ).fetchone()
    wins = int(row[0] or 0)
    losses = int(row[1] or 0)
    closed_cnt = int(row[2] or 0)
    realized = float(row[3] or 0.0)
    win_rate = (100.0 * wins / closed_cnt) if closed_cnt else 0.0

    # total_pnl here = realized only (unrealized requires mark-to-market)
    total_pnl = realized
    num_trades = closed_cnt

    conn.execute(
        "INSERT INTO paper_ledger (timestamp, bankroll, open_positions, total_pnl, num_trades, win_rate) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, cash_free, open_cnt, total_pnl, num_trades, win_rate),
    )
    conn.commit()
