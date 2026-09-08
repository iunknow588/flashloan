// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./UnifiedFlashLoanMevExecutorInterfaces.sol";

abstract contract UnifiedFlashLoanMevExecutorTypes {
    error NotOwner();
    error NotPool();
    error BadInitiator();
    error Paused();
    error InvalidRequest();
    error UnsupportedAdapterKind(uint8 adapterKind);
    error ExecutionRouterMissing(uint8 adapterKind);
    error ExecutionQuoterMissing(uint8 adapterKind);
    error RouterSwapFailed(bytes4 selector);
    error ExecutionConstraintFailed(
        uint256 protectedMinFinal,
        uint256 repaymentRequired,
        uint256 finalAmount,
        uint256 actualBalance,
        uint256 requiredBalance
    );
    error ApproveFailed();
    error TransferFailed();
    error Reentrancy();
    error OrderedRuntimeExecutionFailed(
        uint256 code,
        uint256 failedStatus,
        uint256 tradeArrayIndex,
        uint256 detailCode,
        int256 expectedProfit,
        uint256 quotedFinal,
        uint256 requiredFinal,
        uint8 attemptedStatusMask,
        uint8 remainingStatusMask
    );

    uint8 internal constant ADAPTER_NONE = 0;
    uint8 internal constant ADAPTER_UNISWAP_V3 = 1;

    uint8 internal constant EXECUTION_KIND_NONE = 0;
    uint8 internal constant EXECUTION_KIND_USDC_TRIANGULAR = 1;
    uint8 internal constant EXECUTION_KIND_TOKEN_CROSS_POOL = 2;
    uint8 internal constant EXECUTION_KIND_USDC_CROSS_POOL = 3;

    uint8 internal constant ROUTE_DIRECTION_NONE = 0;
    uint8 internal constant ROUTE_DIRECTION_U_X_Y_U = 1;
    uint8 internal constant ROUTE_DIRECTION_U_Y_X_U = 2;

    uint8 internal constant TRIANGULAR_EXECUTION_PACKED_PATH = 1;

    uint8 internal constant STEP_NOT_CHECKED = 0;
    uint8 internal constant STEP_CHECKED_FAILED = 1;
    uint8 internal constant STEP_SELECTED = 2;
    uint8 internal constant STEP_NOT_EXECUTED_AFTER_SELECTION = 3;

    uint256 internal constant STATUS_UX_CROSS_POOL = 1;
    uint256 internal constant STATUS_UY_CROSS_POOL = 2;
    uint256 internal constant STATUS_XY_CROSS_POOL = 3;
    uint256 internal constant STATUS_XY_USDC_FALLBACK = 4;
    uint256 internal constant STATUS_COMBINED_FALLBACK = 5;

    uint256 internal constant ERR_NONE = 0;
    uint256 internal constant ERR_NOT_ENOUGH_POOLS = 1;
    uint256 internal constant ERR_NO_PRICE_SPREAD = 2;
    uint256 internal constant ERR_QUOTE_FAILED = 3;
    uint256 internal constant ERR_PROFIT_NOT_ENOUGH = 4;
    uint256 internal constant ERR_BORROW_ASSET_DISABLED = 5;
    uint256 internal constant ERR_ROUTE_LAYOUT_INVALID = 6;
    uint256 internal constant ERR_NO_PROFITABLE_ROUTE = 55555;

    uint256 internal constant MAX_POOLS_PER_TRADE = 5;
    uint256 internal constant MAX_ORDERED_TRADE_INPUTS = 4;
    uint256 internal constant MAX_QUOTER_CALL_GAS = 2_500_000;

    struct AdapterConfig {
        bool allowed;
        address factory;
        address router;
        address quoter;
    }

    struct RuntimeRiskConfig {
        uint128 minPoolLiquidity;
        int256 minTickDelta;
    }

    struct BorrowConfig {
        bool enabled;
        uint256 maxAmount;
    }

    struct RuntimePoolSpec {
        uint8 adapterKind;
        address pool;
    }

    struct RuntimeTradeSpec {
        uint256 tradeIndex;
        address tokenX;
        address tokenY;
        RuntimePoolSpec[MAX_POOLS_PER_TRADE] pools;
    }

    struct RuntimeExecutionParams {
        uint256 amount;
        uint256 deadline;
        uint256 amountOutMinUsdc;
        uint256 minProfitUsdc;
    }

    struct RuntimeTokenBorrowParams {
        uint256 amount;
        uint256 deadline;
        uint256 minFinalToken;
        uint256 minProfitToken;
    }

    struct RuntimeTradeDecision {
        bool viable;
        uint256 tradeIndex;
        address tokenX;
        address tokenY;
        address lowPool;
        address highPool;
        uint24 lowFee;
        uint24 highFee;
        uint128 lowLiquidity;
        uint128 highLiquidity;
        int24 lowNormalizedTick;
        int24 highNormalizedTick;
        int256 tickDelta;
        uint256 validPoolCount;
        uint256 failureCode;
    }

    struct RuntimeExecutionPreview {
        address borrowedAsset;
        address profitAsset;
        uint256 borrowedAmount;
        uint8 routeDirection;
        uint8 failedHopIndex;
        bytes swapPath;
        uint256 quotedFinal;
        uint256 premium;
        uint256 requiredFinal;
        uint256 protectedMinFinal;
        uint256 minProfit;
        int256 expectedProfit;
    }

    struct RuntimeHopPreview {
        uint8 hopIndex;
        address tokenIn;
        address tokenOut;
        address pool;
        uint24 fee;
        uint256 amountIn;
        uint256 quotedAmountOut;
        uint256 amountOutMin;
    }

    struct RuntimeTriangularRoutePreview {
        uint8 routeDirection;
        uint8 failedHopIndex;
        uint8 executionMode;
        RuntimeHopPreview[3] hops;
        uint256 quotedFinalUsdc;
        uint256 premiumUsdc;
        uint256 requiredFinalUsdc;
        int256 expectedProfitUsdc;
    }

    struct RuntimeStepReport {
        uint8 strategyStatus;
        uint8 phase;
        uint8 routeDirection;
        uint8 failedHopIndex;
        uint256 resultCode;
        uint256 detailCode;
        uint256 tradeArrayIndex;
        uint256 tradeIndex;
        address profitAsset;
        int256 expectedProfit;
        uint256 quotedFinal;
        uint256 requiredFinal;
    }

    struct RuntimeProgress {
        uint256 finalResultCode;
        uint8 selectedStatus;
        uint8 lastCheckedStatus;
        uint8 attemptedStatusMask;
        uint8 selectedStatusMask;
        uint8 remainingStatusMask;
        uint8 remainingStepCount;
        RuntimeStepReport[5] steps;
    }

    struct RuntimeOrderPreview {
        bool found;
        uint256 strategyStatus;
        uint8 executionKind;
        uint256 selectedTradeArrayIndex;
        RuntimeTradeDecision decision;
        RuntimeExecutionPreview executionPreview;
        RuntimeTriangularRoutePreview triangularRoute;
        RuntimeProgress progress;
    }

    struct RuntimeRunResult {
        uint256 resultCode;
        uint256 strategyStatus;
        uint8 executionKind;
        uint8 routeDirection;
        uint256 selectedTradeArrayIndex;
        address profitAsset;
        uint256 profitAmount;
        uint256 profitSwept;
        uint8 attemptedStatusMask;
        uint8 remainingStatusMask;
        uint8 remainingStepCount;
    }

    struct PoolSnapshot {
        bool valid;
        address pool;
        uint24 fee;
        uint128 liquidity;
        int24 normalizedTick;
    }

    struct CallbackPlan {
        uint256 strategyStatus;
        uint8 executionKind;
        uint8 routeDirection;
        address borrowedAsset;
        address profitAsset;
        address router;
        bytes swapPath;
        uint256 deadline;
        uint256 protectedMinFinal;
        uint256 startingProfitBalance;
        uint256 tradeArrayIndex;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event AdapterConfigSet(
        uint8 indexed adapterKind,
        bool allowed,
        address indexed factory,
        address router,
        address quoter
    );
    event RuntimeRiskConfigSet(uint128 minPoolLiquidity, int256 minTickDelta);
    event BorrowConfigSet(address indexed token, bool enabled, uint256 maxAmount);
    event ProfitConfigSet(bool sweepEnabled, uint256 reserveUsdc, uint256 sweepThreshold);
    event OrderedRuntimePreviewSelected(
        uint256 indexed strategyStatus,
        uint8 indexed executionKind,
        uint256 indexed tradeArrayIndex,
        uint256 tradeIndex
    );
    event RuntimeProfitChecked(uint256 quotedFinal, uint256 requiredFinal, uint256 premium, uint256 minProfit);
    event RuntimeTriangularHopQuoted(
        uint8 indexed routeDirection,
        uint8 indexed hopIndex,
        address indexed pool,
        address tokenIn,
        address tokenOut,
        uint24 fee,
        uint256 amountIn,
        uint256 quotedAmountOut,
        uint256 amountOutMin
    );
    event RuntimeStepEvaluated(
        uint8 indexed strategyStatus,
        uint8 phase,
        uint256 indexed resultCode,
        uint256 indexed tradeArrayIndex,
        int256 expectedProfit,
        uint256 quotedFinal,
        uint256 requiredFinal
    );
    event RuntimeWorkflowFinished(
        uint256 indexed finalResultCode,
        uint8 indexed selectedStatus,
        uint8 attemptedStatusMask,
        uint8 remainingStatusMask,
        uint8 remainingStepCount
    );
    event FlashLoanRouteExecuted(
        uint256 indexed strategyStatus,
        address indexed borrowedAsset,
        uint256 amount,
        uint256 profit
    );
    event ProfitSwept(address indexed recipient, address indexed token, uint256 amount, uint256 reserveUsdc);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    function _scanResultCode(uint256 status, RuntimeTradeDecision memory decision) internal pure returns (uint256) {
        return decision.failureCode == ERR_NO_PRICE_SPREAD ? 3200 + status : 3100 + status;
    }

    function _previewSelectedCode(uint256 status) internal pure returns (uint256) {
        return 1100 + status;
    }

    function _executedCode(uint256 status) internal pure returns (uint256) {
        return 1200 + status;
    }

    function _hop(
        uint8 hopIndex,
        address tokenIn,
        address tokenOut,
        address pool,
        uint24 fee,
        uint256 amountIn,
        uint256 quotedAmountOut,
        uint256 amountOutMin
    ) internal pure returns (RuntimeHopPreview memory hopPreview) {
        hopPreview = RuntimeHopPreview({
            hopIndex: hopIndex,
            tokenIn: tokenIn,
            tokenOut: tokenOut,
            pool: pool,
            fee: fee,
            amountIn: amountIn,
            quotedAmountOut: quotedAmountOut,
            amountOutMin: amountOutMin
        });
    }

    function _emptyDecision() internal pure returns (RuntimeTradeDecision memory decision) {
        decision.failureCode = ERR_NOT_ENOUGH_POOLS;
    }

    function _signedDiff(uint256 left, uint256 right) internal pure returns (int256) {
        return left >= right ? int256(left - right) : -int256(right - left);
    }

    function _revertSelector(bytes memory reason) internal pure returns (bytes4 selector) {
        if (reason.length < 4) return bytes4(0);
        assembly {
            selector := mload(add(reason, 32))
        }
    }


}
