import logging
import os 
import time
import ccxt
import pandas as pd
import threading
import random
from web3 import Web3
from decimal import Decimal, getcontext
from concurrent.futures import ThreadPoolExecutor

from config import config, load_abis
from uniswap_pool_helper import UniswapPoolHelper, Token
from telegram_utils import send_telegram_message, send_error_notification
from arbitrage_executor import execute_arbitrage_trade

# ---- Setup logging ----
def setup_logging():
    """Configures logging for the application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("arbitrage_bot.log", mode='a'),
            logging.StreamHandler()
        ]
    )
    # Configure the trade executor logger separately
    trade_log_handler = logging.FileHandler("trades.log", mode='a')
    trade_log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    trade_log_handler.setFormatter(trade_log_formatter)

    trade_logger = logging.getLogger('trade_executor')
    trade_logger.setLevel(logging.INFO)
    trade_logger.addHandler(trade_log_handler)
    trade_logger.propagate = False

# ---- Constants ----
CSV_INPUT = "arbitrage_pairs.csv"
STABLECOINS_USD = ['USDT', 'USDC', 'BUSD', 'TUSD', 'DAI', 'USDP', 'FDUSD', 'USDD']
MAX_WORKERS = 10 # Maximum number of concurrent trades

# ---- Concurrency and State Management ----
trade_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
active_trades = set()
active_trades_lock = threading.Lock()

getcontext().prec = 30 # Set decimal precision for calculations

# ---- Helper Functions ----
def get_binance_mid_price(exchange, pair):
    """Fetches and calculates the mid-price from Binance."""
    try:
        ticker = exchange.fetch_ticker(pair)
        if 'ask' in ticker and 'bid' in ticker:
            return (Decimal(str(ticker['ask'])) + Decimal(str(ticker['bid']))) / Decimal(2)
        logging.warning(f"Ask/bid prices missing for {pair}. Mid price cannot be calculated.")
        return Decimal(0)
    except Exception as e:
        logging.warning(f"Cannot fetch Binance mid price for {pair}: {e}")
        return Decimal(0)
    
def get_uniswap_mid_price(pool, reverse_price):
    """Fetches the mid-price from a Uniswap pool."""
    try:
        return pool.get_current_price(reverse_price)
    except Exception as e:
        logging.warning(f"Cannot fetch Uniswap mid price: {e}. Returning 0.")
        return Decimal(0)

def get_eth_price_in_currency(exchange, currency_symbol="USDT"):
    """Fetches the price of ETH in a given currency, using USDT as a bridge if needed."""
    if currency_symbol.upper() in ["ETH", "WETH"]:
        return Decimal(1)
    try:
        # Attempt direct conversion
        return Decimal(str(exchange.fetch_ticker(f"ETH/{currency_symbol.upper()}")['last']))
    except (ccxt.Error, KeyError):
        try:
            # Attempt conversion via USDT
            eth_usdt_price = Decimal(str(exchange.fetch_ticker("ETH/USDT")['last']))
            currency_usdt_price = Decimal(str(exchange.fetch_ticker(f"{currency_symbol.upper()}/USDT")['last']))
            return eth_usdt_price / currency_usdt_price if currency_usdt_price else Decimal(0)
        except (ccxt.Error, KeyError) as e:
            logging.warning(f"Could not determine ETH price in {currency_symbol}: {e}")
            return Decimal(0)

def get_base_price_in_stablecoin(exchange, base_symbol, quote_symbol, binance_mid_price):
    """Determines the price of the base asset in a stablecoin to calculate trade size."""
    if quote_symbol in STABLECOINS_USD and binance_mid_price > 0:
        return binance_mid_price, quote_symbol
    if base_symbol in STABLECOINS_USD:
        return Decimal(1), base_symbol
    
    # Find price against any available stablecoin
    for stable in STABLECOINS_USD:
        try:
            ticker = exchange.fetch_ticker(f"{base_symbol}/{stable}")
            if 'last' in ticker:
                return Decimal(str(ticker['last'])), stable
        except (ccxt.Error, KeyError):
            continue
    return Decimal(0), None

def get_sepolia_pool(abis):
    """Returns a Uniswap V3 pool instance for the Sepolia testnet for testing."""
    pool_address = "0x3289680dD4d6C10bb19b899729cda5eEF58AEfF1" # WETH/USDC 0.05% on Sepolia
    w3_sepolia = Web3(Web3.HTTPProvider("https://eth-sepolia.public.blastapi.io"))
    return UniswapPoolHelper(w3=w3_sepolia, pool_address=pool_address, abis=abis)

def run_arbitrage_cycle(trade_amount_usd, csv_output):
    """Main arbitrage cycle to find and evaluate opportunities."""
    logging.info("Starting new Binance-Uniswap arbitrage cycle. Press Ctrl+C to stop.")

    try:
        pairs_df = pd.read_csv(CSV_INPUT)
        if pairs_df.empty:
            logging.critical(f"{CSV_INPUT} is empty. Skipping cycle.")
            return
        
        exchange = ccxt.binance()
        exchange.load_markets()
        w3 = Web3(Web3.HTTPProvider(config.ETHEREUM_RPC_URL))
        if not w3.is_connected():
            raise ConnectionError("Failed to connect to Ethereum RPC")
        abis = load_abis()
    except Exception as e:
        logging.critical(f"Setup failed: {e}. Skipping cycle.")
        return

    results = []

    logging.info(f"Processing {len(pairs_df)} pairs from {CSV_INPUT}...")

    for index, row in pairs_df.iterrows():
        binance_pair = row["binance_pair"]
        uniswap_pool_id = row["uniswap_pool_id"]
        # reverse_price: False -> Binance price: QUOTE/BASE, Uniswap pool's price: token1/token0 (token0=BASE, token1=QUOTE)
        #                True  -> Binance price: QUOTE/BASE, Uniswap reversed price: token0/token1 (token0=QUOTE, token1=BASE)
        reverse_price_on_uniswap = bool(row["reverse_price"])
        base_symbol, quote_symbol = binance_pair.split('/')

        logging.info(f"({index+1}/{len(pairs_df)}) Processing: {binance_pair}, Uniswap pool: {uniswap_pool_id}")

        try:
            uniswap_pool = UniswapPoolHelper(w3, uniswap_pool_id, abis)
            uniswap_base_token = uniswap_pool.token0 if not reverse_price_on_uniswap else uniswap_pool.token1

            binance_mid_price = get_binance_mid_price(exchange, binance_pair)
            base_stablecoin_price, stablecoin_symbol = get_base_price_in_stablecoin(exchange, base_symbol, quote_symbol, binance_mid_price)

            if not base_stablecoin_price:
                logging.warning(f"Could not find stablecoin price for {base_symbol}. Skipping.")
                continue

            trade_amount_base = trade_amount_usd / base_stablecoin_price

            with ThreadPoolExecutor() as executor:
                fut_ticker = executor.submit(exchange.fetch_ticker, binance_pair)
                fut_sell = executor.submit(uniswap_pool.get_sell_quote, uniswap_base_token, trade_amount_base)
                fut_buy = executor.submit(uniswap_pool.get_buy_quote, uniswap_base_token, trade_amount_base)

                ticker = fut_ticker.result(timeout=5)
                binance_ask, binance_bid = Decimal(str(ticker['ask'])), Decimal(str(ticker['bid']))
                uniswap_sell_quote = fut_sell.result(timeout=5)
                uniswap_buy_quote = fut_buy.result(timeout=5)

            base_result_data = {
                "blocknumber": w3.eth.block_number,
                "timestamp": int(time.time()),
                "binance_pair": binance_pair,
                "uniswap_pair": row["uniswap_pair"],
                "uniswap_pool_id": uniswap_pool_id,
                "reverse_price": int(reverse_price_on_uniswap),
                "base_symbol": base_symbol, "quote_symbol": quote_symbol,
                "uniswap_fee": float(uniswap_pool.fee) / 1e6,
                "binance_fee": config.BINANCE_FEE,
                "binance_mid_price": float(binance_mid_price),
                "uniswap_mid_price": float(get_uniswap_mid_price(uniswap_pool, reverse_price_on_uniswap)),
                "trade_amount_base": float(trade_amount_base),
                "base_stablecoin_price": float(base_stablecoin_price),
                "stablecoin_symbol": stablecoin_symbol
            }

            # Evaluate both trade directions
            for direction, binance_price, uniswap_quote in [
                ("Buy on Binance, Sell on Uniswap", binance_ask, uniswap_sell_quote),
                ("Buy on Uniswap, Sell on Binance", binance_bid, uniswap_buy_quote)
            ]:
                amount_out, new_price, actual_price, gas_eth = uniswap_quote
                
                if "Buy on Binance" in direction:
                    spend = trade_amount_base * binance_price * Decimal(str(1 + config.BINANCE_FEE))
                    received = amount_out
                else: # Buy on Uniswap
                    spend = amount_out
                    received = trade_amount_base * binance_price * Decimal(str(1 - config.BINANCE_FEE))

                gas_fee_quote = gas_eth * get_eth_price_in_currency(exchange, quote_symbol)
                profit = received - spend - gas_fee_quote
                margin = profit / spend if spend > 0 else Decimal(0)
                profit_stablecoin = profit / binance_price * base_stablecoin_price if binance_price > 0 else Decimal(0)

                results.append(base_result_data | {
                    "decision": direction, "binance_actual_price": float(binance_price),
                    "uniswap_actual_price": float(actual_price), "amount_in_quote": float(spend),
                    "amount_out_quote": float(received), "uniswap_new_price": float(new_price),
                    "gas_fee_eth": float(gas_eth), "gas_fee_quote": float(gas_fee_quote),
                    "profit": float(profit), "margin": float(margin),
                    "profit_stablecoin": float(profit_stablecoin)
                })

                logging.info(f"\t{direction}: Margin={margin:.4%}, Profit={profit_stablecoin:.4f} {stablecoin_symbol}")

                if profit > 0 and margin > config.PROFIT_THRESHOLD:
                    handle_arbitrage_opportunity(direction, binance_pair, profit_stablecoin, stablecoin_symbol, margin, uniswap_pool, abis)

        except Exception as e:
            logging.error(f"Failed processing {binance_pair}: {e}", exc_info=False)
            continue
    
    if results:
        results_df = pd.DataFrame(results)
        header = not os.path.exists(csv_output)
        results_df.to_csv(csv_output, index=False, float_format='%.10f', mode='a', header=header)
        logging.info(f"Saved {len(results_df)} results to {csv_output}")

def handle_arbitrage_opportunity(direction, pair, profit, stablecoin, margin, pool, abis):
    """Logs, notifies, and executes a trade for an arbitrage opportunity."""
    logging.info(f"Arbitrage opportunity found for {pair}!")
    message = (f"Arbitrage Found!\nPair: {pair}\nDirection: {direction}\n"
               f"Est. Profit: {profit:.4f} {stablecoin}\nMargin: {margin:.4%}")
    send_telegram_message(message)

    with active_trades_lock:
        pair_key = (pool.token0.symbol, pool.token1.symbol)
        if pair_key in active_trades:
            logging.info(f"Trade for {pair_key} is already active. Skipping.")
            return
        active_trades.add(pair_key)
        logging.info(f"Locking pair {pair_key} for execution.")

    # Execute trade on Sepolia testnet for demonstration
    test_pool = get_sepolia_pool(abis)
    token_to_trade = random.choice([test_pool.token0, test_pool.token1])
    amount = Decimal("0.1") if token_to_trade == test_pool.token0 else Decimal("0.00001")
    trade_executor.submit(
        execute_arbitrage_trade, direction=direction, 
        pool=test_pool, token_to_trade=token_to_trade, amount=amount, 
        slippage=Decimal("0.01"), active_trades_lock=active_trades_lock,
        active_trades_set=active_trades, pair_key_locked=pair_key
    )
    logging.info(f"Trade submitted for {pair_key} ({direction}).")

def main():
    """Initializes and runs the arbitrage bot."""
    setup_logging()
    
    try:
        user_input = input("Enter the trade amount in USD (e.g., 1000): ")
        trade_amount_usd = Decimal(str(user_input))
        if trade_amount_usd <= 0:
            raise ValueError("Trade amount must be a positive number.")
    except (ValueError, TypeError):
        logging.error("Invalid trade amount. Exiting.")
        return

    csv_output = f"arbitrage_results_{trade_amount_usd}.csv"
    logging.info(f"Bot starting with trade amount: ${trade_amount_usd:,.2f}")
    send_telegram_message(f"Arbitrage bot started. Trade Amount: ${trade_amount_usd:,.2f}")

    while True:
        try:
            run_arbitrage_cycle(trade_amount_usd, csv_output)
        except KeyboardInterrupt:
            logging.info("Bot stopped by user.")
            send_telegram_message("Arbitrage bot stopped by user.")
            break
        except Exception as e:
            logging.critical(f"A critical error occurred in the main loop: {e}", exc_info=True)
            send_error_notification(f"Bot encountered a critical error: {e}")
        
        logging.info("Cycle finished. Waiting 10 seconds...")
        time.sleep(10)

if __name__ == "__main__":
    main()