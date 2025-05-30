import logging
import os 
import requests
import time
import ccxt
import pandas as pd
from web3 import Web3
from decimal import Decimal, getcontext
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from config import config, load_abis
from uniswap_pool_helper import UniswapPoolHelper

# ---- Setup logging ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("arbitrage_bot.log", mode='a'), # 'a' for append mode
        logging.StreamHandler() # Output to console
    ]
)

# ---- Telegram Notification ----
def send_telegram_message(message: str):
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        logging.warning("Telegram token or chat ID not set. Skipping Telegram notification.")
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        logging.warning(f"Failed to send Telegram message: {e}")
 
# ---- Constants ----
CSV_INPUT = "arbitrage_pairs.csv"
CSV_OUTPUT = "arbitrage_results.csv"

TRADE_AMOUNT_USD = Decimal(1000)
STABLECOINS_USD = ['USDT', 'USDC', 'BUSD', 'TUSD', 'DAI', 'USDP', 'FDUSD', 'USDD']

getcontext().prec = 30 # Set decimal precision for calculations

# ---- Helper Functions ----
def get_binance_mid_price(exchange, pair):
    try:
        ticker = exchange.fetch_ticker(pair)
        if ticker and ticker.get('ask') and ticker.get('bid'):
            return Decimal(str(ticker['ask'] + ticker['bid'])) / Decimal(2)
        else:
            logging.warning(f"Missing 'ask' or 'bid' price. Binance mid price cannot be calculated. Returning 0.")
            return Decimal(0)
    except Exception as e:
        logging.warning(f"Cannot fetch Binance mid price: {e}. Returning 0.")
        return Decimal(0)
    
def get_uniswap_mid_price(pool, reverse_price):
    try:
        return pool.get_current_price(reverse_price)
    except Exception as e:
        logging.warning(f"Cannot fetch Uniswap mid price: {e}. Returning 0.")
        return Decimal(0)

def get_eth_price_in_currency(exchange, currency_symbol="USDT"):
    try:
        if currency_symbol.upper() in ["ETH", "WETH"]:
            return Decimal(1)
        
        # Trying to fetch ETH price directly in the specified currency
        eth_currency_pair = f"ETH/{currency_symbol.upper()}"
        if eth_currency_pair in exchange.markets:
            ticker = exchange.fetch_ticker(eth_currency_pair)
            if ticker and ticker.get('last'):
                return Decimal(str(ticker['last']))
        
        # If direct ticker not found, try to get ETH price through USDT
        eth_usdt_ticker = exchange.fetch_ticker("ETH/USDT")
        currency_usdt_pair = f"{currency_symbol.upper}/USDT"
        
        if currency_usdt_pair in exchange.markets: # Check if the currency/USDT pair exists
            currency_usdt_ticker = exchange.fetch_ticker(currency_usdt_pair)
            if eth_usdt_ticker and eth_usdt_ticker.get('last') and \
               currency_usdt_ticker and currency_usdt_ticker.get('last'):
                price_eth_usdt = Decimal(str(eth_usdt_ticker['last']))
                price_currency_usdt = Decimal(str(currency_usdt_ticker['last']))
                return price_eth_usdt / price_currency_usdt
        
        logging.warning(f"Cannot find ETH price in {currency_symbol}. Returning 0.")
        return Decimal(0)
        
    except Exception as e:
        logging.warning(f"Warning: cannot fetch ETH price in {currency_symbol}: {e}. Returning 0.")
        return Decimal(0)


def get_base_price_in_stablecoin(exchange, base_symbol, quote_symbol, binance_mid_price):
    if quote_symbol in STABLECOINS_USD and binance_mid_price > 0: return binance_mid_price, quote_symbol
    elif base_symbol in STABLECOINS_USD: return Decimal(1), base_symbol
    else:
        base_stablecoin_price = Decimal(0)
        stablecoin_symbol = None
        for stablecoin_symbol in STABLECOINS_USD:
            # Try to get the price of base currency in terms of stablecoin
            pair = f"{base_symbol}/{stablecoin_symbol}"
            if pair in exchange.markets:
                try:
                    ticker = exchange.fetch_ticker(pair)   
                    if ticker and ticker.get('last'):
                        base_stablecoin_price = Decimal(str(ticker['last']))
                        break          
                except Exception as e:
                    logging.warning(f"Error fetching ticker for {pair}: {e}. Skipping this stablecoin.")
                    continue
    
        if base_stablecoin_price <= 0: return Decimal(0), None  # Default to 1 base currency if no stablecoin price found
        return base_stablecoin_price, stablecoin_symbol


def run_arbitrage_bot():

    logging.info("Starting new Binance-Uniswap arbitrage cycle. Press Ctrl+C to stop.")

    # Load token pairs from CSV
    if not os.path.exists(CSV_INPUT):
        logging.critical(f"Critical Error: {CSV_INPUT} not found. Skipping this cycle.")
        return
    pairs_df = pd.read_csv(CSV_INPUT)
    if pairs_df.empty:
        logging.critical(f"Critical Error: {CSV_INPUT} is empty or not found. Skipping this cycle.")
        return

    # Initialize Binance exchange instance
    try:
        exchange = ccxt.binance()
        exchange.load_markets()
        logging.info("Connected to Binance.")
    except Exception as e:
        logging.critical(f"Failed to connect to Binance: {e}. Skipping this cycle.")
        return

    # Initialize Web3 instance
    try:
        web3_instance = Web3(Web3.HTTPProvider(config.ETHEREUM_RPC_URL))
        if not web3_instance.is_connected():
            raise ConnectionError("Not connected")
        logging.info("Connected to Ethereum RPC.")
    except Exception as e:
        logging.critical(f"Failed to connect to Ethereum RPC: {e}. Skipping this cycle.")
        return

    # Load Uniswap ABIs
    try:
        abis = load_abis()
    except Exception as e:
        logging.critical(f"Failed to load ABIs: {e}. Skipping this cycle.")
        return

    results = []

    logging.info(f"Starting arbitrage calculations for {len(pairs_df)} pairs from {CSV_INPUT}...")
    
    for index, row in pairs_df.iterrows():
        binance_pair = row["binance_pair"]
        uniswap_pair = row["uniswap_pair"]
        uniswap_pool_id = row["uniswap_pool_id"]
        # reverse_price: False -> Binance price: QUOTE/BASE, Uniswap pool's price: token1/token0 (token0=BASE, token1=QUOTE)
        #                True  -> Binance price: QUOTE/BASE, Uniswap reversed price: token0/token1 (token0=QUOTE, token1=BASE)
        reverse_price_on_uniswap = bool(row["reverse_price"]) 

        base_symbol = binance_pair.split('/')[0].upper()
        quote_symbol = binance_pair.split('/')[1].upper()

        logging.info(f"({index+1}/{len(pairs_df)}) Processing pair: {binance_pair}, Uniswap pool: {uniswap_pool_id}")

        blocknumber = web3_instance.eth.block_number
        timestamp = exchange.fetch_time() // 1000  # Convert milliseconds to seconds

        # Initialize UniswapPoolHelper
        try:
            uniswap_pool = UniswapPoolHelper(web3_instance, uniswap_pool_id, abis)
        except Exception as e:
            logging.error(f"Failed to initialize Uniswap pool: {e}. Skipping this pair.")
            continue

        # Determine Uniswap pool's base token
        if not reverse_price_on_uniswap:
            uniswap_base_token = uniswap_pool.token0_address_cs
        else:
            uniswap_base_token = uniswap_pool.token1_address_cs

        # Get mid-prices
        binance_mid_price = get_binance_mid_price(exchange, binance_pair)
        uniswap_mid_price = get_uniswap_mid_price(uniswap_pool, reverse_price_on_uniswap)

        # Calculate trade amount in base currency
        # We use TRADE_AMOUNT_USD to determine how much base currency we can trade
        base_stablecoin_price, stablecoin_symbol = get_base_price_in_stablecoin(exchange, base_symbol, quote_symbol, binance_mid_price)
        trade_amount_base = TRADE_AMOUNT_USD / base_stablecoin_price if base_stablecoin_price > 0 else Decimal(1)

        result = {
            "blocknumber": blocknumber,
            "timestamp": timestamp,
            "binance_pair": binance_pair,
            "uniswap_pair": uniswap_pair,
            "uniswap_pool_id": uniswap_pool_id,
            "reverse_price": int(reverse_price_on_uniswap),
            "base_symbol": base_symbol,
            "quote_symbol": quote_symbol,
            "uniswap_fee": float(uniswap_pool.fee)/1e6,  # Uniswap fee is in basis points
            "binance_fee": config.BINANCE_FEE,
            "binance_mid_price": float(binance_mid_price),
            "uniswap_mid_price": float(uniswap_mid_price),
            "trade_amount_base": float(trade_amount_base),
            "base_stablecoin_price": float(base_stablecoin_price),
            "stablecoin_symbol": stablecoin_symbol
        }

        # We fetch Binance ticker once more and request Uniswap quotas
        # We do it concurrently so we get the prices as close in time as possible
        try:
            with ThreadPoolExecutor() as executor:
                fut_ticker = executor.submit(exchange.fetch_ticker, binance_pair)
                fut_sell = executor.submit(uniswap_pool.get_sell_quote, uniswap_base_token, trade_amount_base)
                fut_buy = executor.submit(uniswap_pool.get_buy_quote, uniswap_base_token, trade_amount_base)

                ticker = fut_ticker.result(timeout=5)
                binance_ask_price, binance_bid_price = Decimal(str(ticker['ask'])), Decimal(str(ticker['bid']))
                uniswap_sell_quote = fut_sell.result(timeout=5)
                uniswap_buy_quote = fut_buy.result(timeout=5)
        except Exception as e:
            logging.error(f"Concurrent price fetch error: {e}. Skipping this pair.")
            continue

        for direction, direction_txt, binance_actual_price, uniswap_quote in [
            (1, "Buy on Binance, Sell on Uniswap", binance_ask_price, uniswap_sell_quote),
            (2, "Buy on Uniswap, Sell on Binance", binance_bid_price, uniswap_buy_quote)
        ]:
            uniswap_amount, uniswap_new_price, uniswap_actual_price, gas_fee_eth = uniswap_quote
            if direction == 1: # spend on Binance, receive on Uniswap
                spend = trade_amount_base * binance_actual_price * Decimal(str(1+config.BINANCE_FEE))
                received = uniswap_amount
            else: # spend on Uniswap, receive on Binance
                spend = uniswap_amount
                received = trade_amount_base * binance_actual_price * Decimal(str(1-config.BINANCE_FEE))          

            # Convert gas fee from ETH to quote currency
            gas_fee = gas_fee_eth * get_eth_price_in_currency(exchange, quote_symbol)

            # Calculate profit
            profit = received - spend - gas_fee
            margin = profit / spend if spend > 0 else Decimal(0)

            # Calculate profit in stablecoin
            profit_stablecoin = profit / binance_actual_price * base_stablecoin_price

            # Save results for this direction
            results.append(
                result | {
                    "decision": direction_txt,
                    "binance_actual_price": float(binance_actual_price),
                    "uniswap_actual_price": float(uniswap_actual_price),
                    "amount_in_quote": float(spend),
                    "amount_out_quote": float(received),
                    "uniswap_new_price": float(uniswap_new_price),
                    "gas_fee_eth": float(gas_fee_eth),
                    "gas_fee_quote": float(gas_fee),
                    "profit": float(profit),
                    "margin": float(margin),
                    "profit_stablecoin": float(profit_stablecoin)
                }
            )

            # Log and notify if profit is above threshold
            if profit > 0 and margin > config.PROFIT_THRESHOLD:
                logging.info("Arbitrage opportunity found!")
                send_telegram_message(
                    f"Arbitrage Opportunity Found!\n"
                    f"timestamp: {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Block Number: {blocknumber}\n"
                    f"Pair: {binance_pair}\n"
                    f"Uniswap Pool: {uniswap_pool_id}\n"
                    f"Direction: {direction_txt}\n"
                    f"Margin: {margin:.6f}, Profit: {profit:.6f} {quote_symbol}, Profit in {stablecoin_symbol}: {profit_stablecoin:.6f}"
                )

            logging.info(f"\tDirection {direction}, Margin: {margin:.6f}, Profit: {profit:.6f} {quote_symbol}, Profit in {stablecoin_symbol}: {profit_stablecoin:.6f}")

    # Save results to CSV
    results_df = pd.DataFrame(results)
    write_header = not os.path.exists(CSV_OUTPUT) # Write header only if file does not exist
    results_df.to_csv(
        CSV_OUTPUT,
        index=False,
        float_format='%.10f',
        mode='a',
        header=write_header
    )
    logging.info(f"Results saved to {CSV_OUTPUT}")

if __name__ == "__main__":
    while True:
        try:
            run_arbitrage_bot()
        except KeyboardInterrupt:
            logging.info("Interrupted by user. Exiting...")
            break
        except Exception as e:
            logging.error(f"An error occurred: {e}")
            send_telegram_message(f"Arbitrage bot encountered an error: {e}")
        
        # Wait for a while before the next cycle
        logging.info("Waiting for 10 seconds before the next cycle...")
        time.sleep(10)
