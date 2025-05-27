import config
import ccxt
import pandas as pd
from web3 import Web3
from decimal import Decimal, getcontext
from uniswap_pool_helper import UniswapPoolHelper


# --- 0. Constants ---

CSV_INPUT = "arbitrage_pairs.csv"
CSV_OUTPUT = "arbitrage_results.csv"

exchange = ccxt.binance()
web3_instance = Web3(Web3.HTTPProvider(config.ETHEREUM_RPC_URL))

getcontext().prec = 60


# --- 1. Get mid price from Binance ---
def get_binance_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        bid, ask = ticker.get("bid"), ticker.get("ask")
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2
    except Exception as e:
        print(f"Binance error ({symbol}): {e}")
        return None

# --- 2. Get price from Uniswap V3 ---
def get_uniswap_price(pool_id, reverse_price):
    return float(UniswapPoolHelper(web3_instance, pool_id).get_current_price(price_of_token0_in_token1=reverse_price))


# --- 3. Check for arbitrage opportunity ---
def check_arbitrage(cex_price, dex_price, threshold):
    diff = abs(cex_price - dex_price)
    rel_diff = diff / min(cex_price, dex_price)
    if rel_diff >= threshold:
        direction = "Buy on CEX" if cex_price < dex_price else "Buy on DEX"
        return direction, rel_diff
    return "No arbitrage", rel_diff

# --- 4. Main processing function per pair ---
def process_pair(row):
    binance_pair = row["binance_pair"]
    pool_id = row["uniswap_pool_id"]
    reverse_price = row["reverse_price"]
    
    binance_price = get_binance_price(binance_pair)
    uniswap_price = get_uniswap_price(pool_id, reverse_price)

    if binance_price and uniswap_price:
        direction, rel_diff = check_arbitrage(binance_price, uniswap_price, config.PROFIT_THRESHOLD)
    else:
        direction, rel_diff = "Error", 0.0

    print(f"{binance_pair} vs {pool_id} → {direction} | diff: {rel_diff:.4%}")

    return {
        "binance_pair": binance_pair,
        "uniswap_pool_id": pool_id,
        "binance_price": binance_price,
        "uniswap_price": uniswap_price,
        "decision": direction,
        "relative_diff": f"{rel_diff:.4%}"
    }

# --- 5. Main function ---
def main():
    # Load pairs from CSV
    try:
        pairs_df = pd.read_csv(CSV_INPUT)
    except FileNotFoundError:
        print(f"Error: {CSV_INPUT} not found.")
        return

    results = []
    
    for _, row in pairs_df.iterrows():
        result = process_pair(row)
        results.append(result)

    # Save results to CSV
    results_df = pd.DataFrame(results)
    results_df.to_csv(CSV_OUTPUT, index=False)
    print(f"Results saved to {CSV_OUTPUT}")

# --- 6. Run the script ---
if __name__ == "__main__":
    main()
