MAX_RISK_PER_TRADE = 0.02
MAX_THEME_EXPOSURE = 0.05
MAX_TOTAL_EXPOSURE = 0.15
SOFT_DRAWDOWN = 0.10
HARD_DRAWDOWN = 0.20

def approve_trade(bankroll, proposed_size, drawdown,
                  total_exposure, theme_exposure):

    if drawdown >= HARD_DRAWDOWN:
        return False, "HARD_DRAWDOWN"

    if total_exposure >= bankroll * MAX_TOTAL_EXPOSURE:
        return False, "TOTAL_EXPOSURE_LIMIT"

    if theme_exposure >= bankroll * MAX_THEME_EXPOSURE:
        return False, "THEME_EXPOSURE_LIMIT"

    if proposed_size > bankroll * MAX_RISK_PER_TRADE:
        proposed_size = bankroll * MAX_RISK_PER_TRADE

    if drawdown >= SOFT_DRAWDOWN:
        proposed_size *= 0.5

    return True, proposed_size