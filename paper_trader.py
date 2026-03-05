"""
Paper trading engine.

Hardening goals:
- Single-row outbox lifecycle: attempt -> skipped/opened/filled/error via UPDATE
- fills tied to outbox_id
- no silent failures (errors are written back to outbox row)
"""
import time
import random
import json
import logging

import db
import config
from config import EXECUTION_MODE, MAX_PAPER_TRADE_USD

try:
    from live_trader import LiveTrader
except Exception:
    LiveTrader = None

logger = logging.getLogger("polymarket-bot")


def _qinfo(q):
    try:
        t = type(q).__name__
        if isinstance(q, (str, bytes)):
            s = q.decode('utf-8','ignore') if isinstance(q, bytes) else q
            s = s.replace('\n',' ').replace('\r',' ')
            return t, s[:160]
        return t, str(q)[:160]
    except Exception as e:
        return 'unknown', f'qinfo_error:{e}'

def _sim_fill_price(side, bid, ask, size_usd, cfg):
    slip_bps = float(getattr(cfg, "SIM_SLIP_BPS_BASE", 8)) + float(getattr(cfg, "SIM_SLIP_BPS_PER_100USD", 4)) * (
        float(size_usd) / 100.0
    )
    fee_usd = float(size_usd) * (float(getattr(cfg, "SIM_FEE_BPS", 10)) / 10000.0)

    if str(side).upper() in ("BUY", "YES", "LONG"):
        base = float(ask)
        fill = base * (1.0 + slip_bps / 10000.0)
    else:
        base = float(bid)
        fill = base * (1.0 - slip_bps / 10000.0)

    return float(fill), float(slip_bps), float(fee_usd)


def _sim_latency_sleep(cfg):
    lo = int(getattr(cfg, "SIM_LATENCY_MS_MIN", 250))
    hi = int(getattr(cfg, "SIM_LATENCY_MS_MAX", 1200))
    ms = random.randint(min(lo, hi), max(lo, hi))
    time.sleep(ms / 1000.0)


live_trader = None
if EXECUTION_MODE == "LIVE" and LiveTrader is not None:
    try:
        live_trader = LiveTrader()
    except Exception as e:
        logger.exception("Failed to init LiveTrader: %s", e)
        live_trader = None  # noqa: F841


class PaperTrader:
    def __init__(self, conn, bankroll: float | None = None):
        self.conn = conn
        self.bankroll = bankroll or config.STARTING_BANKROLL
        self._load_state()
        self.last_skip_reason = None
        self.cap_block_until = 0.0
        self.pos_block_until = 0.0
        self._last_throttle_log_ts = 0.0

    def _load_state(self):
        row = self.conn.execute("SELECT bankroll FROM paper_ledger ORDER BY id DESC LIMIT 1").fetchone()
        if row and row["bankroll"] is not None:
            loaded = float(row["bankroll"])
            # Safety floor: if ledger bankroll is impossibly low (ghost drain from
            # old bugs), don't load it — start fresh from STARTING_BANKROLL instead.
            floor = float(config.STARTING_BANKROLL) * 0.05  # 5% of start = clearly wrong
            if loaded >= floor:
                self.bankroll = loaded
            else:
                import logging as _log
                _log.getLogger("polymarket-bot").warning(
                    "[BANKROLL] Ignoring suspiciously low ledger value %.2f (floor=%.2f), "
                    "starting fresh at %.2f", loaded, floor, self.bankroll
                )

    # ---------- Outbox helpers (single-row lifecycle) ----------

    def _log_outbox(self, mode, market_slug, side, outcome, order_type, size_usd, status, error=None, payload="{}"):
        """
        Insert an outbox row and return its id. This must not break the bot;
        if it fails, return None.
        """
        try:
            cur = self.conn.execute(
                """
                INSERT INTO order_outbox
                  (ts, mode, market_slug, side, outcome, order_type, size_usd, payload, status, error)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    time.time(),
                    mode,
                    str(market_slug or ""),
                    str(side or ""),
                    str(outcome or ""),
                    str(order_type or "MARKET"),
                    float(size_usd or 0.0),
                    payload if payload is not None else "{}",
                    str(status or ""),
                    error,
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid) if cur is not None else None
        except Exception:
            return None

    def _update_outbox(self, outbox_id: int, status: str, error: str | None = None, payload: str | None = None):
        """
        Update same outbox row to final status. Try to set updated_ts if column exists.
        """
        if not outbox_id:
            return
        now = time.time()
        # keep errors compact
        if error is not None and len(error) > 500:
            error = error[:500]

        try:
            if payload is None:
                self.conn.execute(
                    "UPDATE order_outbox SET status=?, error=?, updated_ts=? WHERE id=?",
                    (status, error, now, int(outbox_id)),
                )
            else:
                self.conn.execute(
                    "UPDATE order_outbox SET status=?, error=?, payload=?, updated_ts=? WHERE id=?",
                    (status, error, payload, now, int(outbox_id)),
                )
            self.conn.commit()
            return
        except Exception:
            # fallback if updated_ts doesn't exist
            try:
                if payload is None:
                    self.conn.execute(
                        "UPDATE order_outbox SET status=?, error=? WHERE id=?",
                        (status, error, int(outbox_id)),
                    )
                else:
                    self.conn.execute(
                        "UPDATE order_outbox SET status=?, error=?, payload=? WHERE id=?",
                        (status, error, payload, int(outbox_id)),
                    )
                self.conn.commit()
            except Exception:
                # last-resort: nothing else we can do here
                return

    def _insert_fill(self, *, outbox_id: int, market_slug: str, side: str, outcome: str, size_usd: float,
                     bid: float, ask: float, fill_price: float, slip_bps: float, fee_usd: float, notes: str):
        """
        Insert into fills with outbox_id. If schema doesn't support outbox_id, fallback.
        No silent failures: if this fails, caller must handle and outbox must become error.
        """
        ts = time.time()
        order_id = int(outbox_id) if outbox_id else None

        try:
            self.conn.execute(
                """
                INSERT INTO fills
                  (ts, order_id, outbox_id, market_slug, side, outcome, size_usd, bid, ask, fill_price, slip_bps, fee_usd, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    order_id,
                    int(outbox_id) if outbox_id else None,
                    market_slug,
                    side,
                    outcome,
                    float(size_usd),
                    float(bid),
                    float(ask),
                    float(fill_price),
                    float(slip_bps),
                    float(fee_usd),
                    notes,
                ),
            )
            return
        except Exception:
            # fallback: older schema without outbox_id
            self.conn.execute(
                """
                INSERT INTO fills
                  (ts, order_id, market_slug, side, outcome, size_usd, bid, ask, fill_price, slip_bps, fee_usd, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    order_id,
                    market_slug,
                    side,
                    outcome,
                    float(size_usd),
                    float(bid),
                    float(ask),
                    float(fill_price),
                    float(slip_bps),
                    float(fee_usd),
                    notes,
                ),
            )

    # ---------- Kelly sizing ----------

    @staticmethod
    def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        p = float(win_rate)
        q = 1.0 - p
        b = float(avg_win) / float(avg_loss)
        full_kelly = (p * b - q) / b
        if full_kelly <= 0:
            return 0.0
        fraction = full_kelly * float(getattr(config, "DEFAULT_KELLY_FRACTION", 0.25))
        fraction = min(fraction, float(getattr(config, "MAX_POSITION_PCT", 0.10)))
        return round(float(fraction), 4)

    def size_trade(self, wallet_metrics: dict, trade: dict) -> dict | None:
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
            if getattr(config, "MIRROR_ALWAYS", False) and self.bankroll >= float(getattr(config, "MIN_TRADE_SIZE", 5)):
                return {"kelly_fraction": 0.0, "size_usd": float(getattr(config, "MIN_TRADE_SIZE", 5)), "bankroll": self.bankroll}
            return None

        size_usd = round(self.bankroll * fraction, 2)
        min_trade = float(getattr(config, "MIN_TRADE_SIZE", 5))
        max_trade = float(getattr(config, "MAX_PAPER_TRADE_USD", 50))

        if size_usd < min_trade:
            if self.bankroll >= min_trade:
                size_usd = min_trade
            else:
                return None

        size_usd = min(size_usd, self.bankroll, max_trade)
        size_usd = round(size_usd, 2)
        if size_usd < min_trade:
            return None

        return {"kelly_fraction": fraction, "size_usd": size_usd, "bankroll": self.bankroll}

    # ---------- Execution (single-row outbox lifecycle) ----------

    def open_position(self, wallet: dict, trade: dict, sizing: dict) -> int:
        self.last_skip_reason = 'open_position_returned_None'  # AUTOPATCH
        self.last_skip_reason = None
        mode = getattr(config, "EXECUTION_MODE", "PAPER")
        now = time.time()

        market_slug = trade.get("market_slug", trade.get("slug", trade.get("market", "")))
        outcome = trade.get("outcome", "")
        side = trade.get("side", "")
        size_usd = float(sizing.get("size_usd") or 0.0)

        # throttle (avoid spam): log at intervals as SKIPPED rows (no attempt row each tick)
        throttle_sec = int((getattr(config,"CAP_THROTTLE_SEC",60) or 60))
        log_every = int(getattr(config, "THROTTLE_LOG_EVERY_SEC", 60))

        if now < float(getattr(self, "cap_block_until", 0.0)):
            self.last_skip_reason = "cap_throttle_active"
            if (now - float(getattr(self, "_last_throttle_log_ts", 0.0))) >= log_every:
                self._log_outbox(mode, market_slug, side, outcome, "MARKET", size_usd, status="skipped", error=self.last_skip_reason)
                self._last_throttle_log_ts = now
            return -1

        if now < float(getattr(self, "pos_block_until", 0.0)):
            self.last_skip_reason = "pos_throttle_active"
            if (now - float(getattr(self, "_last_throttle_log_ts", 0.0))) >= log_every:
                self._log_outbox(mode, market_slug, side, outcome, "MARKET", size_usd, status="skipped", error=self.last_skip_reason)
                self._last_throttle_log_ts = now
            return -1

        # ── EARLY duplicate guard — before any API calls or outbox creation ──
        try:
            _dup_early = self.conn.execute(
                """
                SELECT id FROM paper_positions
                WHERE (closed_at IS NULL OR closed_at=0 OR status='open')
                  AND market_slug=? AND outcome=? AND side=?
                LIMIT 1
                """,
                (market_slug, outcome, side),
            ).fetchone()
            if _dup_early:
                self.last_skip_reason = "duplicate_open_position"
                return -1
        except Exception:
            pass

        # create ONE outbox row (attempt) up front
        outbox_id = self._log_outbox(
            mode, market_slug, side, outcome, "MARKET", size_usd,
            status="attempt", error=None, payload="{}"
        )

        try:
            pos = {
                "wallet_address": wallet["address"],
                "market_slug": market_slug,
                "market_question": trade.get("market_question", trade.get("market", "")),
                "outcome": outcome,
                "side": side,
                "entry_price": float(trade.get("price", 0.0)),
                "size_usd": size_usd,
                "kelly_fraction": float(sizing.get("kelly_fraction") or 0.0),
                "opened_at": time.time(),
            }

            # fetch quote
            quote = None
            try:
                import httpx
                import poly_api
                with httpx.Client(timeout=10.0) as http:
                    quote = poly_api.get_quote(http, pos["market_slug"], outcome=pos.get("outcome"))
            except Exception as e:
                self.last_skip_reason = f"quote_fetch_error:{e.__class__.__name__}"
                self._update_outbox(outbox_id, "error", self.last_skip_reason, payload=str(quote))
                return -1

            if not isinstance(quote, dict):
                qtype,qsnip=_qinfo(quote); self.last_skip_reason = f"quote_invalid:not_dict type={qtype} q={qsnip}"
                self._update_outbox(outbox_id, "skipped", self.last_skip_reason, payload=str(quote))
                return -1

            try:
                bid = float(quote.get("bid"))
                ask = float(quote.get("ask"))
            except Exception:
                self.last_skip_reason = "quote_invalid:non_numeric"
                self._update_outbox(outbox_id, "skipped", self.last_skip_reason, payload=json.dumps(quote))
                return -1

            if bid <= 0 or ask <= 0 or ask < bid:
                self.last_skip_reason = f"quote_invalid:bid={bid:.6f},ask={ask:.6f}"
                self._update_outbox(outbox_id, "skipped", self.last_skip_reason, payload=json.dumps(quote))
                return -1

            mid = (bid + ask) / 2.0
            spread = ask - bid

            # store quote snapshot best-effort
            try:
                self.conn.execute(
                    "INSERT INTO quotes (market_slug, ts, bid, ask, mid, spread, source) VALUES (?,?,?,?,?,?,?)",
                    (pos["market_slug"], time.time(), bid, ask, mid, spread, "clob"),
                )
            except Exception:
                pass
            # Defaults so _insert_fill() never hits NameError in LIVE mode
            slip_bps = 0.0
            fee_usd = 0.0

            # LIVE routing: call real CLOB executor instead of simulator
            if mode == "LIVE" and live_trader is not None:
                try:
                    market_id = quote.get("marketId") or quote.get("market_id") or quote.get("id")
                    token_id = quote.get("tokenId") or quote.get("token_id") or quote.get("outcomeId")
                    side_live = str(pos.get("side", "") or side).upper()
                    size_usd_live = float(min(pos.get("size_usd", 0.0) or 0.0, MAX_PAPER_TRADE_USD))

                    if size_usd_live <= 0:
                        self.last_skip_reason = "live_invalid_size"
                        self._update_outbox(outbox_id, "skipped", self.last_skip_reason, payload=json.dumps(quote))
                        return -1

                    live_res = live_trader.open_position(
                        market_id=market_id,
                        token_id=token_id,
                        side=side_live,
                        size_usd=size_usd_live,
                        price=mid,
                    )

                    if not live_res or not live_res.get("order_id"):
                        self.last_skip_reason = "live_order_failed"
                        self._update_outbox(outbox_id, "error", self.last_skip_reason, payload=json.dumps(quote))
                        return -1

                    pos["entry_price"] = float(live_res.get("avg_fill_price", mid))
                    pos["size_usd"] = float(live_res.get("filled_size", size_usd_live))
                except Exception as e:
                    self.last_skip_reason = f"live_exception:{e.__class__.__name__}"
                    self._update_outbox(outbox_id, "error", self.last_skip_reason, payload=json.dumps(quote))
                    return -1
            else:
                _sim_latency_sleep(config)
                fill_price, slip_bps, fee_usd = _sim_fill_price(pos["side"], bid, ask, pos["size_usd"], config)
                pos["entry_price"] = float(fill_price)


            # duplicate guard BEFORE opening
            row_dup = self.conn.execute(
                """
                SELECT id FROM paper_positions
                WHERE (closed_at IS NULL OR closed_at=0 OR status='open')
                  AND market_slug=? AND outcome=? AND side=?
                LIMIT 1
                """,
                (pos["market_slug"], pos.get("outcome", ""), pos.get("side", "")),
            ).fetchone()
            if row_dup:
                self.last_skip_reason = "duplicate_open_position"
                self._update_outbox(outbox_id, "skipped", self.last_skip_reason, payload=json.dumps(quote))
                return -1

            # risk caps
            max_open_pos = int(getattr(config, "MAX_OPEN_POSITIONS", 10))
            max_open_exp = float(getattr(config, "MAX_OPEN_EXPOSURE_USD", 300))

            row = self.conn.execute(
                """
                SELECT COUNT(*) AS n, COALESCE(SUM(size_usd),0) AS exp
                FROM paper_positions
                WHERE (closed_at IS NULL OR closed_at=0 OR status='open')
                """
            ).fetchone()
            n_open = int(row["n"]) if row and row["n"] is not None else 0
            exp_open = float(row["exp"]) if row and row["exp"] is not None else 0.0

            if n_open >= max_open_pos:
                self.last_skip_reason = "cap_max_open_positions"
                self.pos_block_until = time.time() + throttle_sec
                self._update_outbox(outbox_id, "skipped", self.last_skip_reason, payload=json.dumps(quote))
                return -1

            if (exp_open + float(pos["size_usd"])) > max_open_exp:
                self.last_skip_reason = f"cap_max_open_exposure exp={exp_open:.2f} add={float(pos['size_usd']):.2f} max={max_open_exp:.2f}"
                self.cap_block_until = time.time() + throttle_sec
                self._update_outbox(outbox_id, "skipped", self.last_skip_reason, payload=json.dumps(quote))
                return -1

            # open position + write fill in one transaction.
            # IMPORTANT: do NOT touch self.bankroll inside the transaction block.
            # DB transactions roll back on failure but Python memory does not.
            # Deduct only AFTER the with-block exits successfully.
            pos_id = None
            try:
                with db.transaction(self.conn):
                    pos_id = db.open_paper_position(self.conn, pos)
                    self._insert_fill(
                        outbox_id=outbox_id or 0,
                        market_slug=pos["market_slug"],
                        side=pos["side"],
                        outcome=pos["outcome"],
                        size_usd=float(pos["size_usd"]),
                        bid=float(bid),
                        ask=float(ask),
                        fill_price=float(pos["entry_price"]),
                        slip_bps=float(slip_bps),
                        fee_usd=float(fee_usd),
                        notes="sim_fill",
                    )
            except Exception as e:
                # Transaction rolled back — in-memory bankroll is untouched (safe)
                self.last_skip_reason = f"db_error:{e.__class__.__name__}:{e}"
                self._update_outbox(outbox_id, "error", self.last_skip_reason)
                return -1

            # Transaction committed — NOW safe to deduct from in-memory bankroll
            self.bankroll -= float(pos["size_usd"])
            db.snapshot_ledger(self.conn, self.bankroll)

            # lifecycle: opened -> filled (paper fill is immediate)
            self._update_outbox(outbox_id, "opened", None, payload=json.dumps(quote))
            self._update_outbox(outbox_id, "filled", None, payload=json.dumps(quote))
            return int(pos_id)

        except Exception as e:
            # no silent failures
            err = f"{e.__class__.__name__}:{e}"
            self.last_skip_reason = err
            self._update_outbox(outbox_id, "error", err)
            return -1

    # ---------- Close / resolution ----------

    def close_position(self, pos_id: int, exit_price: float):
        row = self.conn.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
        if not row:
            return None
        pos = dict(row)

        entry = float(pos.get("entry_price") or 0.0)
        size = float(pos.get("size_usd") or 0.0)
        exit_price = float(exit_price)

        side = str(pos.get("side", "")).upper()
        if side in ("BUY", "YES", "LONG"):
            pnl = size * (exit_price - entry) / entry if entry else 0.0
        else:
            pnl = size * (entry - exit_price) / (1 - entry) if entry < 1 else 0.0

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
        closed = []
        for pos in db.get_open_positions(self.conn):
            slug = pos["market_slug"]
            if slug in resolved_markets:
                info = self.close_position(pos["id"], resolved_markets[slug])
                if info:
                    closed.append(info)
        return closed



    def auto_close_positions(self) -> int:
        """
        Auto-close paper positions older than AUTO_CLOSE_SEC.

        Exit price priority:
          1. Fresh live quote from Polymarket CLOB API (real market price)
          2. Most recent quote stored in DB for this market
          3. Entry price as last resort (PnL=0 — only if API and DB both fail)
        """
        auto_sec = int(getattr(config, "AUTO_CLOSE_SEC", 0) or 0)
        if auto_sec <= 0:
            return 0

        now = time.time()
        closed = 0

        rows = self.conn.execute(
            """
            SELECT id, market_slug, outcome, entry_price, opened_at
            FROM paper_positions
            WHERE (closed_at IS NULL OR closed_at=0 OR status='open')
            """
        ).fetchall()

        # import here to avoid circular at module level
        try:
            import httpx as _httpx
            import poly_api as _poly_api
        except Exception:
            _httpx = None
            _poly_api = None

        for r in rows:
            try:
                pos_id    = int(r["id"])
                opened_at = float(r["opened_at"] or 0.0)
                if opened_at <= 0:
                    continue
                if (now - opened_at) < auto_sec:
                    continue

                slug  = r["market_slug"]
                entry = float(r["entry_price"] or 0.0)
                exit_price = None

                # ── 1. Try live API quote ─────────────────────────────────
                if _httpx and _poly_api and slug:
                    try:
                        with _httpx.Client(timeout=8.0) as http:
                            q = _poly_api.get_quote(http, slug, outcome=r["outcome"])
                        if q and isinstance(q, dict):
                            mid = q.get("mid")
                            if mid is not None:
                                exit_price = float(mid)
                    except Exception:
                        exit_price = None

                # ── 2. Fall back to most recent DB quote for this market ───
                if exit_price is None and slug:
                    try:
                        row = self.conn.execute(
                            """
                            SELECT mid FROM quotes
                            WHERE market_slug=?
                            ORDER BY ts DESC
                            LIMIT 1
                            """,
                            (slug,),
                        ).fetchone()
                        if row and row["mid"] is not None:
                            exit_price = float(row["mid"])
                    except Exception:
                        pass

                # ── 3. Last resort: entry price (zero PnL) ────────────────
                if exit_price is None:
                    exit_price = entry

                self.close_position(pos_id, exit_price)
                closed += 1

            except Exception:
                continue

        return closed


    def get_summary(self) -> dict:
        stats = db.get_paper_stats(self.conn)
        open_positions = db.get_open_positions(self.conn)
        open_exposure = sum(float(p.get("size_usd") or 0.0) for p in open_positions)
        # --- TRUTH_GUARD: bankroll cannot drift above starting + realized pnl - locked exposure ---
        # If this trips, some logic is crediting principal without debiting it (fake profit).
        realized_pnl = float(stats.get("total_pnl", 0.0) or 0.0)
        locked = float(open_exposure or 0.0)
        expected_max_cash = float(config.STARTING_BANKROLL) + realized_pnl - locked
        if float(self.bankroll) > (expected_max_cash + 1e-6):
            # TRUTH_GUARD: bankroll in memory is ahead of what DB can justify.
            # Most common cause: bot restarted after DB wipe but open positions
            # remain, so DB shows locked exposure that memory doesn't account for.
            # FIX: correct memory bankroll DOWN to match DB reality, then continue.
            try:
                logger.warning(
                    "[TRUTH_GUARD] correcting bankroll %.2f -> %.2f "
                    "(realized_pnl=%.4f locked=%.2f)",
                    float(self.bankroll), expected_max_cash, realized_pnl, locked
                )
            except Exception:
                pass
            self.bankroll = expected_max_cash  # snap memory to DB reality

        # truth-based return: use realized P&L from stats (DB has paper_positions.pnl)
        total_pnl = float(stats.get("total_pnl", stats.get("pnl", 0.0)) or 0.0)
        total_return_pct = round((total_pnl / float(config.STARTING_BANKROLL)) * 100, 2)

        return {
            "bankroll": round(float(self.bankroll or 0.0), 2),
            "starting_bankroll": float(config.STARTING_BANKROLL),
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "open_positions": len(open_positions),
            "open_exposure": round(open_exposure, 2),
            **stats,
        }
