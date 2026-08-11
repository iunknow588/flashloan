// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Controller {
    function transfer(address to, uint256 value) external returns (bool);
}

interface IV3PoolControllerLike {
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

interface IAaveTriangularExecutorController {
    struct ExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        bytes swapPath;
        uint256 amount;
        uint256 deadline;
        uint256 amountOutMinUsdc;
    }

    function execute(ExecutionRequest calldata request) external returns (uint256 profitSwept);
    function flashLoanPremiumBps() external view returns (uint256);
}

interface IRouterQuoteControllerLike {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);
}

interface IAaveCrossPoolExecutorController {
    struct CrossPoolExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        bytes swapPath;
        address buyPool;
        address sellPool;
        uint256 amount;
        uint256 deadline;
        uint256 minFinalTokenX;
    }

    function execute(CrossPoolExecutionRequest calldata request) external returns (uint256 profitSwept);
    function flashLoanPremiumBps() external view returns (uint256);
}

contract TriangularRouteController {
    error NotOwner();
    error Paused();
    error InvalidRequest();
    error UnsupportedAdapterKind(uint8 adapterKind);
    error NoRuntimeOpportunity(uint256 failureCode);
    error ExecutionRouterMissing(uint8 adapterKind);
    error ExecutionQuoterMissing(uint8 adapterKind);
    error RouterQuoteFailed(bytes4 selector);
    error RouterQuoteResultInvalid(uint256 resultLength);
    error V3QuoteFailed(bytes4 selector);
    error V3QuoteResultInvalid(uint256 resultLength);
    error RuntimeProfitCheckFailed(
        uint256 quotedFinalUsdc,
        uint256 protectedAmountOutMinUsdc,
        uint256 premiumUsdc,
        uint256 minProfitUsdc
    );
    error RuntimeCrossPoolProfitCheckFailed(
        uint256 quotedFinalTokenX,
        uint256 protectedMinFinalTokenX,
        uint256 premiumTokenX,
        uint256 minProfitTokenX
    );
    error NoProfitableRuntimeExecution(uint256 checkedTradeCount);
    error CrossPoolExecutorMissing();
    error TransferFailed();
    error Reentrancy();

    uint8 public constant ADAPTER_NONE = 0;
    uint8 public constant ADAPTER_UNISWAP_V3 = 1;
    uint8 public constant EXECUTION_KIND_NONE = 0;
    uint8 public constant EXECUTION_KIND_TRIANGULAR = 1;
    uint8 public constant EXECUTION_KIND_CROSS_POOL = 2;

    uint256 public constant FAIL_NONE = 0;
    uint256 public constant FAIL_RUNTIME_NOT_ENOUGH_POOLS = 101;
    uint256 public constant FAIL_RUNTIME_NO_PRICE_SPREAD = 102;
    uint256 public constant MAX_RUNTIME_POOL_SCAN = 10;
    uint256 public constant MAX_ORDERED_RUNTIME_POOL_SCAN = 5;
    uint256 public constant MAX_TRADE_SCAN = 16;
    uint256 public constant STRATEGY_STATUS_UX_CROSS_POOL = 1;
    uint256 public constant STRATEGY_STATUS_UY_CROSS_POOL = 2;
    uint256 public constant STRATEGY_STATUS_XY_CROSS_POOL = 3;
    uint256 public constant STRATEGY_STATUS_XY_USDC_FALLBACK = 4;
    uint256 public constant STRATEGY_STATUS_COMBINED_FALLBACK = 5;
    uint256 public constant STRATEGY_STATUS_NO_PROFITABLE_ROUTE = 55555;

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

    struct RuntimePoolSpec {
        uint8 adapterKind;
        address pool;
    }

    struct RuntimeTradeSpec {
        uint256 tradeIndex;
        address tokenX;
        address tokenY;
        RuntimePoolSpec[MAX_RUNTIME_POOL_SCAN] pools;
    }

    struct RuntimeExecutionParams {
        uint256 amount;
        uint256 deadline;
        uint256 amountOutMinUsdc;
        uint256 minProfitUsdc;
        uint24 usdcToTokenXFee;
        uint24 tokenYToUsdcFee;
    }

    struct RuntimeCrossPoolExecutionParams {
        uint256 amount;
        uint256 deadline;
        uint256 minFinalTokenX;
        uint256 minProfitTokenX;
    }

    struct RuntimeExecutionPreview {
        address router;
        bytes swapPath;
        uint256 quotedFinalUsdc;
        uint256 premiumUsdc;
        uint256 requiredFinalUsdc;
        uint256 protectedAmountOutMinUsdc;
        uint256 minProfitUsdc;
    }

    struct RuntimePoolSnapshot {
        bool valid;
        uint8 adapterKind;
        address pool;
        address token0;
        address token1;
        uint24 fee;
        uint128 liquidity;
        uint160 sqrtPriceX96;
        int24 tick;
        int24 normalizedTick;
    }

    struct RuntimeTradeDecision {
        bool viable;
        uint256 tradeIndex;
        address tokenX;
        address tokenY;
        address lowPool;
        address highPool;
        uint8 adapterKind;
        uint24 lowFee;
        uint24 highFee;
        uint128 lowLiquidity;
        uint128 highLiquidity;
        int24 lowNormalizedTick;
        int24 highNormalizedTick;
        int256 tickDelta;
        uint256 scannedPoolCount;
        uint256 validPoolCount;
        uint256 failureCode;
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
    event RuntimeTradeSelected(
        uint256 indexed tradeArrayIndex,
        uint256 indexed tradeIndex,
        address indexed tokenX,
        address tokenY,
        address lowPool,
        address highPool,
        int256 tickDelta
    );
    event RuntimeTradeExecuted(
        uint256 indexed tradeArrayIndex,
        uint256 indexed tradeIndex,
        address indexed router,
        uint256 amount,
        uint256 amountOutMinUsdc,
        uint256 profitSwept
    );
    event RuntimeProfitChecked(
        address indexed router,
        uint256 amount,
        uint256 quotedFinalUsdc,
        uint256 premiumUsdc,
        uint256 minProfitUsdc,
        uint256 requiredFinalUsdc,
        uint256 protectedAmountOutMinUsdc
    );
    event CrossPoolExecutorSet(address indexed previousExecutor, address indexed newExecutor);
    event RuntimeCrossPoolProfitChecked(
        address indexed buyPool,
        address indexed sellPool,
        uint256 amount,
        uint256 quotedFinalTokenX,
        uint256 premiumTokenX,
        uint256 minProfitTokenX,
        uint256 requiredFinalTokenX,
        uint256 protectedMinFinalTokenX
    );
    event RuntimeCrossPoolExecuted(
        uint256 indexed tradeArrayIndex,
        uint256 indexed tradeIndex,
        address indexed buyPool,
        address sellPool,
        uint256 amount,
        uint256 protectedMinFinalTokenX,
        uint256 profitSwept
    );
    event RuntimeAutoExecutionSelected(
        uint8 indexed executionKind,
        uint256 indexed tradeArrayIndex,
        uint256 indexed tradeIndex
    );
    event RuntimeOrderedAutoExecutionSelected(
        uint256 indexed strategyStatus,
        uint8 indexed executionKind,
        uint256 indexed tradeArrayIndex,
        uint256 tradeIndex
    );
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable usdc;
    address public immutable executor;
    address public crossPoolExecutor;
    address public owner;
    bool public paused;
    bool private locked;
    RuntimeRiskConfig public runtimeRiskConfig;
    mapping(uint8 => AdapterConfig) public adapterConfigs;

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

    constructor(address usdcAddress, address executorAddress, address initialOwner) {
        if (usdcAddress == address(0) || executorAddress == address(0)) revert InvalidRequest();
        usdc = usdcAddress;
        executor = executorAddress;
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
        runtimeRiskConfig = RuntimeRiskConfig({minPoolLiquidity: 1, minTickDelta: 1});
        emit OwnershipTransferred(address(0), owner);
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

    function setRuntimeRiskConfig(uint128 minPoolLiquidity, int256 minTickDelta) external onlyOwner {
        if (minPoolLiquidity == 0 || minTickDelta <= 0) revert InvalidRequest();
        runtimeRiskConfig = RuntimeRiskConfig({minPoolLiquidity: minPoolLiquidity, minTickDelta: minTickDelta});
        emit RuntimeRiskConfigSet(minPoolLiquidity, minTickDelta);
    }

    function setCrossPoolExecutor(address newExecutor) external onlyOwner {
        if (newExecutor == address(0)) revert InvalidRequest();
        emit CrossPoolExecutorSet(crossPoolExecutor, newExecutor);
        crossPoolExecutor = newExecutor;
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

    function previewRuntimeTrade(RuntimeTradeSpec calldata trade)
        external
        view
        returns (RuntimeTradeDecision memory decision)
    {
        decision = _previewRuntimeTrade(trade);
    }

    function previewBestRuntimeTrades(RuntimeTradeSpec[] calldata trades)
        external
        view
        returns (uint256 bestTradeArrayIndex, RuntimeTradeDecision memory decision)
    {
        (bestTradeArrayIndex, decision) = _previewBestRuntimeTrades(trades);
    }

    function previewBestRuntimeExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata params
    )
        external
        view
        returns (
            uint256 bestTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        (bestTradeArrayIndex, decision) = _previewBestRuntimeTrades(trades);
        if (!decision.viable) return (bestTradeArrayIndex, decision, executionPreview);
        executionPreview = _previewRuntimeExecution(decision, params);
    }

    function previewFirstProfitableRuntimeExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata params
    )
        external
        view
        returns (
            bool found,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        return _previewFirstProfitableRuntimeExecution(trades, params);
    }

    function runBestRuntimeTrades(RuntimeTradeSpec[] calldata trades)
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (uint256 bestTradeArrayIndex, RuntimeTradeDecision memory decision)
    {
        (bestTradeArrayIndex, decision) = _previewBestRuntimeTrades(trades);
        if (!decision.viable) revert NoRuntimeOpportunity(decision.failureCode);
        emit RuntimeTradeSelected(
            bestTradeArrayIndex,
            decision.tradeIndex,
            decision.tokenX,
            decision.tokenY,
            decision.lowPool,
            decision.highPool,
            decision.tickDelta
        );
    }

    function runBestRuntimeTradesAndExecute(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata params
    )
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (uint256 bestTradeArrayIndex, RuntimeTradeDecision memory decision, uint256 profitSwept)
    {
        if (params.amount == 0 || params.minProfitUsdc == 0 || params.deadline < block.timestamp) revert InvalidRequest();

        (bestTradeArrayIndex, decision) = _previewBestRuntimeTrades(trades);
        if (!decision.viable) revert NoRuntimeOpportunity(decision.failureCode);

        RuntimeExecutionPreview memory executionPreview = _previewRuntimeExecution(decision, params);
        _validateRuntimeExecutionProfit(executionPreview);

        emit RuntimeTradeSelected(
            bestTradeArrayIndex,
            decision.tradeIndex,
            decision.tokenX,
            decision.tokenY,
            decision.lowPool,
            decision.highPool,
            decision.tickDelta
        );
        emit RuntimeProfitChecked(
            executionPreview.router,
            params.amount,
            executionPreview.quotedFinalUsdc,
            executionPreview.premiumUsdc,
            executionPreview.minProfitUsdc,
            executionPreview.requiredFinalUsdc,
            executionPreview.protectedAmountOutMinUsdc
        );

        profitSwept = IAaveTriangularExecutorController(executor).execute(
            IAaveTriangularExecutorController.ExecutionRequest({
                tokenX: decision.tokenX,
                tokenY: decision.tokenY,
                router: executionPreview.router,
                swapPath: executionPreview.swapPath,
                amount: params.amount,
                deadline: params.deadline,
                amountOutMinUsdc: executionPreview.protectedAmountOutMinUsdc
            })
        );

        emit RuntimeTradeExecuted(
            bestTradeArrayIndex,
            decision.tradeIndex,
            executionPreview.router,
            params.amount,
            executionPreview.protectedAmountOutMinUsdc,
            profitSwept
        );
    }

    function runFirstProfitableRuntimeTradesAndExecute(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata params
    )
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (uint256 selectedTradeArrayIndex, RuntimeTradeDecision memory decision, uint256 profitSwept)
    {
        if (params.amount == 0 || params.minProfitUsdc == 0 || params.deadline < block.timestamp) revert InvalidRequest();

        RuntimeExecutionPreview memory executionPreview;
        bool found;
        (found, selectedTradeArrayIndex, decision, executionPreview) = _previewFirstProfitableRuntimeExecution(trades, params);
        if (!found) revert NoProfitableRuntimeExecution(trades.length);

        emit RuntimeTradeSelected(
            selectedTradeArrayIndex,
            decision.tradeIndex,
            decision.tokenX,
            decision.tokenY,
            decision.lowPool,
            decision.highPool,
            decision.tickDelta
        );
        emit RuntimeProfitChecked(
            executionPreview.router,
            params.amount,
            executionPreview.quotedFinalUsdc,
            executionPreview.premiumUsdc,
            executionPreview.minProfitUsdc,
            executionPreview.requiredFinalUsdc,
            executionPreview.protectedAmountOutMinUsdc
        );

        profitSwept = IAaveTriangularExecutorController(executor).execute(
            IAaveTriangularExecutorController.ExecutionRequest({
                tokenX: decision.tokenX,
                tokenY: decision.tokenY,
                router: executionPreview.router,
                swapPath: executionPreview.swapPath,
                amount: params.amount,
                deadline: params.deadline,
                amountOutMinUsdc: executionPreview.protectedAmountOutMinUsdc
            })
        );

        emit RuntimeTradeExecuted(
            selectedTradeArrayIndex,
            decision.tradeIndex,
            executionPreview.router,
            params.amount,
            executionPreview.protectedAmountOutMinUsdc,
            profitSwept
        );
    }

    function previewBestRuntimeCrossPoolExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeCrossPoolExecutionParams calldata params
    )
        external
        view
        returns (
            uint256 bestTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        (bestTradeArrayIndex, decision) = _previewBestRuntimeTrades(trades);
        if (!decision.viable) return (bestTradeArrayIndex, decision, executionPreview);
        executionPreview = _previewRuntimeCrossPoolExecution(decision, params);
    }

    function previewFirstProfitableRuntimeAutoExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata triangularParams,
        RuntimeCrossPoolExecutionParams calldata crossPoolParams
    )
        external
        view
        returns (
            bool found,
            uint8 executionKind,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        return _previewFirstProfitableRuntimeAutoExecution(trades, triangularParams, crossPoolParams);
    }

    function previewOrderedRuntimeAutoExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata triangularParams,
        RuntimeCrossPoolExecutionParams calldata crossPoolParams,
        bool enableNonUsdcCrossPool
    )
        external
        view
        returns (
            bool found,
            uint256 strategyStatus,
            uint8 executionKind,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        return _previewOrderedRuntimeAutoExecution(
            trades,
            triangularParams,
            crossPoolParams,
            enableNonUsdcCrossPool
        );
    }

    function runBestRuntimeTradesAndExecuteCrossPool(
        RuntimeTradeSpec[] calldata trades,
        RuntimeCrossPoolExecutionParams calldata params
    )
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (uint256 bestTradeArrayIndex, RuntimeTradeDecision memory decision, uint256 profitSwept)
    {
        if (params.amount == 0 || params.minProfitTokenX == 0 || params.deadline < block.timestamp) revert InvalidRequest();

        (bestTradeArrayIndex, decision) = _previewBestRuntimeTrades(trades);
        if (!decision.viable) revert NoRuntimeOpportunity(decision.failureCode);

        RuntimeExecutionPreview memory executionPreview = _previewRuntimeCrossPoolExecution(decision, params);
        _validateRuntimeCrossPoolProfit(executionPreview);
        if (crossPoolExecutor == address(0)) revert CrossPoolExecutorMissing();

        emit RuntimeTradeSelected(
            bestTradeArrayIndex,
            decision.tradeIndex,
            decision.tokenX,
            decision.tokenY,
            decision.lowPool,
            decision.highPool,
            decision.tickDelta
        );
        emit RuntimeCrossPoolProfitChecked(
            decision.highPool,
            decision.lowPool,
            params.amount,
            executionPreview.quotedFinalUsdc,
            executionPreview.premiumUsdc,
            executionPreview.minProfitUsdc,
            executionPreview.requiredFinalUsdc,
            executionPreview.protectedAmountOutMinUsdc
        );

        profitSwept = IAaveCrossPoolExecutorController(crossPoolExecutor).execute(
            IAaveCrossPoolExecutorController.CrossPoolExecutionRequest({
                tokenX: decision.tokenX,
                tokenY: decision.tokenY,
                router: executionPreview.router,
                swapPath: executionPreview.swapPath,
                buyPool: decision.highPool,
                sellPool: decision.lowPool,
                amount: params.amount,
                deadline: params.deadline,
                minFinalTokenX: executionPreview.protectedAmountOutMinUsdc
            })
        );

        emit RuntimeCrossPoolExecuted(
            bestTradeArrayIndex,
            decision.tradeIndex,
            decision.highPool,
            decision.lowPool,
            params.amount,
            executionPreview.protectedAmountOutMinUsdc,
            profitSwept
        );
    }

    function runFirstProfitableRuntimeTradesAndExecuteAuto(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata triangularParams,
        RuntimeCrossPoolExecutionParams calldata crossPoolParams
    )
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (
            uint8 executionKind,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            uint256 profitSwept
        )
    {
        RuntimeExecutionPreview memory executionPreview;
        bool found;
        (found, executionKind, selectedTradeArrayIndex, decision, executionPreview) =
            _previewFirstProfitableRuntimeAutoExecution(trades, triangularParams, crossPoolParams);
        if (!found) revert NoProfitableRuntimeExecution(trades.length);

        emit RuntimeTradeSelected(
            selectedTradeArrayIndex,
            decision.tradeIndex,
            decision.tokenX,
            decision.tokenY,
            decision.lowPool,
            decision.highPool,
            decision.tickDelta
        );
        emit RuntimeAutoExecutionSelected(executionKind, selectedTradeArrayIndex, decision.tradeIndex);

        if (executionKind == EXECUTION_KIND_CROSS_POOL) {
            emit RuntimeCrossPoolProfitChecked(
                decision.highPool,
                decision.lowPool,
                crossPoolParams.amount,
                executionPreview.quotedFinalUsdc,
                executionPreview.premiumUsdc,
                executionPreview.minProfitUsdc,
                executionPreview.requiredFinalUsdc,
                executionPreview.protectedAmountOutMinUsdc
            );

            profitSwept = IAaveCrossPoolExecutorController(crossPoolExecutor).execute(
                IAaveCrossPoolExecutorController.CrossPoolExecutionRequest({
                    tokenX: decision.tokenX,
                    tokenY: decision.tokenY,
                    router: executionPreview.router,
                    swapPath: executionPreview.swapPath,
                    buyPool: decision.highPool,
                    sellPool: decision.lowPool,
                    amount: crossPoolParams.amount,
                    deadline: crossPoolParams.deadline,
                    minFinalTokenX: executionPreview.protectedAmountOutMinUsdc
                })
            );

            emit RuntimeCrossPoolExecuted(
                selectedTradeArrayIndex,
                decision.tradeIndex,
                decision.highPool,
                decision.lowPool,
                crossPoolParams.amount,
                executionPreview.protectedAmountOutMinUsdc,
                profitSwept
            );
            return (executionKind, selectedTradeArrayIndex, decision, profitSwept);
        }

        if (executionKind != EXECUTION_KIND_TRIANGULAR) revert InvalidRequest();
        emit RuntimeProfitChecked(
            executionPreview.router,
            triangularParams.amount,
            executionPreview.quotedFinalUsdc,
            executionPreview.premiumUsdc,
            executionPreview.minProfitUsdc,
            executionPreview.requiredFinalUsdc,
            executionPreview.protectedAmountOutMinUsdc
        );

        profitSwept = IAaveTriangularExecutorController(executor).execute(
            IAaveTriangularExecutorController.ExecutionRequest({
                tokenX: decision.tokenX,
                tokenY: decision.tokenY,
                router: executionPreview.router,
                swapPath: executionPreview.swapPath,
                amount: triangularParams.amount,
                deadline: triangularParams.deadline,
                amountOutMinUsdc: executionPreview.protectedAmountOutMinUsdc
            })
        );

        emit RuntimeTradeExecuted(
            selectedTradeArrayIndex,
            decision.tradeIndex,
            executionPreview.router,
            triangularParams.amount,
            executionPreview.protectedAmountOutMinUsdc,
            profitSwept
        );
    }

    function runOrderedRuntimeTradesAndExecuteAuto(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata triangularParams,
        RuntimeCrossPoolExecutionParams calldata crossPoolParams,
        bool enableNonUsdcCrossPool
    )
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (
            uint256 strategyStatus,
            uint8 executionKind,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            uint256 profitSwept
        )
    {
        RuntimeExecutionPreview memory executionPreview;
        bool found;
        (found, strategyStatus, executionKind, selectedTradeArrayIndex, decision, executionPreview) =
            _previewOrderedRuntimeAutoExecution(
                trades,
                triangularParams,
                crossPoolParams,
                enableNonUsdcCrossPool
            );
        if (!found) revert NoProfitableRuntimeExecution(trades.length);

        emit RuntimeTradeSelected(
            selectedTradeArrayIndex,
            decision.tradeIndex,
            decision.tokenX,
            decision.tokenY,
            decision.lowPool,
            decision.highPool,
            decision.tickDelta
        );
        emit RuntimeAutoExecutionSelected(executionKind, selectedTradeArrayIndex, decision.tradeIndex);
        emit RuntimeOrderedAutoExecutionSelected(
            strategyStatus,
            executionKind,
            selectedTradeArrayIndex,
            decision.tradeIndex
        );

        if (executionKind == EXECUTION_KIND_CROSS_POOL) {
            emit RuntimeCrossPoolProfitChecked(
                decision.highPool,
                decision.lowPool,
                crossPoolParams.amount,
                executionPreview.quotedFinalUsdc,
                executionPreview.premiumUsdc,
                executionPreview.minProfitUsdc,
                executionPreview.requiredFinalUsdc,
                executionPreview.protectedAmountOutMinUsdc
            );

            profitSwept = IAaveCrossPoolExecutorController(crossPoolExecutor).execute(
                IAaveCrossPoolExecutorController.CrossPoolExecutionRequest({
                    tokenX: decision.tokenX,
                    tokenY: decision.tokenY,
                    router: executionPreview.router,
                    swapPath: executionPreview.swapPath,
                    buyPool: decision.highPool,
                    sellPool: decision.lowPool,
                    amount: crossPoolParams.amount,
                    deadline: crossPoolParams.deadline,
                    minFinalTokenX: executionPreview.protectedAmountOutMinUsdc
                })
            );

            emit RuntimeCrossPoolExecuted(
                selectedTradeArrayIndex,
                decision.tradeIndex,
                decision.highPool,
                decision.lowPool,
                crossPoolParams.amount,
                executionPreview.protectedAmountOutMinUsdc,
                profitSwept
            );
            return (strategyStatus, executionKind, selectedTradeArrayIndex, decision, profitSwept);
        }

        if (executionKind != EXECUTION_KIND_TRIANGULAR) revert InvalidRequest();
        emit RuntimeProfitChecked(
            executionPreview.router,
            triangularParams.amount,
            executionPreview.quotedFinalUsdc,
            executionPreview.premiumUsdc,
            executionPreview.minProfitUsdc,
            executionPreview.requiredFinalUsdc,
            executionPreview.protectedAmountOutMinUsdc
        );

        profitSwept = IAaveTriangularExecutorController(executor).execute(
            IAaveTriangularExecutorController.ExecutionRequest({
                tokenX: decision.tokenX,
                tokenY: decision.tokenY,
                router: executionPreview.router,
                swapPath: executionPreview.swapPath,
                amount: triangularParams.amount,
                deadline: triangularParams.deadline,
                amountOutMinUsdc: executionPreview.protectedAmountOutMinUsdc
            })
        );

        emit RuntimeTradeExecuted(
            selectedTradeArrayIndex,
            decision.tradeIndex,
            executionPreview.router,
            triangularParams.amount,
            executionPreview.protectedAmountOutMinUsdc,
            profitSwept
        );
    }

    function withdrawToken(address token, address to, uint256 tokenAmount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        if (!IERC20Controller(token).transfer(to, tokenAmount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, tokenAmount);
    }

    function _previewBestRuntimeTrades(RuntimeTradeSpec[] calldata trades)
        private
        view
        returns (uint256 bestTradeArrayIndex, RuntimeTradeDecision memory decision)
    {
        if (trades.length == 0 || trades.length > MAX_TRADE_SCAN) revert InvalidRequest();
        bestTradeArrayIndex = 0;
        decision = _previewRuntimeTrade(trades[0]);
        for (uint256 i = 1; i < trades.length; i++) {
            RuntimeTradeDecision memory candidate = _previewRuntimeTrade(trades[i]);
            if (_shouldReplaceRuntimeBest(candidate, decision)) {
                bestTradeArrayIndex = i;
                decision = candidate;
            }
        }
    }

    function _previewRuntimeTrade(RuntimeTradeSpec calldata trade)
        private
        view
        returns (RuntimeTradeDecision memory decision)
    {
        decision = _previewRuntimeTradeWithLimit(trade, MAX_RUNTIME_POOL_SCAN);
    }

    function _previewRuntimeTradeWithLimit(
        RuntimeTradeSpec calldata trade,
        uint256 poolScanLimit
    )
        private
        view
        returns (RuntimeTradeDecision memory decision)
    {
        _validateRuntimePoolTokens(trade.tokenX, trade.tokenY);
        if (poolScanLimit == 0 || poolScanLimit > MAX_RUNTIME_POOL_SCAN) revert InvalidRequest();

        RuntimePoolSnapshot memory low;
        RuntimePoolSnapshot memory high;
        uint256 scannedPoolCount;
        uint256 validPoolCount;

        for (uint256 i = 0; i < poolScanLimit; i++) {
            RuntimePoolSpec calldata spec = trade.pools[i];
            if (spec.adapterKind == ADAPTER_NONE && spec.pool == address(0)) {
                continue;
            }
            scannedPoolCount++;
            RuntimePoolSnapshot memory snapshot = _runtimePoolSnapshot(spec, trade.tokenX, trade.tokenY);
            if (!snapshot.valid) {
                continue;
            }
            validPoolCount++;
            if (!low.valid || snapshot.normalizedTick < low.normalizedTick) {
                low = snapshot;
            }
            if (!high.valid || snapshot.normalizedTick > high.normalizedTick) {
                high = snapshot;
            }
        }

        if (validPoolCount < 2) {
            return _runtimeDecision(
                false,
                trade,
                low,
                high,
                0,
                scannedPoolCount,
                validPoolCount,
                FAIL_RUNTIME_NOT_ENOUGH_POOLS
            );
        }

        int256 tickDelta = int256(high.normalizedTick) - int256(low.normalizedTick);
        if (tickDelta < runtimeRiskConfig.minTickDelta) {
            return _runtimeDecision(
                false,
                trade,
                low,
                high,
                tickDelta,
                scannedPoolCount,
                validPoolCount,
                FAIL_RUNTIME_NO_PRICE_SPREAD
            );
        }

        decision = _runtimeDecision(true, trade, low, high, tickDelta, scannedPoolCount, validPoolCount, FAIL_NONE);
    }

    function _runtimePoolSnapshot(
        RuntimePoolSpec calldata spec,
        address tokenX,
        address tokenY
    ) private view returns (RuntimePoolSnapshot memory snapshot) {
        if (spec.pool == address(0) || spec.adapterKind == ADAPTER_NONE) return snapshot;
        AdapterConfig memory config = adapterConfigs[spec.adapterKind];
        if (!config.allowed) revert UnsupportedAdapterKind(spec.adapterKind);
        if (spec.adapterKind != ADAPTER_UNISWAP_V3) revert UnsupportedAdapterKind(spec.adapterKind);
        snapshot = _v3PoolSnapshot(spec.pool, tokenX, tokenY, config.factory);
    }

    function _previewRuntimeExecution(
        RuntimeTradeDecision memory decision,
        RuntimeExecutionParams calldata params
    ) private view returns (RuntimeExecutionPreview memory executionPreview) {
        if (params.amount == 0 || params.minProfitUsdc == 0 || params.deadline < block.timestamp) revert InvalidRequest();
        AdapterConfig memory config = adapterConfigs[decision.adapterKind];
        if (config.router == address(0)) revert ExecutionRouterMissing(decision.adapterKind);
        if (config.quoter == address(0)) revert ExecutionQuoterMissing(decision.adapterKind);

        bytes memory swapPath = _v3TriangularPath(decision, params);
        uint256 quotedFinalUsdc = _quoteV3ExactInput(config.quoter, params.amount, swapPath);
        uint256 premiumUsdc = _flashLoanPremiumUsdc(params.amount);
        uint256 requiredFinalUsdc = params.amount + premiumUsdc + params.minProfitUsdc;
        uint256 protectedAmountOutMinUsdc =
            params.amountOutMinUsdc > requiredFinalUsdc ? params.amountOutMinUsdc : requiredFinalUsdc;

        executionPreview = RuntimeExecutionPreview({
            router: config.router,
            swapPath: swapPath,
            quotedFinalUsdc: quotedFinalUsdc,
            premiumUsdc: premiumUsdc,
            requiredFinalUsdc: requiredFinalUsdc,
            protectedAmountOutMinUsdc: protectedAmountOutMinUsdc,
            minProfitUsdc: params.minProfitUsdc
        });
    }

    function _previewFirstProfitableRuntimeExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata params
    )
        private
        view
        returns (
            bool found,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        if (trades.length == 0 || trades.length > MAX_TRADE_SCAN) revert InvalidRequest();
        if (params.amount == 0 || params.minProfitUsdc == 0 || params.deadline < block.timestamp) revert InvalidRequest();

        for (uint256 i = 0; i < trades.length; i++) {
            RuntimeTradeDecision memory candidate = _previewRuntimeTrade(trades[i]);
            if (!candidate.viable) continue;

            (bool quoteSucceeded, RuntimeExecutionPreview memory candidatePreview) =
                _tryPreviewRuntimeExecution(candidate, params);
            if (!quoteSucceeded || candidatePreview.quotedFinalUsdc < candidatePreview.protectedAmountOutMinUsdc) {
                continue;
            }

            return (true, i, candidate, candidatePreview);
        }
    }

    function _tryPreviewRuntimeExecution(
        RuntimeTradeDecision memory decision,
        RuntimeExecutionParams calldata params
    ) private view returns (bool quoteSucceeded, RuntimeExecutionPreview memory executionPreview) {
        AdapterConfig memory config = adapterConfigs[decision.adapterKind];
        if (config.router == address(0)) return (false, executionPreview);
        if (config.quoter == address(0)) return (false, executionPreview);

        bytes memory swapPath = _v3TriangularPath(decision, params);
        (bool quoteReturned, uint256 quotedFinalUsdc) = _tryQuoteV3ExactInput(config.quoter, params.amount, swapPath);
        if (!quoteReturned) return (false, executionPreview);

        uint256 premiumUsdc = _flashLoanPremiumUsdc(params.amount);
        uint256 requiredFinalUsdc = params.amount + premiumUsdc + params.minProfitUsdc;
        uint256 protectedAmountOutMinUsdc =
            params.amountOutMinUsdc > requiredFinalUsdc ? params.amountOutMinUsdc : requiredFinalUsdc;

        executionPreview = RuntimeExecutionPreview({
            router: config.router,
            swapPath: swapPath,
            quotedFinalUsdc: quotedFinalUsdc,
            premiumUsdc: premiumUsdc,
            requiredFinalUsdc: requiredFinalUsdc,
            protectedAmountOutMinUsdc: protectedAmountOutMinUsdc,
            minProfitUsdc: params.minProfitUsdc
        });
        quoteSucceeded = true;
    }

    function _previewRuntimeCrossPoolExecution(
        RuntimeTradeDecision memory decision,
        RuntimeCrossPoolExecutionParams calldata params
    ) private view returns (RuntimeExecutionPreview memory executionPreview) {
        if (params.amount == 0 || params.minProfitTokenX == 0 || params.deadline < block.timestamp) revert InvalidRequest();
        if (crossPoolExecutor == address(0)) revert CrossPoolExecutorMissing();

        AdapterConfig memory config = adapterConfigs[decision.adapterKind];
        if (config.router == address(0)) revert ExecutionRouterMissing(decision.adapterKind);
        if (config.quoter == address(0)) revert ExecutionQuoterMissing(decision.adapterKind);

        bytes memory swapPath = _v3CrossPoolPath(decision);
        uint256 quotedFinalTokenX = _quoteV3ExactInput(config.quoter, params.amount, swapPath);
        uint256 premiumTokenX = _crossPoolPremium(params.amount);
        uint256 requiredFinalTokenX = params.amount + premiumTokenX + params.minProfitTokenX;
        uint256 protectedMinFinalTokenX =
            params.minFinalTokenX > requiredFinalTokenX ? params.minFinalTokenX : requiredFinalTokenX;

        executionPreview = RuntimeExecutionPreview({
            router: config.router,
            swapPath: swapPath,
            quotedFinalUsdc: quotedFinalTokenX,
            premiumUsdc: premiumTokenX,
            requiredFinalUsdc: requiredFinalTokenX,
            protectedAmountOutMinUsdc: protectedMinFinalTokenX,
            minProfitUsdc: params.minProfitTokenX
        });
    }

    function _previewFirstProfitableRuntimeAutoExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata triangularParams,
        RuntimeCrossPoolExecutionParams calldata crossPoolParams
    )
        private
        view
        returns (
            bool found,
            uint8 executionKind,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        if (trades.length == 0 || trades.length > MAX_TRADE_SCAN) revert InvalidRequest();
        if (
            triangularParams.amount == 0
                || triangularParams.minProfitUsdc == 0
                || triangularParams.deadline < block.timestamp
                || crossPoolParams.amount == 0
                || crossPoolParams.minProfitTokenX == 0
                || crossPoolParams.deadline < block.timestamp
        ) {
            revert InvalidRequest();
        }

        for (uint256 i = 0; i < trades.length; i++) {
            RuntimeTradeDecision memory candidate = _previewRuntimeTrade(trades[i]);
            if (!candidate.viable) continue;

            (bool crossPoolSucceeded, RuntimeExecutionPreview memory crossPoolPreview) =
                _tryPreviewRuntimeCrossPoolExecution(candidate, crossPoolParams);
            if (
                crossPoolSucceeded
                    && crossPoolPreview.quotedFinalUsdc >= crossPoolPreview.protectedAmountOutMinUsdc
            ) {
                return (true, EXECUTION_KIND_CROSS_POOL, i, candidate, crossPoolPreview);
            }

            if (!_isTriangularRuntimeTokens(candidate.tokenX, candidate.tokenY)) continue;
            (bool triangularSucceeded, RuntimeExecutionPreview memory triangularPreview) =
                _tryPreviewRuntimeExecution(candidate, triangularParams);
            if (
                triangularSucceeded
                    && triangularPreview.quotedFinalUsdc >= triangularPreview.protectedAmountOutMinUsdc
            ) {
                return (true, EXECUTION_KIND_TRIANGULAR, i, candidate, triangularPreview);
            }
        }
    }

    function _previewOrderedRuntimeAutoExecution(
        RuntimeTradeSpec[] calldata trades,
        RuntimeExecutionParams calldata triangularParams,
        RuntimeCrossPoolExecutionParams calldata crossPoolParams,
        bool enableNonUsdcCrossPool
    )
        private
        view
        returns (
            bool found,
            uint256 strategyStatus,
            uint8 executionKind,
            uint256 selectedTradeArrayIndex,
            RuntimeTradeDecision memory decision,
            RuntimeExecutionPreview memory executionPreview
        )
    {
        if (trades.length == 0 || trades.length > MAX_TRADE_SCAN) revert InvalidRequest();
        if (
            triangularParams.amount == 0
                || triangularParams.minProfitUsdc == 0
                || triangularParams.deadline < block.timestamp
                || crossPoolParams.amount == 0
                || crossPoolParams.minProfitTokenX == 0
                || crossPoolParams.deadline < block.timestamp
        ) {
            revert InvalidRequest();
        }

        for (uint256 i = 0; i < trades.length; i++) {
            RuntimeTradeDecision memory candidate =
                _previewRuntimeTradeWithLimit(trades[i], MAX_ORDERED_RUNTIME_POOL_SCAN);
            if (!candidate.viable) continue;

            bool directUsdcPair = candidate.tokenX == usdc || candidate.tokenY == usdc;
            if (directUsdcPair || enableNonUsdcCrossPool) {
                (bool crossPoolSucceeded, RuntimeExecutionPreview memory crossPoolPreview) =
                    _tryPreviewRuntimeCrossPoolExecution(candidate, crossPoolParams);
                if (
                    crossPoolSucceeded
                        && crossPoolPreview.quotedFinalUsdc >= crossPoolPreview.protectedAmountOutMinUsdc
                ) {
                    return (
                        true,
                        _orderedStrategyStatus(i, EXECUTION_KIND_CROSS_POOL),
                        EXECUTION_KIND_CROSS_POOL,
                        i,
                        candidate,
                        crossPoolPreview
                    );
                }
            }

            if (!_isTriangularRuntimeTokens(candidate.tokenX, candidate.tokenY)) continue;
            (bool triangularSucceeded, RuntimeExecutionPreview memory triangularPreview) =
                _tryPreviewRuntimeExecution(candidate, triangularParams);
            if (
                triangularSucceeded
                    && triangularPreview.quotedFinalUsdc >= triangularPreview.protectedAmountOutMinUsdc
            ) {
                return (
                    true,
                    _orderedStrategyStatus(i, EXECUTION_KIND_TRIANGULAR),
                    EXECUTION_KIND_TRIANGULAR,
                    i,
                    candidate,
                    triangularPreview
                );
            }
        }

        strategyStatus = STRATEGY_STATUS_NO_PROFITABLE_ROUTE;
    }

    function _tryPreviewRuntimeCrossPoolExecution(
        RuntimeTradeDecision memory decision,
        RuntimeCrossPoolExecutionParams calldata params
    ) private view returns (bool quoteSucceeded, RuntimeExecutionPreview memory executionPreview) {
        if (crossPoolExecutor == address(0)) return (false, executionPreview);
        AdapterConfig memory config = adapterConfigs[decision.adapterKind];
        if (config.router == address(0)) return (false, executionPreview);
        if (config.quoter == address(0)) return (false, executionPreview);
        if (decision.highFee == 0 || decision.lowFee == 0 || decision.highFee == decision.lowFee) {
            return (false, executionPreview);
        }

        bytes memory swapPath = _v3CrossPoolPath(decision);
        (bool quoteReturned, uint256 quotedFinalTokenX) = _tryQuoteV3ExactInput(config.quoter, params.amount, swapPath);
        if (!quoteReturned) return (false, executionPreview);

        uint256 premiumTokenX = _crossPoolPremium(params.amount);
        uint256 requiredFinalTokenX = params.amount + premiumTokenX + params.minProfitTokenX;
        uint256 protectedMinFinalTokenX =
            params.minFinalTokenX > requiredFinalTokenX ? params.minFinalTokenX : requiredFinalTokenX;

        executionPreview = RuntimeExecutionPreview({
            router: config.router,
            swapPath: swapPath,
            quotedFinalUsdc: quotedFinalTokenX,
            premiumUsdc: premiumTokenX,
            requiredFinalUsdc: requiredFinalTokenX,
            protectedAmountOutMinUsdc: protectedMinFinalTokenX,
            minProfitUsdc: params.minProfitTokenX
        });
        quoteSucceeded = true;
    }

    function _validateRuntimeExecutionProfit(RuntimeExecutionPreview memory executionPreview) private pure {
        if (executionPreview.quotedFinalUsdc < executionPreview.protectedAmountOutMinUsdc) {
            revert RuntimeProfitCheckFailed(
                executionPreview.quotedFinalUsdc,
                executionPreview.protectedAmountOutMinUsdc,
                executionPreview.premiumUsdc,
                executionPreview.minProfitUsdc
            );
        }
    }

    function _validateRuntimeCrossPoolProfit(RuntimeExecutionPreview memory executionPreview) private pure {
        if (executionPreview.quotedFinalUsdc < executionPreview.protectedAmountOutMinUsdc) {
            revert RuntimeCrossPoolProfitCheckFailed(
                executionPreview.quotedFinalUsdc,
                executionPreview.protectedAmountOutMinUsdc,
                executionPreview.premiumUsdc,
                executionPreview.minProfitUsdc
            );
        }
    }

    function _v3TriangularPath(
        RuntimeTradeDecision memory decision,
        RuntimeExecutionParams calldata params
    ) private view returns (bytes memory swapPath) {
        if (params.usdcToTokenXFee == 0 || params.tokenYToUsdcFee == 0 || decision.highFee == 0) {
            revert InvalidRequest();
        }
        _validateRuntimeTradeTokens(decision.tokenX, decision.tokenY);
        swapPath = abi.encodePacked(
            usdc,
            params.usdcToTokenXFee,
            decision.tokenX,
            decision.highFee,
            decision.tokenY,
            params.tokenYToUsdcFee,
            usdc
        );
    }

    function _v3CrossPoolPath(RuntimeTradeDecision memory decision) private pure returns (bytes memory swapPath) {
        if (decision.highFee == 0 || decision.lowFee == 0 || decision.highFee == decision.lowFee) {
            revert InvalidRequest();
        }
        _validateRuntimePoolTokens(decision.tokenX, decision.tokenY);
        swapPath = abi.encodePacked(
            decision.tokenX,
            decision.highFee,
            decision.tokenY,
            decision.lowFee,
            decision.tokenX
        );
    }

    function _quoteV3ExactInput(
        address quoter,
        uint256 amount,
        bytes memory path
    ) private view returns (uint256 quotedFinalAmount) {
        (bool quoteReturned, bytes memory data) = quoter.staticcall(
            abi.encodeWithSignature("quoteExactInput(bytes,uint256)", path, amount)
        );
        if (!quoteReturned) revert V3QuoteFailed(_revertSelector(data));
        if (data.length < 32) revert V3QuoteResultInvalid(data.length);
        quotedFinalAmount = abi.decode(data, (uint256));
    }

    function _tryQuoteV3ExactInput(
        address quoter,
        uint256 amount,
        bytes memory path
    ) private view returns (bool quoteReturned, uint256 quotedFinalAmount) {
        (bool ok, bytes memory data) = quoter.staticcall(
            abi.encodeWithSignature("quoteExactInput(bytes,uint256)", path, amount)
        );
        if (!ok || data.length < 32) return (false, 0);
        return (true, abi.decode(data, (uint256)));
    }

    function _quoteFinalUsdc(
        address router,
        uint256 amount,
        address[] memory path
    ) private view returns (uint256 quotedFinalUsdc) {
        quotedFinalUsdc = _quoteFinalAmount(router, amount, path);
    }

    function _quoteFinalAmount(
        address venue,
        uint256 amount,
        address[] memory path
    ) private view returns (uint256 quotedFinalAmount) {
        uint256[] memory amounts;
        try IRouterQuoteControllerLike(venue).getAmountsOut(amount, path) returns (uint256[] memory result) {
            amounts = result;
        } catch (bytes memory reason) {
            revert RouterQuoteFailed(_revertSelector(reason));
        }
        if (amounts.length != path.length) revert RouterQuoteResultInvalid(amounts.length);
        quotedFinalAmount = amounts[amounts.length - 1];
    }

    function _tryQuoteFinalAmount(
        address venue,
        uint256 amount,
        address[] memory path
    ) private view returns (bool quoteReturned, uint256 quotedFinalAmount) {
        try IRouterQuoteControllerLike(venue).getAmountsOut(amount, path) returns (uint256[] memory amounts) {
            if (amounts.length != path.length) return (false, 0);
            return (true, amounts[amounts.length - 1]);
        } catch {
            return (false, 0);
        }
    }

    function _flashLoanPremiumUsdc(uint256 amount) private view returns (uint256) {
        uint256 premiumBps = IAaveTriangularExecutorController(executor).flashLoanPremiumBps();
        if (premiumBps == 0) return 0;
        return (amount * premiumBps + 9999) / 10000;
    }

    function _crossPoolPremium(uint256 amount) private view returns (uint256) {
        uint256 premiumBps = IAaveCrossPoolExecutorController(crossPoolExecutor).flashLoanPremiumBps();
        if (premiumBps == 0) return 0;
        return (amount * premiumBps + 9999) / 10000;
    }

    function _routePath(address tokenX, address tokenY) private view returns (address[] memory path) {
        _validateRuntimeTradeTokens(tokenX, tokenY);
        path = new address[](4);
        path[0] = usdc;
        path[1] = tokenX;
        path[2] = tokenY;
        path[3] = usdc;
    }

    function _twoTokenPath(address tokenIn, address tokenOut) private pure returns (address[] memory path) {
        if (tokenIn == address(0) || tokenOut == address(0) || tokenIn == tokenOut) revert InvalidRequest();
        path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;
    }

    function _v3PoolSnapshot(
        address pool,
        address tokenX,
        address tokenY,
        address allowedFactory
    ) private view returns (RuntimePoolSnapshot memory snapshot) {
        address factory;
        address token0;
        address token1;
        uint24 poolFee;
        uint128 poolLiquidity;
        uint160 sqrtPriceX96;
        int24 tick;

        try IV3PoolControllerLike(pool).factory() returns (address value) {
            factory = value;
        } catch {
            return snapshot;
        }
        if (allowedFactory != address(0) && factory != allowedFactory) {
            return snapshot;
        }
        try IV3PoolControllerLike(pool).token0() returns (address value) {
            token0 = value;
        } catch {
            return snapshot;
        }
        try IV3PoolControllerLike(pool).token1() returns (address value) {
            token1 = value;
        } catch {
            return snapshot;
        }
        try IV3PoolControllerLike(pool).fee() returns (uint24 value) {
            poolFee = value;
        } catch {
            return snapshot;
        }
        try IV3PoolControllerLike(pool).liquidity() returns (uint128 value) {
            poolLiquidity = value;
        } catch {
            return snapshot;
        }
        if (poolLiquidity < runtimeRiskConfig.minPoolLiquidity) {
            return snapshot;
        }
        try IV3PoolControllerLike(pool).slot0() returns (
            uint160 sqrtValue,
            int24 tickValue,
            uint16,
            uint16,
            uint16,
            uint8,
            bool
        ) {
            sqrtPriceX96 = sqrtValue;
            tick = tickValue;
        } catch {
            return snapshot;
        }
        if (sqrtPriceX96 == 0) {
            return snapshot;
        }

        int24 normalizedTick;
        if (token0 == tokenX && token1 == tokenY) {
            normalizedTick = tick;
        } else if (token0 == tokenY && token1 == tokenX) {
            normalizedTick = -tick;
        } else {
            return snapshot;
        }

        snapshot = RuntimePoolSnapshot({
            valid: true,
            adapterKind: ADAPTER_UNISWAP_V3,
            pool: pool,
            token0: token0,
            token1: token1,
            fee: poolFee,
            liquidity: poolLiquidity,
            sqrtPriceX96: sqrtPriceX96,
            tick: tick,
            normalizedTick: normalizedTick
        });
    }

    function _runtimeDecision(
        bool viable,
        RuntimeTradeSpec calldata trade,
        RuntimePoolSnapshot memory low,
        RuntimePoolSnapshot memory high,
        int256 tickDelta,
        uint256 scannedPoolCount,
        uint256 validPoolCount,
        uint256 failureCode
    ) private pure returns (RuntimeTradeDecision memory decision) {
        decision = RuntimeTradeDecision({
            viable: viable,
            tradeIndex: trade.tradeIndex,
            tokenX: trade.tokenX,
            tokenY: trade.tokenY,
            lowPool: low.pool,
            highPool: high.pool,
            adapterKind: high.adapterKind,
            lowFee: low.fee,
            highFee: high.fee,
            lowLiquidity: low.liquidity,
            highLiquidity: high.liquidity,
            lowNormalizedTick: low.normalizedTick,
            highNormalizedTick: high.normalizedTick,
            tickDelta: tickDelta,
            scannedPoolCount: scannedPoolCount,
            validPoolCount: validPoolCount,
            failureCode: failureCode
        });
    }

    function _shouldReplaceRuntimeBest(
        RuntimeTradeDecision memory candidate,
        RuntimeTradeDecision memory current
    ) private pure returns (bool) {
        if (!candidate.viable) return false;
        if (!current.viable) return true;
        if (candidate.tickDelta > current.tickDelta) return true;
        if (candidate.tickDelta < current.tickDelta) return false;
        return _runtimeMinLiquidity(candidate) > _runtimeMinLiquidity(current);
    }

    function _runtimeMinLiquidity(RuntimeTradeDecision memory decision) private pure returns (uint128) {
        return decision.lowLiquidity < decision.highLiquidity ? decision.lowLiquidity : decision.highLiquidity;
    }

    function _orderedStrategyStatus(uint256 tradeArrayIndex, uint8 executionKind) private pure returns (uint256) {
        if (tradeArrayIndex == 0) return STRATEGY_STATUS_UX_CROSS_POOL;
        if (tradeArrayIndex == 1) return STRATEGY_STATUS_UY_CROSS_POOL;
        if (tradeArrayIndex == 2) {
            return executionKind == EXECUTION_KIND_CROSS_POOL
                ? STRATEGY_STATUS_XY_CROSS_POOL
                : STRATEGY_STATUS_XY_USDC_FALLBACK;
        }
        return STRATEGY_STATUS_COMBINED_FALLBACK;
    }

    function _validateRuntimeTradeTokens(address tokenX, address tokenY) private view {
        if (
            tokenX == address(0)
                || tokenY == address(0)
                || tokenX == usdc
                || tokenY == usdc
                || tokenX == tokenY
        ) {
            revert InvalidRequest();
        }
    }

    function _validateRuntimePoolTokens(address tokenX, address tokenY) private pure {
        if (tokenX == address(0) || tokenY == address(0) || tokenX == tokenY) revert InvalidRequest();
    }

    function _isTriangularRuntimeTokens(address tokenX, address tokenY) private view returns (bool) {
        return tokenX != address(0) && tokenY != address(0) && tokenX != tokenY && tokenX != usdc && tokenY != usdc;
    }

    function _revertSelector(bytes memory reason) private pure returns (bytes4 selector) {
        if (reason.length < 4) return bytes4(0);
        assembly {
            selector := mload(add(reason, 32))
        }
    }
}
