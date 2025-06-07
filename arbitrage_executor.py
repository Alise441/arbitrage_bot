import logging
from decimal import Decimal
from uniswap_pool_helper import UniswapPoolHelper, Token
from config import config
from telegram_utils import send_telegram_message, send_error_notification

# Create a logger specifically for the execution module
logger = logging.getLogger('trade_executor')

def execute_arbitrage_trade(
    direction: str,
    pool: UniswapPoolHelper,
    token_to_trade: Token,
    amount: Decimal,
    slippage: Decimal,
    active_trades_lock,
    active_trades_set,
    pair_key_locked: tuple
):
    """
    Executes one leg of an arbitrage trade on Uniswap and is designed to be run in a separate thread.

    Args:
        direction (str): Either "Buy on Binance, Sell on Uniswap" or "Buy on Uniswap, Sell on Binance".
        pool (UniswapPoolHelper): The initialized helper for the target pool.
        token_to_trade (Token): The token object to be bought or sold on Uniswap.
        amount (Decimal): The amount of the token to trade.
        slippage (Decimal): The slippage tolerance for the transaction.
        active_trades_lock (threading.Lock): Lock for safely modifying the active trades set.
        active_trades_set (set): The set of currently active trading pairs.
        pair_key_locked (tuple): A tuple containing the symbols of the tokens locked in the trade. 
            It is used only for testing purposes because in testing we trade a different pair than the one we are monitoring.
    """
    pair_key = (pool.token0.symbol, pool.token1.symbol)

    try:
        logger.info(f"EXECUTION THREAD for {pair_key_locked}: "
                    f"Starting trade execution for direction: {direction}.")

        # For a real implementation, you would add your Binance API calls here.
        # For now, we will proceed directly with the Uniswap transaction.

        if "Sell on Uniswap" in direction:
            # In this direction, we are selling on Uniswap.
            # The 'token_to_trade' is the one we sell, 'amount' is how much we sell.

            logger.info(f"Executing Uniswap SELL of {amount:.6f} {token_to_trade.symbol}...")
            send_telegram_message(f"Executing Uniswap SELL of {amount:.6f} {token_to_trade.symbol}...")

            tx_hash = pool.sell(
                token_in=token_to_trade,
                amount_in=amount,
                recipient_address=config.WALLET_ADDRESS,
                private_key=config.WALLET_PRIVATE_KEY,
                slippage=slippage,
                quoter_address=config.QUOTER_ADDRESS_SEPOLIA,
                router_address=config.ROUTER_ADDRESS_SEPOLIA
            )
            if tx_hash:
                logger.info(f"SUCCESS: Uniswap sell transaction successful for {pair_key}. See: https://sepolia.etherscan.io/tx/0x{tx_hash}")
                send_telegram_message(f"SUCCESS: Uniswap sell transaction successful for {pair_key}. See: https://sepolia.etherscan.io/tx/0x{tx_hash}")
            else:
                logger.warning(f"Uniswap sell was not confirmed or was cancelled for {pair_key}.")
                send_error_notification(f"Uniswap sell was not confirmed or was cancelled for {pair_key}.")

        elif "Buy on Uniswap" in direction:
            # In this direction, we are buying on Uniswap.
            # The 'token_to_trade' is the one we want to receive, 'amount' is how much.

            logger.info(f"Executing Uniswap BUY of {amount:.6f} {token_to_trade.symbol}...")
            send_telegram_message(f"Executing Uniswap BUY of {amount:.6f} {token_to_trade.symbol}...")

            tx_hash = pool.buy(
                token_out=token_to_trade,
                amount_out=amount,
                recipient_address=config.WALLET_ADDRESS,
                private_key=config.WALLET_PRIVATE_KEY,
                slippage=slippage,
                quoter_address=config.QUOTER_ADDRESS_SEPOLIA,
                router_address=config.ROUTER_ADDRESS_SEPOLIA
            )
            if tx_hash:
                logger.info(f"SUCCESS: Uniswap buy transaction successful for {pair_key}. See: https://sepolia.etherscan.io/tx/0x{tx_hash}")
                send_telegram_message(f"SUCCESS: Uniswap buy transaction successful for {pair_key}. See: https://sepolia.etherscan.io/tx/0x{tx_hash}")
            else:
                logger.warning(f"Uniswap buy was not confirmed or was cancelled for {pair_key}.")
                send_error_notification(f"Uniswap buy was not confirmed or was cancelled for {pair_key}.")

        # Here you would add the logic for the second leg of the trade on Binance.

    except Exception as e:
        logger.error(f"EXECUTION THREAD for {pair_key_locked}: "
                     f"An error occurred during trade execution: {e}", exc_info=True)
        send_error_notification(f"EXECUTION THREAD for {pair_key_locked}: "
                                f"An error occurred during trade execution: {e}")
    finally:
        # CRITICAL: Always remove the pair from the active set, even if the trade fails.
        with active_trades_lock:
            if pair_key_locked in active_trades_set:
                active_trades_set.remove(pair_key_locked)
                logger.info(f"EXECUTION THREAD for {pair_key_locked}: Trade lock released.")
