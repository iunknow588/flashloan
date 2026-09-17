// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 简单 AMM PoolManager Mock，用于本地构造失衡池和套利机会。

interface IERC20MockSimpleAmm {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract MockSimpleAmmPoolManager {
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

    mapping(bytes32 => Reserve) public pools;

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
        if (!IERC20MockSimpleAmm(token0).transferFrom(msg.sender, address(this), amount0)) revert TransferFailed();
        if (!IERC20MockSimpleAmm(token1).transferFrom(msg.sender, address(this), amount1)) revert TransferFailed();
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

        if (!IERC20MockSimpleAmm(tokenIn).transferFrom(msg.sender, address(this), amountIn)) revert TransferFailed();
        if (!IERC20MockSimpleAmm(tokenOut).transfer(msg.sender, amountOut)) revert TransferFailed();

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
