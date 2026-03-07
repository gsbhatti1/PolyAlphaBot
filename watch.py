#!/usr/bin/env python3
"""
Polymarket Bot — Split Dashboard
  Top half : portfolio / open positions / recent closed  (frozen, refreshes in place)
  Bottom   : live scrolling log
Run: python3 watch.py
"""
import curses, sqlite3, subprocess, time, threading, os, sys
from collections import deque
from datetime import datetime

DB_PATH      = "/home/polymarket/poly_alpha_bot/poly_alpha.db"
REFRESH_SEC  = 4
LOG_BUF      = 200
START_BR     = 1000.0

C_NORMAL  = 0
C_CYAN    = 1
C_GREEN   = 2
C_RED     = 3
C_YELLOW  = 4
C_MAGENTA = 5
C_DIM     = 6
C_WHITE   = 7

def db_connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def fetch_all(conn):
    summary = conn.execute("""
        SELECT
            SUM(CASE WHEN status='open'             THEN 1       ELSE 0 END) AS open_n,
            SUM(CASE WHEN status='open'             THEN size_usd ELSE 0 END) AS locked,
            SUM(CASE WHEN status='closed' AND pnl>0 THEN 1       ELSE 0 END) AS wins,
            SUM(CASE WHEN status='closed' AND pnl<0 THEN 1       ELSE 0 END) AS losses,
            COALESCE(SUM(CASE WHEN status='closed'  THEN pnl     ELSE 0 END),0) AS total_pnl
        FROM paper_positions
    """).fetchone()
    bankroll_row = conn.execute(
        "SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    open_pos = conn.execute("""
        SELECT market_slug, side, outcome, entry_price, size_usd,
               datetime(opened_at,'unixepoch','localtime') AS opened
        FROM paper_positions WHERE status='open'
        ORDER BY opened_at DESC LIMIT 10
    """).fetchall()
    closed = conn.execute("""
        SELECT market_slug, side, entry_price, exit_price, pnl, size_usd,
               datetime(closed_at,'unixepoch','localtime') AS closed
        FROM paper_positions WHERE status='closed'
        ORDER BY closed_at DESC LIMIT 8
    """).fetchall()
    return dict(summary), bankroll_row, open_pos, closed

log_buf    = deque(maxlen=LOG_BUF)
log_lock   = threading.Lock()
log_scroll = [0]

def log_colour(line):
    if "SUPER_INSIDER" in line: return C_MAGENTA
    if "INSIDER"       in line: return C_MAGENTA
    if "ANOMALY"       in line:
        try:
            sc = float(line.split("score=")[1].split()[0])
            if sc >= 7: return C_MAGENTA
            if sc >= 5: return C_YELLOW
            if sc >= 3: return C_CYAN
        except: pass
        return C_CYAN
    if "OPENED" in line or "BUY"   in line: return C_GREEN
    if "CLOSED" in line:                    return C_YELLOW
    if "ERROR"  in line or "error" in line: return C_RED
    if "WARN"   in line:                    return C_YELLOW
    return C_DIM

def tail_logs():
    try:
        proc = subprocess.Popen(
            ["journalctl", "-u", "polymarket-bot", "-f",
             "--no-pager", "--output=short"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                with log_lock:
                    log_buf.append(line)
                    log_scroll[0] = 0
    except Exception:
        pass

def addstr_safe(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w: return
    if x < 0: text = text[-x:]; x = 0
    text = text[:w - x]
    try: win.addstr(y, x, text, attr)
    except curses.error: pass

def hline_safe(win, y, ch, pair=C_CYAN):
    h, w = win.getmaxyx()
    if 0 <= y < h:
        try: win.addstr(y, 0, (ch * w)[:w], curses.color_pair(pair))
        except curses.error: pass

def draw_divider(win, y, title, pair=C_CYAN):
    h, w = win.getmaxyx()
    if y < 0 or y >= h: return
    lbl  = f" {title} " if title else ""
    pad  = (w - len(lbl)) // 2
    line = "─"*pad + lbl + "─"*(w - pad - len(lbl))
    addstr_safe(win, y, 0, line[:w], curses.color_pair(pair))

def wr_bar(wr, width=14):
    filled = int(width * max(0, min(1, wr/100)))
    return "█"*filled + "░"*(width - filled)

def draw_top(win, conn):
    win.erase()
    h, w = win.getmaxyx()
    row  = 0

    try:
        s, br_row, open_pos, closed = fetch_all(conn)
    except Exception as e:
        addstr_safe(win, 0, 0, f"DB error: {e}", curses.color_pair(C_RED))
        win.noutrefresh()
        return

    # header
    title = "  POLYMARKET ALPHA BOT  "
    now   = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    pad   = max(0, (w - len(title)) // 2)
    hline_safe(win, row, "█", C_CYAN); row += 1
    addstr_safe(win, row, pad, title,  curses.color_pair(C_CYAN)|curses.A_BOLD); row += 1
    hline_safe(win, row, "█", C_CYAN); row += 1
    addstr_safe(win, row, 0,
        f"  {now}   refresh {REFRESH_SEC}s   ↑↓ scroll log   q quit",
        curses.color_pair(C_DIM))
    row += 1

    # portfolio
    draw_divider(win, row, "PORTFOLIO"); row += 1

    wins      = int(s.get("wins",      0) or 0)
    losses    = int(s.get("losses",    0) or 0)
    locked    = float(s.get("locked",  0) or 0)
    total_pnl = float(s.get("total_pnl",0) or 0)
    open_n    = int(s.get("open_n",    0) or 0)
    closed_n  = wins + losses
    wr        = (wins / closed_n * 100) if closed_n else 0.0
    bankroll  = float(br_row["bankroll"]) if br_row else (START_BR + total_pnl - locked)
    equity    = bankroll + locked
    ret_pct   = (equity - START_BR) / START_BR * 100

    labels  = ["BANKROLL","EQUITY RTN","REALISED PnL","WIN RATE","W / L","OPEN POS","CLOSED"]
    col_w   = w // len(labels)

    addstr_safe(win, row, 0,
        "".join(l.ljust(col_w) for l in labels),
        curses.color_pair(C_DIM))
    row += 1

    x = 0
    def put(text, pair, bold=False):
        nonlocal x
        a = curses.color_pair(pair) | (curses.A_BOLD if bold else 0)
        addstr_safe(win, row, x, text.ljust(col_w), a)
        x += col_w

    ret_pair = C_GREEN if ret_pct   >= 0  else C_RED
    pnl_pair = C_GREEN if total_pnl >= 0  else C_RED
    wr_pair  = C_GREEN if wr >= 50 else (C_YELLOW if wr >= 35 else C_RED)

    put(f"${bankroll:,.2f}",                                    C_WHITE,   True)
    put(f"{'+' if ret_pct>=0 else ''}{ret_pct:.1f}%",           ret_pair,  True)
    put(f"{'+' if total_pnl>=0 else ''}{total_pnl:.2f}",        pnl_pair,  True)
    put(f"{wr:.1f}%  {wr_bar(wr)}",                             wr_pair,   False)
    put(f"{wins} / {losses}",                                   C_WHITE,   True)
    put(f"{open_n}  ${locked:.0f} locked",                      C_CYAN,    False)
    put(f"{closed_n} trades",                                   C_DIM,     False)
    row += 1

    # open positions
    draw_divider(win, row, "OPEN POSITIONS"); row += 1
    hdr = f"  {'MARKET':<38} {'SIDE':<5} {'OUTCOME':<10} {'ENTRY':>6}  {'SIZE':>7}  OPENED"
    addstr_safe(win, row, 0, hdr[:w], curses.color_pair(C_DIM)); row += 1

    if open_pos:
        for r in open_pos:
            if row >= h: break
            slug    = (r["market_slug"] or "")[:36]
            side    = r["side"] or ""
            outcome = (r["outcome"]     or "")[:8]
            entry   = float(r["entry_price"] or 0)
            size    = float(r["size_usd"]    or 0)
            opened  = (r["opened"] or "")[-8:]
            sp      = C_GREEN if side == "BUY" else C_YELLOW
            line    = f"  {slug:<38} {side:<5} {outcome:<10} {entry:>6.3f}  ${size:>6.2f}  {opened}"
            addstr_safe(win, row, 0, line[:w], curses.color_pair(C_WHITE))
            addstr_safe(win, row, 2+38+1, f"{side:<5}", curses.color_pair(sp)|curses.A_BOLD)
            row += 1
    else:
        addstr_safe(win, row, 2, "no open positions", curses.color_pair(C_DIM)); row += 1

    # recent closed
    draw_divider(win, row, "RECENT CLOSED"); row += 1
    hdr2 = f"  {'MARKET':<36} {'SIDE':<5} {'ENTRY':>6} {'EXIT':>6} {'SIZE':>7}  {'PnL':>9}  CLOSED"
    addstr_safe(win, row, 0, hdr2[:w], curses.color_pair(C_DIM)); row += 1

    if closed:
        for r in closed:
            if row >= h: break
            slug  = (r["market_slug"] or "")[:34]
            side  = r["side"] or ""
            entry = float(r["entry_price"] or 0)
            ex    = float(r["exit_price"]  or 0)
            pnl   = float(r["pnl"]         or 0)
            size  = float(r["size_usd"]    or 0)
            cl    = (r["closed"] or "")[-8:]
            trend = "▲" if ex >= entry else "▼"
            pc    = C_GREEN if pnl >= 0 else C_RED
            pnl_s = f"{'+' if pnl>=0 else ''}{pnl:.2f}"
            line  = (f"  {slug:<36} {side:<5} {entry:>6.3f} {ex:>6.3f} ${size:>6.2f}  "
                     f"{pnl_s:>9}  {trend} {cl}")
            addstr_safe(win, row, 0, line[:w], curses.color_pair(C_WHITE))
            pnl_x = 2+36+1+5+1+6+1+6+1+7+2
            addstr_safe(win, row, pnl_x, f"{pnl_s:>9}", curses.color_pair(pc)|curses.A_BOLD)
            tc = C_GREEN if trend == "▲" else C_RED
            addstr_safe(win, row, pnl_x+10+2, trend, curses.color_pair(tc)|curses.A_BOLD)
            row += 1
    else:
        addstr_safe(win, row, 2, "no closed trades yet", curses.color_pair(C_DIM)); row += 1

    win.noutrefresh()

def draw_bottom(win):
    win.erase()
    h, w = win.getmaxyx()
    draw_divider(win, 0, "LIVE LOG  (↑/↓ scroll  PgUp/PgDn)")

    with log_lock:
        lines = list(log_buf)

    scroll       = log_scroll[0]
    visible_area = h - 1
    total        = len(lines)
    end          = max(total - scroll, 0)
    start        = max(end - visible_area, 0)
    visible      = lines[start:end]

    for i, line in enumerate(visible):
        row = i + 1
        if row >= h: break
        addstr_safe(win, row, 2, line[:w-2], curses.color_pair(log_colour(line)))

    # scroll indicator
    if scroll > 0:
        ind = f" ↑ scrolled {scroll} lines "
        addstr_safe(win, h-1, w-len(ind)-2, ind, curses.color_pair(C_YELLOW))

    win.noutrefresh()

def main(stdscr):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_CYAN,    curses.COLOR_CYAN,    -1)
    curses.init_pair(C_GREEN,   curses.COLOR_GREEN,   -1)
    curses.init_pair(C_RED,     curses.COLOR_RED,     -1)
    curses.init_pair(C_YELLOW,  curses.COLOR_YELLOW,  -1)
    curses.init_pair(C_MAGENTA, curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_DIM,     8,                    -1)
    curses.init_pair(C_WHITE,   curses.COLOR_WHITE,   -1)

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(200)

    # seed with history
    try:
        result = subprocess.run(
            ["journalctl", "-u", "polymarket-bot",
             "--no-pager", "-n", "80", "--output=short"],
            capture_output=True, text=True, timeout=5
        )
        with log_lock:
            for ln in result.stdout.strip().split("\n"):
                if ln: log_buf.append(ln)
    except Exception:
        pass

    threading.Thread(target=tail_logs, daemon=True).start()

    conn         = db_connect()
    last_refresh = 0

    while True:
        H, W = stdscr.getmaxyx()
        top_h = max(8,  int(H * 0.60))
        bot_h = max(5,  H - top_h)

        top_win = curses.newwin(top_h, W, 0,     0)
        bot_win = curses.newwin(bot_h, W, top_h, 0)

        now = time.time()
        if now - last_refresh >= REFRESH_SEC:
            try:
                conn = db_connect()
                draw_top(top_win, conn)
                conn.close()
            except Exception as e:
                addstr_safe(top_win, 0, 0, f"error: {e}", curses.color_pair(C_RED))
                top_win.noutrefresh()
            last_refresh = now

        draw_bottom(bot_win)
        curses.doupdate()

        try:    key = stdscr.getch()
        except: key = -1

        if key in (ord('q'), ord('Q')):
            break
        elif key == curses.KEY_UP:
            log_scroll[0] = min(log_scroll[0] + 1, max(0, len(log_buf)-1))
        elif key == curses.KEY_DOWN:
            log_scroll[0] = max(log_scroll[0] - 1, 0)
        elif key == curses.KEY_PPAGE:
            log_scroll[0] = min(log_scroll[0] + 10, max(0, len(log_buf)-1))
        elif key == curses.KEY_NPAGE:
            log_scroll[0] = max(log_scroll[0] - 10, 0)

        time.sleep(0.05)

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}"); sys.exit(1)
    curses.wrapper(main)
    print("Dashboard closed.")
