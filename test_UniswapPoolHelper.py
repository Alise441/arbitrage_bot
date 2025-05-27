
import config
from web3 import Web3
from decimal import Decimal, getcontext

# Импортируем наш класс
from uniswap_pool_helper import UniswapPoolHelper

if __name__ == '__main__':
    getcontext().prec = 60  # Set decimal precision for calculations
    # Example usage
    
    w3 = Web3(Web3.HTTPProvider(config.ETHEREUM_RPC_URL))
    if not w3.is_connected():
        raise ConnectionError("Failed to connect to Ethereum RPC.")
    print("Connected to Ethereum RPC.")

    pool_address = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"  # Example Uniswap V3 pool address
    pool = UniswapPoolHelper(w3, pool_address)
    usdc_address = pool.token0_address_cs  # USDC address in this pool
    weth_address = pool.token1_address_cs  # WETH address in this pool

    # Get current price of USDC in terms of WETH
    current_price = pool.get_current_price()
    # Get current price of WETH in terms of USDC
    current_price_inverted = pool.get_current_price(price_of_token0_in_token1=False)
    print(f"Current Price: 1 USDC = {current_price:.8f} WETH")
    print(f"Current Price: 1 WETH = {current_price_inverted:.8f} USDC")

    print("-"*50)

    # Get quote for selling 100 USDC (token0)
    print("Sell 100 USDC (token0):")
    amount_in = Decimal('100.0')  # 1 token0
    amount_out, new_price, actual_price, gas_fee = pool.get_sell_quote(usdc_address, amount_in)
    print(f"Get: {amount_out:.8f} WETH, \nNew Price: {new_price:.8f}, \nActual Price: {actual_price:.8f}, \nGas Fee: {gas_fee:.8f} ETH")

    print("-"*50)

    # Get quote for selling 1 WETH (token1)
    print("Sell 1 WETH (token1):")
    amount_in = Decimal('1.0')  # 1 token1
    amount_out, new_price, actual_price, gas_fee = pool.get_sell_quote(pool.token1_address_cs, amount_in)
    print(f"Get: {amount_out:.8f} USDC, \nNew Price: {new_price:.8f}, \nActual Price: {actual_price:.8f}, \nGas Fee: {gas_fee:.8f} ETH")

    print("-"*50)

    # Get quote for buying 100 USDC (token0)
    print("Buy 100 USDC (token0):")
    amount_out = Decimal('100.0')  # 100 token0
    amount_in, new_price, actual_price, gas_fee = pool.get_buy_quote(pool.token0_address_cs, amount_out)
    print(f"Pay: {amount_in:.8f} WETH, \nNew Price: {new_price:.8f}, \nActual Price: {actual_price:.8f}, \nGas Fee: {gas_fee:.8f} ETH")

    print("-"*50)

    # Get quote for buying 1 WETH (token1)
    print("Buy 1 WETH (token1):")
    amount_out = Decimal('1.0')  # 1 token1
    amount_in, new_price, actual_price, gas_fee = pool.get_buy_quote(pool.token1_address_cs, amount_out)
    print(f"Pay: {amount_in:.8f} USDC, \nNew Price: {new_price:.8f}, \nActual Price: {actual_price:.8f}, \nGas Fee: {gas_fee:.8f} ETH")

    print("-"*50)

    print("Buy 2 WETH (token1):")
    amount_out = Decimal('2.0')  # 2 token1
    amount_in, new_price, actual_price, gas_fee = pool.get_buy_quote(pool.token1_address_cs, amount_out)
    print(f"Pay: {amount_in:.8f} USDC, \nNew Price: {new_price:.8f}, \nActual Price: {actual_price:.8f}, \nGas Fee: {gas_fee:.8f} ETH")

    print("-"*50)

    print(f"Sell {amount_in:.8f} USDC (token0):")
    amount_out, new_price, actual_price, gas_fee = pool.get_sell_quote(usdc_address, amount_in)
    print(f"Get: {amount_out:.8f} WETH, \nNew Price: {new_price:.8f}, \nActual Price: {actual_price:.8f}, \nGas Fee: {gas_fee:.8f} ETH")
    
    print("-"*50)

