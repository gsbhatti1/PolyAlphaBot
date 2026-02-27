#!/usr/bin/env python3
"""
POLYMKT ALPHA SCANNER - ULTRA AGGRESSIVE MODE
"""

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('poly_alpha_aggressive.log'),
        logging.StreamHandler()
    ]
)

import asyncio
import json
import time
import random

import click
import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel

class AggressiveConfig:
    DEFAULT_SCAN_LIMIT = 500
    MIN_PNL = 50
    MIN_TRADES = 3
    MIN_WIN_RATE = 0.51
    MAX_AVG_BET = 1000000
    MIN_ACTIVE_DAYS = 1
    TOP_VOLUME_EXCLUDE = 100
    REQUEST_DELAY = 0.1
    MAX_RETRIES = 1
    SCORE_WEIGHTS = {
        "pnl_magnitude": 0.30,
        "win_rate": 0.25,
        "profit_factor": 0.20,
        "sharpe_ratio": 0.10,
        "consistency": 0.05,
        "recency": 0.05,
        "inverse_visibility": 0.05,
    }
    DB_PATH = "poly_alpha_aggressive.db"
    OUTPUT_FILE = "ultra_alpha_wallets.json"

import config
import db
import poly_api

console = Console()
logger = logging.getLogger(__name__)

def ultra_score_pnl(pnl: float) -> float:
    if pnl <= 0:
        return 0.0
    log_pnl = max(0, pnl / 100)
    return min(1.0, log_pnl)

def ultra_score_win_rate(wr: float) -> float:
    return max(0.0, min(1.0, (wr - 0.5) * 10))

class WalletMetrics:
    def __init__(self, address):
        self.address = address
        self.username = ""
        self.pnl = 0.0
        self.num_trades = 0
        self.win_rate = 0.0
        self.profit_factor = 0.0
        self.sharpe_ratio = 0.0
        self.consistency = 0.0
        self.recency_score = 0.0
        self.visibility = 0.0
        self.first_trade_ts = 0.0
        self.last_trade_ts = 0.0
        self.alpha_score = 0.0

def ultra_compute_metrics(address: str, leaderboard_entry: dict, trades: list, profile: dict):
    m = WalletMetrics(address)
    
    m.username = leaderboard_entry.get("userName", "") or address[:10]
    m.pnl = float(leaderboard_entry.get("pnl", 0) or 0)
    
    trade_entries = [t for t in trades if t.get("type", "TRADE") == "TRADE"]
    m.num_trades = len(trade_entries)
    
    if m.pnl > 0 and m.num_trades > 0:
        m.win_rate = min(0.95, 0.5 + (m.pnl / (m.num_trades * 100)))
    
    timestamps = []
    for t in trade_entries:
        ts = t.get("timestamp", 0)
        try:
            ts = float(ts)
            if ts > 1e12:
                ts /= 1000
            if ts > 0:
                timestamps.append(ts)
        except (ValueError, TypeError):
            pass
    
    if timestamps:
        m.first_trade_ts = min(timestamps)
        m.last_trade_ts = max(timestamps)
    
    m.recency_score = 1.0 if time.time() - m.last_trade_ts < 86400*7 else 0.5
    m.visibility = 0.5
    m.profit_factor = min(10.0, max(1.0, m.pnl / max(1, m.num_trades)))
    m.sharpe_ratio = min(5.0, m.win_rate * 3)
    m.consistency = 0.7
    
    return m

def ultra_compute_alpha_score(metrics) -> float:
    components = {
        "pnl_magnitude": ultra_score_pnl(metrics.pnl),
        "win_rate": ultra_score_win_rate(metrics.win_rate),
        "profit_factor": min(1.0, metrics.profit_factor / 5.0),
        "sharpe_ratio": min(1.0, metrics.sharpe_ratio / 3.0),
        "consistency": metrics.consistency,
        "recency": metrics.recency_score,
        "inverse_visibility": 1.0 - metrics.visibility,
    }
    
    score = sum(AggressiveConfig.SCORE_WEIGHTS[k] * components[k] for k in AggressiveConfig.SCORE_WEIGHTS)
    return round(max(0.0, min(1.0, score)), 4)

def ultra_passes_filters(metrics, top_volume_addresses: set) -> tuple[bool, str]:
    if metrics.pnl < AggressiveConfig.MIN_PNL:
        return False, f"PnL ${metrics.pnl:.0f} < ${AggressiveConfig.MIN_PNL}"
    
    if metrics.num_trades < AggressiveConfig.MIN_TRADES:
        return False, f"Trades {metrics.num_trades} < {AggressiveConfig.MIN_TRADES}"
    
    if metrics.win_rate < AggressiveConfig.MIN_WIN_RATE:
        return False, f"Win rate {metrics.win_rate:.2%} < {AggressiveConfig.MIN_WIN_RATE:.0%}"
    
    if metrics.address.lower() in top_volume_addresses:
        return False, "Top volume wallet"
    
    active_days = (metrics.last_trade_ts - metrics.first_trade_ts) / 86400 if metrics.first_trade_ts else 0
    if active_days < AggressiveConfig.MIN_ACTIVE_DAYS:
        return False, f"Active {active_days:.0f} days < {AggressiveConfig.MIN_ACTIVE_DAYS}"
    
    return True, ""

async def run_ultra_scan(limit: int, output_path: str, db_path: str):
    config.DEFAULT_SCAN_LIMIT = AggressiveConfig.DEFAULT_SCAN_LIMIT
    config.MIN_PNL = AggressiveConfig.MIN_PNL
    config.MIN_TRADES = AggressiveConfig.MIN_TRADES
    config.MIN_WIN_RATE = AggressiveConfig.MIN_WIN_RATE
    config.REQUEST_DELAY = AggressiveConfig.REQUEST_DELAY
    config.MAX_RETRIES = AggressiveConfig.MAX_RETRIES
    config.DB_PATH = db_path
    
    conn = db.get_connection(db_path)
    db.init_db(conn)

    console.print(Panel.fit(
        "[bold red]POLYMKT ALPHA SCANNER - ULTRA AGGRESSIVE MODE[/]\n"
        f"[yellow]Scanning {limit} wallets · Maximum opportunity capture",
        border_style="red"
    ))

    async with httpx.AsyncClient(
        headers={"User-Agent": "PolyAlphaScanner/ULTRA-AGGRESSIVE"},
        follow_redirects=True,
    ) as client:

        console.print("\n[bold red]PHASE 1:[/] Maximum leaderboard extraction...")

        with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                      console=console) as progress:
            task = progress.add_task("Extracting maximum PnL data...", total=None)
            leaderboard = await poly_api.fetch_leaderboard_paginated(
                client, total=limit, time_period="ALL", order_by="PNL"
            )
            progress.update(task, description=f"Snagged {len(leaderboard)} entries")

            progress.add_task("Getting volume exclusions (minimal)...", total=None)
            top_volume = await poly_api.fetch_top_volume_addresses(
                client, top_n=AggressiveConfig.TOP_VOLUME_EXCLUDE
            )

        console.print(f"  Raw entries captured: [red]{len(leaderboard)}[/]")
        console.print(f"  Volume exclusions: [yellow]{len(top_volume)}[/] addresses")

        if not leaderboard:
            console.print("[red]NO DATA CAPTURED. Check API access.[/]")
            return

        console.print(f"\n[bold red]PHASE 2:[/] Ultra-fast scanning {len(leaderboard)} targets...")

        all_metrics = []
        filtered_reasons: dict[str, int] = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Rapid scanning...", total=len(leaderboard))

            for entry in leaderboard:
                addr = (entry.get("proxyWallet") or "").strip()
                if not addr:
                    progress.advance(task)
                    continue

                name = entry.get("userName", addr[:10])
                progress.update(task, description=f"Grabbing {name}...")
                
                profile, trades = None, []
                retry_count = 0
                
                while retry_count < 3:
                    try:
                        profile_coro = poly_api.fetch_profile(client, addr)
                        activity_coro = poly_api.fetch_all_activity(client, addr)
                        profile, trades = await asyncio.gather(
                            profile_coro, activity_coro,
                            return_exceptions=True
                        )
                        
                        if isinstance(profile, Exception):
                            profile = None
                        if isinstance(trades, Exception):
                            trades = []
                            
                        break
                    except Exception as e:
                        retry_count += 1
                        if retry_count >= 3:
                            break
                        await asyncio.sleep(0.5 * retry_count)

                try:
                    metrics = ultra_compute_metrics(addr, entry, trades, profile)
                    
                    passed, reason = ultra_passes_filters(metrics, top_volume)
                    if not passed:
                        bucket = reason.split()[0] if reason else "Unknown"
                        filtered_reasons[bucket] = filtered_reasons.get(bucket, 0) + 1
                        progress.advance(task)
                        continue

                    metrics.alpha_score = ultra_compute_alpha_score(metrics)
                    all_metrics.append(metrics)
                    
                except Exception as e:
                    filtered_reasons["MetricsError"] = filtered_reasons.get("MetricsError", 0) + 1
                
                progress.advance(task)
                
                if random.random() < 0.1:
                    await asyncio.sleep(0.01)

        console.print(f"\n[bold red]PHASE 3:[/] Maximum capture and ranking...")

        try:
            all_metrics.sort(key=lambda m: (m.pnl, m.alpha_score), reverse=True)

            with db.transaction(conn):
                for m in all_metrics:
                    db.upsert_wallet(conn, {
                        "address": m.address,
                        "username": m.username,
                        "alpha_score": m.alpha_score,
                        "pnl": m.pnl,
                        "win_rate": m.win_rate,
                        "num_trades": m.num_trades,
                        "profit_factor": m.profit_factor,
                        "sharpe_ratio": m.sharpe_ratio,
                        "consistency": m.consistency,
                        "recency_score": m.recency_score,
                        "visibility": m.visibility,
                        "avg_bet_size": 0,
                        "markets_traded": 0,
                        "first_seen": m.first_trade_ts,
                        "last_updated": m.last_trade_ts,
                        "meta": {}
                    })
            
            output_data = []
            for m in all_metrics:
                output_data.append({
                    "address": m.address,
                    "username": m.username,
                    "alpha_score": m.alpha_score,
                    "pnl": m.pnl,
                    "win_rate": m.win_rate,
                    "num_trades": m.num_trades,
                    "profit_factor": m.profit_factor,
                    "sharpe_ratio": m.sharpe_ratio,
                    "consistency": m.consistency,
                    "recency_score": m.recency_score,
                    "visibility": m.visibility,
                    "avg_bet_size": 0,
                    "markets_traded": 0,
                    "first_seen": m.first_trade_ts,
                    "last_updated": m.last_trade_ts,
                })
                
            with open(output_path, "w") as f:
                json.dump(output_data, f, indent=2)

        except Exception as e:
            logger.error(f"Save failure: {e}")
            console.print(f"[red]Save error: {e}[/]")
            return

        console.print(f"\n[bold red]PHASE 4:[/] ULTRA RESULTS\n")

        if filtered_reasons:
            filter_table = Table(title="Ultra Filter Results", show_header=True)
            filter_table.add_column("Reason", style="yellow")
            filter_table.add_column("Count", justify="right")
            for reason, count in sorted(filtered_reasons.items(), key=lambda x: -x[1]):
                filter_table.add_row(reason, str(count))
            filter_table.add_row("[bold]QUALIFIED", f"[bold red]{len(all_metrics)}")
            console.print(filter_table)

        if all_metrics:
            console.print()
            top_table = Table(
                title=f"ULTRA ALPHA TARGETS ({len(all_metrics)} captured)",
                show_header=True,
                header_style="bold red",
            )
            top_table.add_column("#", justify="right", width=3)
            top_table.add_column("Wallet", min_width=15)
            top_table.add_column("Score", justify="right")
            top_table.add_column("PnL", justify="right")
            top_table.add_column("Win%", justify="right")
            top_table.add_column("Trades", justify="right")
            
            sorted_metrics = sorted(all_metrics, key=lambda m: (-m.pnl, -m.alpha_score))
            
            for i, m in enumerate(sorted_metrics[:50], 1):
                pnl_color = "green" if m.pnl > 1000 else "yellow" if m.pnl > 100 else "white"
                score_color = "red" if m.alpha_score > 0.7 else "yellow" if m.alpha_score > 0.4 else "white"
                top_table.add_row(
                    str(i),
                    m.username[:12] if m.username else m.address[:10],
                    f"[{score_color}]{m.alpha_score:.3f}[/]",
                    f"[{pnl_color}]${m.pnl:,.0f}[/]",
                    f"{m.win_rate:.0%}",
                    str(m.num_trades),
                )

            console.print(top_table)

        console.print(f"\n  Results saved to [red]{output_path}[/]")
        console.print(f"  Database: [red]{db_path}[/]")
        console.print(f"  Targets acquired: [red]{len(all_metrics)}[/]")
        console.print(f"\n  NEXT: [bold red]python poly_alpha_monitor.py --wallets {output_path}[/]")

    conn.close()

@click.command()
@click.option("--limit", default=AggressiveConfig.DEFAULT_SCAN_LIMIT, help="Maximum leaderboard wallets to scan")
@click.option("--output", default=AggressiveConfig.OUTPUT_FILE, help="JSON output path")
@click.option("--db", "db_path", default=AggressiveConfig.DB_PATH, help="SQLite database path")
def main(limit, output, db_path):
    try:
        asyncio.run(run_ultra_scan(limit, output, db_path))
    except KeyboardInterrupt:
        console.print("\n[yellow]ULTRA SCAN TERMINATED BY USER[/]")
    except Exception as e:
        logger.error(f"CATASTROPHIC FAILURE: {e}")
        console.print(f"[red]FATAL: {e}[/]")

if __name__ == "__main__":
    main()
