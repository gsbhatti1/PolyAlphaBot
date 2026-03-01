import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "paper_trader.py"

s = TARGET.read_text(encoding="utf-8")

def must_find(pattern: str, text: str):
    if re.search(pattern, text, flags=re.DOTALL) is None:
        raise SystemExit(f"[FAIL] pattern not found: {pattern[:120]}...")

print("[*] Loaded", TARGET)

# 1) Fix the unconditional quote_missing skip (this currently forces return -1 before bid/ask use)
# This exact substring exists in your file (minified format).
quote_missing_block = (
    'self.last_skip_reason = "quote_missing" '
    'self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason) '
    'return -1 '
)
if quote_missing_block in s:
    s = s.replace(quote_missing_block, "")
    print("[+] Removed quote_missing unconditional return block")
else:
    print("[!] quote_missing unconditional block not found (maybe already fixed)")

# 2) Insert _update_outbox helper right after _log_outbox method
must_find(r"def _log_outbox\(.*?\):", s)

if "def _update_outbox" not in s:
    insert_after = r"def _log_outbox\(mode, market_slug, side, outcome, order_type, size_usd, status, error=None, payload=\"\{\}\"\):.*?except Exception: return None"
    must_find(insert_after, s)

    update_fn = (
        " def _update_outbox(self, outbox_id: int, status: str, error: str | None = None, payload: str | None = None):"
        " \"\"\"Update a single outbox row in-place (attempt -> skipped/filled/error/opened).\"\"\""
        " try:"
        " now = time.time()"
        " if payload is None:"
        " self.conn.execute(\"UPDATE order_outbox SET status=?, error=?, updated_ts=? WHERE id=?\", (status, error, now, int(outbox_id)))"
        " else:"
        " self.conn.execute(\"UPDATE order_outbox SET status=?, error=?, payload=?, updated_ts=? WHERE id=?\", (status, error, payload, now, int(outbox_id)))"
        " self.conn.commit()"
        " except Exception:"
        " pass"
    )

    s = re.sub(insert_after, lambda m: m.group(0) + update_fn, s, flags=re.DOTALL)
    print("[+] Inserted _update_outbox helper")
else:
    print("[!] _update_outbox already exists")

# 3) Make attempt return an outbox_id we can update
attempt_old = 'self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="attempt")'
attempt_new = 'outbox_id = self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="attempt")'
if attempt_old in s:
    s = s.replace(attempt_old, attempt_new, 1)
    print("[+] attempt now captures outbox_id")
else:
    print("[!] attempt log line not found (maybe already changed)")

# 4) After attempt, every skip should UPDATE that same outbox row (not INSERT a new one)
# Replace the known skip patterns after attempt.
repls = [
    (
        'self.last_skip_reason = "duplicate_open_position" self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason) return -1',
        'self.last_skip_reason = "duplicate_open_position" self._update_outbox(outbox_id, "skipped", error=self.last_skip_reason) return -1'
    ),
    (
        'self.last_skip_reason = "cap_max_open_positions" throttle_sec = int(getattr(config, \'CAP_THROTTLE_SEC\', 60)) self.pos_block_until = time.time() + throttle_sec self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason) return -1',
        'self.last_skip_reason = "cap_max_open_positions" throttle_sec = int(getattr(config, \'CAP_THROTTLE_SEC\', 60)) self.pos_block_until = time.time() + throttle_sec self._update_outbox(outbox_id, "skipped", error=self.last_skip_reason) return -1'
    ),
    (
        'self.last_skip_reason = f"cap_max_open_exposure exp={exp_open:.2f} add={float(sizing[\'size_usd\']):.2f} max={max_open_exp:.2f}" self.cap_block_until = time.time() + 60 self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason) return -1',
        'self.last_skip_reason = f"cap_max_open_exposure exp={exp_open:.2f} add={float(sizing[\'size_usd\']):.2f} max={max_open_exp:.2f}" self.cap_block_until = time.time() + 60 self._update_outbox(outbox_id, "skipped", error=self.last_skip_reason) return -1'
    ),
    (
        'self.last_skip_reason = "duplicate_unique_index" self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason) return -1',
        'self.last_skip_reason = "duplicate_unique_index" self._update_outbox(outbox_id, "skipped", error=self.last_skip_reason) return -1'
    ),
]
for a,b in repls:
    if a in s:
        s = s.replace(a,b)
        print("[+] Updated a skip path to UPDATE outbox_id")
    else:
        print("[!] Skip pattern not found (might be ok):", a[:70], "...")

# 5) Replace the "select latest outbox by slug + silent fill insert" with correct fills insert (with outbox_id) + update outbox row
# Old block contains: SELECT id FROM order_outbox WHERE market_slug=? ORDER BY id DESC LIMIT 1 ... INSERT INTO fills (...) ... UPDATE order_outbox SET status='filled' WHERE id=? ... except: pass
must_find(r"try: with db\.transaction\(self\.conn\):", s)

fill_old_regex = r"with db\.transaction\(self\.conn\): pos_id = db\.open_paper_position\(self\.conn, pos\) # log fill try: row_o = self\.conn\.execute\( \"SELECT id FROM order_outbox WHERE market_slug=\? ORDER BY id DESC LIMIT 1\", \(pos\[\"market_slug\"\],\) \)\.fetchone\(\) order_id = int\(row_o\[\"id\"\]\) if row_o and row_o\[\"id\"\] else None self\.conn\.execute\( \"INSERT INTO fills \(ts, order_id, market_slug, side, outcome, size_usd, bid, ask, fill_price, slip_bps, fee_usd, notes\) VALUES \(\\\?\,\\\?\,\\\?\,\\\?\,\\\?\,\\\?\,\\\?\,\\\?\,\\\?\,\\\?\,\\\?\,\\\?\\\)\", \(time\.time\(\), order_id, pos\[\"market_slug\"\], pos\[\"side\"\], pos\[\"outcome\"\], float\(pos\[\"size_usd\"\]\), bid, ask, float\(pos\[\"entry_price\"\]\), float\(slip_bps\), float\(fee_usd\), \"sim_fill\"\) \) if order_id: self\.conn\.execute\( \"UPDATE order_outbox SET status='filled' WHERE id=\\\?\", \(order_id,\) \) except Exception: pass"

new_block = (
    "with db.transaction(self.conn): pos_id = db.open_paper_position(self.conn, pos) "
    "# log fill (hard) - tie to outbox_id "
    "if outbox_id: "
    "self.conn.execute("
    "\"INSERT INTO fills (ts, order_id, outbox_id, market_slug, side, outcome, size_usd, bid, ask, fill_price, slip_bps, fee_usd, notes) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)\", "
    "(time.time(), int(outbox_id), int(outbox_id), pos[\"market_slug\"], pos[\"side\"], pos[\"outcome\"], float(pos[\"size_usd\"]), bid, ask, float(pos[\"entry_price\"]), float(slip_bps), float(fee_usd), \"sim_fill\")"
    ") "
    "self._update_outbox(outbox_id, \"filled\") "
)

if re.search(fill_old_regex, s, flags=re.DOTALL):
    s = re.sub(fill_old_regex, new_block, s, flags=re.DOTALL)
    print("[+] Replaced fill logging with outbox_id-tied INSERT + update")
else:
    print("[FAIL] Could not find old fill block to replace. File drifted.")
    raise SystemExit(2)

# 6) Remove the trailing "opened" INSERT outbox row; instead update same outbox row (or do nothing because we set filled)
opened_old = 'self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="opened")'
if opened_old in s:
    s = s.replace(opened_old, 'self._update_outbox(outbox_id, "opened")' , 1)
    print("[+] Replaced opened INSERT with opened UPDATE (usually overridden by filled anyway)")
else:
    print("[!] opened log not found (maybe already removed)")

TARGET.write_text(s, encoding="utf-8")
print("[OK] Patched paper_trader.py")
