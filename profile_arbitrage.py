import cProfile
import pstats
import io
import os

import config
import ccxt
import pandas as pd
from web3 import Web3
from decimal import Decimal, getcontext
from uniswap_pool_helper import UniswapPoolHelper

CSV_INPUT = "arbitrage_pairs.csv"
CSV_OUTPUT = "arbitrage_results.csv"
TRADE_VALUE_USD = Decimal(1000)
STABLECOINS_USD = ['USDT', 'USDC', 'BUSD', 'TUSD', 'DAI', 'USDP', 'FDUSD', 'USDD']

# Set decimal precision for Decimal operations
getcontext().prec = 30


def get_binance_mid_price(exchange, symbol):
    ticker = exchange.fetch_ticker(symbol)
    bid, ask = ticker.get("bid"), ticker.get("ask")
    return (Decimal(str(bid)) + Decimal(str(ask))) / Decimal(2)

def get_eth_price_in_currency(exchange, currency_symbol="USDT"):
    if currency_symbol.upper() in ["ETH", "WETH"]:
        return Decimal(1)
    
    ticker = exchange.fetch_ticker(f"ETH/{currency_symbol.upper()}")
    if ticker and ticker.get('last'):
        return Decimal(str(ticker['last']))
        
    eth_usdt_ticker = exchange.fetch_ticker("ETH/USDT")
    currency_usdt_ticker = exchange.fetch_ticker(f"{currency_symbol.upper()}/USDT")
    if eth_usdt_ticker and eth_usdt_ticker.get('last') and \
       currency_usdt_ticker and currency_usdt_ticker.get('last') and \
       Decimal(str(currency_usdt_ticker['last'])) > 0:
        price_eth_usdt = Decimal(str(eth_usdt_ticker['last']))
        price_currency_usdt = Decimal(str(currency_usdt_ticker['last']))
        return price_eth_usdt / price_currency_usdt
        
    return Decimal(0)

def run_arbitrage():
    # Load pairs from CSV
    pairs_df = pd.read_csv(CSV_INPUT)

    # Initialize Binance exchange instance
    exchange = ccxt.binance()
    # Check if the exchange is connected
    markets = exchange.load_markets()
    if not markets:
        raise ConnectionError("Failed to load Binance markets.")

    # Initialize Web3 instance
    web3_instance = Web3(Web3.HTTPProvider(config.ETHEREUM_RPC_URL))
    # Check if Web3 is connected
    if not web3_instance.is_connected():
        raise ConnectionError("Failed to connect to Ethereum RPC.")


    results = []
    
    for _, row in pairs_df.iterrows():
        binance_pair = row["binance_pair"]
        uniswap_pair = row["uniswap_pair"]
        uniswap_pool_id = row["uniswap_pool_id"]
        # reverse_price: False -> Binance price: QUOTE/BASE, Uniswap pool's price: token1/token0 (token0=BASE, token1=QUOTE)
        #                True  -> Binance price: QUOTE/BASE, Uniswap reversed price: token0/token1 (token0=QUOTE, token1=BASE)
        reverse_price_on_uniswap = bool(row["reverse_price"]) 

        base_symbol = binance_pair.split('/')[0].upper()
        quote_symbol = binance_pair.split('/')[1].upper()

        uniswap_pool = UniswapPoolHelper(web3_instance, uniswap_pool_id)

        # Determine Uniswap pool's base token
        if not reverse_price_on_uniswap:
            uniswap_base_token_address = uniswap_pool.token0_address_cs
        else:
            uniswap_base_token_address = uniswap_pool.token1_address_cs

        # Get mid-prices
        binance_mid_price = Decimal(str(get_binance_mid_price(exchange, binance_pair)))
        uniswap_mid_price = uniswap_pool.get_current_price(reverse_price_on_uniswap)

        # Ask price on Binance - this is the price at which we can buy the base currency
        # Bid price on Binance - this is the price at which we can sell the base currency
        binance_ticker = exchange.fetch_ticker(binance_pair)
        binance_ask_price = Decimal(str(binance_ticker['ask']))
        binance_bid_price = Decimal(str(binance_ticker['bid']))

        # Calculate trade amount in base currency
        # We use TRADE_VALUE_USD to determine how much base currency we can trade
        trade_amount_base = Decimal(0)
        base_stablecoin_price = Decimal(0)
        stablecoin_symbol = None
        if quote_symbol in STABLECOINS_USD:
            trade_amount_base = TRADE_VALUE_USD / binance_mid_price
            base_stablecoin_price = binance_mid_price
            stablecoin_symbol = quote_symbol
        elif base_symbol in STABLECOINS_USD:
            trade_amount_base = TRADE_VALUE_USD
            base_stablecoin_price = Decimal(1)
            stablecoin_symbol = base_symbol
        else:
            for stablecoin_symbol in STABLECOINS_USD:
                # Try to get the price of base currency in terms of stablecoin
                base_stablecoin_pair = f"{base_symbol}/{stablecoin_symbol}"
                if base_stablecoin_pair in markets:
                    base_stablecoin_price = Decimal(str(exchange.fetch_ticker(base_stablecoin_pair)['last']))
                    trade_amount_base = TRADE_VALUE_USD / base_stablecoin_price
                    break
        
        if trade_amount_base <= 0:
            trade_amount_base = Decimal(1) # Default to 1 base currency if no stablecoin price found
            base_stablecoin_price = Decimal(0)
            stablecoin_symbol = None

        # --- Direction 1: Buy on Binance, Sell on Uniswap ---

        # We are buying base currency on Binance so we use the ask price
        binance_actual_price = binance_ask_price

        # Calculate the amount of quote currency we need to spend to buy the base currency + binance fee
        amount_in_quote = trade_amount_base * binance_actual_price * Decimal(str(1+config.BINANCE_FEE))

        # We are selling base currency on Uniswap so we use UniswapPoolHelper.get_sell_quote
        amount_out_quote, uniswap_new_price, uniswap_actual_price, gas_fee_eth = uniswap_pool.get_sell_quote(
            token_in_address_str=uniswap_base_token_address,
            amount_in=trade_amount_base
        )

        # Convert gas fee from ETH to quote currency
        gas_fee = gas_fee_eth * get_eth_price_in_currency(exchange, quote_symbol)

        # Calculate profit
        profit = amount_out_quote - amount_in_quote - gas_fee
        margin = profit / amount_in_quote if amount_in_quote > 0 else Decimal(0)

        # Calculate profit in stablecoin
        profit_stablecoin = Decimal(0)
        if base_stablecoin_price > 0:
            profit_stablecoin = profit / binance_mid_price * base_stablecoin_price

        # save results for this direction
        results.append(
            {
                "binance_pair": binance_pair,
                "uniswap_pair": uniswap_pair,
                "uniswap_pool_id": uniswap_pool_id,
                "reverse_price": int(reverse_price_on_uniswap),
                "base_symbol": base_symbol,
                "quote_symbol": quote_symbol,
                "uniswap_fee": float(uniswap_pool.fee)/1000000, # Uniswap fee is in basis points
                "binance_fee": config.BINANCE_FEE,
                "binance_mid_price": float(binance_mid_price),
                "uniswap_mid_price": float(uniswap_mid_price),
                "decision": "Buy on Binance, Sell on Uniswap",
                "trade_amount_base": float(trade_amount_base),
                "binance_actual_price": float(binance_actual_price),
                "uniswap_actual_price": float(uniswap_actual_price),
                "amount_in_quote": float(amount_in_quote),
                "amount_out_quote": float(amount_out_quote),
                "uniswap_new_price": float(uniswap_new_price),
                "gas_fee": float(gas_fee),
                "profit": float(profit),
                "margin": float(margin),
                "base_stablecoin_price": float(base_stablecoin_price),
                "stablecoin_symbol": stablecoin_symbol,
                "profit_stablecoin": float(profit_stablecoin)
            }
        )

        print(f"Processed pair: {binance_pair} direction 1, Margin: {margin:.6f}, Profit: {profit:.6f} {quote_symbol}, Profit in {stablecoin_symbol}: {profit_stablecoin:.6f}")

        # --- Direction 2: Buy on Uniswap, Sell on Binance ---

        # We are selling base currency on Binance so we use the bid price
        binance_actual_price = binance_bid_price

        # Calculate the amount of quote currency we receive from selling the base currency on Binance - binance fee
        amount_out_quote = trade_amount_base * binance_actual_price * Decimal(str(1-config.BINANCE_FEE))

        # We are buying base currency on Uniswap so we use UniswapPoolHelper.get_buy_quote
        amount_in_quote, uniswap_new_price, uniswap_actual_price, gas_fee_eth = uniswap_pool.get_buy_quote(
            token_out_address_str=uniswap_base_token_address,
            amount_out=trade_amount_base
        )

        # Convert gas fee from ETH to quote currency
        gas_fee = gas_fee_eth * get_eth_price_in_currency(exchange, quote_symbol)

        # Calculate profit
        profit = amount_out_quote - amount_in_quote - gas_fee
        margin = profit / amount_in_quote if amount_in_quote > 0 else Decimal(0)

        # Calculate profit in stablecoin
        profit_stablecoin = Decimal(0)
        if base_stablecoin_price > 0:
            profit_stablecoin = profit / binance_mid_price * base_stablecoin_price

        # save results for this direction
        results.append(
            {
                "binance_pair": binance_pair,
                "uniswap_pair": uniswap_pair,
                "uniswap_pool_id": uniswap_pool_id,
                "reverse_price": int(reverse_price_on_uniswap),
                "base_symbol": base_symbol,
                "quote_symbol": quote_symbol,
                "uniswap_fee": float(uniswap_pool.fee)/1000000, # Uniswap fee is in basis points
                "binance_fee": config.BINANCE_FEE,
                "binance_mid_price": float(binance_mid_price),
                "uniswap_mid_price": float(uniswap_mid_price),
                "decision": "Buy on Uniswap, Sell on Binance",
                "trade_amount_base": float(trade_amount_base),
                "binance_actual_price": float(binance_actual_price),
                "uniswap_actual_price": float(uniswap_actual_price),
                "amount_in_quote": float(amount_in_quote),
                "amount_out_quote": float(amount_out_quote),
                "uniswap_new_price": float(uniswap_new_price),
                "gas_fee": float(gas_fee),
                "profit": float(profit),
                "margin": float(margin),
                "base_stablecoin_price": float(base_stablecoin_price),
                "stablecoin_symbol": stablecoin_symbol,
                "profit_stablecoin": float(profit_stablecoin)
            }
        )

        print(f"Processed pair: {binance_pair} direction 2, Margin: {margin:.6f}, Profit: {profit:.6f} {quote_symbol}, Profit in {stablecoin_symbol}: {profit_stablecoin:.6f}")

    # Save results to CSV
    results_df = pd.DataFrame(results)
    results_df.to_csv(CSV_OUTPUT, index=False, float_format='%.10f')
    print(f"Results saved to {CSV_OUTPUT}")

if __name__ == "__main__":
    profiler = cProfile.Profile()
    profiler.enable()

    # Запускаем основную логику
    run_arbitrage()


    profiler.disable()

    s = io.StringIO()
    # Сортировка по совокупному времени
    sortby = 'cumulative' # Другие опции: 'tottime', 'ncalls'
    ps = pstats.Stats(profiler, stream=s).sort_stats(sortby)
    ps.print_stats(25)  # Показать топ-25 функций

    print("\n--- Результаты профилирования ---")
    print(s.getvalue())

    # Сохранение результатов профилирования для snakeviz
    profiler_filename = 'arbitrage_profile.prof'
    profiler.dump_stats(profiler_filename)
    print(f"\nСтатистика профилирования сохранена в {profiler_filename}")
    print(f"Для визуализации можете использовать команду в терминале: snakeviz {profiler_filename}")

