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

contract TriangularRouteController {
    error NotOwner();
    error Paused();
    error InvalidRequest();
    error UnsupportedAdapterKind(uint8 adapterKind);
    error NoRuntimeOpportunity(uint256 failureCode);
    error TransferFailed();
    error Reentrancy();

    uint8 public constant ADAPTER_NONE = 0;
    uint8 public constant ADAPTER_UNISWAP_V3 = 1;

    uint256 public constant FAIL_NONE = 0;
    uint256 public constant FAIL_RUNTIME_NOT_ENOUGH_POOLS = 101;
    uint256 public constant FAIL_RUNTIME_NO_PRICE_SPREAD = 102;
    uint256 public constant MAX_RUNTIME_POOL_SCAN = 10;
    uint256 public constant MAX_TRADE_SCAN = 16;

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
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable usdc;
    address public immutable executor;
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
        _validateRuntimeTradeTokens(trade.tokenX, trade.tokenY);

        RuntimePoolSnapshot memory low;
        RuntimePoolSnapshot memory high;
        uint256 scannedPoolCount;
        uint256 validPoolCount;

        for (uint256 i = 0; i < MAX_RUNTIME_POOL_SCAN; i++) {
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
}
