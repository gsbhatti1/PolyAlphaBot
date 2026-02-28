"""
Paper trading engine.

Mirrors detected trades with Kelly-criterion position sizing.
Tracks a virtual bankroll and P&L.
"""
import time

import db
import config


class PaperTrader:
    def __init__(self, conn, bankroll: float | None = None):
        self.conn = conn
        self.bankroll = bankroll or config.STARTING_BANKROLL
        self._load_state()

    def _load_state(self):
        """Resume from last ledger snapshot if available."""
        row = self.conn.execute(
            "SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            self.bankroll = row["bankroll"]

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
            return None

        size_usd = round(self.bankroll * fraction, 2)

        if size_usd < 5:
            return None

        return {
            "kelly_fraction": fraction,
            "size_usd": size_usd,
            "bankroll": self.bankroll,
        }


    def open_position(self, wallet: dict, trade: dict, sizing: dict) -> int:
        """Open a paper position mirroring a detected trade."""
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

        with db.transaction(self.conn):
            pos_id = db.open_paper_position(self.conn, pos)
            self.bankroll -= sizing["size_usd"]
            db.snapshot_ledger(self.conn, self.bankroll)

        return pos_id

    def close_position(self, pos_id: int, exit_price: float):
    """Close a paper position and realize P&L. Returns dict summary or None."""
    row = self.conn.execute(
        "SELECT * FROM paper_positions WHERE id=?", (pos_id,)
    ).fetchone()
    if not row:
        return None

    pos = dict(row)
    entry = pos["entry_price"]
    size = pos["size_usd"]

    if pos["side"].upper() in ("BUY", "YES", "LONG"):
        if exit_price > entry:
            pnl = size * (exit_price - entry) / entry
        else:
            pnl = -size * (entry - exit_price) / entry
    else:
        if exit_price < entry:
            pnl = size * (entry - exit_price) / (1 - entry) if entry < 1 else 0
        else:
            pnl = -size * (exit_price - entry) / (1 - entry) if entry < 1 else -size

    pnl = round(pnl, 2)

    with db.transaction(self.conn):
        db.close_paper_position(self.conn, pos_id, exit_price, pnl)
        self.bankroll += size + pnl
        db.snapshot_ledger(self.conn, self.bankroll)

    # Return info for notifications
    return {
        "pos_id": pos_id,
        "market_slug": pos.get("market_slug", ""),
        "market_question": pos.get("market_question", ""),
        "outcome": pos.get("outcome", ""),
        "side": pos.get("side", ""),
        "entry_price": entry,
        "exit_price": float(exit_price),
        "size_usd": float(size),
        "pnl": float(pnl),
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
