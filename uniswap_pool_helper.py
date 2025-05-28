from web3 import Web3
from decimal import Decimal
import config


class UniswapPoolHelper:
    def __init__(self, w3: Web3, pool_address: str):
        """
        Initializes the UniswapPoolHelper for a specific pool.

        Args:
            w3: Web3 instance.
            pool_address: Address of the Uniswap V3 pool.
            quoter_address: Address of the Uniswap V3 QuoterV2 contract.
        """
        self.w3 = w3
        self.pool_address_cs = Web3.to_checksum_address(pool_address)
        self.quoter_address_cs = Web3.to_checksum_address(config.QUOTER_ADDRESS)

        self.pool_abi = config.POOL_ABI
        self.quoter_abi = config.QUOTER_ABI
        self.erc20_abi = config.ERC20_ABI

        self.pool_contract = self.w3.eth.contract(address=self.pool_address_cs, abi=self.pool_abi)
        self.quoter_contract = self.w3.eth.contract(address=self.quoter_address_cs, abi=self.quoter_abi)

        self.token0_address_cs = Web3.to_checksum_address(self.pool_contract.functions.token0().call())
        self.token1_address_cs = Web3.to_checksum_address(self.pool_contract.functions.token1().call())

        token0_contract = self.w3.eth.contract(address=self.token0_address_cs, abi=self.erc20_abi)
        self.decimals_token0 = token0_contract.functions.decimals().call()

        token1_contract = self.w3.eth.contract(address=self.token1_address_cs, abi=self.erc20_abi)
        self.decimals_token1 = token1_contract.functions.decimals().call()

        self.fee = self.pool_contract.functions.fee().call()

    @staticmethod
    def _calculate_price_from_sqrtprice(
        sqrt_price_x96: Decimal,
        decimals_t0: int,
        decimals_t1: int
    ) -> Decimal:
        """
        Calculates readable price of pool's token0 in terms of pool's token1.
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
        slot0 = self.pool_contract.functions.slot0().call()
        sqrt_price_x96 = Decimal(str(slot0[0]))
        
        # Calculate price of token1 in terms of token0
        price_t1_per_t0 = UniswapPoolHelper._calculate_price_from_sqrtprice(
            sqrt_price_x96,
            self.decimals_token0, 
            self.decimals_token1
        )
        
        if not reverse_price: # return price token1/token0
            return price_t1_per_t0
        else: # return price token0/token1
            if price_t1_per_t0 == Decimal(0):
                return Decimal('inf') # Avoid division by zero
            return Decimal(1) / price_t1_per_t0

    def get_sell_quote(self, token_in_address_str: str, amount_in: Decimal):
        """
        Calculates a Uniswap V3 quote for selling an exact amount of an input token.
        Uses the pool associated with this helper instance.

        Args:
            token_in_address_str (str): Address of the input token (the token you are selling).
            amount_in (Decimal): The human-readable amount of token_in to sell.

        Returns:
            tuple: (amount_out, new_price_tOut_per_tIn, actual_price_tOut_per_tIn, gas_fee_eth)
        """
        token_in_address_cs = Web3.to_checksum_address(token_in_address_str)

        token_out_address_cs = None
        is_token_in_token0 = False # True if token_in (the token we are selling) is token0 of the pool
        decimals_in = 0
        decimals_out = 0

        if token_in_address_cs == self.token0_address_cs:
            token_out_address_cs = self.token1_address_cs
            is_token_in_token0 = True
            decimals_in = self.decimals_token0
            decimals_out = self.decimals_token1
        elif token_in_address_cs == self.token1_address_cs:
            token_out_address_cs = self.token0_address_cs
            is_token_in_token0 = False
            decimals_in = self.decimals_token1
            decimals_out = self.decimals_token0
        else:
            raise ValueError(f"Token {token_in_address_str} is not part of this pool ({self.pool_address_cs}).")

        amount_in_base_units = int(amount_in * (Decimal(10) ** decimals_in))

        quote_params = {
            'tokenIn': token_in_address_cs,
            'tokenOut': token_out_address_cs,
            'fee': self.fee, 
            'amountIn': amount_in_base_units,
            'sqrtPriceLimitX96': 0
        }
        quote_result = self.quoter_contract.functions.quoteExactInputSingle(quote_params).call()

        amount_out_base_units = Decimal(str(quote_result[0]))
        sqrt_price_x96_after_swap = Decimal(str(quote_result[1]))
        gas_estimate = Decimal(str(quote_result[3]))

        # Convert amount_out from base units to human-readable format
        amount_out = amount_out_base_units / (Decimal(10) ** decimals_out)

        # Price after swap: token_out / token_in
        price_pool_t1_per_t0_after_swap = UniswapPoolHelper._calculate_price_from_sqrtprice(
            sqrt_price_x96_after_swap,
            self.decimals_token0,
            self.decimals_token1
        )
        
        new_price_tOut_per_tIn = Decimal(0)
        if is_token_in_token0: # token_in: t0, token_out: t1, price: t1/t0 (token_out/token_in)
            new_price_tOut_per_tIn = price_pool_t1_per_t0_after_swap
        else: # token_in: t1, token_out: t0, price: t0/t1 (token_out/token_in)
            if price_pool_t1_per_t0_after_swap == Decimal(0):
                new_price_tOut_per_tIn = Decimal('inf')
            else:
                new_price_tOut_per_tIn = Decimal(1) / price_pool_t1_per_t0_after_swap
        
        actual_price_tOut_per_tIn = Decimal(0)
        if amount_in != Decimal(0):
            actual_price_tOut_per_tIn = amount_out / amount_in
        else: # amount_in is 0, so amount_out_readable will be 0
             actual_price_tOut_per_tIn = Decimal(0)
        
        current_gas_price_wei = Decimal(str(self.w3.eth.gas_price))
        gas_fee_eth = gas_estimate * current_gas_price_wei / (Decimal(10)**18)

        return amount_out, new_price_tOut_per_tIn, actual_price_tOut_per_tIn, gas_fee_eth

    def get_buy_quote(self, token_out_address_str: str, amount_out: Decimal):
        """
        Calculates a Uniswap V3 quote for buying an exact amount of an output token.
        Uses the pool associated with this helper instance.

        Args:
            token_out_address_str (str): Address of the output token (token you want to buy).
            amount_out (Decimal): The human-readable amount of token_out to receive.

        Returns:
            tuple: (amount_in, new_price_tIn_per_tOut, actual_price_tIn_per_tOut, gas_fee_eth)
        """
        token_out_address_cs = Web3.to_checksum_address(token_out_address_str)

        token_in_address_cs = None
        is_token_in_token0 = False # True if token_in (the token we are paying with) is token0 of the pool
        decimals_in = 0
        decimals_out = 0
        
        if token_out_address_cs == self.token1_address_cs: 
            token_in_address_cs = self.token0_address_cs
            is_token_in_token0 = True
            decimals_in = self.decimals_token0
            decimals_out = self.decimals_token1
        elif token_out_address_cs == self.token0_address_cs:
            token_in_address_cs = self.token1_address_cs
            is_token_in_token0 = False
            decimals_in = self.decimals_token1
            decimals_out = self.decimals_token0
        else:
            raise ValueError(f"Token {token_out_address_str} is not part of this pool ({self.pool_address_cs}).")
            
        amount_out_base_units = int(amount_out * (Decimal(10) ** decimals_out))

        quote_params = {
            'tokenIn': token_in_address_cs,
            'tokenOut': token_out_address_cs,
            'fee': self.fee,
            'amount': amount_out_base_units,
            'sqrtPriceLimitX96': 0
        }
        quote_result = self.quoter_contract.functions.quoteExactOutputSingle(quote_params).call()

        amount_in_base_units = Decimal(str(quote_result[0]))
        sqrt_price_x96_after_swap = Decimal(str(quote_result[1]))
        gas_estimate = Decimal(str(quote_result[3]))

        amount_in = amount_in_base_units / (Decimal(10) ** decimals_in)

        # Price after swap: token_out / token_in
        price_pool_t1_per_t0_after_swap = UniswapPoolHelper._calculate_price_from_sqrtprice(
            sqrt_price_x96_after_swap,
            self.decimals_token0,
            self.decimals_token1
        )
        
        new_price_tIn_per_tOut = Decimal(0)
        if is_token_in_token0: # token_in: t0, token_out: t1, price: t0/t1 (token_in/token_out)
            new_price_tIn_per_tOut = Decimal(1) / price_pool_t1_per_t0_after_swap
        else: # token_in: t1, token_out: t0, price: t1/t0 (token_in/token_out)
            if price_pool_t1_per_t0_after_swap == Decimal(0):
                new_price_tIn_per_tOut = Decimal('inf')
            else:
                new_price_tIn_per_tOut = price_pool_t1_per_t0_after_swap
        
        actual_price_tIn_per_tOut = Decimal(0) # price: token_in / token_out
        if amount_out != Decimal(0):
            actual_price_tIn_per_tOut = amount_in / amount_out
        else: # amount_out is 0, so amount_in will be 0
            actual_price_tIn_per_tOut = Decimal(0)

        current_gas_price_wei = Decimal(str(self.w3.eth.gas_price))
        gas_fee_eth = gas_estimate * current_gas_price_wei / (Decimal(10)**18)
        
        return amount_in, new_price_tIn_per_tOut, actual_price_tIn_per_tOut, gas_fee_eth