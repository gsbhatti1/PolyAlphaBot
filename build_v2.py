import os
from pathlib import Path

BASE = Path("v2_engine")
BASE.mkdir(exist_ok=True)

FILES = {
    "edge_engine.py": """
MIN_EDGE = 0.05

def estimate_true_prob(wallet_score, market_price):
    confidence_boost = min(wallet_score / 100, 0.10)
    return min(1.0, market_price + confidence_boost)

def compute_edge(true_prob, market_price):
    return true_prob - market_price

def edge_is_valid(wallet_score, market_price):
    true_prob = estimate_true_prob(wallet_score, market_price)
    edge = compute_edge(true_prob, market_price)
    return edge >= MIN_EDGE, edge
""",

    "risk_engine.py": """
MAX_RISK_PER_TRADE = 0.02
MAX_THEME_EXPOSURE = 0.05
MAX_TOTAL_EXPOSURE = 0.15
SOFT_DRAWDOWN = 0.10
HARD_DRAWDOWN = 0.20

def approve_trade(bankroll, proposed_size, drawdown,
                  total_exposure, theme_exposure):

    if drawdown >= HARD_DRAWDOWN:
        return False, "HARD_DRAWDOWN"

    if total_exposure >= bankroll * MAX_TOTAL_EXPOSURE:
        return False, "TOTAL_EXPOSURE_LIMIT"

    if theme_exposure >= bankroll * MAX_THEME_EXPOSURE:
        return False, "THEME_EXPOSURE_LIMIT"

    if proposed_size > bankroll * MAX_RISK_PER_TRADE:
        proposed_size = bankroll * MAX_RISK_PER_TRADE

    if drawdown >= SOFT_DRAWDOWN:
        proposed_size *= 0.5

    return True, proposed_size
""",

    "execution_simulator.py": """
import sqlite3
import time

def insert_paper_trade(conn, wallet, slug, outcome,
                       side, price, size_usd):

    conn.execute(\"\"\"
        INSERT INTO paper_positions
        (wallet_address, market_slug, outcome, side,
         entry_price, size_usd, opened_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
    \"\"\", (
        wallet, slug, outcome, side,
        price, size_usd, time.time()
    ))

    conn.commit()
""",

    "main.py": """
import sqlite3
from edge_engine import edge_is_valid
from risk_engine import approve_trade
from execution_simulator import insert_paper_trade

DB = "../poly_alpha.db"

def fetch_recent_signals(conn):
    return conn.execute(\"\"\"
        SELECT t.wallet_address,
               t.market_slug,
               t.outcome,
               t.side,
               t.price,
               w.alpha_score
        FROM trades t
        JOIN wallets w
        ON t.wallet_address = w.address
        WHERE t.timestamp > strftime('%s','now') - 300
    \"\"\").fetchall()

def main():
    conn = sqlite3.connect(DB)
    bankroll = 10000

    signals = fetch_recent_signals(conn)

    for wallet, slug, outcome, side, price, score in signals:

        valid, edge = edge_is_valid(score, price)
        if not valid:
            continue

        proposed_size = bankroll * 0.01

        approved, result = approve_trade(
            bankroll,
            proposed_size,
            drawdown=0,
            total_exposure=0,
            theme_exposure=0
        )

        if approved:
            insert_paper_trade(
                conn, wallet, slug, outcome,
                side, price, result
            )

if __name__ == "__main__":
    main()
"""
}

def build():
    for name, content in FILES.items():
        file_path = BASE / name
        file_path.write_text(content.strip())
        print(f"[CREATED] {file_path}")

    print("\\nV2 ENGINE BOOTSTRAPPED SUCCESSFULLY.")

if __name__ == "__main__":
    build()
