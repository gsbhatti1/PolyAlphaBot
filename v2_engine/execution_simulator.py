import sqlite3
import time

def insert_paper_trade(conn, wallet, slug, outcome,
                       side, price, size_usd):

    conn.execute("""
        INSERT INTO paper_positions
        (wallet_address, market_slug, outcome, side,
         entry_price, size_usd, opened_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
    """, (
        wallet, slug, outcome, side,
        price, size_usd, time.time()
    ))

    conn.commit()