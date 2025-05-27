from pathlib import Path
import json
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Global variables
PROFIT_THRESHOLD = 0.001  # Minimum profit threshold for arbitrage (0.1%)
ETHEREUM_RPC_URL = os.environ.get("ETHEREUM_RPC_URL", "https://ethereum-rpc.publicnode.com")  # Default RPC URL if not set

# Address of the Uniswap V3 Quoter V2 contract
QUOTER_ADDRESS = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

# Directory containing ABI files
ABI_DIR = Path("abi") # Assumes 'abi' folder is in the same dir or accessible

# Paths to ABI files
QUOTER_ABI_PATH = ABI_DIR / "UniswapV3QuoterV2.json"
POOL_ABI_PATH = ABI_DIR / "UniswapV3Pool.json"
ERC20_ABI_PATH = ABI_DIR / "Erc20.json"

# Load ABIs from JSON files
try:
    with open(QUOTER_ABI_PATH, 'r') as f:
        QUOTER_ABI = json.load(f)
    with open(POOL_ABI_PATH, 'r') as f:
        POOL_ABI = json.load(f)
    with open(ERC20_ABI_PATH, 'r') as f:
        ERC20_ABI = json.load(f)
except FileNotFoundError as e:
    print(
        f"Critical Error: ABI file not found. "
        f"Please ensure '{QUOTER_ABI_PATH}', '{POOL_ABI_PATH}', and '{ERC20_ABI_PATH}' exist. "
        f"Original error: {e}"
    )
    raise
except json.JSONDecodeError as e:
    print(f"Critical Error: Failed to decode ABI JSON from file. Error: {e}")
    raise