from pathlib import Path
import re

target = Path("paper_trader.py")
code = target.read_text()

# ----------------------------
# PATCH OPEN HOOK
# ----------------------------

open_pattern = r"(INSERT INTO paper_positions[\s\S]+?\)\n\s*\))"

open_hook = """
\\1

        # ---- EXECUTION LEDGER OPEN ----
        _exec_open_insert(self.conn, {
            "wallet_address": wallet_address,
            "market_slug": market_slug,
            "market_question": market_question,
            "outcome": outcome,
            "side": side,
            "size_usd": size_usd,
            "qty": qty,
            "entry_price": fill_price,
            "entry_ts": int(time.time()),
        })
        self.conn.commit()
        # ---- END EXECUTION LEDGER OPEN ----
"""

code = re.sub(open_pattern, open_hook, code, count=1)

# ----------------------------
# PATCH CLOSE HOOK
# ----------------------------

close_pattern = r"(UPDATE paper_positions[\s\S]+?WHERE id=\?\n\s*\))"

close_hook = """
\\1

        # ---- EXECUTION LEDGER CLOSE ----
        _exec_close_update(self.conn, exec_id, exit_price, pnl)
        self.conn.commit()
        # ---- END EXECUTION LEDGER CLOSE ----
"""

code = re.sub(close_pattern, close_hook, code, count=1)

target.write_text(code)

print("LEDGER AUTOPATCH COMPLETE.")
