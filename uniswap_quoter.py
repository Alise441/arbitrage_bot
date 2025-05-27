from web3 import Web3
import json
from pathlib import Path
from decimal import Decimal, getcontext

# Address of the Uniswap V3 Quoter V2 contract
_QUOTER_ADDRESS = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

# Directory containing ABI files
_ABI_DIR = Path("abi") # Assumes 'abi' folder is in the same dir or accessible

# Paths to ABI files
_QUOTER_ABI_PATH = _ABI_DIR / "UniswapV3QuoterV2.json"
_POOL_ABI_PATH = _ABI_DIR / "UniswapV3Pool.json"
_ERC20_ABI_PATH = _ABI_DIR / "Erc20.json"

# Load ABIs from JSON files
try:
    with open(_QUOTER_ABI_PATH, 'r') as f:
        _quoter_abi = json.load(f)
    with open(_POOL_ABI_PATH, 'r') as f:
        _pool_abi = json.load(f)
    with open(_ERC20_ABI_PATH, 'r') as f:
        _erc20_abi = json.load(f)
except FileNotFoundError as e:
    print(
        f"Critical Error: ABI file not found. "
        f"Please ensure '{_QUOTER_ABI_PATH}', '{_POOL_ABI_PATH}', and '{_ERC20_ABI_PATH}' exist. "
        f"Original error: {e}"
    )
    # Depending on your project, you might want to exit or raise the error
    # For now, we'll re-raise to halt execution if ABIs are essential.
    raise
except json.JSONDecodeError as e:
    print(f"Critical Error: Failed to decode ABI JSON from file. Error: {e}")
    raise

# Helper function to calculate readable pool's price from sqrtPriceX96
def _calculate_price_from_sqrtprice(
    sqrt_price_x96: Decimal,
    decimals_pool_token0: int,
    decimals_pool_token1: int
) -> Decimal:
    """
    Calculates readable price of pool's token0 in terms of pool's token1.
    Result is: amount of pool_token1 per one unit of pool_token0.
    """
    price_raw_t1_per_t0 = (sqrt_price_x96 / Decimal(2**96)) ** 2
    adjusted_price = price_raw_t1_per_t0 * (Decimal(10) ** (decimals_pool_token0 - decimals_pool_token1))
    return adjusted_price


# Uniswap V3 quote for selling an exact amount of an input token
def uniswap_sell_quote(
    w3: Web3,
    amount_in: Decimal,
    token_in_address: str,
    pool_address: str
):
    """
    Calculates a Uniswap V3 quote for selling an exact amount of an input token.

    Args:
        w3: Web3 instance connected to an Ethereum node.
        amount_in (Decimal): The human-readable amount of token_in to sell.
        token_in_address (str): Address of the input token (the token you are selling).
        pool_address (str): Address of the Uniswap V3 pool for the token pair.

    Returns:
        tuple: (amount_out, new_price, actual_price, gas_fee_eth)
            - amount_out (Decimal): The amount of token_out you will receive.
            - new_price (Decimal): The new pool price after the swap (token_out per token_in).
            - actual_price (Decimal): The effective price of the swap (token_out per token_in).
            - gas_fee_eth (Decimal): Estimated gas fee for the transaction in ETH.
    """

    # Ensure addresses are checksummed
    token_in_address_cs = Web3.to_checksum_address(token_in_address)
    pool_address_cs = Web3.to_checksum_address(pool_address)
    quoter_address_cs = Web3.to_checksum_address(_QUOTER_ADDRESS)

    # Initialize contracts (using globally loaded ABIs)
    quoter_contract = w3.eth.contract(address=quoter_address_cs, abi=_quoter_abi)
    pool_contract = w3.eth.contract(address=pool_address_cs, abi=_pool_abi)
    token_in_contract = w3.eth.contract(address=token_in_address_cs, abi=_erc20_abi)

    # Determine token_out_address based on pool's token0 and token1
    pool_token0_addr_cs = Web3.to_checksum_address(pool_contract.functions.token0().call())
    pool_token1_addr_cs = Web3.to_checksum_address(pool_contract.functions.token1().call())

    token_out_address_cs = None
    is_token_in_token0 = False # Flag to indicate if token_in is token0 or token1

    if token_in_address_cs == pool_token0_addr_cs:
        token_out_address_cs = pool_token1_addr_cs
        is_token_in_token0 = True
    elif token_in_address_cs == pool_token1_addr_cs:
        token_out_address_cs = pool_token0_addr_cs
        is_token_in_token0 = False
    else:
        raise ValueError(f"token_in_address {token_in_address_cs} is not part of the specified pool {pool_address_cs}.")

    # Initialize token_out contract
    token_out_contract = w3.eth.contract(address=token_out_address_cs, abi=_erc20_abi)

    # Get decimals for input and output tokens
    decimals_in = token_in_contract.functions.decimals().call()
    decimals_out = token_out_contract.functions.decimals().call()

    # Convert human-readable input amount to base units
    amount_in_base_units = int(amount_in * (Decimal(10) ** decimals_in))

    # Get pool fee
    fee = pool_contract.functions.fee().call()

    # Prepare parameters for the QuoterV2 quoteExactInputSingle function
    quote_params = {
        'tokenIn': token_in_address_cs,
        'tokenOut': token_out_address_cs,
        'fee': fee,
        'amountIn': amount_in_base_units,
        'sqrtPriceLimitX96': 0  # 0 means no price limit
    }

    # Call the quoter
    # Returns (uint256 amountOut, uint160 sqrtPriceX96After, uint32 initializedTicksCrossed, uint256 gasEstimate)
    quote_result = quoter_contract.functions.quoteExactInputSingle(quote_params).call()

    amount_out_base_units = Decimal(quote_result[0])
    sqrt_price_x96_after_swap = Decimal(quote_result[1])
    gas_estimate = Decimal(quote_result[3])

    # Convert amount_out to readable format
    amount_out = amount_out_base_units / (Decimal(10) ** decimals_out)

    # Calculate new pool price after the hypothetical swap
    decimals_pool_token0 = decimals_in if is_token_in_token0 else decimals_out
    decimals_pool_token1 = decimals_out if is_token_in_token0 else decimals_in
    price_pool_t1_per_t0_after_swap = _calculate_price_from_sqrtprice(
        sqrt_price_x96_after_swap,
        decimals_pool_token0,
        decimals_pool_token1
    ) 

    # Calculate new price (token_out per token_in)
    if is_token_in_token0:
        new_price = price_pool_t1_per_t0_after_swap
    else:
        new_price = Decimal(1) / price_pool_t1_per_t0_after_swap

    # Calculate actual swap price
    actual_price = amount_out / amount_in

    # Calculate gas fee in ETH
    current_gas_price_wei = Decimal(w3.eth.gas_price) # This is in wei
    gas_fee_eth = gas_estimate * current_gas_price_wei / (Decimal(10)**18) # Convert to ETH

    return amount_out, new_price, actual_price, gas_fee_eth


# # Uniswap V3 quote for buying an exact amount of an output token
def uniswap_buy_quote(
    w3: Web3,
    amount_out: Decimal,
    token_out_address: str, # Address of the token we want to buy
    pool_address: str
):
    """
    Calculates a Uniswap V3 quote for buying an exact amount of an output token.

    Args:
        w3 (Web3): Web3 instance connected to an Ethereum node.
        amount_out (Decimal): The human-readable amount of token_out you want to receive.
        token_out_address (str): Address of the output token (the token you are buying).
        pool_address (str): Address of the Uniswap V3 pool for the token pair.

    Returns:
        tuple: (amount_in, new_price, actual_price, gas_fee_eth)
            - amount_in (Decimal): How much token_in needs to be spent.
            - new_price (Decimal): The new pool price after the swap (token_in per token_out).
            - actual_price (Decimal): The effective price of the purchase (token_in per token_out).
            - gas_fee_eth (Decimal): Estimated gas fee for the transaction in ETH.
    """

    # Checksum addresses
    token_out_address_cs = Web3.to_checksum_address(token_out_address)
    pool_address_cs = Web3.to_checksum_address(pool_address)
    quoter_address_cs = Web3.to_checksum_address(_QUOTER_ADDRESS)

    # Initialize contracts
    quoter_contract = w3.eth.contract(address=quoter_address_cs, abi=_quoter_abi)
    pool_contract = w3.eth.contract(address=pool_address_cs, abi=_pool_abi)
    token_out_contract = w3.eth.contract(address=token_out_address_cs, abi=_erc20_abi) 

    # Determine pool's token0 and token1, and derive token_in_address
    pool_token0_addr_cs = Web3.to_checksum_address(pool_contract.functions.token0().call())
    pool_token1_addr_cs = Web3.to_checksum_address(pool_contract.functions.token1().call())

    token_in_address_cs = None
    is_token_in_token0 = False # Flag to indicate if token_in is token0 or token1

    if token_out_address_cs == pool_token1_addr_cs:
        token_in_address_cs = pool_token0_addr_cs
        is_token_in_token0 = True
    elif token_out_address_cs == pool_token0_addr_cs:
        token_in_address_cs = pool_token1_addr_cs
        is_token_in_token0 = False
    else:
        raise ValueError(f"token_out_address {token_out_address} is not part of the pool {pool_address}.")

    # Initialize token_in contract and get its decimals
    token_in_contract = w3.eth.contract(address=token_in_address_cs, abi=_erc20_abi)

    # Get decimals for input and output tokens
    decimals_in = token_in_contract.functions.decimals().call()
    decimals_out = token_out_contract.functions.decimals().call()

    # Convert desired output amount to its base units
    amount_out_base_units = int(amount_out * (Decimal(10) ** decimals_out))

    # Get pool fee
    fee = pool_contract.functions.fee().call()

    # Prepare parameters for QuoterV2 quoteExactOutputSingle function
    quote_params = {
        'tokenIn': token_in_address_cs,
        'tokenOut': token_out_address_cs,
        'fee': fee,
        'amount': amount_out_base_units,
        'sqrtPriceLimitX96': 0 # 0 means no price limit
    }

    # Call the quoter
    # Returns (uint256 amountIn, uint160 sqrtPriceX96After, uint32 initializedTicksCrossed, uint256 gasEstimate)
    quote_result = quoter_contract.functions.quoteExactOutputSingle(quote_params).call()

    amount_in_base_units = Decimal(quote_result[0])
    sqrt_price_x96_after_swap = Decimal(quote_result[1])
    gas_estimate = Decimal(quote_result[3])

    # Convert required input amount to readable format
    amount_in = amount_in_base_units / (Decimal(10) ** decimals_in)

    # Calculate new pool price after the hypothetical swap
    decimals_pool_token0 = decimals_in if is_token_in_token0 else decimals_out
    decimals_pool_token1 = decimals_out if is_token_in_token0 else decimals_in
    price_pool_t1_per_t0_after_swap = _calculate_price_from_sqrtprice(
        sqrt_price_x96_after_swap,
        decimals_pool_token0,
        decimals_pool_token1
    ) 

    # Calculate new price (token_in per token_out)
    if is_token_in_token0: 
        new_price = Decimal(1) / price_pool_t1_per_t0_after_swap
    else: 
        new_price = price_pool_t1_per_t0_after_swap

    # Calculate actual swap price
    actual_price = amount_in / amount_out

    # Calculate gas fee in ETH
    current_gas_price_wei = Decimal(w3.eth.gas_price) # This is in wei
    gas_fee_eth = gas_estimate * current_gas_price_wei / (Decimal(10)**18) # Convert to ETH

    return amount_in, new_price, actual_price, gas_fee_eth


# --- Example Usage (you would call this from another file/part of your project) ---
if __name__ == '__main__':
    # This example part will only run if the script is executed directly.
    # Ensure getcontext().prec is set before running!
    getcontext().prec = 60 # Example: Set precision

    # Replace with your actual RPC URL
    RPC_URL = "https://eth-mainnet.blastapi.io/5c4fa95a-9255-4cde-accd-92de8cf74e8b" # Example RPC
    
    # Connect to Ethereum
    web3_instance = Web3(Web3.HTTPProvider(RPC_URL))
    if not web3_instance.is_connected():
        print("Failed to connect to Ethereum mainnet via RPC_URL.")
        exit()
    print(f"Connected to Ethereum chain ID: {web3_instance.eth.chain_id}")

    # --- Define parameters for the quote ---
    # Example: Selling 1000 USDC for WETH using the USDC/WETH 0.05% fee pool
    # (Make sure these addresses and pool are correct for your use case)
    
    usdc_address = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    weth_address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    # Common USDC/WETH pool with 0.05% fee (500)
    usdc_weth_pool_005_address = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"


    # Example: Selling 1000 USDC for WETH
    amount_to_sell_usdc = Decimal(1000)
    print(f"\nFetching quote to sell {amount_to_sell_usdc} USDC for WETH...")
    amount_out, new_price, actual_price, gas_fee = uniswap_sell_quote(
            w3=web3_instance,
            amount_in=amount_to_sell_usdc,
            token_in_address=usdc_address,
            pool_address=usdc_weth_pool_005_address
        )

    print(f"  -> Amount WETH out: {amount_out:.8f} WETH")
    print(f"  -> New pool price after hypothetical swap: {new_price:.10f} WETH per USDC")
    print(f"  -> Actual swap price: {actual_price:.10f} WETH per USDC")
    print(f"  -> Estimated gas fee for swap: {gas_fee:.6f} ETH")

    # Example: Selling WETH for USDC
    amount_to_sell_weth = Decimal(1)
    print(f"\nFetching quote to sell {amount_to_sell_weth} WETH for USDC...")
    amount_out_usdc, new_price_w_u, actual_price_w_u, gas_fee_w_u = uniswap_sell_quote(
        w3=web3_instance,
        amount_in=amount_to_sell_weth,
        token_in_address=weth_address,
        pool_address=usdc_weth_pool_005_address
    )
    
    print(f"  -> Amount USDC out: {amount_out_usdc:.2f} USDC")
    print(f"  -> New pool price after hypothetical swap: {new_price_w_u:.6f} USDC per WETH")
    print(f"  -> Actual swap price: {actual_price_w_u:.6f} USDC per WETH")
    print(f"  -> Estimated gas fee for swap: {gas_fee_w_u:.6f} ETH")


    # Example: Buying WETH with USDC
    amount_to_buy_weth = Decimal(1)
    print(f"\nFetching quote to buy {amount_to_buy_weth:.2f} WETH with USDC...")
    amount_in_usdc, new_price_buy, actual_price_buy, gas_fee_buy = uniswap_buy_quote(
        w3=web3_instance,
        amount_out=amount_to_buy_weth,
        token_out_address=weth_address,
        pool_address=usdc_weth_pool_005_address
    )
    print(f"  -> Amount USDC in: {amount_in_usdc:.2f} USDC")
    print(f"  -> New pool price after hypothetical swap: {new_price_buy:.10f} USDC per WETH")
    print(f"  -> Actual swap price: {actual_price_buy:.10f} USDC per WETH")
    print(f"  -> Estimated gas fee for swap: {gas_fee_buy:.6f} ETH")

    # Example: Buying USDC with WETH
    amount_to_buy_usdc = Decimal(1000)
    print(f"\nFetching quote to buy {amount_to_buy_usdc:.2f} USDC with WETH...")
    amount_in_weth, new_price_buy_u, actual_price_buy_u, gas_fee_buy_u = uniswap_buy_quote(
        w3=web3_instance,
        amount_out=amount_to_buy_usdc,
        token_out_address=usdc_address,
        pool_address=usdc_weth_pool_005_address
    )
    print(f"  -> Amount WETH in: {amount_in_weth:.8f} WETH")
    print(f"  -> New pool price after hypothetical swap: {new_price_buy_u:.10f} WETH per USDC")
    print(f"  -> Actual swap price: {actual_price_buy_u:.10f} WETH per USDC")
    print(f"  -> Estimated gas fee for swap: {gas_fee_buy_u:.6f} ETH")




    # Example: Selling WETH for USDC
    amount_to_sell_weth = Decimal(1)
    print(f"\nFetching quote to sell {amount_to_sell_weth} WETH for USDC...")
    amount_out_usdc, new_price_w_u, actual_price_w_u, gas_fee_w_u = uniswap_sell_quote(
        w3=web3_instance,
        amount_in=amount_to_sell_weth,
        token_in_address=weth_address,
        pool_address=usdc_weth_pool_005_address
    )
    
    print(f"  -> Amount USDC out: {amount_out_usdc:.2f} USDC")
    print(f"  -> New pool price after hypothetical swap: {new_price_w_u:.6f} USDC per WETH")
    print(f"  -> Actual swap price: {actual_price_w_u:.6f} USDC per WETH")
    print(f"  -> Estimated gas fee for swap: {gas_fee_w_u:.6f} ETH")

    # Example: Buying USDC with WETH
    amount_to_buy_usdc = amount_out_usdc # Use the amount we just got from selling WETH
    print(f"\nFetching quote to buy {amount_to_buy_usdc:.2f} USDC with WETH...")
    amount_in_weth, new_price_buy_u, actual_price_buy_u, gas_fee_buy_u = uniswap_buy_quote(
        w3=web3_instance,
        amount_out=amount_to_buy_usdc,
        token_out_address=usdc_address,
        pool_address=usdc_weth_pool_005_address
    )
    print(f"  -> Amount WETH in: {amount_in_weth:.8f} WETH")
    print(f"  -> New pool price after hypothetical swap: {new_price_buy_u:.10f} WETH per USDC")
    print(f"  -> Actual swap price: {actual_price_buy_u:.10f} WETH per USDC")
    print(f"  -> Estimated gas fee for swap: {gas_fee_buy_u:.6f} ETH")