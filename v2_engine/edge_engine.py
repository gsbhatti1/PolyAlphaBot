MIN_EDGE = 0.05

def estimate_true_prob(wallet_score, market_price):
    confidence_boost = min(wallet_score / 100, 0.10)
    return min(1.0, market_price + confidence_boost)

def compute_edge(true_prob, market_price):
    return true_prob - market_price

def edge_is_valid(wallet_score, market_price):
    true_prob = estimate_true_prob(wallet_score, market_price)
    edge = compute_edge(true_prob, market_price)
    return edge >= MIN_EDGE, edge