// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 带 flash accounting 的 V4 AMM PoolManager Mock，用于本地测试 unlock/take/settle。

interface IERC20MockV4Amm {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

interface IUnlockCallbackMockV4Amm {
    function unlockCallback(bytes calldata data) external returns (bytes memory);
}

contract MockV4AmmPoolManager {
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
    error InsufficientOutput();
    error TransferFailed();
    error NotUnlocked();
    error AlreadyUnlocked();
    error DebtNotSettled(address currency, uint256 debt);

    event PoolCreated(bytes32 indexed poolKey, address indexed token0, address indexed token1);
    event LiquidityAdded(
        bytes32 indexed poolKey,
        address indexed token0,
        address indexed token1,
        uint256 amount0,
        uint256 amount1
    );
    event SwapExecuted(
        bytes32 indexed poolKey,
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut
    );
    event Unlocked(address indexed caller);
    event Taken(address indexed caller, address indexed currency, address indexed to, uint256 amount);
    event Synced(address indexed caller, address indexed currency, uint256 balance);
    event Settled(address indexed caller, address indexed currency, uint256 paid, uint256 remainingDebt);

    mapping(bytes32 => Reserve) public pools;
    mapping(address caller => bool unlocked) public isUnlocked;
    mapping(address caller => address currency) public activeCurrency;
    mapping(address caller => mapping(address currency => uint256 debt)) public currencyDebt;
    mapping(address caller => address currency) public syncedCurrency;
    mapping(address caller => uint256 balanceAtSync) public syncedBalance;

    function createPool(address tokenA, address tokenB) external returns (bytes32 poolKey) {
        (poolKey, , ) = _sortTokens(tokenA, tokenB);
        if (pools[poolKey].initialized) revert PoolExists();
        pools[poolKey].initialized = true;
        emit PoolCreated(poolKey, tokenA, tokenB);
    }

    function addLiquidity(address tokenA, address tokenB, uint256 amountA, uint256 amountB) external {
        if (amountA == 0 || amountB == 0) revert ZeroAmount();
        (bytes32 poolKey, address token0, address token1) = _sortTokens(tokenA, tokenB);
        Reserve storage reserve = pools[poolKey];
        if (!reserve.initialized) revert PoolNotFound();
        uint256 amount0 = tokenA == token0 ? amountA : amountB;
        uint256 amount1 = tokenA == token0 ? amountB : amountA;
        reserve.amount0 += amount0;
        reserve.amount1 += amount1;
        if (!IERC20MockV4Amm(token0).transferFrom(msg.sender, address(this), amount0)) revert TransferFailed();
        if (!IERC20MockV4Amm(token1).transferFrom(msg.sender, address(this), amount1)) revert TransferFailed();
        emit LiquidityAdded(poolKey, token0, token1, amount0, amount1);
    }

    function swap(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOutMin
    ) external returns (uint256 amountOut) {
        if (amountIn == 0) revert ZeroAmount();
        (bytes32 poolKey, address token0, ) = _sortTokens(tokenIn, tokenOut);
        Reserve storage reserve = pools[poolKey];
        if (!reserve.initialized) revert PoolNotFound();

        uint256 reserveIn;
        uint256 reserveOut;
        if (tokenIn == token0) {
            reserveIn = reserve.amount0;
            reserveOut = reserve.amount1;
        } else {
            reserveIn = reserve.amount1;
            reserveOut = reserve.amount0;
        }

        amountOut = (reserveOut * amountIn) / (reserveIn + amountIn);
        if (amountOut == 0 || amountOut >= reserveOut) revert InvalidOutput();
        if (amountOut < amountOutMin) revert InsufficientOutput();

        if (tokenIn == token0) {
            reserve.amount0 = reserveIn + amountIn;
            reserve.amount1 = reserveOut - amountOut;
        } else {
            reserve.amount1 = reserveIn + amountIn;
            reserve.amount0 = reserveOut - amountOut;
        }

        if (!IERC20MockV4Amm(tokenIn).transferFrom(msg.sender, address(this), amountIn)) revert TransferFailed();
        if (!IERC20MockV4Amm(tokenOut).transfer(msg.sender, amountOut)) revert TransferFailed();

        emit SwapExecuted(poolKey, tokenIn, tokenOut, amountIn, amountOut);
    }

    function getReserves(address tokenA, address tokenB) external view returns (uint256 reserveA, uint256 reserveB) {
        (bytes32 poolKey, address token0, ) = _sortTokens(tokenA, tokenB);
        Reserve storage reserve = pools[poolKey];
        if (!reserve.initialized) revert PoolNotFound();
        if (tokenA == token0) {
            return (reserve.amount0, reserve.amount1);
        }
        return (reserve.amount1, reserve.amount0);
    }

    function unlock(bytes calldata data) external returns (bytes memory result) {
        if (isUnlocked[msg.sender]) revert AlreadyUnlocked();
        isUnlocked[msg.sender] = true;
        emit Unlocked(msg.sender);
        result = IUnlockCallbackMockV4Amm(msg.sender).unlockCallback(data);
        address currency = activeCurrency[msg.sender];
        uint256 debt = currencyDebt[msg.sender][currency];
        isUnlocked[msg.sender] = false;
        if (debt != 0) revert DebtNotSettled(currency, debt);
    }

    function take(address currency, address to, uint256 amount) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        if (currency == address(0)) revert ZeroToken();
        if (!IERC20MockV4Amm(currency).transfer(to, amount)) revert TransferFailed();
        activeCurrency[msg.sender] = currency;
        currencyDebt[msg.sender][currency] += amount;
        emit Taken(msg.sender, currency, to, amount);
    }

    function sync(address currency) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        syncedCurrency[msg.sender] = currency;
        syncedBalance[msg.sender] = _balance(currency);
        emit Synced(msg.sender, currency, syncedBalance[msg.sender]);
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
        emit Settled(msg.sender, currency, paid, currencyDebt[msg.sender][currency]);
    }

    function _balance(address currency) private view returns (uint256) {
        return IERC20MockV4Amm(currency).balanceOf(address(this));
    }

    function _sortTokens(
        address tokenA,
        address tokenB
    ) private pure returns (bytes32 poolKey, address token0, address token1) {
        if (tokenA == address(0) || tokenB == address(0)) revert ZeroToken();
        if (tokenA == tokenB) revert SameToken();
        if (uint160(tokenA) < uint160(tokenB)) {
            token0 = tokenA;
            token1 = tokenB;
        } else {
            token0 = tokenB;
            token1 = tokenA;
        }
        poolKey = keccak256(abi.encodePacked(token0, token1));
    }
}
