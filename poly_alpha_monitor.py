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
import os
import time
import logging
from pathlib import Path
from datetime import datetime
import dbtruth

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
SCAN_REFRESH_EVERY_SEC = getattr(config, 'SCAN_REFRESH_EVERY_SEC', 21600)
SCAN_LIMIT = getattr(config, 'SCAN_LIMIT', 1000)

# --- SIMPLE DB TRUTH SNAPSHOT ---
