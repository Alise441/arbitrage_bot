from pathlib import Path
import json
import os
import logging
from dotenv import load_dotenv
from types import SimpleNamespace

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# ======== Configuration Constants ========
config = SimpleNamespace(
    PROFIT_THRESHOLD=float(os.getenv("PROFIT_THRESHOLD", "0.0001")),       # 0.01%
    BINANCE_FEE=float(os.getenv("BINANCE_FEE", "0.00017250")),            # 0.01725%
    ETHEREUM_RPC_URL=os.getenv("ETHEREUM_RPC_URL", "https://ethereum-rpc.publicnode.com"),
    UNISWAP_SUBGRAPH_ID="5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    QUOTER_ADDRESS="0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
)

# ======== Validate THEGRAPH_API_KEY ========
THEGRAPH_API_KEY = os.getenv("THEGRAPH_API_KEY")
if not THEGRAPH_API_KEY:
    logger.critical(
        "Critical Error: THEGRAPH_API_KEY environment variable is not set. "
        "Visit https://thegraph.com/studio to obtain an API key."
    )
    raise EnvironmentError("Missing THEGRAPH_API_KEY")

config.THEGRAPH_ENDPOINT = (
    f"https://gateway.thegraph.com/api/{THEGRAPH_API_KEY}/subgraphs/id/{config.UNISWAP_SUBGRAPH_ID}"
)

# ======== ABI Paths ========
ABI_DIR = Path("abi")  # Directory with ABI files

ABI_PATHS = {
    "QUOTER": ABI_DIR / "UniswapV3QuoterV2.json",
    "POOL": ABI_DIR / "UniswapV3Pool.json",
    "ERC20": ABI_DIR / "Erc20.json",
}

# ======== Load ABI Function ========
def load_abis():
    abis = {}
    for name, path in ABI_PATHS.items():
        try:
            with open(path, "r") as f:
                abis[name] = json.load(f)
        except FileNotFoundError:
            logger.critical(f"Critical Error: ABI file not found at {path}")
            raise
        except json.JSONDecodeError as e:
            logger.critical(f"Critical Error: Failed to parse JSON in {path}. Error: {e}")
            raise
    return abis