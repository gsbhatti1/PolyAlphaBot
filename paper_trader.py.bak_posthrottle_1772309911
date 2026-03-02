"""
Paper trading engine.

Mirrors detected trades with Kelly-criterion position sizing.
Tracks a virtual bankroll and P&L.
"""
import time
import random


import db
import config



def _sim_fill_price(side, bid, ask, size_usd, config):
    slip_bps = float(getattr(config, "SIM_SLIP_BPS_BASE", 8)) + float(getattr(config, "SIM_SLIP_BPS_PER_100USD", 4)) * (float(size_usd)/100.0)
    fee_usd = float(size_usd) * (float(getattr(config, "SIM_FEE_BPS", 10))/10000.0)
    if side.upper() == "BUY":
        base = float(ask)
        fill = base * (1.0 + slip_bps/10000.0)
    else:
        base = float(bid)
        fill = base * (1.0 - slip_bps/10000.0)
    return fill, slip_bps, fee_usd

def _sim_latency_sleep(config):
    lo = int(getattr(config, "SIM_LATENCY_MS_MIN", 250))
    hi = int(getattr(config, "SIM_LATENCY_MS_MAX", 1200))
    ms = random.randint(min(lo,hi), max(lo,hi))
    time.sleep(ms/1000.0)
class PaperTrader:
    def __init__(self, conn, bankroll: float | None = None):
        self.conn = conn
        self.bankroll = bankroll or config.STARTING_BANKROLL
        self._load_state()
        self.last_skip_reason = None
        self.cap_block_until = 0
        self._last_throttle_log_ts = 0

    def _load_state(self):
        """Resume from last ledger snapshot if available."""
        row = self.conn.execute(
            "SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            self.bankroll = row["bankroll"]


    def _log_outbox(self, mode, market_slug, side, outcome, order_type, size_usd, status, error=None, payload="{}"):
        """Best-effort outbox logging; must never break execution."""
        try:
            self.conn.execute(
                """
                INSERT INTO order_outbox (ts, mode, market_slug, side, outcome, order_type, size_usd, payload, status, error)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (time.time(), mode, market_slug, side, outcome, order_type, float(size_usd), payload or "{}", status, error),
            )
        except Exception:
            pass

    # ── Kelly Criterion ────────────────────────────────────────────────────

    @staticmethod
    def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Kelly criterion for optimal bet sizing.
          f* = (p * b - q) / b
        where p = win probability, q = 1-p, b = avg_win / avg_loss

        Returns fraction of bankroll to risk (capped at quarter-Kelly).
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0

        p = win_rate
        q = 1.0 - p
        b = avg_win / avg_loss

        full_kelly = (p * b - q) / b
        if full_kelly <= 0:
            return 0.0

        # Use quarter-Kelly for safety
        fraction = full_kelly * config.DEFAULT_KELLY_FRACTION
        # Cap at max position size
        fraction = min(fraction, config.MAX_POSITION_PCT)

        return round(fraction, 4)

    # ── Trade Execution ────────────────────────────────────────────────────
    def size_trade(self, wallet_metrics: dict, trade: dict) -> dict:
        """
        Determine paper trade size based on Kelly criterion.

        Returns dict with sizing info or None if skip.
        """
        # Use wallet "alpha" as a proxy probability if real metrics aren't present.
        alpha = wallet_metrics.get("alpha")
        if alpha is None:
            alpha = wallet_metrics.get("score")

        if isinstance(alpha, (int, float)):
            win_rate = max(0.51, min(float(alpha), 0.75))
        else:
            win_rate = float(wallet_metrics.get("win_rate", 0.55))

        pf = float(wallet_metrics.get("profit_factor", 1.2))

        avg_win = pf
        avg_loss = 1.0

        
        fraction = self.kelly_fraction(win_rate, avg_win, avg_loss)
        if fraction <= 0:
            if getattr(config, 'MIRROR_ALWAYS', False) and self.bankroll >= float(getattr(config,'MIN_TRADE_SIZE',5)):
                return {
                    'kelly_fraction': 0,
                    'size_usd': float(getattr(config,'MIN_TRADE_SIZE',5)),
                    'bankroll': self.bankroll,
                }
            return None

        size_usd = round(self.bankroll * fraction, 2)

        min_trade = float(getattr(config, "MIN_TRADE_SIZE", 5))
        max_trade = float(getattr(config, "MAX_PAPER_TRADE_USD", 50))

        # If Kelly suggests too small but bankroll can support minimum, use minimum
        if size_usd < min_trade:
            if self.bankroll >= min_trade:
                size_usd = min_trade
            else:
                return None

        size_usd = min(size_usd, self.bankroll, max_trade)
        size_usd = round(size_usd, 2)

        if size_usd < min_trade:
            return None

        return {
            "kelly_fraction": fraction,
            "size_usd": size_usd,
            "bankroll": self.bankroll,
        }



    def open_position(self, wallet: dict, trade: dict, sizing: dict) -> int:
        """Open a paper position mirroring a detected trade."""
        self.last_skip_reason = None
        mode = getattr(config, 'EXECUTION_MODE', 'PAPER')
        # cap throttle (prevents outbox spam when exposure cap is hit)
        throttle_sec = int(getattr(config, "CAP_THROTTLE_SEC", 60))
        now = time.time()

        # compute key fields early so we can log throttle skips without writing an 'attempt'
        _slug = trade.get("market_slug", trade.get("slug", trade.get("market", "")))
        _outcome = trade.get("outcome", "")
        _side = trade.get("side", "")
        _size = float(sizing.get("size_usd") or 0)

        if now < getattr(self, "cap_block_until", 0):
            self.last_skip_reason = "cap_throttle_active"
            log_every = int(getattr(config, "THROTTLE_LOG_EVERY_SEC", 60))
            if (now - getattr(self, "_last_throttle_log_ts", 0)) >= log_every:
                self._log_outbox(mode, _slug, _side, _outcome, "MARKET", _size, status="skipped", error=self.last_skip_reason)
                self._last_throttle_log_ts = now
            return -1

        pos = {
            "wallet_address": wallet["address"],
            "market_slug": trade.get("market_slug", trade.get("slug", trade.get("market", ""))),
            "market_question": trade.get("market_question", trade.get("market", "")),
            "outcome": trade.get("outcome", ""),
            "side": trade.get("side", ""),
            "entry_price": float(trade.get("price", 0)),
            "size_usd": sizing["size_usd"],
            "kelly_fraction": sizing["kelly_fraction"],
            "opened_at": time.time(),
        }

        # Log every attempt (even if skipped)
        self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="attempt")

        # Duplicate guard: skip if already open for same market/outcome/side
        row_dup = self.conn.execute(
            "SELECT id FROM paper_positions WHERE (closed_at IS NULL OR closed_at=0) AND market_slug=? AND outcome=? AND side=? LIMIT 1",
            (pos["market_slug"], pos.get("outcome",""), pos.get("side","")),
        ).fetchone()
        if row_dup:
            self.last_skip_reason = "duplicate_open_position"
            self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason)
            return -1

        # ── Institutional risk caps ─────────────────────────────
        max_open_pos = int(getattr(config, "MAX_OPEN_POSITIONS", 10))
        max_open_exp = float(getattr(config, "MAX_OPEN_EXPOSURE_USD", 300))

        # open_positions / exposure from DB
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_usd),0) AS exp FROM paper_positions WHERE closed_at IS NULL OR closed_at=0"
        ).fetchone()
        n_open = int(row["n"]) if row and row["n"] is not None else 0
        exp_open = float(row["exp"]) if row and row["exp"] is not None else 0.0

        if n_open >= max_open_pos:
            self.last_skip_reason = "cap_max_open_positions"
            self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason)
            return -1
        if (exp_open + float(sizing["size_usd"])) > max_open_exp:
            self.last_skip_reason = f"cap_max_open_exposure exp={exp_open:.2f} add={float(sizing['size_usd']):.2f} max={max_open_exp:.2f}"
            self.cap_block_until = time.time() + 60
            self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason)
            return -1

        try:
            with db.transaction(self.conn):
                pos_id = db.open_paper_position(self.conn, pos)
                # NOTE: bankroll represents equity; do NOT subtract on open (prevents fake decay)
                db.snapshot_ledger(self.conn, self.bankroll)
        except Exception as e:
            # If unique index blocks duplicates, just skip silently
            if 'uq_open_pos_market_outcome_side' in str(e) or 'UNIQUE constraint failed' in str(e):
                self.last_skip_reason = "duplicate_unique_index"
                self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="skipped", error=self.last_skip_reason)
                return -1
            raise

        self._log_outbox(mode, pos["market_slug"], pos["side"], pos["outcome"], "MARKET", pos["size_usd"], status="opened")

        return pos_id

    def close_position(self, pos_id: int, exit_price: float):
        """Close a paper position and realize P&L. Returns dict summary or None."""
        row = self.conn.execute(
            "SELECT * FROM paper_positions WHERE id=?", (pos_id,)
        ).fetchone()
        if not row:
            return None

        pos = dict(row)
        entry = float(pos["entry_price"])
        size = float(pos["size_usd"])
        exit_price = float(exit_price)

        if str(pos.get("side", "")).upper() in ("BUY", "YES", "LONG"):
            if exit_price > entry:
                pnl = size * (exit_price - entry) / entry if entry else 0
            else:
                pnl = -size * (entry - exit_price) / entry if entry else -size
        else:
            if exit_price < entry:
                pnl = size * (entry - exit_price) / (1 - entry) if entry < 1 else 0
            else:
                pnl = -size * (exit_price - entry) / (1 - entry) if entry < 1 else -size

        pnl = round(float(pnl), 2)

        with db.transaction(self.conn):
            db.close_paper_position(self.conn, pos_id, exit_price, pnl)
            self.bankroll += size + pnl
            db.snapshot_ledger(self.conn, self.bankroll)

        return {
            "pos_id": pos_id,
            "market_slug": pos.get("market_slug", ""),
            "market_question": pos.get("market_question", ""),
            "outcome": pos.get("outcome", ""),
            "side": pos.get("side", ""),
            "entry_price": entry,
            "exit_price": exit_price,
            "size_usd": size,
            "pnl": pnl,
            "bankroll": float(self.bankroll),
            "wallet_address": pos.get("wallet_address", ""),
        }

    def check_resolutions(self, resolved_markets: dict[str, float]):
        """
        Check open positions against resolved markets.
        Returns list of closed position summaries.
        """
        closed = []
        open_pos = db.get_open_positions(self.conn)
        for pos in open_pos:
            slug = pos["market_slug"]
            if slug in resolved_markets:
                info = self.close_position(pos["id"], resolved_markets[slug])
                if info:
                    closed.append(info)
        return closed

    def get_summary(self) -> dict:
        """Get current paper trading summary."""
        stats = db.get_paper_stats(self.conn)
        open_positions = db.get_open_positions(self.conn)
        open_exposure = sum(p["size_usd"] for p in open_positions)

        return {
            "bankroll": round(self.bankroll, 2),
            "starting_bankroll": config.STARTING_BANKROLL,
            "total_return_pct": round(
                (self.bankroll - config.STARTING_BANKROLL)
                / config.STARTING_BANKROLL * 100, 2
            ),
            "open_positions": len(open_positions),
            "open_exposure": round(open_exposure, 2),
            **stats,
        }