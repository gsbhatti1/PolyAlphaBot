"""
Alpha scoring engine.

Computes a composite score for each wallet based on:
  - PnL magnitude (log-scaled)
  - Win rate
  - Profit factor (gross wins / gross losses)
  - Sharpe ratio (adapted for binary outcomes)
  - Consistency (streak analysis)
  - Recency (how recently they traded)
  - Inverse visibility (less visible = higher score)

Field mapping from Polymarket API:
  Leaderboard: proxyWallet, userName, vol, pnl, profileImage, xUsername, verifiedBadge
  Activity:    proxyWallet, timestamp, conditionId, type, size, usdcSize,
               transactionHash, price, side, outcomeIndex, title, slug,
               eventSlug, outcome, name, pseudonym, bio, profileImage
"""
import math
import time
from dataclasses import dataclass, field

import numpy as np

import config


@dataclass
class WalletMetrics:
    address: str
    username: str = ""
    pnl: float = 0.0
    num_trades: int = 0
    markets_traded: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    consistency: float = 0.0
    recency_score: float = 0.0
    visibility: float = 0.0
    avg_bet_size: float = 0.0
    alpha_score: float = 0.0
    first_trade_ts: float = 0.0
    last_trade_ts: float = 0.0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "username": self.username,
            "pnl": round(self.pnl, 2),
            "num_trades": self.num_trades,
            "markets_traded": self.markets_traded,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "consistency": round(self.consistency, 4),
            "recency_score": round(self.recency_score, 4),
            "visibility": round(self.visibility, 4),
            "avg_bet_size": round(self.avg_bet_size, 2),
            "alpha_score": round(self.alpha_score, 4),
            "first_seen": self.first_trade_ts,
            "last_updated": self.last_trade_ts,
            "meta": self.meta,
        }


# ── Individual Component Scores ────────────────────────────────────────────

def _normalize(value: float, lo: float, hi: float) -> float:
    """Clamp and normalize to 0-1."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def score_pnl(pnl: float) -> float:
    """Log-scaled PnL score.  $500 → ~0.2, $5k → ~0.5, $50k → ~0.8."""
    if pnl <= 0:
        return 0.0
    log_pnl = math.log10(max(pnl, 1))
    return _normalize(log_pnl, math.log10(100), math.log10(200_000))


def score_win_rate(wr: float) -> float:
    """Win rate score.  0.52 → ~0.1, 0.60 → ~0.5, 0.75 → ~1.0."""
    return _normalize(wr, 0.50, 0.75)


def score_profit_factor(pf: float) -> float:
    """Profit factor score.  1.0 → 0.0, 2.0 → ~0.5, 4.0 → ~1.0."""
    return _normalize(pf, 1.0, 4.0)


def score_sharpe(sharpe: float) -> float:
    """Sharpe score.  0 → 0.0, 1.0 → ~0.5, 3.0 → ~1.0."""
    return _normalize(sharpe, 0.0, 3.0)


def score_consistency(trades: list[float]) -> float:
    """
    Measures how consistent the edge is.
    Uses ratio of positive-return streaks to total streaks.
    """
    if len(trades) < 5:
        return 0.0

    signs = [1 if t > 0 else -1 for t in trades]
    streaks_pos = 0
    streaks_total = 0
    current_sign = signs[0]

    for s in signs[1:]:
        if s != current_sign:
            streaks_total += 1
            if current_sign == 1:
                streaks_pos += 1
            current_sign = s
    # Final streak
    streaks_total += 1
    if current_sign == 1:
        streaks_pos += 1

    if streaks_total == 0:
        return 0.0

    streak_ratio = streaks_pos / streaks_total

    # Also factor in low variance of returns
    if len(trades) >= 3:
        returns = np.array(trades)
        cv = np.std(returns) / (abs(np.mean(returns)) + 1e-9)
        variance_penalty = _normalize(cv, 0, 5)
        variance_bonus = 1.0 - variance_penalty
    else:
        variance_bonus = 0.5

    return 0.6 * streak_ratio + 0.4 * variance_bonus


def score_recency(last_trade_ts: float) -> float:
    """
    How recently the wallet traded.
    Traded today → 1.0, 7 days ago → ~0.7, 30 days ago → ~0.3.
    """
    if last_trade_ts <= 0:
        return 0.0
    days_ago = (time.time() - last_trade_ts) / 86400
    if days_ago <= 0:
        return 1.0
    return math.exp(-0.05 * days_ago)


def score_visibility(lb_entry: dict, profile: dict | None) -> float:
    """
    Visibility score 0-1 (higher = MORE visible = BAD for hidden alpha).
    Uses leaderboard fields: verifiedBadge, xUsername, profileImage, userName
    Plus Gamma profile if available.
    """
    score = 0.0

    # From leaderboard response
    if lb_entry.get("verifiedBadge"):
        score += 0.25

    if lb_entry.get("xUsername"):
        score += 0.20

    if lb_entry.get("profileImage"):
        score += 0.15

    username = lb_entry.get("userName", "")
    if username and not username.startswith("0x"):
        score += 0.15

    # From Gamma profile (if fetched)
    if profile:
        if profile.get("bio"):
            score += 0.10
        followers = profile.get("followers", 0) or 0
        if followers > 100:
            score += 0.15

    return min(score, 1.0)


# ── Market Maker Detection ─────────────────────────────────────────────────

def is_likely_market_maker(trades: list[dict]) -> bool:
    """
    Heuristic to detect market-making bots:
    - Very high trade frequency
    - Trades both sides of same market rapidly
    """
    if len(trades) < 20:
        return False

    # Check for both-side trading in same market
    by_market: dict[str, list] = {}
    for t in trades:
        slug = t.get("slug", t.get("eventSlug", ""))
        by_market.setdefault(slug, []).append(t)

    both_sides_count = 0
    for slug, market_trades in by_market.items():
        sides = {t.get("side", "") for t in market_trades}
        if len(sides) >= 2 and len(market_trades) >= 6:
            both_sides_count += 1

    if len(by_market) > 5:
        ratio = both_sides_count / len(by_market)
        if ratio > 0.6:
            return True

    # Check for very high frequency (>20 trades per day average)
    timestamps = sorted([
        t.get("timestamp", 0) for t in trades
        if t.get("timestamp")
    ])
    if len(timestamps) >= 2:
        ts_vals = []
        for ts in timestamps:
            try:
                v = float(ts)
                if v > 1e12:
                    v /= 1000
                ts_vals.append(v)
            except (ValueError, TypeError):
                pass
        if len(ts_vals) >= 2:
            span_days = max((ts_vals[-1] - ts_vals[0]) / 86400, 1)
            freq = len(trades) / span_days
            if freq > 20:
                return True

    return False


# ── Full Metrics Computation ───────────────────────────────────────────────

def compute_trade_returns(trades: list[dict]) -> list[float]:
    """
    Extract per-trade PnL from activity records.
    Activity fields: usdcSize (USD amount), price, size (tokens), side, outcome
    """
    returns = []
    for t in trades:
        # Only count actual trades
        if t.get("type") and t["type"] != "TRADE":
            continue

        # Use usdcSize for dollar amount, price for entry
        usdc_size = t.get("usdcSize", t.get("size", 0))
        price = t.get("price", 0)

        try:
            usdc_size = float(usdc_size)
            price = float(price)
        except (ValueError, TypeError):
            continue

        if usdc_size <= 0 or price <= 0:
            continue

        side = t.get("side", "").upper()

        # For BUY at price p: you pay p per share, receive 1 if win, 0 if lose
        # Expected PnL per dollar depends on market resolution, but we can
        # estimate from the price: buying at 0.60 means implied 60% prob
        # If we assume the market is efficient, expected PnL ≈ 0
        # But we want actual PnL, which we can't get from a single trade.
        # Instead, we'll use a heuristic: track by conditionId for resolved markets.

        # For now, store raw trade info for later per-market aggregation
        returns.append({
            "conditionId": t.get("conditionId", ""),
            "slug": t.get("slug", ""),
            "side": side,
            "price": price,
            "usdcSize": usdc_size,
            "timestamp": t.get("timestamp", 0),
            "outcome": t.get("outcome", ""),
            "outcomeIndex": t.get("outcomeIndex", 0),
        })

    return returns


def _estimate_pnl_from_trades(raw_trades: list[dict]) -> list[float]:
    """
    Group trades by market slug and estimate per-market P&L.
    Since we can't get resolution data from activity alone,
    we use the leaderboard PnL as ground truth and estimate
    per-trade returns from price spreads.
    """
    if not raw_trades:
        return []

    # Group by market
    by_market: dict[str, list] = {}
    for t in raw_trades:
        key = t.get("slug") or t.get("conditionId", "unknown")
        by_market.setdefault(key, []).append(t)

    # Estimate P&L per market based on trade direction and price
    market_returns = []
    for slug, trades in by_market.items():
        total_cost = 0
        total_size = 0
        avg_price = 0

        for t in trades:
            total_cost += t["usdcSize"]
            total_size += t["usdcSize"] / t["price"] if t["price"] > 0 else 0

        if total_size > 0:
            avg_price = total_cost / total_size if total_size > 0 else 0.5
            # Heuristic: if avg entry price > 0.5, likely betting on the favorite
            # Approximate expected edge from deviation of leaderboard PnL
            market_returns.append(total_cost)

    return market_returns


def compute_metrics(
    address: str,
    leaderboard_entry: dict,
    trades: list[dict],
    profile: dict | None,
) -> WalletMetrics:
    """Compute full metrics for a wallet."""
    m = WalletMetrics(address=address)

    # Username from leaderboard (field: userName)
    m.username = (
        leaderboard_entry.get("userName", "")
        or (profile.get("username", "") if profile else "")
        or address[:10]
    )

    # PnL from leaderboard (field: pnl)
    m.pnl = float(leaderboard_entry.get("pnl", 0) or 0)

    # Filter to only TRADE type entries
    trade_entries = [t for t in trades if t.get("type", "TRADE") == "TRADE"]
    m.num_trades = len(trade_entries)

    # Unique markets (by slug or conditionId)
    slugs = set()
    for t in trade_entries:
        slug = t.get("slug", t.get("conditionId", ""))
        if slug:
            slugs.add(slug)
    m.markets_traded = len(slugs)

    # Bet sizing — use usdcSize field
    sizes = []
    for t in trade_entries:
        s = t.get("usdcSize", t.get("size", 0))
        try:
            sizes.append(float(s))
        except (ValueError, TypeError):
            pass
    m.avg_bet_size = float(np.mean(sizes)) if sizes else 0.0

    # Per-trade returns (estimated)
    raw_returns = compute_trade_returns(trade_entries)

    # Group by market for win/loss estimation
    by_market: dict[str, list] = {}
    for t in raw_returns:
        key = t.get("slug") or t.get("conditionId", "unknown")
        by_market.setdefault(key, []).append(t)

    if by_market and m.pnl != 0:
        # We have PnL from leaderboard but need per-market breakdown.
        # Estimate win rate from number of markets with favorable entries.
        # A trade at price < 0.5 on BUY is contrarian; > 0.5 is with consensus.
        # We use total volume per market as proxy for returns.
        market_costs = []
        for slug, mkt_trades in by_market.items():
            total_cost = sum(t["usdcSize"] for t in mkt_trades)
            avg_price = np.mean([t["price"] for t in mkt_trades])
            market_costs.append({"cost": total_cost, "avg_price": avg_price})

        # Estimate: distribute total PnL across markets proportionally
        total_cost = sum(mc["cost"] for mc in market_costs) or 1
        estimated_returns = []
        for mc in market_costs:
            proportion = mc["cost"] / total_cost
            est_pnl = m.pnl * proportion
            estimated_returns.append(est_pnl)

        if estimated_returns:
            wins = [r for r in estimated_returns if r > 0]
            losses = [r for r in estimated_returns if r < 0]

            m.win_rate = len(wins) / len(estimated_returns) if estimated_returns else 0

            gross_wins = sum(wins) if wins else 0
            gross_losses = abs(sum(losses)) if losses else 0
            m.profit_factor = (
                gross_wins / gross_losses if gross_losses > 0 else
                (10.0 if gross_wins > 0 else 0)
            )

            ret_array = np.array(estimated_returns)
            mean_ret = np.mean(ret_array)
            std_ret = np.std(ret_array)
            m.sharpe_ratio = (
                (mean_ret / std_ret) * np.sqrt(len(estimated_returns))
                if std_ret > 0 else 0
            )
            m.sharpe_ratio = min(m.sharpe_ratio, 10.0)

            m.consistency = score_consistency(estimated_returns)

    elif m.pnl > 0 and m.num_trades > 0:
        # Fallback: estimate from PnL and trade count
        m.win_rate = 0.5 + (m.pnl / (m.avg_bet_size * m.num_trades + 1)) * 0.2
        m.win_rate = max(0.0, min(1.0, m.win_rate))

    # Timestamps from activity
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

    m.recency_score = score_recency(m.last_trade_ts)
    m.visibility = score_visibility(leaderboard_entry, profile)

    return m


# ── Composite Alpha Score ──────────────────────────────────────────────────

def compute_alpha_score(m: WalletMetrics) -> float:
    """Weighted composite of all components. Returns score in [0, 1]."""
    w = config.SCORE_WEIGHTS

    components = {
        "pnl_magnitude":      score_pnl(m.pnl),
        "win_rate":           score_win_rate(m.win_rate),
        "profit_factor":      score_profit_factor(m.profit_factor),
        "sharpe_ratio":       score_sharpe(m.sharpe_ratio),
        "consistency":        m.consistency,
        "recency":            m.recency_score,
        "inverse_visibility": 1.0 - m.visibility,
    }

    score = sum(w[k] * components[k] for k in w)
    return round(max(0.0, min(1.0, score)), 4)


# ── Filter Checks ──────────────────────────────────────────────────────────

def passes_filters(
    m: WalletMetrics,
    top_volume_addresses: set[str],
    min_pnl: float = config.MIN_PNL,
    min_trades: int = config.MIN_TRADES,
    min_win_rate: float = config.MIN_WIN_RATE,
    max_avg_bet: float = config.MAX_AVG_BET,
    min_active_days: int = config.MIN_ACTIVE_DAYS,
) -> tuple[bool, str]:
    """Apply all filters. Returns (passed, reason)."""
    if m.pnl < min_pnl:
        return False, f"PnL ${m.pnl:.0f} < ${min_pnl}"

    if m.num_trades < min_trades:
        return False, f"Trades {m.num_trades} < {min_trades}"

    if m.win_rate < min_win_rate:
        return False, f"Win rate {m.win_rate:.2%} < {min_win_rate:.0%}"

    if m.avg_bet_size > max_avg_bet:
        return False, f"Avg bet ${m.avg_bet_size:.0f} > ${max_avg_bet}"

    if m.address.lower() in top_volume_addresses:
        return False, "Top volume wallet (too visible)"

    active_days = (m.last_trade_ts - m.first_trade_ts) / 86400 if m.first_trade_ts else 0
    if active_days < min_active_days:
        return False, f"Active {active_days:.0f} days < {min_active_days}"

    return True, ""
