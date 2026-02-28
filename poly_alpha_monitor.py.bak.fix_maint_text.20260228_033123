#!/usr/bin/env python3
"""
Polymarket Hidden Alpha Monitor.

Watches discovered wallets for new trades and paper-trades alongside them.

Usage:
    python poly_alpha_monitor.py [OPTIONS]

Options:
    --max-wallets   Max wallets to watch (default: 20)
    --interval      Poll interval in seconds (default: 30)
    --min-size      Min trade size to alert on (default: $50)
    --wallet        Add a specific wallet address to watch
    --bankroll      Starting paper bankroll (default: $10,000)
    --no-paper      Disable paper trading
    --db            SQLite DB path (default: poly_alpha.db)
"""
import asyncio
import json
import signal
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

import click
import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel

import config
import config
print(f"[BOOT] config file: {config.__file__}")
print(f"[BOOT] POLL_INTERVAL={config.POLL_INTERVAL} MAX_WATCH_WALLETS={config.MAX_WATCH_WALLETS} MIN_TRADE_SIZE={config.MIN_TRADE_SIZE}")

import db
import poly_api
import alerts
from paper_trader import PaperTrader

# ── Autonomous maintenance defaults (override in config.py if you want)
DEAD_WALLET_DAYS = getattr(config, 'DEAD_WALLET_DAYS', 5)
MAINTENANCE_EVERY_SEC = getattr(config, 'MAINTENANCE_EVERY_SEC', 3600)
REFILL_FROM_INACTIVE = getattr(config, 'REFILL_FROM_INACTIVE', True)

def telegram_heartbeat(text: str) -> None:
    try:
        print("[telegram] heartbeat: sync", file=sys.stderr)
        alerts.send_telegram_sync(text)
    except Exception as e:
        print(f"[telegram] heartbeat failed: {e!r}", file=sys.stderr)

console = Console()
logger = logging.getLogger('polymarket-bot')
logging.basicConfig(level=logging.INFO)
RUNNING = True


def handle_signal(sig, frame):
    global RUNNING
    RUNNING = False
    console.print("\n[yellow]Shutting down gracefully...[/]")


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def build_dashboard(
    wallets: list[dict],
    recent_trades: list[dict],
    paper: PaperTrader | None,
    poll_count: int,
    last_poll: float,
) -> Panel:
    """Build the live dashboard display."""
    # ── Wallet table
    w_table = Table(
        title="Watched Wallets",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    w_table.add_column("Name", min_width=14)
    w_table.add_column("Alpha", justify="right", width=7)
    w_table.add_column("PnL", justify="right", width=10)
    w_table.add_column("Win%", justify="right", width=7)
    w_table.add_column("Last Trade", justify="right", width=12)

    for w in wallets[:15]:
        last = w.get("last_updated", 0)
        last_str = (
            datetime.fromtimestamp(last).strftime("%m/%d %H:%M")
            if last else "—"
        )
        w_table.add_row(
            (w.get("username") or w["address"][:10]),
            f"{w.get('alpha_score', 0):.3f}",
            f"${w.get('pnl', 0):,.0f}",
            f"{w.get('win_rate', 0):.1%}",
            last_str,
        )

    # ── Recent trades table
    t_table = Table(
        title="Recent Detections",
        show_header=True,
        header_style="bold green",
        expand=True,
    )
    t_table.add_column("Time", width=8)
    t_table.add_column("Wallet", min_width=10)
    t_table.add_column("Market", min_width=20)
    t_table.add_column("Side", width=8)
    t_table.add_column("Size", justify="right", width=10)
    t_table.add_column("Paper", justify="right", width=10)

    for t in recent_trades[-10:]:
        ts = datetime.fromtimestamp(t.get("detected_at", time.time()))
        t_table.add_row(
            ts.strftime("%H:%M:%S"),
            t.get("wallet_name", "?")[:12],
            (t.get("market_question", "?"))[:30],
            t.get("side", "?"),
            f"${float(t.get('size_usd', 0)):,.0f}",
            f"${t.get('paper_size', 0):,.0f}" if t.get("paper_size") else "—",
        )

    # ── Paper trading summary
    paper_text = ""
    if paper:
        s = paper.get_summary()
        pnl_color = "green" if s["total_pnl"] >= 0 else "red"
        ret_color = "green" if s["total_return_pct"] >= 0 else "red"
        paper_text = (
            f"[bold]Paper Portfolio[/]  "
            f"Bankroll: ${s['bankroll']:,.2f}  "
            f"Return: [{ret_color}]{s['total_return_pct']:+.2f}%[/]  "
            f"P&L: [{pnl_color}]${s['total_pnl']:+,.2f}[/]  "
            f"Open: {s['open_positions']}  "
            f"Closed: {s['total_trades']}  "
            f"Win rate: {s['win_rate']:.1%}"
        )

    ago = time.time() - last_poll if last_poll else 0
    status = (
        f"Polls: {poll_count} · Last: {ago:.0f}s ago · "
        f"Watching: {len(wallets)} wallets"
    )

    content = f"{w_table}\n\n{t_table}"
    if paper_text:
        content += f"\n\n{paper_text}"
    content += f"\n\n[dim]{status}[/]"

    return Panel(
        content,
        title="[bold cyan]Polymarket Alpha Monitor[/]",
        border_style="cyan",
    )


async def poll_wallet(
    client: httpx.AsyncClient,
    conn,
    wallet: dict,
    paper: PaperTrader | None,
    min_size: float,
) -> list[dict]:
    """
    Check a wallet for new trades since last check.
    Returns list of new trade dicts.
    """
    addr = wallet["address"]
    last_ts = db.get_latest_trade_ts(conn, addr)

    # Use activity endpoint: GET /activity?user=<addr>
    activity = await poly_api.fetch_activity(client, addr, limit=20)
    if not activity:
        return []

    new_trades = []
    for trade in activity:
        # Only process actual trades
        if trade.get("type") and trade["type"] != "TRADE":
            continue

        ts = trade.get("timestamp", 0)
        try:
            ts = float(ts)
            if ts > 1e12:
                ts /= 1000
        except (ValueError, TypeError):
            continue

        if ts <= last_ts:
            continue

        # Activity fields: usdcSize (USD), size (tokens), price, side, etc.
        size = float(trade.get("usdcSize", trade.get("size", 0)) or 0)
        if size < min_size:
            continue

        tx_hash = trade.get("transactionHash", f"{addr}_{ts}")

        trade_record = {
            "wallet_address": addr,
            "wallet_name": wallet.get("username", addr[:10]),
            "market_slug": trade.get("slug", trade.get("eventSlug", "")),
            "market_question": trade.get("title", ""),
            "outcome": trade.get("outcome", ""),
            "side": trade.get("side", ""),
            "size_usd": size,
            "price": float(trade.get("price", 0) or 0),
            "timestamp": ts,
            "tx_hash": tx_hash,
            "detected_at": time.time(),
        }

        # Insert into DB (dedup by tx_hash)
        is_new = db.insert_trade(conn, trade_record)
        if not is_new:
            continue

        # Paper trade
        paper_size = 0
        if paper:
            alpha = wallet.get("alpha", wallet.get("score", wallet.get("alpha_score")))
            wallet_metrics = {"alpha": alpha}
            sizing = paper.size_trade(wallet_metrics, trade)
            logger.info("[PAPER_DEBUG] sizing=%s wallet_keys=%s", sizing, list(wallet.keys()))
            if sizing:
                paper.open_position(wallet, trade_record, sizing)
                paper_size = sizing["size_usd"]
                trade_record["paper_size"] = paper_size
            else:
                logger.info("[PAPER_DEBUG] sizing is None/false -> skipped paper trade")
        else:
            logger.info("[PAPER_DEBUG] paper trader is OFF (paper=None)")
        # Send alerts
        # Send alerts
        paper_action = {
            "size_usd": paper_size,
            "kelly_fraction": 0,
            "bankroll": paper.bankroll,
        } if paper and paper_size else None

        msg = alerts.format_new_trade_alert(wallet, trade_record, paper_action)

        # Send Telegram alert for the new trade
        try:
            alerts.send_telegram_sync(msg)
        except Exception as e:
            logger.warning(f"[telegram] send trade alert failed: {e!r}")



    return new_trades





async def check_resolutions(
    client: httpx.AsyncClient,
    conn,
    paper: PaperTrader,
):
    """Check if any open paper positions have resolved."""
    open_positions = db.get_open_positions(conn)
    if not open_positions:
        return

    resolved = {}
    checked_slugs = set()

    for pos in open_positions:
        slug = pos["market_slug"]
        if slug in checked_slugs:
            continue
        checked_slugs.add(slug)

        market = await poly_api.fetch_market_by_slug(client, slug)
        if not market:
            continue

        if market.get("closed") or market.get("resolved"):
            result = market.get("result", market.get("outcome"))
            if result is not None:
                try:
                    resolved[slug] = float(result)
                except (ValueError, TypeError):
                    if result in ("Yes", "yes", True, "1"):
                        resolved[slug] = 1.0
                    elif result in ("No", "no", False, "0"):
                        resolved[slug] = 0.0

    if not resolved:
        return

    closed = paper.check_resolutions(resolved) or []
    for c in closed:
        pnl = float(c.get("pnl", 0))
        outcome_tag = "✅ WIN" if pnl >= 0 else "❌ LOSS"
        market_q = (c.get("market_question") or "")[:160]
        side = c.get("side", "?")
        outcome = c.get("outcome", "?")
        size = float(c.get("size_usd", 0))
        entry = c.get("entry_price", "?")
        exitp = c.get("exit_price", "?")
        bankroll = c.get("bankroll", 0)

        text = (
            f"{outcome_tag}  P&L ${pnl:+.2f}\n"
            f"{market_q}\n"
            f"{side} {outcome} | Size ${size:,.0f}\n"
            f"Entry {entry} -> Exit {exitp}\n"
            f"Bankroll: ${bankroll:,.2f}"
        )

        try:
            alerts.send_telegram_sync(text)
        except Exception as e:
            logger.warning(f"[telegram] send win/loss failed: {e!r}")


async def run_monitor(




    max_wallets: int,
    interval: int,
    min_size: float,
    extra_wallets: list[str],
    bankroll: float,
    no_paper: bool,
    db_path: str,
):
    global RUNNING

    conn = db.get_connection(db_path)
    db.init_db(conn)

    # Load wallets from DB (from scanner output)
    wallets = db.get_top_wallets(conn, limit=max_wallets)

    # Add any manually specified wallets
    for addr in extra_wallets:
        if not any(w["address"].lower() == addr.lower() for w in wallets):
            wallets.append({
                "address": addr,
                "username": addr[:10],
                "alpha_score": 0,
                "pnl": 0,
                "win_rate": 0.5,
                "profit_factor": 1.0,
            })

    if not wallets:
        console.print(
            "[red]No wallets to monitor.[/] "
            "Run [cyan]poly_alpha_scanner.py[/] first, or use --wallet to add one."
        )
        return

    paper = None if no_paper else PaperTrader(conn, bankroll)

    console.print(Panel.fit(
        f"[bold cyan]Polymarket Alpha Monitor[/]\n"
        f"Watching {len(wallets)} wallets · "
        f"Poll every {interval}s · "
        f"Min trade ${min_size:,.0f}\n"
        f"Paper trading: {'[green]ON[/]' if paper else '[red]OFF[/]'}"
        + (f" · Bankroll: ${paper.bankroll:,.0f}" if paper else ""),
        border_style="cyan",
    ))

    recent_trades = []
    poll_count = 0
    last_poll = 0.0

    async with httpx.AsyncClient(
        headers={"User-Agent": "PolyAlphaMonitor/1.0"},
        follow_redirects=True,
    ) as client:

        with Live(
            build_dashboard(wallets, recent_trades, paper, poll_count, last_poll),
            console=console,
            refresh_per_second=1,
        ) as live:
              while RUNNING:
                  poll_count += 1
                  if poll_count % 5 == 0:
                      print(f"[heartbeat] polls={poll_count} wallets={len(wallets)}", file=sys.stderr)

                  last_poll = time.time()

                  for wallet in wallets:
                      if not RUNNING:
                          break
                      try:
                          new = await poll_wallet(client, conn, wallet, paper, min_size)
                          recent_trades.extend(new)
                      except Exception:
                          pass

                  recent_trades = recent_trades[-50:]

                  if paper and poll_count % 10 == 0:
                      try:
                          await check_resolutions(client, conn, paper)
                      except Exception:
                          pass

                  conn.commit()

                  # ── Autonomous maintenance (timestamp-based)
                  now = time.time()
                  if now >= next_maintenance_ts:
                      try:
                          pruned, added, active_n = await run_maintenance(conn, max_wallets)
                          conn.commit()
                          wallets = db.get_active_wallets(conn, limit=max_wallets)
                          if pruned or added:
                              text = (
                                  f"🧹 Wallet maintenance
"
                                  f"Pruned: {len(pruned)} (>{DEAD_WALLET_DAYS}d inactive)
"
                                  f"Added: {len(added)}
"
                                  f"Active watching: {len(wallets)}/{max_wallets}"
                              )
                              try:
                                  alerts.send_telegram_sync(text)
                              except Exception:
                                  pass
                      except Exception:
                          pass
                      next_maintenance_ts = now + MAINTENANCE_EVERY_SEC

                  live.update(
                      build_dashboard(wallets, recent_trades, paper, poll_count, last_poll)
                  )

                  if poll_count % 20 == 0:
                      msg = f"🟦 Polymarket bot alive: polls={poll_count}"
                      try:
                          telegram_heartbeat(msg)
                      except Exception as e:
                          logger.exception("telegram_heartbeat failed: %s", e)

                  for _ in range(interval * 2):
                      if not RUNNING:
                          break
                      await asyncio.sleep(0.5)


    # Final summary
    if paper:
        s = paper.get_summary()
        console.print("\n[bold]Paper Trading Final Summary:[/]")
        console.print(f"  Bankroll: ${s['bankroll']:,.2f}")
        console.print(f"  Total P&L: ${s['total_pnl']:+,.2f}")
        console.print(f"  Return: {s['total_return_pct']:+.2f}%")
        console.print(f"  Trades: {s['total_trades']} "
                       f"(W:{s['wins']} / L:{s['losses']})")
        console.print(f"  Win rate: {s['win_rate']:.1%}")

    conn.close()


@click.command()
@click.option("--max-wallets", default=config.MAX_WATCH_WALLETS,
              help="Max wallets to monitor")
@click.option("--interval", default=config.POLL_INTERVAL,
              help="Poll interval in seconds")
@click.option("--min-size", default=config.MIN_TRADE_SIZE,
              help="Min trade size in USD to alert on")
@click.option("--wallet", "extra_wallets", multiple=True,
              help="Additional wallet address(es) to watch")
@click.option("--bankroll", default=config.STARTING_BANKROLL,
              help="Starting paper bankroll")
@click.option("--no-paper", is_flag=True, help="Disable paper trading")
@click.option("--db", "db_path", default=config.DB_PATH,
              help="SQLite database path")
def main(max_wallets, interval, min_size, extra_wallets, bankroll, no_paper, db_path):
    asyncio.run(run_monitor(
        max_wallets, interval, min_size, list(extra_wallets),
        bankroll, no_paper, db_path,
    ))


_start_notice_file = Path("/tmp/polymarket_bot_start_notice")

def send_start_notice_once_per_hour() -> None:
    try:
        if _start_notice_file.exists():
            age = time.time() - _start_notice_file.stat().st_mtime
            if age < 3600:
                print(f"[telegram] start notice skipped (age={int(age)}s)", file=sys.stderr)
                return

        print("[telegram] start notice sending", file=sys.stderr)
        telegram_heartbeat("✅ Polymarket bot started (systemd) — heartbeat OK.")
        _start_notice_file.touch()
    except Exception:
        pass

if __name__ == "__main__":
    send_start_notice_once_per_hour()
    main()
