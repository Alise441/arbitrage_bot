# UniBin Arbitrage

**UniBin Arbitrage** is a Python-based arbitrage bot designed to monitor and identify price discrepancies between Binance (a centralized exchange) and Uniswap V3 (a decentralized exchange). When a profitable opportunity is found, it logs the data, sends a notification via Telegram, and can execute a test trade on the Sepolia testnet.

## Features

- **Cross-Exchange Monitoring:** Simultaneously fetches price data from Binance and Uniswap V3.
- **Arbitrage Calculation:** Calculates potential profit margins after accounting for trading fees and estimated gas costs.
- **Configurable Thresholds:** Only flags opportunities that meet a user-defined profit margin.
- **Asynchronous Execution:** Uses thread pools for non-blocking data fetching and trade execution.
- **Telegram Notifications:** Sends real-time alerts for bot status and found opportunities.
- **Detailed Logging:** Saves all potential arbitrage opportunities to a CSV file for analysis and logs trade executions separately.
- **Testnet Trading:** Executes demonstrative trades on the Sepolia testnet to validate the execution logic without risking real funds.

---

## Getting Started

Follow these instructions to set up and run the project locally.

### 1. Prerequisites

- **Python:** Ensure you have Python 3.8 or newer installed.
- **Git:** Required to clone the repository.

### 2. Clone the Repository

Clone this repository to your local machine:
```bash
git clone https://gitlab.uzh.ch/alina.ponomareva/unibin-arbitrage.git
cd unibin-arbitrage
```

### 3. Set Up a Virtual Environment

It is highly recommended to use a virtual environment to manage project dependencies.

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows
venv\Scripts\activate
# On macOS/Linux
source venv/bin/activate
```

### 4. Install Dependencies

Install all the required Python packages from the `requirements.txt` file.

```bash
pip install -r requirements.txt
```

### 5. Configure Environment Variables

The bot uses a `.env` file to manage sensitive keys and configuration.

1. Create a file named `.env` in the root of the project.

2. Copy the content of the example below and paste it into your `.env` file.

3. Replace the placeholder values with your actual credentials.

```ini
# .env.example

# -- APIs & Endpoints --
# Your Ethereum node RPC URL (e.g., from Infura, Alchemy, or a public node). By default https://ethereum-rpc.publicnode.com is used.
ETHEREUM_RPC_URL=""
# Your API key for The Graph (required for fetching Uniswap pool data)
# Get one from: [https://thegraph.com/studio/](https://thegraph.com/studio/)
THEGRAPH_API_KEY=""

# -- Wallet for Testnet Trading --
# Your public wallet address (e.g., 0x...)
WALLET_ADDRESS=""
# The private key for the wallet above (prefixed with 0x)
# DANGER: Keep this key secure and never commit it to Git.
WALLET_PRIVATE_KEY=""

# -- Telegram Notifications (Optional) --
# Your Telegram Bot Token from BotFather
TELEGRAM_TOKEN=""
# Your Telegram Chat ID
TELEGRAM_CHAT_ID=""

# -- Trading Parameters (Optional) --
# Minimum profit margin to trigger a trade (e.g., 0.001 for 0.1%). By default 0.0001
PROFIT_THRESHOLD="0.0001"
# Your trading fee on Binance (e.g., 0.001 for 0.1%). By default 0.00017250
BINANCE_FEE="0.00017250"
```

## Usage Instructions

Follow these steps to run the arbitrage bot.

### Step 1: Generate Arbitrage Pairs

First, you need to generate the `arbitrage_pairs.csv` file, which contains the list of trading pairs the bot will monitor.

Run the Jupyter Notebook: `get_arbitrage_pairs.ipynb` (for example in Google Colab).

Execute all the cells in the notebook. This will create the `arbitrage_pairs.csv` file in your project directory.

### Step 2: Run the Arbitrage Bot

Once the CSV file is generated, you can start the main bot.

Execute the main script from your terminal:

```bash
python binance_uniswap_arbitrage.py
```

The script will prompt you to enter a trade amount in USD. After you provide an amount, the bot will start its monitoring cycle.

- Real-time logs will be printed to the console and saved to `arbitrage_bot.log`.
- All calculations will be recorded in `arbitrage_results_{amount}.csv`.
- Executed test trades will be logged in `trades.log`.

To stop the bot, press `Ctrl+C` in the terminal.

## Disclaimer

This project is for educational and demonstrative purposes only. It executes trades on the **Sepolia testnet** and does not handle real funds on the mainnet in its current state. Financial markets are volatile, and arbitrage opportunities are often fleeting and highly competitive. Using automated trading bots carries significant risk. The authors are not responsible for any financial losses. Always do your own research and use at your own risk.
