// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 最小 ERC20 接口，仅包含执行器需要的 approve/balanceOf/transfer。
interface IERC20RobinhoodMinimal {
    function approve(address spender, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

/// @notice Morpho Blue style flashLoan 来源接口，用于兼容测试，不作为 Robinhood Testnet 默认来源。
interface IMorphoBlueFlashSource {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}

/// @notice Uniswap V4 Universal Router execute 接口。
interface IUniversalRouterRobinhood {
    function execute(bytes calldata commands, bytes[] calldata inputs, uint256 deadline) external payable;
}

/// @notice Permit2 授权接口，用于 V4 Universal Router 拉取代币。
interface IPermit2Robinhood {
    function approve(address token, address spender, uint160 amount, uint48 expiration) external;
}

/// @notice Uniswap V3 SwapRouter exactInputSingle 接口。
interface IV3RouterRobinhood {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

/// @notice Uniswap V4 PoolManager flash accounting 基础接口：unlock/take/sync/settle。
interface IV4PoolManagerRobinhood {
    function unlock(bytes calldata data) external returns (bytes memory);
    function take(address currency, address to, uint256 amount) external;
    function sync(address currency) external;
    function settle() external payable returns (uint256 paid);
}

/// @notice Uniswap V4 PoolManager swap 接口，用于直接构造 PoolManager::swap calldata。
interface IV4PoolManagerSwapRobinhood {
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

    function swap(PoolKey calldata key, SwapParams calldata params, bytes calldata hookData)
        external
        payable
        returns (int256 amount0, int256 amount1);
}

/// @notice Morpho flash loan 回调接口。
interface IMorphoBlueFlashLoanCallback {
    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external;
}

