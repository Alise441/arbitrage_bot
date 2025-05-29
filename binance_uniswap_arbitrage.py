import logging
import sys 
import ccxt
import pandas as pd
from web3 import Web3
from decimal import Decimal, getcontext
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
 
# ---- Constants ----
CSV_INPUT = "arbitrage_pairs.csv"
CSV_OUTPUT = "arbitrage_results.csv"

TRADE_VALUE_USD = Decimal(1000)
STABLECOINS_USD = ['USDT', 'USDC', 'BUSD', 'TUSD', 'DAI', 'USDP', 'FDUSD', 'USDD']

getcontext().prec = 30 # Set decimal precision for calculations

# ---- Helper Functions ----
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
    if quote_symbol in STABLECOINS_USD: return binance_mid_price, quote_symbol
    elif base_symbol in STABLECOINS_USD: return Decimal(1), base_symbol
    else:
        base_stablecoin_price = Decimal(0)
        stablecoin_symbol = None
        for stablecoin_symbol in STABLECOINS_USD:
            # Try to get the price of base currency in terms of stablecoin
            base_stablecoin_pair = f"{base_symbol}/{stablecoin_symbol}"
            if base_stablecoin_pair in exchange.markets:
                try:
                    base_stablecoin_ticker = exchange.fetch_ticker(base_stablecoin_pair)   
                    if base_stablecoin_ticker and base_stablecoin_ticker.get('last'):
                        base_stablecoin_price = Decimal(str(base_stablecoin_ticker['last']))
                        break          
                except Exception as e:
                    logging.warning(f"Error fetching ticker for {base_stablecoin_pair}: {e}. Skipping this stablecoin.")
                    continue
    
        if base_stablecoin_price <= 0: return Decimal(0), None  # Default to 1 base currency if no stablecoin price found
        return base_stablecoin_price, stablecoin_symbol


if __name__ == "__main__":

    logging.info("Starting Binance-Uniswap arbitrage bot...")

    # Load pairs from CSV
    pairs_df = pd.read_csv(CSV_INPUT)
    if pairs_df.empty:
        logging.critical(f"Critical Error: {CSV_INPUT} is empty or not found. Stopping execution.")
        sys.exit(1)

    # Initialize Binance exchange instance
    try:
        exchange = ccxt.binance()
        exchange.load_markets()
        logging.info("Connected to Binance.")
    except Exception as e:
        logging.critical(f"Failed to connect to Binance: {e}")
        sys.exit(1)

    # Initialize Web3 instance
    try:
        web3_instance = Web3(Web3.HTTPProvider(config.ETHEREUM_RPC_URL))
        if not web3_instance.is_connected():
            raise ConnectionError("Not connected")
        logging.info("Connected to Ethereum RPC.")
    except Exception as e:
        logging.critical(f"Failed to connect to Ethereum RPC: {e}")
        sys.exit(1)

    # Load Uniswap ABIs
    try:
        abis = load_abis()
    except Exception as e:
        logging.critical(f"Failed to load ABIs: {e}")
        sys.exit(1)

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

        # Initialize UniswapPoolHelper
        try:
            uniswap_pool = UniswapPoolHelper(web3_instance, uniswap_pool_id, abis)
        except Exception as e:
            logging.error(f"Failed to initialize Uniswap pool: {e}. Skipping this pair.")
            continue

        blocknumber = web3_instance.eth.block_number
        timestamp = exchange.fetch_time() // 1000  # Convert milliseconds to seconds

        # Determine Uniswap pool's base token
        if not reverse_price_on_uniswap:
            uniswap_base_token_address = uniswap_pool.token0_address_cs
        else:
            uniswap_base_token_address = uniswap_pool.token1_address_cs

        # Fetch Binance Ticker
        # Ask price on Binance - this is the price at which we can buy the base currency
        # Bid price on Binance - this is the price at which we can sell the base currency
        try:
            binance_ticker = exchange.fetch_ticker(binance_pair)
            binance_ask_price = Decimal(str(binance_ticker['ask']))
            binance_bid_price = Decimal(str(binance_ticker['bid']))
            if binance_ask_price is None or binance_bid_price is None:
                logging.error("Binance ticker returned None values. Skipping this pair.")
                continue
        except Exception as e:
            logging.error(f"Failed to fetch Binance ticker: {e}. Skipping this pair.")
            continue

        # Fetch Uniswap pool's current price
        try:
            uniswap_current_price = uniswap_pool.get_current_price(reverse_price_on_uniswap)
        except Exception as e:
            logging.error(f"Failed to fetch Uniswap pool price: {e}. Skipping this pair.")
            continue

        # Get mid-prices
        binance_mid_price = (binance_ask_price + binance_bid_price) / Decimal(2)
        uniswap_mid_price = uniswap_current_price

        # Calculate trade amount in base currency
        # We use TRADE_VALUE_USD to determine how much base currency we can trade
        base_stablecoin_price, stablecoin_symbol = get_base_price_in_stablecoin(exchange, base_symbol, quote_symbol, binance_mid_price)
        trade_amount_base = TRADE_VALUE_USD / base_stablecoin_price if base_stablecoin_price > 0 else Decimal(1)

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

        ##############################################################################
        # --- Direction 1: Buy on Binance, Sell on Uniswap ---

        # We are buying base currency on Binance so we use the ask price
        binance_actual_price = binance_ask_price

        # Calculate the amount of quote currency we need to spend to buy the base currency + binance fee
        amount_in_quote = trade_amount_base * binance_actual_price * Decimal(str(1+config.BINANCE_FEE))

        # We are selling base currency on Uniswap so we use UniswapPoolHelper.get_sell_quote
        try:
            amount_out_quote, uniswap_new_price, uniswap_actual_price, gas_fee_eth = uniswap_pool.get_sell_quote(
                token_in_address_str=uniswap_base_token_address,
                amount_in=trade_amount_base
            )
        except Exception as e:
            logging.error(f"Failed to fetch Uniswap sell quote: {e}. Skipping this direction.")
            continue

        # Convert gas fee from ETH to quote currency
        gas_fee_quote = gas_fee_eth *  get_eth_price_in_currency(exchange, quote_symbol)

        # Calculate profit
        profit = amount_out_quote - amount_in_quote - gas_fee_quote
        margin = profit / amount_in_quote if amount_in_quote > 0 else Decimal(0)

        # Calculate profit in stablecoin
        profit_stablecoin = profit / binance_mid_price * base_stablecoin_price

        # save results for this direction
        results.append( 
            result | {
                "decision": "Buy on Binance, Sell on Uniswap",
                "binance_actual_price": float(binance_actual_price),
                "uniswap_actual_price": float(uniswap_actual_price),
                "amount_in_quote": float(amount_in_quote),
                "amount_out_quote": float(amount_out_quote),
                "uniswap_new_price": float(uniswap_new_price),
                "gas_fee_eth": float(gas_fee_eth),
                "gas_fee_quote": float(gas_fee_quote),
                "profit": float(profit),
                "margin": float(margin),
                "profit_stablecoin": float(profit_stablecoin)
            }
        )

        logging.info(f"\tDirection 1, Margin: {margin:.6f}, Profit: {profit:.6f} {quote_symbol}, Profit in {stablecoin_symbol}: {profit_stablecoin:.6f}")

        ############################################################################
        # --- Direction 2: Buy on Uniswap, Sell on Binance ---

        # We are selling base currency on Binance so we use the bid price
        binance_actual_price = binance_bid_price

        # Calculate the amount of quote currency we receive from selling the base currency on Binance - binance fee
        amount_out_quote = trade_amount_base * binance_actual_price * Decimal(str(1-config.BINANCE_FEE))

        # We are buying base currency on Uniswap so we use UniswapPoolHelper.get_buy_quote
        try:
            amount_in_quote, uniswap_new_price, uniswap_actual_price, gas_fee_eth = uniswap_pool.get_buy_quote(
                token_out_address_str=uniswap_base_token_address,
                amount_out=trade_amount_base
            )
        except Exception as e:
            logging.error(f"Failed to fetch Uniswap sell quote: {e}. Skipping this direction.")
            continue

        # Convert gas fee from ETH to quote currency
        gas_fee_quote = gas_fee_eth * get_eth_price_in_currency(exchange, quote_symbol)

        # Calculate profit
        profit = amount_out_quote - amount_in_quote - gas_fee_quote
        margin = profit / amount_in_quote if amount_in_quote > 0 else Decimal(0)

        # Calculate profit in stablecoin
        profit_stablecoin = profit / binance_mid_price * base_stablecoin_price

        # save results for this direction
        results.append(
            result | { 
                "decision": "Buy on Uniswap, Sell on Binance",
                "binance_actual_price": float(binance_actual_price),
                "uniswap_actual_price": float(uniswap_actual_price),
                "amount_in_quote": float(amount_in_quote),
                "amount_out_quote": float(amount_out_quote),
                "uniswap_new_price": float(uniswap_new_price),
                "gas_fee_eth": float(gas_fee_eth),
                "gas_fee_quote": float(gas_fee_quote),
                "profit": float(profit),
                "margin": float(margin),
                "profit_stablecoin": float(profit_stablecoin)
            }
        )

        logging.info(f"\tDirection 2, Margin: {margin:.6f}, Profit: {profit:.6f} {quote_symbol}, Profit in {stablecoin_symbol}: {profit_stablecoin:.6f}")

    # Save results to CSV
    results_df = pd.DataFrame(results)
    results_df.to_csv(CSV_OUTPUT, index=False, float_format='%.10f')
    logging.info(f"Results saved to {CSV_OUTPUT}")
