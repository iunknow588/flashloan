// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Unified {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IAavePoolUnified {
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IV3PoolUnifiedLike {
    function factory() external view returns (address);
    function token0() external view returns (address);
    function token1() external view returns (address);
    function fee() external view returns (uint24);
    function liquidity() external view returns (uint128);
    function slot0()
        external
        view
        returns (
            uint160 sqrtPriceX96,
            int24 tick,
            uint16 observationIndex,
            uint16 observationCardinality,
            uint16 observationCardinalityNext,
            uint8 feeProtocol,
            bool unlocked
        );
}

interface IV3QuoterUnifiedLike {
    function quoteExactInput(bytes calldata path, uint256 amountIn) external view returns (uint256 amountOut);
}

interface IV3RouterUnifiedLike {
    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);
}

interface IFlashLoanSimpleReceiverUnified {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract UnifiedFlashLoanMevExecutor is IFlashLoanSimpleReceiverUnified {
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
    uint256 internal constant MAX_ORDERED_TRADE_SCAN = 5;

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
    event RuntimePoolExtremesSelected(
        uint256 indexed tradeArrayIndex,
        address lowPool,
        address highPool,
        int256 tickDelta
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

    address public immutable aavePool;
    address public immutable usdc;
    address public owner;
    bool public paused;
    bool private locked;
    bool public profitSweepEnabled;
    uint256 public profitReserveUsdc;
    uint256 public profitSweepThreshold;
    RuntimeRiskConfig public runtimeRiskConfig;
    mapping(uint8 => AdapterConfig) public adapterConfigs;
    mapping(address => BorrowConfig) public borrowConfigs;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert Paused();
        _;
    }

    modifier nonReentrantEntry() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(address poolAddress, address usdcAddress, address initialOwner) {
        if (poolAddress == address(0) || usdcAddress == address(0)) revert InvalidRequest();
        aavePool = poolAddress;
        usdc = usdcAddress;
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
        profitSweepEnabled = true;
        runtimeRiskConfig = RuntimeRiskConfig({minPoolLiquidity: 1, minTickDelta: 1});
        borrowConfigs[usdcAddress] = BorrowConfig({enabled: true, maxAmount: type(uint256).max});
        emit OwnershipTransferred(address(0), owner);
        emit BorrowConfigSet(usdcAddress, true, type(uint256).max);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidRequest();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function setPaused(bool value) external onlyOwner {
        paused = value;
        emit PausedSet(value);
    }

    function setAdapterConfig(
        uint8 adapterKind,
        bool allowed,
        address factory,
        address router,
        address quoter
    ) external onlyOwner {
        if (adapterKind == ADAPTER_NONE) revert InvalidRequest();
        if (allowed && adapterKind != ADAPTER_UNISWAP_V3) revert UnsupportedAdapterKind(adapterKind);
        adapterConfigs[adapterKind] = AdapterConfig({
            allowed: allowed,
            factory: factory,
            router: router,
            quoter: quoter
        });
        emit AdapterConfigSet(adapterKind, allowed, factory, router, quoter);
    }

    function setRuntimeRiskConfig(uint128 minPoolLiquidity, int256 minTickDelta) external onlyOwner {
        if (minPoolLiquidity == 0 || minTickDelta <= 0) revert InvalidRequest();
        runtimeRiskConfig = RuntimeRiskConfig({minPoolLiquidity: minPoolLiquidity, minTickDelta: minTickDelta});
        emit RuntimeRiskConfigSet(minPoolLiquidity, minTickDelta);
    }

    function setBorrowConfig(address token, bool enabled, uint256 maxAmount) external onlyOwner {
        if (token == address(0)) revert InvalidRequest();
        borrowConfigs[token] = BorrowConfig({enabled: enabled, maxAmount: maxAmount});
        emit BorrowConfigSet(token, enabled, maxAmount);
    }

    function setProfitConfig(bool sweepEnabled, uint256 reserveUsdc, uint256 sweepThreshold) external onlyOwner {
        profitSweepEnabled = sweepEnabled;
        profitReserveUsdc = reserveUsdc;
        profitSweepThreshold = sweepThreshold;
        emit ProfitConfigSet(sweepEnabled, reserveUsdc, sweepThreshold);
    }

    function flashLoanPremiumBps() external view returns (uint256) {
        return IAavePoolUnified(aavePool).FLASHLOAN_PREMIUM_TOTAL();
    }

    function previewRuntimeTrade(RuntimeTradeSpec calldata trade)
        external
        view
        returns (RuntimeTradeDecision memory decision)
    {
        decision = _previewRuntimeTrade(trade);
    }

    function previewOrderedRuntimeAutoExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata usdcParams,
        RuntimeTokenBorrowParams calldata tokenBorrowParams,
        bool enableNonUsdcCrossPool
    ) external view returns (RuntimeOrderPreview memory result) {
        result = _previewOrderedRuntimeAutoExecution(trades, usdcParams, tokenBorrowParams, enableNonUsdcCrossPool);
    }

    function runOrderedRuntimeTradesAndExecuteAuto(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata usdcParams,
        RuntimeTokenBorrowParams calldata tokenBorrowParams,
        bool enableNonUsdcCrossPool
    )
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (RuntimeRunResult memory result)
    {
        RuntimeOrderPreview memory preview =
            _previewOrderedRuntimeAutoExecution(trades, usdcParams, tokenBorrowParams, enableNonUsdcCrossPool);
        _emitProgress(preview.progress);

        if (!preview.found) {
            RuntimeStepReport memory last = preview.progress.steps[
                preview.progress.lastCheckedStatus == 0 ? 0 : preview.progress.lastCheckedStatus - 1
            ];
            revert OrderedRuntimeExecutionFailed(
                ERR_NO_PROFITABLE_ROUTE,
                preview.progress.lastCheckedStatus,
                last.tradeArrayIndex,
                last.detailCode,
                last.expectedProfit,
                last.quotedFinal,
                last.requiredFinal,
                preview.progress.attemptedStatusMask,
                preview.progress.remainingStatusMask
            );
        }

        emit OrderedRuntimePreviewSelected(
            preview.strategyStatus,
            preview.executionKind,
            preview.selectedTradeArrayIndex,
            preview.decision.tradeIndex
        );
        emit RuntimePoolExtremesSelected(
            preview.selectedTradeArrayIndex,
            preview.decision.lowPool,
            preview.decision.highPool,
            preview.decision.tickDelta
        );
        emit RuntimeProfitChecked(
            preview.executionPreview.quotedFinal,
            preview.executionPreview.requiredFinal,
            preview.executionPreview.premium,
            preview.executionPreview.minProfit
        );
        _emitTriangularHopQuotes(preview.triangularRoute);

        uint256 startingProfitBalance = IERC20Unified(preview.executionPreview.profitAsset).balanceOf(address(this));
        CallbackPlan memory plan = CallbackPlan({
            strategyStatus: preview.strategyStatus,
            executionKind: preview.executionKind,
            routeDirection: preview.executionPreview.routeDirection,
            borrowedAsset: preview.executionPreview.borrowedAsset,
            profitAsset: preview.executionPreview.profitAsset,
            router: _routerForDecision(preview.decision),
            swapPath: preview.executionPreview.swapPath,
            deadline: _deadlineForPreview(preview.executionPreview, usdcParams, tokenBorrowParams),
            protectedMinFinal: preview.executionPreview.protectedMinFinal,
            startingProfitBalance: startingProfitBalance,
            tradeArrayIndex: preview.selectedTradeArrayIndex
        });

        IAavePoolUnified(aavePool).flashLoanSimple(
            address(this),
            preview.executionPreview.borrowedAsset,
            preview.executionPreview.borrowedAmount,
            abi.encode(plan),
            0
        );

        uint256 endingProfitBalance = IERC20Unified(preview.executionPreview.profitAsset).balanceOf(address(this));
        if (endingProfitBalance > startingProfitBalance) {
            result.profitAmount = endingProfitBalance - startingProfitBalance;
            result.profitSwept = _sweepProfit(preview.executionPreview.profitAsset, result.profitAmount);
        }

        result.resultCode = _executedCode(preview.strategyStatus);
        result.strategyStatus = preview.strategyStatus;
        result.executionKind = preview.executionKind;
        result.routeDirection = preview.executionPreview.routeDirection;
        result.selectedTradeArrayIndex = preview.selectedTradeArrayIndex;
        result.profitAsset = preview.executionPreview.profitAsset;
        result.attemptedStatusMask = preview.progress.attemptedStatusMask;
        result.remainingStatusMask = preview.progress.remainingStatusMask;
        result.remainingStepCount = preview.progress.remainingStepCount;

        emit RuntimeWorkflowFinished(
            result.resultCode,
            uint8(preview.strategyStatus),
            result.attemptedStatusMask,
            result.remainingStatusMask,
            result.remainingStepCount
        );
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override whenNotPaused returns (bool) {
        if (msg.sender != aavePool) revert NotPool();
        if (initiator != address(this)) revert BadInitiator();
        if (asset == address(0) || amount == 0) revert InvalidRequest();

        CallbackPlan memory plan = abi.decode(params, (CallbackPlan));
        if (asset != plan.borrowedAsset || plan.deadline < block.timestamp || plan.router == address(0)) {
            revert InvalidRequest();
        }

        uint256 owed = amount + premium;
        _forceApprove(plan.borrowedAsset, plan.router, amount);
        uint256 finalAmount;
        try IV3RouterUnifiedLike(plan.router).exactInput(
            IV3RouterUnifiedLike.ExactInputParams({
                path: plan.swapPath,
                recipient: address(this),
                deadline: plan.deadline,
                amountIn: amount,
                amountOutMinimum: plan.protectedMinFinal
            })
        ) returns (uint256 result) {
            finalAmount = result;
        } catch (bytes memory reason) {
            revert RouterSwapFailed(_revertSelector(reason));
        }
        _forceApprove(plan.borrowedAsset, plan.router, 0);

        uint256 actualBalance = IERC20Unified(plan.profitAsset).balanceOf(address(this));
        uint256 requiredBalance = plan.startingProfitBalance + owed;
        if (plan.borrowedAsset != plan.profitAsset) revert InvalidRequest();
        if (actualBalance < requiredBalance || finalAmount < plan.protectedMinFinal) {
            revert ExecutionConstraintFailed(
                plan.protectedMinFinal,
                owed,
                finalAmount,
                actualBalance,
                requiredBalance
            );
        }

        _forceApprove(plan.borrowedAsset, aavePool, owed);
        emit FlashLoanRouteExecuted(plan.strategyStatus, plan.borrowedAsset, amount, actualBalance - requiredBalance);
        return true;
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        if (!IERC20Unified(token).transfer(to, amount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, amount);
    }

    function _previewOrderedRuntimeAutoExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata usdcParams,
        RuntimeTokenBorrowParams calldata tokenBorrowParams,
        bool enableNonUsdcCrossPool
    ) private view returns (RuntimeOrderPreview memory result) {
        if (trades.length == 0 || trades.length > MAX_ORDERED_TRADE_SCAN) revert InvalidRequest();
        if (usdcParams.deadline < block.timestamp || tokenBorrowParams.deadline < block.timestamp) {
            revert InvalidRequest();
        }
        _initProgress(result.progress);

        RuntimeTradeDecision memory ux = trades.length > 0 ? _previewRuntimeTrade(trades[0]) : _emptyDecision();
        if (_evaluateUsdcCrossPool(result, ux, usdcParams, 0, STATUS_UX_CROSS_POOL)) return result;

        RuntimeTradeDecision memory uy = trades.length > 1 ? _previewRuntimeTrade(trades[1]) : _emptyDecision();
        if (_evaluateUsdcCrossPool(result, uy, usdcParams, 1, STATUS_UY_CROSS_POOL)) return result;

        RuntimeTradeDecision memory xy = trades.length > 2 ? _previewRuntimeTrade(trades[2]) : _emptyDecision();
        if (
            _evaluateTokenCrossPool(
                result,
                xy,
                tokenBorrowParams,
                2,
                STATUS_XY_CROSS_POOL,
                enableNonUsdcCrossPool
            )
        ) {
            return result;
        }

        if (_evaluateTriangular(result, ux, uy, xy, usdcParams, 2, STATUS_XY_USDC_FALLBACK)) return result;

        RuntimeTradeDecision memory yx = trades.length > 3 ? _previewRuntimeTrade(trades[3]) : _emptyDecision();
        if (_evaluateFallbackTriangular(result, ux, uy, yx, usdcParams, 3)) return result;

        result.progress.finalResultCode = ERR_NO_PROFITABLE_ROUTE;
    }

    function _evaluateUsdcCrossPool(
        RuntimeOrderPreview memory result,
        RuntimeTradeDecision memory decision,
        RuntimeExecutionParams calldata params,
        uint256 tradeArrayIndex,
        uint256 status
    ) private view returns (bool selected) {
        _attempt(result.progress, status);
        if (!decision.viable) {
            _failStep(result.progress, status, tradeArrayIndex, decision, _scanResultCode(status, decision), decision.failureCode);
            return false;
        }
        if (params.amount == 0 || params.minProfitUsdc == 0 || !_isBorrowEnabled(usdc, params.amount)) {
            _failStep(result.progress, status, tradeArrayIndex, decision, 3500 + status, ERR_BORROW_ASSET_DISABLED);
            return false;
        }
        if (decision.tokenX != usdc && decision.tokenY != usdc) {
            _failStep(result.progress, status, tradeArrayIndex, decision, 3500 + status, ERR_BORROW_ASSET_DISABLED);
            return false;
        }

        RuntimeExecutionPreview memory executionPreview;
        bool quoteOk;
        (quoteOk, executionPreview) = _tryPreviewCrossPool(decision, usdc, params.amount, params.amountOutMinUsdc, params.minProfitUsdc);
        if (!quoteOk) {
            _failStep(result.progress, status, tradeArrayIndex, decision, 3300 + status, ERR_QUOTE_FAILED);
            return false;
        }
        if (executionPreview.quotedFinal < executionPreview.protectedMinFinal) {
            _failStepWithPreview(result.progress, status, tradeArrayIndex, decision, 3400 + status, executionPreview);
            return false;
        }
        _select(result, status, EXECUTION_KIND_USDC_CROSS_POOL, tradeArrayIndex, decision, executionPreview);
        return true;
    }

    function _evaluateTokenCrossPool(
        RuntimeOrderPreview memory result,
        RuntimeTradeDecision memory decision,
        RuntimeTokenBorrowParams calldata params,
        uint256 tradeArrayIndex,
        uint256 status,
        bool enableNonUsdcCrossPool
    ) private view returns (bool selected) {
        _attempt(result.progress, status);
        if (!decision.viable) {
            _failStep(result.progress, status, tradeArrayIndex, decision, _scanResultCode(status, decision), decision.failureCode);
            return false;
        }
        if (
            !enableNonUsdcCrossPool
                || params.amount == 0
                || params.minProfitToken == 0
                || !_isBorrowEnabled(decision.tokenX, params.amount)
                || decision.tokenX == usdc
                || decision.tokenY == usdc
        ) {
            _failStep(result.progress, status, tradeArrayIndex, decision, 3500 + status, ERR_BORROW_ASSET_DISABLED);
            return false;
        }

        RuntimeExecutionPreview memory executionPreview;
        bool quoteOk;
        (quoteOk, executionPreview) =
            _tryPreviewCrossPool(decision, decision.tokenX, params.amount, params.minFinalToken, params.minProfitToken);
        if (!quoteOk) {
            _failStep(result.progress, status, tradeArrayIndex, decision, 3300 + status, ERR_QUOTE_FAILED);
            return false;
        }
        if (executionPreview.quotedFinal < executionPreview.protectedMinFinal) {
            _failStepWithPreview(result.progress, status, tradeArrayIndex, decision, 3400 + status, executionPreview);
            return false;
        }
        _select(result, status, EXECUTION_KIND_TOKEN_CROSS_POOL, tradeArrayIndex, decision, executionPreview);
        return true;
    }

    function _evaluateTriangular(
        RuntimeOrderPreview memory result,
        RuntimeTradeDecision memory ux,
        RuntimeTradeDecision memory uy,
        RuntimeTradeDecision memory xy,
        RuntimeExecutionParams calldata params,
        uint256 tradeArrayIndex,
        uint256 status
    ) private view returns (bool selected) {
        _attempt(result.progress, status);
        if (!ux.viable || !uy.viable || !xy.viable || params.amount == 0 || params.minProfitUsdc == 0) {
            _failStep(result.progress, status, tradeArrayIndex, xy, 3100 + status, ERR_NOT_ENOUGH_POOLS);
            return false;
        }
        if (!_isBorrowEnabled(usdc, params.amount)) {
            _failStep(result.progress, status, tradeArrayIndex, xy, 3500 + status, ERR_BORROW_ASSET_DISABLED);
            return false;
        }
        if (!_hasForwardTriangularLayout(ux, uy, xy)) {
            _failStep(result.progress, status, tradeArrayIndex, xy, 3100 + status, ERR_ROUTE_LAYOUT_INVALID);
            return false;
        }

        RuntimeExecutionPreview memory executionPreview;
        RuntimeTriangularRoutePreview memory triangularRoute;
        bool quoteOk;
        (quoteOk, executionPreview, triangularRoute) = _tryPreviewTriangular(
            ux,
            xy,
            uy,
            params,
            ROUTE_DIRECTION_U_X_Y_U,
            status
        );
        if (!quoteOk) {
            _failStepWithRoute(result.progress, status, tradeArrayIndex, xy, triangularRoute);
            return false;
        }
        if (executionPreview.quotedFinal < executionPreview.protectedMinFinal) {
            _failStepWithPreview(result.progress, status, tradeArrayIndex, xy, 3400 + status, executionPreview);
            return false;
        }
        _select(result, status, EXECUTION_KIND_USDC_TRIANGULAR, tradeArrayIndex, xy, executionPreview);
        result.triangularRoute = triangularRoute;
        return true;
    }

    function _evaluateFallbackTriangular(
        RuntimeOrderPreview memory result,
        RuntimeTradeDecision memory ux,
        RuntimeTradeDecision memory uy,
        RuntimeTradeDecision memory yx,
        RuntimeExecutionParams calldata params,
        uint256 tradeArrayIndex
    ) private view returns (bool selected) {
        uint256 status = STATUS_COMBINED_FALLBACK;
        _attempt(result.progress, status);
        if (!ux.viable || !uy.viable || !yx.viable || params.amount == 0 || params.minProfitUsdc == 0) {
            _failStep(result.progress, status, tradeArrayIndex, yx, 3100 + status, ERR_NOT_ENOUGH_POOLS);
            return false;
        }
        if (!_isBorrowEnabled(usdc, params.amount)) {
            _failStep(result.progress, status, tradeArrayIndex, yx, 3500 + status, ERR_BORROW_ASSET_DISABLED);
            return false;
        }
        if (!_hasReverseTriangularLayout(ux, uy, yx)) {
            _failStep(result.progress, status, tradeArrayIndex, yx, 3100 + status, ERR_ROUTE_LAYOUT_INVALID);
            return false;
        }

        RuntimeExecutionPreview memory executionPreview;
        RuntimeTriangularRoutePreview memory triangularRoute;
        bool quoteOk;
        (quoteOk, executionPreview, triangularRoute) = _tryPreviewTriangular(
            uy,
            yx,
            ux,
            params,
            ROUTE_DIRECTION_U_Y_X_U,
            status
        );
        if (!quoteOk) {
            _failStepWithRoute(result.progress, status, tradeArrayIndex, yx, triangularRoute);
            return false;
        }
        if (executionPreview.quotedFinal < executionPreview.protectedMinFinal) {
            _failStepWithPreview(result.progress, status, tradeArrayIndex, yx, 3400 + status, executionPreview);
            return false;
        }
        _select(result, status, EXECUTION_KIND_USDC_TRIANGULAR, tradeArrayIndex, yx, executionPreview);
        result.triangularRoute = triangularRoute;
        return true;
    }

    function _hasForwardTriangularLayout(
        RuntimeTradeDecision memory ux,
        RuntimeTradeDecision memory uy,
        RuntimeTradeDecision memory xy
    ) private view returns (bool) {
        return (
            ux.tokenX == usdc
                && uy.tokenX == usdc
                && ux.tokenY == xy.tokenX
                && uy.tokenY == xy.tokenY
        );
    }

    function _hasReverseTriangularLayout(
        RuntimeTradeDecision memory ux,
        RuntimeTradeDecision memory uy,
        RuntimeTradeDecision memory yx
    ) private view returns (bool) {
        return (
            ux.tokenX == usdc
                && uy.tokenX == usdc
                && uy.tokenY == yx.tokenX
                && ux.tokenY == yx.tokenY
        );
    }

    function _tryPreviewCrossPool(
        RuntimeTradeDecision memory decision,
        address borrowedAsset,
        uint256 amount,
        uint256 externalMinFinal,
        uint256 minProfit
    ) private view returns (bool quoteOk, RuntimeExecutionPreview memory preview) {
        AdapterConfig memory config = adapterConfigs[ADAPTER_UNISWAP_V3];
        if (config.router == address(0)) revert ExecutionRouterMissing(ADAPTER_UNISWAP_V3);
        if (config.quoter == address(0)) revert ExecutionQuoterMissing(ADAPTER_UNISWAP_V3);

        bytes memory path = _crossPoolPath(decision, borrowedAsset);
        (quoteOk, preview.quotedFinal) = _tryQuoteExactInput(config.quoter, path, amount);
        if (!quoteOk) return (false, preview);
        preview.borrowedAsset = borrowedAsset;
        preview.profitAsset = borrowedAsset;
        preview.borrowedAmount = amount;
        preview.routeDirection = ROUTE_DIRECTION_NONE;
        preview.swapPath = path;
        preview.premium = _premium(amount);
        preview.requiredFinal = amount + preview.premium + minProfit;
        preview.protectedMinFinal = externalMinFinal > preview.requiredFinal ? externalMinFinal : preview.requiredFinal;
        preview.minProfit = minProfit;
        preview.expectedProfit = _signedDiff(preview.quotedFinal, preview.requiredFinal);
    }

    function _tryPreviewTriangular(
        RuntimeTradeDecision memory first,
        RuntimeTradeDecision memory middle,
        RuntimeTradeDecision memory third,
        RuntimeExecutionParams calldata params,
        uint8 routeDirection,
        uint256 status
    )
        private
        view
        returns (
            bool quoteOk,
            RuntimeExecutionPreview memory preview,
            RuntimeTriangularRoutePreview memory route
        )
    {
        AdapterConfig memory config = adapterConfigs[ADAPTER_UNISWAP_V3];
        if (config.router == address(0)) revert ExecutionRouterMissing(ADAPTER_UNISWAP_V3);
        if (config.quoter == address(0)) revert ExecutionQuoterMissing(ADAPTER_UNISWAP_V3);

        address tokenA = middle.tokenX;
        address tokenB = middle.tokenY;
        uint24 fee1 = first.highFee;
        uint24 fee2 = middle.highFee;
        uint24 fee3 = third.lowFee;
        address pool1 = first.highPool;
        address pool2 = middle.highPool;
        address pool3 = third.lowPool;

        route.routeDirection = routeDirection;
        route.executionMode = TRIANGULAR_EXECUTION_PACKED_PATH;
        route.premiumUsdc = _premium(params.amount);
        route.requiredFinalUsdc = params.amount + route.premiumUsdc + params.minProfitUsdc;

        (bool ok1, uint256 out1) = _tryQuoteHop(config.quoter, usdc, tokenA, fee1, params.amount);
        route.hops[0] = _hop(1, usdc, tokenA, pool1, fee1, params.amount, out1, 0);
        if (!ok1) {
            route.failedHopIndex = 1;
            preview.failedHopIndex = 1;
            preview.protectedMinFinal = 4100 + status;
            return (false, preview, route);
        }

        (bool ok2, uint256 out2) = _tryQuoteHop(config.quoter, tokenA, tokenB, fee2, out1);
        route.hops[1] = _hop(2, tokenA, tokenB, pool2, fee2, out1, out2, 0);
        if (!ok2) {
            route.failedHopIndex = 2;
            preview.failedHopIndex = 2;
            preview.protectedMinFinal = 4200 + status;
            return (false, preview, route);
        }

        (bool ok3, uint256 out3) = _tryQuoteHop(config.quoter, tokenB, usdc, fee3, out2);
        route.hops[2] = _hop(3, tokenB, usdc, pool3, fee3, out2, out3, 0);
        if (!ok3) {
            route.failedHopIndex = 3;
            preview.failedHopIndex = 3;
            preview.protectedMinFinal = 4300 + status;
            return (false, preview, route);
        }

        route.quotedFinalUsdc = out3;
        route.expectedProfitUsdc = _signedDiff(out3, route.requiredFinalUsdc);

        uint256 protectedMinFinal =
            params.amountOutMinUsdc > route.requiredFinalUsdc ? params.amountOutMinUsdc : route.requiredFinalUsdc;
        route.hops[2].amountOutMin = protectedMinFinal;

        preview.borrowedAsset = usdc;
        preview.profitAsset = usdc;
        preview.borrowedAmount = params.amount;
        preview.routeDirection = routeDirection;
        preview.swapPath = abi.encodePacked(usdc, fee1, tokenA, fee2, tokenB, fee3, usdc);
        preview.quotedFinal = out3;
        preview.premium = route.premiumUsdc;
        preview.requiredFinal = route.requiredFinalUsdc;
        preview.protectedMinFinal = protectedMinFinal;
        preview.minProfit = params.minProfitUsdc;
        preview.expectedProfit = route.expectedProfitUsdc;
        return (true, preview, route);
    }

    function _previewRuntimeTrade(RuntimeTradeSpec calldata trade)
        private
        view
        returns (RuntimeTradeDecision memory decision)
    {
        if (trade.tokenX == address(0) || trade.tokenY == address(0) || trade.tokenX == trade.tokenY) {
            decision.failureCode = ERR_NOT_ENOUGH_POOLS;
            return decision;
        }

        PoolSnapshot memory low;
        PoolSnapshot memory high;
        uint256 validPoolCount;
        for (uint256 i = 0; i < MAX_POOLS_PER_TRADE; i++) {
            RuntimePoolSpec calldata spec = trade.pools[i];
            if (spec.adapterKind == ADAPTER_NONE && spec.pool == address(0)) continue;
            if (spec.adapterKind != ADAPTER_UNISWAP_V3) revert UnsupportedAdapterKind(spec.adapterKind);
            AdapterConfig memory config = adapterConfigs[spec.adapterKind];
            if (!config.allowed) continue;
            PoolSnapshot memory snapshot = _v3PoolSnapshot(spec.pool, trade.tokenX, trade.tokenY, config.factory);
            if (!snapshot.valid) continue;
            validPoolCount++;
            if (!low.valid || snapshot.normalizedTick < low.normalizedTick) low = snapshot;
            if (!high.valid || snapshot.normalizedTick > high.normalizedTick) high = snapshot;
        }

        decision.tradeIndex = trade.tradeIndex;
        decision.tokenX = trade.tokenX;
        decision.tokenY = trade.tokenY;
        decision.lowPool = low.pool;
        decision.highPool = high.pool;
        decision.lowFee = low.fee;
        decision.highFee = high.fee;
        decision.lowLiquidity = low.liquidity;
        decision.highLiquidity = high.liquidity;
        decision.lowNormalizedTick = low.normalizedTick;
        decision.highNormalizedTick = high.normalizedTick;
        decision.validPoolCount = validPoolCount;

        if (validPoolCount < 2) {
            decision.failureCode = ERR_NOT_ENOUGH_POOLS;
            return decision;
        }
        decision.tickDelta = int256(high.normalizedTick) - int256(low.normalizedTick);
        if (decision.tickDelta < runtimeRiskConfig.minTickDelta) {
            decision.failureCode = ERR_NO_PRICE_SPREAD;
            return decision;
        }
        decision.viable = true;
    }

    function _v3PoolSnapshot(
        address pool,
        address tokenX,
        address tokenY,
        address allowedFactory
    ) private view returns (PoolSnapshot memory snapshot) {
        if (pool == address(0)) return snapshot;
        try IV3PoolUnifiedLike(pool).factory() returns (address factory) {
            if (allowedFactory != address(0) && factory != allowedFactory) return snapshot;
        } catch {
            return snapshot;
        }

        address token0;
        address token1;
        try IV3PoolUnifiedLike(pool).token0() returns (address value) {
            token0 = value;
        } catch {
            return snapshot;
        }
        try IV3PoolUnifiedLike(pool).token1() returns (address value) {
            token1 = value;
        } catch {
            return snapshot;
        }

        int24 signAdjustedTick;
        if (token0 == tokenX && token1 == tokenY) {
            try IV3PoolUnifiedLike(pool).slot0() returns (uint160 sqrtPriceX96, int24 tick, uint16, uint16, uint16, uint8, bool) {
                if (sqrtPriceX96 == 0) return snapshot;
                signAdjustedTick = tick;
            } catch {
                return snapshot;
            }
        } else if (token0 == tokenY && token1 == tokenX) {
            try IV3PoolUnifiedLike(pool).slot0() returns (uint160 sqrtPriceX96, int24 tick, uint16, uint16, uint16, uint8, bool) {
                if (sqrtPriceX96 == 0) return snapshot;
                signAdjustedTick = -tick;
            } catch {
                return snapshot;
            }
        } else {
            return snapshot;
        }

        uint128 poolLiquidity;
        try IV3PoolUnifiedLike(pool).liquidity() returns (uint128 value) {
            poolLiquidity = value;
        } catch {
            return snapshot;
        }
        if (poolLiquidity < runtimeRiskConfig.minPoolLiquidity) return snapshot;

        uint24 poolFee;
        try IV3PoolUnifiedLike(pool).fee() returns (uint24 value) {
            poolFee = value;
        } catch {
            return snapshot;
        }

        snapshot = PoolSnapshot({
            valid: true,
            pool: pool,
            fee: poolFee,
            liquidity: poolLiquidity,
            normalizedTick: signAdjustedTick
        });
    }

    function _crossPoolPath(RuntimeTradeDecision memory decision, address borrowedAsset)
        private
        pure
        returns (bytes memory path)
    {
        address intermediate;
        if (borrowedAsset == decision.tokenX) {
            intermediate = decision.tokenY;
        } else if (borrowedAsset == decision.tokenY) {
            intermediate = decision.tokenX;
        } else {
            revert InvalidRequest();
        }
        path = abi.encodePacked(borrowedAsset, decision.highFee, intermediate, decision.lowFee, borrowedAsset);
    }

    function _tryQuoteHop(
        address quoter,
        address tokenIn,
        address tokenOut,
        uint24 fee,
        uint256 amountIn
    ) private view returns (bool ok, uint256 amountOut) {
        return _tryQuoteExactInput(quoter, abi.encodePacked(tokenIn, fee, tokenOut), amountIn);
    }

    function _tryQuoteExactInput(address quoter, bytes memory path, uint256 amountIn)
        private
        view
        returns (bool ok, uint256 amountOut)
    {
        (bool returned, bytes memory data) =
            quoter.staticcall(abi.encodeWithSelector(IV3QuoterUnifiedLike.quoteExactInput.selector, path, amountIn));
        if (!returned || data.length < 32) return (false, 0);
        amountOut = abi.decode(data, (uint256));
        return (true, amountOut);
    }

    function _routerForDecision(RuntimeTradeDecision memory decision) private view returns (address router) {
        AdapterConfig memory config = adapterConfigs[ADAPTER_UNISWAP_V3];
        if (decision.highFee == 0 || decision.lowFee == 0) revert InvalidRequest();
        router = config.router;
        if (router == address(0)) revert ExecutionRouterMissing(ADAPTER_UNISWAP_V3);
    }

    function _deadlineForPreview(
        RuntimeExecutionPreview memory preview,
        RuntimeExecutionParams calldata usdcParams,
        RuntimeTokenBorrowParams calldata tokenBorrowParams
    ) private view returns (uint256 deadline) {
        deadline = preview.borrowedAsset == usdc ? usdcParams.deadline : tokenBorrowParams.deadline;
    }

    function _premium(uint256 amount) private view returns (uint256) {
        uint256 premiumBps = IAavePoolUnified(aavePool).FLASHLOAN_PREMIUM_TOTAL();
        if (premiumBps == 0) return 0;
        return (amount * premiumBps + 9999) / 10000;
    }

    function _isBorrowEnabled(address token, uint256 amount) private view returns (bool) {
        BorrowConfig memory config = borrowConfigs[token];
        return config.enabled && amount != 0 && amount <= config.maxAmount;
    }

    function _sweepProfit(address token, uint256 profitAmount) private returns (uint256 swept) {
        if (!profitSweepEnabled || profitAmount < profitSweepThreshold) return 0;
        uint256 reserve = token == usdc ? profitReserveUsdc : 0;
        uint256 balance = IERC20Unified(token).balanceOf(address(this));
        if (balance <= reserve) return 0;
        swept = balance - reserve;
        if (!IERC20Unified(token).transfer(owner, swept)) revert TransferFailed();
        emit ProfitSwept(owner, token, swept, reserve);
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20Unified(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20Unified(token).approve(spender, amount)) revert ApproveFailed();
    }

    function _initProgress(RuntimeProgress memory progress) private pure {
        for (uint8 i = 0; i < 5; i++) {
            progress.steps[i].strategyStatus = i + 1;
            progress.steps[i].phase = STEP_NOT_CHECKED;
        }
    }

    function _attempt(RuntimeProgress memory progress, uint256 status) private pure {
        progress.lastCheckedStatus = uint8(status);
        progress.attemptedStatusMask |= uint8(1 << (status - 1));
    }

    function _failStep(
        RuntimeProgress memory progress,
        uint256 status,
        uint256 tradeArrayIndex,
        RuntimeTradeDecision memory decision,
        uint256 resultCode,
        uint256 detailCode
    ) private pure {
        RuntimeStepReport memory step = RuntimeStepReport({
            strategyStatus: uint8(status),
            phase: STEP_CHECKED_FAILED,
            routeDirection: ROUTE_DIRECTION_NONE,
            failedHopIndex: 0,
            resultCode: resultCode,
            detailCode: detailCode,
            tradeArrayIndex: tradeArrayIndex,
            tradeIndex: decision.tradeIndex,
            profitAsset: address(0),
            expectedProfit: 0,
            quotedFinal: 0,
            requiredFinal: 0
        });
        progress.steps[status - 1] = step;
    }

    function _failStepWithPreview(
        RuntimeProgress memory progress,
        uint256 status,
        uint256 tradeArrayIndex,
        RuntimeTradeDecision memory decision,
        uint256 resultCode,
        RuntimeExecutionPreview memory preview
    ) private pure {
        RuntimeStepReport memory step = RuntimeStepReport({
            strategyStatus: uint8(status),
            phase: STEP_CHECKED_FAILED,
            routeDirection: preview.routeDirection,
            failedHopIndex: preview.failedHopIndex,
            resultCode: resultCode,
            detailCode: ERR_PROFIT_NOT_ENOUGH,
            tradeArrayIndex: tradeArrayIndex,
            tradeIndex: decision.tradeIndex,
            profitAsset: preview.profitAsset,
            expectedProfit: preview.expectedProfit,
            quotedFinal: preview.quotedFinal,
            requiredFinal: preview.requiredFinal
        });
        progress.steps[status - 1] = step;
    }

    function _failStepWithRoute(
        RuntimeProgress memory progress,
        uint256 status,
        uint256 tradeArrayIndex,
        RuntimeTradeDecision memory decision,
        RuntimeTriangularRoutePreview memory route
    ) private pure {
        RuntimeStepReport memory step = RuntimeStepReport({
            strategyStatus: uint8(status),
            phase: STEP_CHECKED_FAILED,
            routeDirection: route.routeDirection,
            failedHopIndex: route.failedHopIndex,
            resultCode: 4000 + uint256(route.failedHopIndex) * 100 + status,
            detailCode: ERR_QUOTE_FAILED,
            tradeArrayIndex: tradeArrayIndex,
            tradeIndex: decision.tradeIndex,
            profitAsset: address(0),
            expectedProfit: route.expectedProfitUsdc,
            quotedFinal: route.quotedFinalUsdc,
            requiredFinal: route.requiredFinalUsdc
        });
        progress.steps[status - 1] = step;
    }

    function _select(
        RuntimeOrderPreview memory result,
        uint256 status,
        uint8 executionKind,
        uint256 tradeArrayIndex,
        RuntimeTradeDecision memory decision,
        RuntimeExecutionPreview memory executionPreview
    ) private pure {
        result.found = true;
        result.strategyStatus = status;
        result.executionKind = executionKind;
        result.selectedTradeArrayIndex = tradeArrayIndex;
        result.decision = decision;
        result.executionPreview = executionPreview;

        result.progress.selectedStatus = uint8(status);
        result.progress.selectedStatusMask = uint8(1 << (status - 1));
        result.progress.finalResultCode = _previewSelectedCode(status);
        for (uint8 i = uint8(status + 1); i <= 5; i++) {
            result.progress.steps[i - 1].phase = STEP_NOT_EXECUTED_AFTER_SELECTION;
            result.progress.remainingStatusMask |= uint8(1 << (i - 1));
            result.progress.remainingStepCount++;
        }
        result.progress.steps[status - 1] = RuntimeStepReport({
            strategyStatus: uint8(status),
            phase: STEP_SELECTED,
            routeDirection: executionPreview.routeDirection,
            failedHopIndex: executionPreview.failedHopIndex,
            resultCode: _previewSelectedCode(status),
            detailCode: ERR_NONE,
            tradeArrayIndex: tradeArrayIndex,
            tradeIndex: decision.tradeIndex,
            profitAsset: executionPreview.profitAsset,
            expectedProfit: executionPreview.expectedProfit,
            quotedFinal: executionPreview.quotedFinal,
            requiredFinal: executionPreview.requiredFinal
        });
    }

    function _emitProgress(RuntimeProgress memory progress) private {
        for (uint256 i = 0; i < 5; i++) {
            RuntimeStepReport memory step = progress.steps[i];
            if (step.phase == STEP_NOT_CHECKED || step.phase == STEP_NOT_EXECUTED_AFTER_SELECTION) continue;
            emit RuntimeStepEvaluated(
                step.strategyStatus,
                step.phase,
                step.resultCode,
                step.tradeArrayIndex,
                step.expectedProfit,
                step.quotedFinal,
                step.requiredFinal
            );
        }
    }

    function _emitTriangularHopQuotes(RuntimeTriangularRoutePreview memory route) private {
        if (route.routeDirection == ROUTE_DIRECTION_NONE) return;
        for (uint256 i = 0; i < 3; i++) {
            RuntimeHopPreview memory hop = route.hops[i];
            emit RuntimeTriangularHopQuoted(
                route.routeDirection,
                hop.hopIndex,
                hop.pool,
                hop.tokenIn,
                hop.tokenOut,
                hop.fee,
                hop.amountIn,
                hop.quotedAmountOut,
                hop.amountOutMin
            );
        }
    }

    function _scanResultCode(uint256 status, RuntimeTradeDecision memory decision) private pure returns (uint256) {
        return decision.failureCode == ERR_NO_PRICE_SPREAD ? 3200 + status : 3100 + status;
    }

    function _previewSelectedCode(uint256 status) private pure returns (uint256) {
        return 1100 + status;
    }

    function _executedCode(uint256 status) private pure returns (uint256) {
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
    ) private pure returns (RuntimeHopPreview memory hopPreview) {
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

    function _emptyDecision() private pure returns (RuntimeTradeDecision memory decision) {
        decision.failureCode = ERR_NOT_ENOUGH_POOLS;
    }

    function _signedDiff(uint256 left, uint256 right) private pure returns (int256) {
        return left >= right ? int256(left - right) : -int256(right - left);
    }

    function _revertSelector(bytes memory reason) private pure returns (bytes4 selector) {
        if (reason.length < 4) return bytes4(0);
        assembly {
            selector := mload(add(reason, 32))
        }
    }
}
