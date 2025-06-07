from web3 import Web3
from decimal import Decimal
from config import config
import logging

logger = logging.getLogger(__name__)

class Token:
    def __init__(self, w3: Web3, address: str, erc20_abi: list):
        """
        Initializes a Token object.

        Args:
            w3: Web3 instance.
            address: The token's contract address.
            erc20_abi: The ABI for an ERC20 token.
        """
        self.w3 = w3
        self.address = Web3.to_checksum_address(address)
        self.contract = self.w3.eth.contract(address=self.address, abi=erc20_abi)
        self.decimals = self.contract.functions.decimals().call()
        self.symbol = self.contract.functions.symbol().call()


class UniswapPoolHelper:
    def __init__(self, w3: Web3, pool_address: str, abis: dict):
        """
        Initializes the UniswapPoolHelper for a specific pool.

        Args:
            w3: Web3 instance.
            pool_address: Address of the Uniswap V3 pool.
            abis: Dictionary containing the ABIs.
        """
        self.w3 = w3
        self.pool_address_cs = Web3.to_checksum_address(pool_address)

        # Load ABIs
        self.pool_abi = abis['POOL']
        self.quoter_abi = abis['QUOTER']
        self.erc20_abi = abis['ERC20']
        self.router_abi = abis['ROUTER']

        try:
            self.pool_contract = self.w3.eth.contract(address=self.pool_address_cs, abi=self.pool_abi)

            # Create Token objects for token0 and token1
            token0_address = self.pool_contract.functions.token0().call()
            self.token0 = Token(self.w3, token0_address, self.erc20_abi)

            token1_address = self.pool_contract.functions.token1().call()
            self.token1 = Token(self.w3, token1_address, self.erc20_abi)

            self.fee = self.pool_contract.functions.fee().call()

        except Exception as e:
            logger.error(f"UniswapPoolHelper __init__: Error initializing pool helper for {pool_address}: {e}")
            raise 

    @staticmethod
    def _calculate_price_from_sqrtprice(
        sqrt_price_x96: Decimal,
        decimals_t0: int,
        decimals_t1: int
    ) -> Decimal:
        """
        Calculates readable price of token0 in terms of token1.
        Result is: amount of token1 per one unit of token0.
        """
        price_raw_t1_per_t0 = (sqrt_price_x96 / Decimal(2**96)) ** 2
        adjusted_price = price_raw_t1_per_t0 * (Decimal(10) ** (decimals_t0 - decimals_t1))
        return adjusted_price

    def get_current_price(self, reverse_price: bool = False) -> Decimal:
        """
        Gets the current spot price from the pool.

        Args:
            reverse_price (bool): 
                If False (default), returns price of token0 in terms of token1 (token1/token0).
                If True, returns price of token1 in terms of token0 (token0/token1).
        
        Returns:
            Decimal: The current readable price.
        """
        try:
            slot0 = self.pool_contract.functions.slot0().call()
            sqrt_price_x96 = Decimal(str(slot0[0]))
            
            # Calculate price of token0 in terms of token1
            price = UniswapPoolHelper._calculate_price_from_sqrtprice(
                sqrt_price_x96,
                self.token0.decimals,
                self.token1.decimals
            )

            if not reverse_price: return price
            else: return Decimal(1) / price
        except Exception as e:
            logger.error(f"UniswapPoolHelper get_current_price: Error getting current price for pool {self.pool_address_cs}: {e}")
            raise

    def get_sell_quote(self, token_in: Token, amount_in: Decimal, quoter_address: str = None):
        """
        Calculates a Uniswap V3 quote for selling an exact amount of an input token.
        Uses the pool associated with this helper instance.

        Args:
            token_in (Token): The input token object (the token you are selling).
            amount_in (Decimal): The human-readable amount of token_in to sell.
            quoter_address (str, optional): Address of the Uniswap V3 quoter contract. 
                If not provided, uses the QuoterV2 Mainnet.

        Returns:
            tuple: (amount_out, new_price, actual_price, gas_fee_eth)
            - amount_out (Decimal): The human-readable amount of token_out you will receive.
            - new_price (Decimal): New price of token_out in terms of token_in after the swap.
            - actual_price (Decimal): Actual price of token_out in terms of token_in based on the quote.
            - gas_fee_eth (Decimal): Estimated gas fee in ETH for the transaction.
        """
        try:
            token_out, is_token_in_token0 = self.resolve_token_out(token_in)
            amount_in_base_units = int(amount_in * (Decimal(10) ** token_in.decimals))

            quoter_address_cs = Web3.to_checksum_address(quoter_address or config.QUOTER_ADDRESS)
            quoter_contract = self.w3.eth.contract(address=quoter_address_cs, abi=self.quoter_abi)

            quote_params = {
                'tokenIn': token_in.address,
                'tokenOut': token_out.address,
                'fee': self.fee,
                'amountIn': amount_in_base_units,
                'sqrtPriceLimitX96': 0
            }
            quote_result = quoter_contract.functions.quoteExactInputSingle(quote_params).call()

            amount_out_base_units = Decimal(str(quote_result[0]))
            sqrt_price_x96_after_swap = Decimal(str(quote_result[1]))
            gas_estimate = Decimal(str(quote_result[3]))

            amount_out = amount_out_base_units / (Decimal(10) ** token_out.decimals)

            # Price after swap: token_out / token_in
            price_after_swap = UniswapPoolHelper._calculate_price_from_sqrtprice(
                sqrt_price_x96_after_swap,
                self.token0.decimals,
                self.token1.decimals
            ) # price of token1 in terms of token0 (token1/token0)
            
            new_price = price_after_swap if is_token_in_token0 else (Decimal(1) / price_after_swap)
            actual_price = (amount_out / amount_in) if amount_in != Decimal(0) else Decimal(0)

            current_gas_price_wei = Decimal(str(self.w3.eth.gas_price))
            gas_fee_eth = gas_estimate * current_gas_price_wei / (Decimal(10)**18)

            return amount_out, new_price, actual_price, gas_fee_eth
        except Exception as e:
            logger.error(f"UniswapPoolHelper get_sell_quote: Error getting sell quote for pool {self.pool_address_cs}: {e}")
            raise

    def get_buy_quote(self, token_out: Token, amount_out: Decimal, quoter_address: str = None):
        """
        Calculates a Uniswap V3 quote for buying an exact amount of an output token.
        Uses the pool associated with this helper instance.

        Args:
            token_out (Token): The output token object (the token you want to buy).
            amount_out (Decimal): The human-readable amount of token_out to receive.
            quoter_address (str, optional): Address of the Uniswap V3 quoter contract. 
                If not provided, uses the QuoterV2 Mainnet.

        Returns:
            tuple: (amount_in, new_price, actual_price, gas_fee_eth)
            - amount_in (Decimal): The human-readable amount of token_in you need to pay.
            - new_price (Decimal): New price of token_in in terms of token_out after the swap.
            - actual_price (Decimal): Actual price of token_in in terms of token_out based on the quote.
            - gas_fee_eth (Decimal): Estimated gas fee in ETH for the transaction.
        """
        try:
            token_in, is_token_in_token0 = self.resolve_token_in(token_out)
            amount_out_base_units = int(amount_out * (Decimal(10) ** token_out.decimals))

            quoter_address_cs = Web3.to_checksum_address(quoter_address or config.QUOTER_ADDRESS)
            quoter_contract = self.w3.eth.contract(address=quoter_address_cs, abi=self.quoter_abi)
            
            quote_params = {
                'tokenIn': token_in.address,
                'tokenOut': token_out.address,
                'fee': self.fee,
                'amount': amount_out_base_units,
                'sqrtPriceLimitX96': 0
            }
            quote_result = quoter_contract.functions.quoteExactOutputSingle(quote_params).call()

            amount_in_base_units = Decimal(str(quote_result[0]))
            sqrt_price_x96_after_swap = Decimal(str(quote_result[1]))
            gas_estimate = Decimal(str(quote_result[3]))

            amount_in = amount_in_base_units / (Decimal(10) ** token_in.decimals)

            price_after_swap = UniswapPoolHelper._calculate_price_from_sqrtprice(
                sqrt_price_x96_after_swap,
                self.token0.decimals,
                self.token1.decimals
            )
            
            new_price = (Decimal(1) / price_after_swap) if is_token_in_token0 else price_after_swap
            actual_price = (amount_in / amount_out) if amount_out != Decimal(0) else Decimal(0)

            current_gas_price_wei = Decimal(str(self.w3.eth.gas_price))
            gas_fee_eth = gas_estimate * current_gas_price_wei / (Decimal(10)**18)
            
            return amount_in, new_price, actual_price, gas_fee_eth
        except Exception as e:
            logger.error(f"UniswapPoolHelper get_buy_quote: Error getting buy quote for pool {self.pool_address_cs}: {e}")
            raise

    def sell(
        self,
        token_in: Token,
        amount_in: Decimal,
        recipient_address: str,
        private_key: str,
        slippage: Decimal,
        quoter_address: str,
        router_address: str
    ):
        """
        Executes a sell transaction on the Uniswap V3 pool.

        Args:
            token_in (Token): The Token object you are selling.
            amount_in (Decimal): The human-readable amount of token_in to sell.
            recipient_address (str): Address to receive the output tokens and send the transaction from.
            private_key (str): Private key of the sender's wallet.
            slippage (Decimal): Slippage tolerance as a decimal (e.g., 0.01 for 1%).
            quoter_address (str): Address of the Uniswap V3 quoter contract.
            router_address (str): Address of the Uniswap V3 router contract.

        Returns:
            str: Transaction hash of the executed sell transaction.
        """
        try:
            recipient_address_cs = Web3.to_checksum_address(recipient_address)
            quoter_address_cs = Web3.to_checksum_address(quoter_address or config.QUOTER_ADDRESS)
            router_address_cs = Web3.to_checksum_address(router_address or config.ROUTER_ADDRESS)
            router_contract = self.w3.eth.contract(address=router_address_cs, abi=self.router_abi)

            token_out, _ = self.resolve_token_out(token_in)
            amount_in_base_units = int(amount_in * (Decimal(10) ** token_in.decimals))

            current_balance_base_units = token_in.contract.functions.balanceOf(recipient_address_cs).call()
            if current_balance_base_units < amount_in_base_units:
                raise ValueError(f"Insufficient balance of {token_in.symbol}.")

            # Approve spending if necessary
            allowance = token_in.contract.functions.allowance(recipient_address_cs, router_address_cs).call()
            if allowance < amount_in_base_units:
                approve_tx = token_in.contract.functions.approve(router_address_cs, 2**256 - 1).build_transaction({
                    'from': recipient_address_cs,
                    'chainId': self.w3.eth.chain_id,
                    'gas': 100000,
                    'gasPrice': self.w3.eth.gas_price,
                    'nonce': self.w3.eth.get_transaction_count(recipient_address_cs, 'pending')
                })
                signed_approve_tx = self.w3.eth.account.sign_transaction(approve_tx, private_key)
                approve_tx_hash = self.w3.eth.send_raw_transaction(signed_approve_tx.raw_transaction)
                approve_receipt = self.w3.eth.wait_for_transaction_receipt(approve_tx_hash)
                if approve_receipt.status != 1:
                    raise Exception(f"Approval transaction failed: 0x{approve_tx_hash.hex()}")
                logger.info(f"Approval transaction confirmed. See: https://sepolia.etherscan.io/tx/0x{approve_tx_hash.hex()}")

            expected_amount_out, _, _, _ = self.get_sell_quote(token_in, amount_in, quoter_address=quoter_address_cs)
            amount_out_min = expected_amount_out * (Decimal(1) - slippage)
            amount_out_min_base_units = int(amount_out_min * (Decimal(10) ** token_out.decimals))

            params = {
                'tokenIn': token_in.address,
                'tokenOut': token_out.address,
                'fee': self.fee,
                'recipient': recipient_address_cs,
                'amountIn': amount_in_base_units,
                'amountOutMinimum': amount_out_min_base_units,
                'sqrtPriceLimitX96': 0
            }
            
            # A new nonce is required if an approval was just sent.
            swap_nonce = self.w3.eth.get_transaction_count(recipient_address_cs, 'pending')
            swap_tx = router_contract.functions.exactInputSingle(params).build_transaction({
                'from': recipient_address_cs,
                'gas': 300000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': swap_nonce
            })

            signed_swap_tx = self.w3.eth.account.sign_transaction(swap_tx, private_key)
            swap_tx_hash = self.w3.eth.send_raw_transaction(signed_swap_tx.raw_transaction)
            
            receipt = self.w3.eth.wait_for_transaction_receipt(swap_tx_hash)
            if receipt.status != 1:
                raise Exception(f"Swap transaction failed: 0x{swap_tx_hash.hex()}")
            logger.info(f"Swap transaction confirmed. See: https://sepolia.etherscan.io/tx/0x{swap_tx_hash.hex()}")

            return swap_tx_hash.hex()

        except Exception as e:
            logger.error(f"UniswapPoolHelper sell: Error executing sell for pool {self.pool_address_cs}: {e}")
            raise

    def buy(
        self,
        token_out: Token,
        amount_out: Decimal,
        recipient_address: str,
        private_key: str,
        slippage: Decimal,
        quoter_address: str,
        router_address: str
    ):
        """
        Executes a buy transaction on the Uniswap V3 pool.

        Args:
            token_out (Token): The Token object you are buying.
            amount_out (Decimal): The human-readable amount of token_out to buy.
            recipient_address (str): Address to receive the output tokens and send the transaction from.
            private_key (str): Private key of the sender's wallet.
            slippage (Decimal): Slippage tolerance as a decimal (e.g., 0.01 for 1%).
            quoter_address (str): Address of the Uniswap V3 quoter contract.
            router_address (str): Address of the Uniswap V3 router contract.

        Returns:
            str: Transaction hash of the executed buy transaction.
        """
        try:
            recipient_address_cs = Web3.to_checksum_address(recipient_address)
            quoter_address_cs = Web3.to_checksum_address(quoter_address or config.QUOTER_ADDRESS)
            router_address_cs = Web3.to_checksum_address(router_address or config.ROUTER_ADDRESS)
            router_contract = self.w3.eth.contract(address=router_address_cs, abi=self.router_abi)

            token_in, _ = self.resolve_token_in(token_out)
            amount_out_base_units = int(amount_out * (Decimal(10) ** token_out.decimals))

            # Get quote to determine max input amount
            amount_in_expected, _, _, _ = self.get_buy_quote(token_out, amount_out, quoter_address_cs)
            amount_in_max = amount_in_expected * (Decimal(1) + slippage)
            amount_in_max_base_units = int(amount_in_max * (Decimal(10) ** token_in.decimals))

            current_balance_base_units = token_in.contract.functions.balanceOf(recipient_address_cs).call()
            if current_balance_base_units < amount_in_max_base_units:
                raise ValueError(f"Insufficient balance of {token_in.symbol}.")

            # Approve spending if necessary
            allowance = token_in.contract.functions.allowance(recipient_address_cs, router_address_cs).call()
            if allowance < amount_in_max_base_units:
                approve_tx = token_in.contract.functions.approve(router_address_cs, 2**256 - 1).build_transaction({
                    'from': recipient_address_cs,
                    'chainId': self.w3.eth.chain_id,
                    'gas': 100000,
                    'gasPrice': self.w3.eth.gas_price,
                    'nonce': self.w3.eth.get_transaction_count(recipient_address_cs, 'pending')
                })
                signed_approve_tx = self.w3.eth.account.sign_transaction(approve_tx, private_key)
                approve_tx_hash = self.w3.eth.send_raw_transaction(signed_approve_tx.raw_transaction)
                approve_receipt = self.w3.eth.wait_for_transaction_receipt(approve_tx_hash)
                if approve_receipt.status != 1: 
                    raise Exception(f"Approval transaction failed: 0x{approve_tx_hash.hex()}")
                logger.info(f"Approval transaction confirmed. See: https://sepolia.etherscan.io/tx/0x{approve_tx_hash.hex()}")

            params = {
                'tokenIn': token_in.address,
                'tokenOut': token_out.address,
                'fee': self.fee,
                'recipient': recipient_address_cs,
                'amountOut': amount_out_base_units,
                'amountInMaximum': amount_in_max_base_units,
                'sqrtPriceLimitX96': 0
            }

            # A new nonce is required if an approval was just sent.
            swap_nonce = self.w3.eth.get_transaction_count(recipient_address_cs, 'pending')
            swap_tx = router_contract.functions.exactOutputSingle(params).build_transaction({
                'from': recipient_address_cs,
                'gas': 300000,
                'gasPrice': self.w3.eth.gas_price,
                'nonce': swap_nonce
            })

            signed_swap_tx = self.w3.eth.account.sign_transaction(swap_tx, private_key)
            swap_tx_hash = self.w3.eth.send_raw_transaction(signed_swap_tx.raw_transaction)

            receipt = self.w3.eth.wait_for_transaction_receipt(swap_tx_hash)
            if receipt.status != 1:
                raise Exception(f"Swap transaction failed: 0x{swap_tx_hash.hex()}")
            logger.info(f"Swap transaction confirmed. See: https://sepolia.etherscan.io/tx/0x{swap_tx_hash.hex()}")

            return swap_tx_hash.hex()
        except Exception as e:
            logger.error(f"UniswapPoolHelper buy: Error executing buy for pool {self.pool_address_cs}: {e}")
            raise

    def resolve_token_out(self, token_in: Token) -> tuple[Token, bool]:
        """
        Resolves the output token and determines if the input token is token0.

        Args:
            token_in (Token): The input token object.

        Returns:
            tuple: (token_out, is_token_in_token0)
            - token_out (Token): The output token object.
            - is_token_in_token0 (bool): True if the input token is token0, False if it is token1.
        """
        if token_in.address == self.token0.address:
            return self.token1, True  # token_out, is_token_in_token0
        elif token_in.address == self.token1.address:
            return self.token0, False # token_out, is_token_in_token0
        else:
            raise ValueError(f"Token {token_in.address} is not part of this pool ({self.pool_address_cs}).")

    def resolve_token_in(self, token_out: Token) -> tuple[Token, bool]:
        """
        Resolves the input token and determines if the input token is token0.

        Args:
            token_out (Token): The output token object.

        Returns:
            tuple: (token_in, is_token_in_token0)
            - token_in (Token): The input token object.
            - is_token_in_token0 (bool): True if the input token is token0, False if it is token1.
        """
        if token_out.address == self.token1.address:
            return self.token0, True  # token_in, is_token_in_token0
        elif token_out.address == self.token0.address:
            return self.token1, False # token_in, is_token_in_token0
        else:
            raise ValueError(f"Token {token_out.address} is not part of this pool ({self.pool_address_cs}).")