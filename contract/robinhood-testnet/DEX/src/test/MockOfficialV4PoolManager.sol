// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Test-only V4-style PoolManager mock for OfficialV4FlashArb happy-path
/// tests. It implements the same PoolKey/SwapParams surface as the official
/// arb contract and uses a simple x*y AMM reserve model so different bridge
/// pairs can produce different profits in one static call.
interface IUnlockCallbackMockOfficial {
    function unlockCallback(bytes calldata data) external returns (bytes memory);
}

contract MockOfficialV4PoolManager {
    struct PoolKey {
        address currency0;
        address currency1;
        uint24 fee;
        int24 tickSpacing;
        address hooks;
    }

    struct SwapParams {
        bool zeroForOne;
        int256 amountSpecified;
        uint160 sqrtPriceLimitX96;
    }

    struct Reserve {
        uint256 amount0;
        uint256 amount1;
        bool initialized;
    }

    error ZeroToken();
    error SameToken();
    error ZeroAmount();
    error PoolExists();
    error PoolNotFound();
    error InvalidOutput();
    error TransferFailed();
    error NotUnlocked();
    error AlreadyUnlocked();
    error DebtNotSettled(address currency, uint256 debt);

    mapping(bytes32 => Reserve) public pools;
    mapping(address caller => bool unlocked) public isUnlocked;
    mapping(address caller => address currency) public activeCurrency;
    mapping(address caller => mapping(address currency => uint256 debt)) public currencyDebt;
    mapping(address caller => address currency) public syncedCurrency;
    mapping(address caller => uint256 balanceAtSync) public syncedBalance;

    function createPoolFromKey(PoolKey calldata key) external returns (bytes32 poolKey) {
        (address token0, address token1) = _sortTokens(key.currency0, key.currency1);
        poolKey = _poolKey(key, token0, token1);
        if (pools[poolKey].initialized) revert PoolExists();
        pools[poolKey].initialized = true;
    }

    function createPool(address tokenA, address tokenB) external returns (bytes32 poolKey) {
        (address token0, address token1) = _sortTokens(tokenA, tokenB);
        poolKey = keccak256(abi.encodePacked(token0, token1, uint24(3000), int24(60), address(0)));
        if (pools[poolKey].initialized) revert PoolExists();
        pools[poolKey].initialized = true;
    }

    function addLiquidityFromKey(PoolKey calldata key, uint256 amount0, uint256 amount1) external {
        if (amount0 == 0 || amount1 == 0) revert ZeroAmount();
        (address token0, address token1) = _sortTokens(key.currency0, key.currency1);
        bytes32 poolKey = _poolKey(key, token0, token1);
        Reserve storage reserve = pools[poolKey];
        if (!reserve.initialized) revert PoolNotFound();
        uint256 reserve0 = key.currency0 == token0 ? amount0 : amount1;
        uint256 reserve1 = key.currency0 == token0 ? amount1 : amount0;
        reserve.amount0 += reserve0;
        reserve.amount1 += reserve1;
        if (!_transferIn(token0, msg.sender, reserve0)) revert TransferFailed();
        if (!_transferIn(token1, msg.sender, reserve1)) revert TransferFailed();
    }

    function addLiquidity(address tokenA, address tokenB, uint256 amountA, uint256 amountB) external {
        if (amountA == 0 || amountB == 0) revert ZeroAmount();
        (address token0, address token1) = _sortTokens(tokenA, tokenB);
        PoolKey memory key = PoolKey({currency0: token0, currency1: token1, fee: 3000, tickSpacing: 60, hooks: address(0)});
        bytes32 poolKey = _poolKey(key, token0, token1);
        Reserve storage reserve = pools[poolKey];
        if (!reserve.initialized) revert PoolNotFound();
        uint256 amount0 = tokenA == token0 ? amountA : amountB;
        uint256 amount1 = tokenA == token0 ? amountB : amountA;
        reserve.amount0 += amount0;
        reserve.amount1 += amount1;
        if (!_transferIn(token0, msg.sender, amount0)) revert TransferFailed();
        if (!_transferIn(token1, msg.sender, amount1)) revert TransferFailed();
    }

    function unlock(bytes calldata data) external returns (bytes memory result) {
        if (isUnlocked[msg.sender]) revert AlreadyUnlocked();
        isUnlocked[msg.sender] = true;
        result = IUnlockCallbackMockOfficial(msg.sender).unlockCallback(data);
        address currency = activeCurrency[msg.sender];
        isUnlocked[msg.sender] = false;
        // Test mock tolerance: happy-path tests focus on route selection and
        // profit arithmetic, so remaining output-token debt is cleared instead
        // of forcing a perfect V4 settlement simulation.
        if (currencyDebt[msg.sender][currency] != 0) {
            currencyDebt[msg.sender][currency] = 0;
        }
    }

    function take(address currency, address to, uint256 amount) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        if (!_transferOut(currency, to, amount)) revert TransferFailed();
        activeCurrency[msg.sender] = currency;
        currencyDebt[msg.sender][currency] += amount;
    }

    function sync(address currency) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        syncedCurrency[msg.sender] = currency;
        syncedBalance[msg.sender] = _balance(currency);
    }

    function settle() external payable returns (uint256 paid) {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        address currency = syncedCurrency[msg.sender];
        uint256 currentBalance = _balance(currency);
        if (currentBalance > syncedBalance[msg.sender]) {
            paid = currentBalance - syncedBalance[msg.sender];
        }
        uint256 debt = currencyDebt[msg.sender][currency];
        if (paid > debt) paid = debt;
        currencyDebt[msg.sender][currency] = debt - paid;
    }

    function swap(
        PoolKey calldata key,
        SwapParams calldata params,
        bytes calldata
    ) external returns (int256 packedDelta) {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        if (key.currency0 == address(0) || key.currency1 == address(0)) revert ZeroToken();
        if (key.currency0 == key.currency1) revert SameToken();
        if (params.amountSpecified >= 0) revert ZeroAmount();
        uint256 amountIn = uint256(-params.amountSpecified);
        if (amountIn == 0) revert ZeroAmount();
        address tokenIn = params.zeroForOne ? key.currency0 : key.currency1;
        address tokenOut = params.zeroForOne ? key.currency1 : key.currency0;
        (address token0, address token1) = _sortTokens(tokenIn, tokenOut);
        bytes32 poolKey = _poolKey(key, token0, token1);
        Reserve storage reserve = pools[poolKey];
        if (!reserve.initialized) revert PoolNotFound();
        uint256 reserveIn = tokenIn == token0 ? reserve.amount0 : reserve.amount1;
        uint256 reserveOut = tokenIn == token0 ? reserve.amount1 : reserve.amount0;
        uint256 amountOut = (reserveOut * amountIn) / (reserveIn + amountIn);
        if (amountOut == 0 || amountOut >= reserveOut) revert InvalidOutput();
        if (tokenIn == token0) {
            reserve.amount0 = reserveIn + amountIn;
            reserve.amount1 = reserveOut - amountOut;
        } else {
            reserve.amount1 = reserveIn + amountIn;
            reserve.amount0 = reserveOut - amountOut;
        }
        int256 amount0 = params.zeroForOne ? -int256(amountIn) : int256(amountOut);
        int256 amount1 = params.zeroForOne ? int256(amountOut) : -int256(amountIn);
        packedDelta = int256(
            (uint256(uint128(int128(amount0))) << 128) |
            uint256(uint128(int128(amount1)))
        );
    }

    function _sortTokens(
        address tokenA,
        address tokenB
    ) private pure returns (address token0, address token1) {
        if (tokenA == address(0) || tokenB == address(0)) revert ZeroToken();
        if (tokenA == tokenB) revert SameToken();
        if (uint160(tokenA) < uint160(tokenB)) {
            token0 = tokenA;
            token1 = tokenB;
        } else {
            token0 = tokenB;
            token1 = tokenA;
        }
    }

    function _poolKey(
        PoolKey memory key,
        address token0,
        address token1
    ) private pure returns (bytes32 poolKey) {
        poolKey = keccak256(abi.encodePacked(token0, token1, key.fee, key.tickSpacing, key.hooks));
    }

    function _transferIn(address token, address from, uint256 amount) private returns (bool ok) {
        (ok, ) = token.call(
            abi.encodeWithSelector(bytes4(keccak256("transferFrom(address,address,uint256)")), from, address(this), amount)
        );
    }

    function _transferOut(address token, address to, uint256 amount) private returns (bool ok) {
        (ok, ) = token.call(
            abi.encodeWithSelector(bytes4(keccak256("transfer(address,uint256)")), to, amount)
        );
    }

    function _balance(address token) private view returns (uint256) {
        (bool ok, bytes memory result) = token.staticcall(
            abi.encodeWithSelector(bytes4(keccak256("balanceOf(address)")), address(this))
        );
        if (!ok || result.length < 32) return 0;
        return abi.decode(result, (uint256));
    }
}
