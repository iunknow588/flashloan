// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title RobinhoodPausedFlashLoanExecutor
/// @notice Robinhood Testnet 专用暂停版 flash loan 套利执行器主合约。
/// 默认使用 Uniswap V4 PoolManager flash accounting（kind=4），支持 V3/V4 mixed route、
/// 路由连续性校验、还款守卫和 paused 测试网演练。禁止在生产广播开启前作为真实执行器使用。

interface IERC20RobinhoodMinimal {
    function approve(address spender, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IMorphoBlueFlashSource {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}

interface IUniversalRouterRobinhood {
    function execute(bytes calldata commands, bytes[] calldata inputs, uint256 deadline) external payable;
}

interface IPermit2Robinhood {
    function approve(address token, address spender, uint160 amount, uint48 expiration) external;
}

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

interface IV4PoolManagerRobinhood {
    function unlock(bytes calldata data) external returns (bytes memory);
    function take(address currency, address to, uint256 amount) external;
    function sync(address currency) external;
    function settle() external payable returns (uint256 paid);
}

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

interface IMorphoBlueFlashLoanCallback {
    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external;
}

contract RobinhoodPausedFlashLoanExecutor is IMorphoBlueFlashLoanCallback {
    /// @notice Robinhood Testnet 专用暂停版 flash loan 套利执行器变量定义。
    /// 默认构造函数后为 paused 状态，防止未配置完成前被调用。
    /// 主要目标：复用 Uniswap V4 PoolManager flash accounting 完成借款、交易和还款的测试网演练。

    error NotOwner();
    error Paused();
    error InvalidRequest();
    error UnsupportedFlashSourceKind(uint8 kind);
    error FlashSourceMissing();
    error V4AdapterMissing();
    error BadFlashCallback();
    error BadFlashRepayment(uint256 balance, uint256 requiredBalance);
    error ApproveFailed();
    error TransferFailed();
    error RouterCallFailed(bytes4 selector);
    error Reentrancy();

    uint8 public constant FLASH_SOURCE_NONE = 0;
    uint8 public constant FLASH_SOURCE_MORPHO_BLUE = 1;
    uint8 public constant FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER = 4;

    uint8 public constant SWAP_ADAPTER_NONE = 0;
    uint8 public constant SWAP_ADAPTER_UNISWAP_V3 = 3;
    uint8 public constant SWAP_ADAPTER_UNISWAP_V4 = 4;

    bytes1 public constant UNIVERSAL_ROUTER_COMMAND_V4_SWAP = 0x10;
    bytes1 public constant V4_ACTION_SWAP_EXACT_IN_SINGLE = 0x06;
    bytes1 public constant V4_ACTION_SETTLE_ALL = 0x0c;
    bytes1 public constant V4_ACTION_TAKE_ALL = 0x0f;
    uint160 private constant V4_MIN_SQRT_PRICE_LIMIT = 4295128740;
    uint160 private constant V4_MAX_SQRT_PRICE_LIMIT = 1461446703485210103287273052203988822378723970341;
    uint256 public constant MAX_ROUTE_HOPS = 8;

    uint8 private constant FLASH_EXECUTION_NONE = 0;
    uint8 private constant FLASH_EXECUTION_SINGLE_CALL = 1;
    uint8 private constant FLASH_EXECUTION_ORDERED_ROUTE = 2;
    uint8 private constant FLASH_EXECUTION_V4_ACCOUNTING = 4;
    uint8 private constant FLASH_EXECUTION_V4_ACCOUNTING_WITH_VALUES = 5;

    /// @notice flash source 适配器配置。kind=1 为 Morpho 兼容，kind=4 为 V4 PoolManager flash accounting。
    struct FlashSourceAdapter {
        bool enabled;
        uint8 kind;
        address source;
        uint16 feeBps;
        bytes32 metadataHash;
    }

    /// @notice Uniswap V4 适配器配置，包含 Universal Router、PoolManager 和 Permit2。
    struct UniswapV4Adapter {
        bool enabled;
        address universalRouter;
        address poolManager;
        address permit2;
        bytes32 metadataHash;
    }

    /// @notice Uniswap V3 适配器配置，可选启用，用于包含 V3 hop 的路由。
    struct UniswapV3Adapter {
        bool enabled;
        address swapRouter;
        address factory;
        address quoter;
        bytes32 metadataHash;
    }

    /// @notice 还款守卫配置，控制最低利润、Gas 预留、最大 flash fee 和余额强制校验。
    struct RepaymentGuard {
        uint256 minProfit;
        uint256 gasReserve;
        uint16 maxFlashFeeBps;
        bool enforceBalance;
    }

    /// @notice Uniswap V4 PoolKey 结构，用于直接构造 PoolManager::swap calldata。
    struct PoolKey {
        address currency0;
        address currency1;
        uint24 fee;
        int24 tickSpacing;
        address hooks;
    }

    /// @notice V4 exact-input-single 单跳参数。
    struct V4ExactInputSingle {
        PoolKey poolKey;
        bool zeroForOne;
        uint128 amountIn;
        uint128 amountOutMinimum;
        bytes hookData;
    }

    /// @notice V3 exact-input-single 单跳参数。
    struct V3ExactInputSingle {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    /// @notice V3/V4 混合路由单跳，adapterKind 决定使用 V3 或 V4 结构。
    struct MixedRouteHop {
        uint8 adapterKind;
        V3ExactInputSingle v3;
        V4ExactInputSingle v4;
    }

    /// @notice Morpho 兼容的单笔 flash loan 请求。
    struct FlashRequest {
        address token;
        uint256 amount;
        address swapTarget;
        bytes swapCalldata;
        uint256 minProfit;
        uint256 gasReserve;
    }

    /// @notice Morpho 兼容的顺序多跳路由请求。
    struct FlashRouteRequest {
        address token;
        uint256 amount;
        address[] swapTargets;
        bytes[] swapCalldatas;
        address finalToken;
        uint256 minProfit;
        uint256 gasReserve;
    }

    /// @notice V4 Flash Accounting 请求：执行 unlock/take/settle 路径。
    struct V4FlashAccountingRequest {
        address token;
        uint256 amount;
        address[] swapTargets;
        bytes[] swapCalldatas;
        address finalToken;
        uint256 minProfit;
        uint256 gasReserve;
    }

    /// @notice V4 Flash Accounting 请求，额外提供每跳原生 ETH value。
    struct V4FlashAccountingValueRequest {
        address token;
        uint256 amount;
        address[] swapTargets;
        bytes[] swapCalldatas;
        uint256[] swapValues;
        address finalToken;
        uint256 minProfit;
        uint256 gasReserve;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event FlashSourceAdapterSet(
        uint8 indexed kind,
        bool enabled,
        address indexed source,
        uint16 feeBps,
        bytes32 metadataHash
    );
    event UniswapV4AdapterSet(
        bool enabled,
        address indexed universalRouter,
        address indexed poolManager,
        address permit2,
        bytes32 metadataHash
    );
    event UniswapV3AdapterSet(
        bool enabled,
        address indexed swapRouter,
        address indexed factory,
        address quoter,
        bytes32 metadataHash
    );
    event RepaymentGuardSet(uint256 minProfit, uint256 gasReserve, uint16 maxFlashFeeBps, bool enforceBalance);
    event Permit2ApprovalSet(address indexed token, uint160 amount, uint48 expiration);
    event RobinhoodFlashRequested(address indexed token, uint256 amount, address indexed source);
    event RobinhoodRouteFlashRequested(address indexed token, uint256 amount, address indexed source, uint256 hopCount);
    event RobinhoodRouteHopExecuted(uint256 indexed hopIndex, address indexed target);
    event RobinhoodFlashRepaid(address indexed token, uint256 amount, uint256 requiredBalance);
    event RobinhoodProfitGuardChecked(
        address indexed token,
        uint256 balance,
        uint256 requiredRepay,
        uint256 minProfit,
        uint256 gasReserve,
        uint256 requiredBalance,
        uint256 profitAfterRepay
    );
    event RobinhoodV4FlashAccountingRequested(address indexed token, uint256 amount, address indexed poolManager, uint256 hopCount);
    event RobinhoodV4FlashAccountingBorrowed(address indexed token, uint256 amount, uint256 balanceAfterTake);
    event RobinhoodV4FlashAccountingSettled(address indexed token, uint256 requiredRepay, uint256 paid);

    /// @notice 仅 owner 可操作的管理权限。
    address public owner;
    /// @notice paused=true 时所有 flash 请求入口会被拦截。
    bool public paused;
    /// @notice 重入锁。
    bool private locked;
    /// @notice 当前活跃 flash 执行类型，用于回调阶段鉴权。
    uint8 private activeFlashExecutionKind;
    /// @notice 当前活跃回调数据的哈希，防止陌生调用方伪造回调。
    bytes32 private activeFlashHash;
    FlashSourceAdapter public flashSourceAdapter;
    UniswapV4Adapter public uniswapV4Adapter;
    UniswapV3Adapter public uniswapV3Adapter;
    RepaymentGuard public repaymentGuard;

    /// @notice 只允许 owner 调用的修饰器。
    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice paused 状态下禁止执行 flash 请求或回调。
    modifier whenNotPaused() {
        if (paused) revert Paused();
        _;
    }

    /// @notice 进入函数时锁定，防止跨外部调用重入。
    modifier nonReentrantEntry() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(address initialOwner) {
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
        paused = true;
        repaymentGuard = RepaymentGuard({minProfit: 0, gasReserve: 0, maxFlashFeeBps: 0, enforceBalance: true});
        emit OwnershipTransferred(address(0), owner);
        emit PausedSet(true);
        emit RepaymentGuardSet(0, 0, 0, true);
    }

    receive() external payable {}

    /// @notice 转移 owner；新 owner 不能为地址 0。
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidRequest();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    /// @notice 设置 paused 开关；测试网部署后默认 paused=true。
    function setPaused(bool value) external onlyOwner {
        paused = value;
        emit PausedSet(value);
    }

    /// @notice 设置 flash source 适配器；Robinhood Testnet 实际使用 kind=4。
    function setFlashSourceAdapter(
        uint8 kind,
        bool enabled,
        address source,
        uint16 feeBps,
        bytes32 metadataHash
    ) external onlyOwner {
        if (kind != FLASH_SOURCE_MORPHO_BLUE && kind != FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER) {
            revert UnsupportedFlashSourceKind(kind);
        }
        if (enabled && source == address(0)) revert InvalidRequest();
        flashSourceAdapter = FlashSourceAdapter({
            enabled: enabled,
            kind: kind,
            source: source,
            feeBps: feeBps,
            metadataHash: metadataHash
        });
        emit FlashSourceAdapterSet(kind, enabled, source, feeBps, metadataHash);
    }

    /// @notice 设置 Uniswap V4 适配器，包括 Universal Router、PoolManager、Permit2。
    function setUniswapV4Adapter(
        bool enabled,
        address universalRouter,
        address poolManager,
        address permit2,
        bytes32 metadataHash
    ) external onlyOwner {
        if (enabled && (universalRouter == address(0) || poolManager == address(0) || permit2 == address(0))) {
            revert InvalidRequest();
        }
        uniswapV4Adapter = UniswapV4Adapter({
            enabled: enabled,
            universalRouter: universalRouter,
            poolManager: poolManager,
            permit2: permit2,
            metadataHash: metadataHash
        });
        emit UniswapV4AdapterSet(enabled, universalRouter, poolManager, permit2, metadataHash);
    }

    /// @notice 设置 Uniswap V3 适配器；仅当冻结路由包含 V3 hop 时需要启用。
    function setUniswapV3Adapter(
        bool enabled,
        address swapRouter,
        address factory,
        address quoter,
        bytes32 metadataHash
    ) external onlyOwner {
        if (enabled && swapRouter == address(0)) revert InvalidRequest();
        uniswapV3Adapter = UniswapV3Adapter({
            enabled: enabled,
            swapRouter: swapRouter,
            factory: factory,
            quoter: quoter,
            metadataHash: metadataHash
        });
        emit UniswapV3AdapterSet(enabled, swapRouter, factory, quoter, metadataHash);
    }

    /// @notice 设置还款守卫参数。
    function setRepaymentGuard(
        uint256 minProfit,
        uint256 gasReserve,
        uint16 maxFlashFeeBps,
        bool enforceBalance
    ) external onlyOwner {
        repaymentGuard = RepaymentGuard({
            minProfit: minProfit,
            gasReserve: gasReserve,
            maxFlashFeeBps: maxFlashFeeBps,
            enforceBalance: enforceBalance
        });
        emit RepaymentGuardSet(minProfit, gasReserve, maxFlashFeeBps, enforceBalance);
    }

    /// @notice 只读预览一笔 flash loan 至少需要偿还的本息和守卫余额。
    function previewRequiredBalance(uint256 amount, uint16 feeBps, uint256 minProfit, uint256 gasReserve)
        external
        view
        returns (uint256 requiredRepay, uint256 requiredBalance)
    {
        RepaymentGuard memory guard = repaymentGuard;
        requiredRepay = _requiredRepay(amount, feeBps);
        requiredBalance = requiredRepay + _max(minProfit, guard.minProfit) + _max(gasReserve, guard.gasReserve);
    }

    /// @notice 为指定 token 设置 Permit2 授权，使 Universal Router 可拉取代币。
    function approveTokenWithPermit2(address token, uint160 amount, uint48 expiration)
        external
        onlyOwner
        whenNotPaused
    {
        UniswapV4Adapter memory adapter = uniswapV4Adapter;
        if (!adapter.enabled) revert V4AdapterMissing();
        _forceApprove(token, adapter.permit2, type(uint256).max);
        IPermit2Robinhood(adapter.permit2).approve(token, adapter.universalRouter, amount, expiration);
        emit Permit2ApprovalSet(token, amount, expiration);
    }

    /// @notice 构建单笔 V4 exact-input-single 的 Universal Router calldata。
    function buildV4ExactInputSingleCalldata(V4ExactInputSingle calldata swap, uint256 deadline)
        external
        view
        returns (address target, bytes memory data, bytes memory commands, bytes[] memory inputs)
    {
        UniswapV4Adapter memory adapter = uniswapV4Adapter;
        if (!adapter.enabled) revert V4AdapterMissing();
        _validateV4ExactInputSingle(swap, deadline);

        bytes memory actions = abi.encodePacked(
            V4_ACTION_SWAP_EXACT_IN_SINGLE,
            V4_ACTION_SETTLE_ALL,
            V4_ACTION_TAKE_ALL
        );
        bytes[] memory params = new bytes[](3);
        params[0] = abi.encode(swap);
        params[1] = abi.encode(_currencyIn(swap), swap.amountIn);
        params[2] = abi.encode(_currencyOut(swap), swap.amountOutMinimum);

        commands = abi.encodePacked(UNIVERSAL_ROUTER_COMMAND_V4_SWAP);
        inputs = new bytes[](1);
        inputs[0] = abi.encode(actions, params);
        target = adapter.universalRouter;
        data = abi.encodeWithSelector(IUniversalRouterRobinhood.execute.selector, commands, inputs, deadline);
    }

    /// @notice 构建多跳 V4 Universal Router 路由 calldata，并校验 hop 代币连续性。
    function buildV4ExactInputRouteCalldata(V4ExactInputSingle[] calldata swaps, uint256 deadline)
        external
        view
        returns (address[] memory targets, bytes[] memory calldatas, address initialToken, address finalToken)
    {
        UniswapV4Adapter memory adapter = uniswapV4Adapter;
        if (!adapter.enabled) revert V4AdapterMissing();
        uint256 hopCount = swaps.length;
        if (hopCount == 0 || hopCount > MAX_ROUTE_HOPS) revert InvalidRequest();

        targets = new address[](hopCount);
        calldatas = new bytes[](hopCount);
        for (uint256 i = 0; i < hopCount; i++) {
            _validateV4ExactInputSingle(swaps[i], deadline);
            address tokenIn = _currencyIn(swaps[i]);
            address tokenOut = _currencyOut(swaps[i]);
            if (i == 0) {
                initialToken = tokenIn;
            } else if (_currencyOut(swaps[i - 1]) != tokenIn) {
                revert InvalidRequest();
            }
            if (i == hopCount - 1) finalToken = tokenOut;
            (, bytes memory data,,) = _buildV4ExactInputSingle(adapter.universalRouter, swaps[i], deadline);
            targets[i] = adapter.universalRouter;
            calldatas[i] = data;
        }
    }

    /// @notice 构建直接调用 PoolManager::swap 的多跳 calldata，绕过 Universal Router 嵌套 unlock 限制。
    function buildV4PoolManagerExactInputRouteCalldata(V4ExactInputSingle[] calldata swaps, uint256 deadline)
        external
        view
        returns (address[] memory targets, bytes[] memory calldatas, address initialToken, address finalToken)
    {
        UniswapV4Adapter memory adapter = uniswapV4Adapter;
        if (!adapter.enabled || adapter.poolManager == address(0)) revert V4AdapterMissing();
        uint256 hopCount = swaps.length;
        if (hopCount == 0 || hopCount > MAX_ROUTE_HOPS) revert InvalidRequest();

        targets = new address[](hopCount);
        calldatas = new bytes[](hopCount);
        for (uint256 i = 0; i < hopCount; i++) {
            _validateV4ExactInputSingle(swaps[i], deadline);
            address tokenIn = _currencyIn(swaps[i]);
            address tokenOut = _currencyOut(swaps[i]);
            if (i == 0) {
                initialToken = tokenIn;
            } else if (_currencyOut(swaps[i - 1]) != tokenIn) {
                revert InvalidRequest();
            }
            if (i == hopCount - 1) finalToken = tokenOut;
            (, bytes memory data) = _buildV4PoolManagerExactInputSingle(adapter.poolManager, swaps[i]);
            targets[i] = adapter.poolManager;
            calldatas[i] = data;
        }
    }

    /// @notice 构建 V3/V4 混合路由 calldata，用于兼容包含 V3 hop 的路由。
    function buildV3V4MixedRouteCalldata(MixedRouteHop[] calldata hops, uint256 deadline)
        external
        view
        returns (address[] memory targets, bytes[] memory calldatas, address initialToken, address finalToken)
    {
        uint256 hopCount = hops.length;
        if (hopCount == 0 || hopCount > MAX_ROUTE_HOPS) revert InvalidRequest();
        targets = new address[](hopCount);
        calldatas = new bytes[](hopCount);

        address previousTokenOut;
        for (uint256 i = 0; i < hopCount; i++) {
            (address target, bytes memory data, address tokenIn, address tokenOut) =
                _buildMixedRouteHopCalldata(hops[i], deadline);
            if (i == 0) {
                initialToken = tokenIn;
            } else if (previousTokenOut != tokenIn) {
                revert InvalidRequest();
            }
            previousTokenOut = tokenOut;
            if (i == hopCount - 1) finalToken = tokenOut;
            targets[i] = target;
            calldatas[i] = data;
        }
    }

    /// @notice Morpho 兼容的单笔 flash loan 请求入口；Robinhood Testnet 当前默认不启用该来源。
    function requestMorphoFlashLoan(FlashRequest calldata request)
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
    {
        FlashSourceAdapter memory source = flashSourceAdapter;
        if (!source.enabled || source.kind != FLASH_SOURCE_MORPHO_BLUE || source.source == address(0)) {
            revert FlashSourceMissing();
        }
        if (request.token == address(0) || request.amount == 0) revert InvalidRequest();
        if (request.swapCalldata.length != 0 && request.swapTarget != uniswapV4Adapter.universalRouter) {
            revert InvalidRequest();
        }

        bytes memory callbackData = abi.encode(request);
        activeFlashExecutionKind = FLASH_EXECUTION_SINGLE_CALL;
        activeFlashHash = keccak256(callbackData);
        IMorphoBlueFlashSource(source.source).flashLoan(request.token, request.amount, callbackData);
        activeFlashHash = bytes32(0);
        activeFlashExecutionKind = FLASH_EXECUTION_NONE;
        emit RobinhoodFlashRequested(request.token, request.amount, source.source);
    }

    /// @notice Morpho 兼容的顺序路由 flash loan 请求入口。
    function requestMorphoFlashLoanRoute(FlashRouteRequest calldata request)
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
    {
        FlashSourceAdapter memory source = flashSourceAdapter;
        if (!source.enabled || source.kind != FLASH_SOURCE_MORPHO_BLUE || source.source == address(0)) {
            revert FlashSourceMissing();
        }
        _validateFlashRouteRequest(request);

        bytes memory callbackData = abi.encode(request);
        activeFlashExecutionKind = FLASH_EXECUTION_ORDERED_ROUTE;
        activeFlashHash = keccak256(callbackData);
        IMorphoBlueFlashSource(source.source).flashLoan(request.token, request.amount, callbackData);
        activeFlashHash = bytes32(0);
        activeFlashExecutionKind = FLASH_EXECUTION_NONE;
        emit RobinhoodRouteFlashRequested(request.token, request.amount, source.source, request.swapTargets.length);
    }

    /// @notice Uniswap V4 PoolManager flash accounting 路由请求入口。
    /// PoolManager 内 unlock 时会回调本合约 unlockCallback。
    function requestV4FlashLoan(V4FlashAccountingRequest calldata request)
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (bytes memory result)
    {
        FlashSourceAdapter memory source = flashSourceAdapter;
        UniswapV4Adapter memory adapter = uniswapV4Adapter;
        if (
            !source.enabled
                || source.kind != FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER
                || source.source == address(0)
                || source.source != adapter.poolManager
        ) {
            revert FlashSourceMissing();
        }
        if (!adapter.enabled || adapter.poolManager == address(0)) revert V4AdapterMissing();
        _validateV4FlashAccountingRequest(request);

        bytes memory callbackData = abi.encode(request);
        activeFlashExecutionKind = FLASH_EXECUTION_V4_ACCOUNTING;
        activeFlashHash = keccak256(callbackData);
        result = IV4PoolManagerRobinhood(adapter.poolManager).unlock(callbackData);
        activeFlashHash = bytes32(0);
        activeFlashExecutionKind = FLASH_EXECUTION_NONE;
        emit RobinhoodV4FlashAccountingRequested(request.token, request.amount, adapter.poolManager, request.swapTargets.length);
    }

    /// @notice V4 Flash Accounting 请求入口，额外支持每跳 swapValues 的原生 ETH 价值传递。
    function requestV4FlashLoanWithValues(V4FlashAccountingValueRequest calldata request)
        external
        payable
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (bytes memory result)
    {
        FlashSourceAdapter memory source = flashSourceAdapter;
        UniswapV4Adapter memory adapter = uniswapV4Adapter;
        if (
            !source.enabled
                || source.kind != FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER
                || source.source == address(0)
                || source.source != adapter.poolManager
        ) {
            revert FlashSourceMissing();
        }
        if (!adapter.enabled || adapter.poolManager == address(0)) revert V4AdapterMissing();
        _validateV4FlashAccountingValueRequest(request);

        bytes memory callbackData = abi.encode(request);
        activeFlashExecutionKind = FLASH_EXECUTION_V4_ACCOUNTING_WITH_VALUES;
        activeFlashHash = keccak256(callbackData);
        result = IV4PoolManagerRobinhood(adapter.poolManager).unlock(callbackData);
        activeFlashHash = bytes32(0);
        activeFlashExecutionKind = FLASH_EXECUTION_NONE;
        emit RobinhoodV4FlashAccountingRequested(request.token, request.amount, adapter.poolManager, request.swapTargets.length);
    }

    /// @notice Morpho flashLoan 回调；仅允许配置的 flash source 调用，并按执行类型处理单笔或顺序路由。
    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external override {
        if (paused) revert Paused();
        FlashSourceAdapter memory source = flashSourceAdapter;
        if (msg.sender != source.source || activeFlashHash != keccak256(data)) revert BadFlashCallback();
        if (activeFlashExecutionKind == FLASH_EXECUTION_SINGLE_CALL) {
            _handleSingleFlashCallback(assets, data, source);
        } else if (activeFlashExecutionKind == FLASH_EXECUTION_ORDERED_ROUTE) {
            _handleRouteFlashCallback(assets, data, source);
        } else {
            revert BadFlashCallback();
        }
    }

    /// @notice Uniswap V4 PoolManager unlock 回调；仅允许配置的 PoolManager 调用并校验回调数据哈希。
    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (paused) revert Paused();
        FlashSourceAdapter memory source = flashSourceAdapter;
        UniswapV4Adapter memory adapter = uniswapV4Adapter;
        bool isV4FlashAccounting = activeFlashExecutionKind == FLASH_EXECUTION_V4_ACCOUNTING
            || activeFlashExecutionKind == FLASH_EXECUTION_V4_ACCOUNTING_WITH_VALUES;
        if (
            !isV4FlashAccounting
                || activeFlashHash != keccak256(data)
                || source.kind != FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER
                || msg.sender != source.source
                || msg.sender != adapter.poolManager
        ) {
            revert BadFlashCallback();
        }

        if (activeFlashExecutionKind == FLASH_EXECUTION_V4_ACCOUNTING_WITH_VALUES) {
            V4FlashAccountingValueRequest memory valueRequest = abi.decode(data, (V4FlashAccountingValueRequest));
            _validateV4FlashAccountingValueRequestMemory(valueRequest);
            return _handleV4FlashAccountingCallback(
                valueRequest.token,
                valueRequest.amount,
                valueRequest.swapTargets,
                valueRequest.swapCalldatas,
                valueRequest.swapValues,
                valueRequest.finalToken,
                valueRequest.minProfit,
                valueRequest.gasReserve,
                source,
                adapter
            );
        }

        V4FlashAccountingRequest memory request = abi.decode(data, (V4FlashAccountingRequest));
        _validateV4FlashAccountingRequestMemory(request);
        uint256[] memory swapValues = new uint256[](request.swapTargets.length);
        return _handleV4FlashAccountingCallback(
            request.token,
            request.amount,
            request.swapTargets,
            request.swapCalldatas,
            swapValues,
            request.finalToken,
            request.minProfit,
            request.gasReserve,
            source,
            adapter
        );
    }

    /// @notice V4 Flash Accounting 核心处理：take 借款、执行路由、计算还款、settle 归还。
    function _handleV4FlashAccountingCallback(
        address tokenAddress,
        uint256 amount,
        address[] memory swapTargets,
        bytes[] memory swapCalldatas,
        uint256[] memory swapValues,
        address,
        uint256 minProfit,
        uint256 gasReserve,
        FlashSourceAdapter memory source,
        UniswapV4Adapter memory adapter
    ) private returns (bytes memory) {
        IV4PoolManagerRobinhood poolManager = IV4PoolManagerRobinhood(adapter.poolManager);
        bool hasDirectPoolManagerRoute = swapTargets.length != 0 && swapTargets[0] == adapter.poolManager;
        uint256 balanceBefore = _currencyBalance(tokenAddress);
        poolManager.take(tokenAddress, address(this), amount);
        uint256 balanceAfterTake = _currencyBalance(tokenAddress);
        if (balanceAfterTake < balanceBefore + amount) revert BadFlashCallback();
        emit RobinhoodV4FlashAccountingBorrowed(tokenAddress, amount, balanceAfterTake);

        if (hasDirectPoolManagerRoute) {
            _executeV4DirectRouteSwaps(swapTargets, swapCalldatas, swapValues, adapter.poolManager);
        }

        if (!hasDirectPoolManagerRoute) {
            for (uint256 i = 0; i < swapTargets.length; i++) {
                _executeSwap(swapTargets[i], swapCalldatas[i], swapValues[i], i + 1);
            }
        }

        uint256 requiredRepay = _requiredRepay(amount, source.feeBps);
        uint256 requiredBalance = requiredRepay + _max(minProfit, repaymentGuard.minProfit)
            + _max(gasReserve, repaymentGuard.gasReserve);
        if (source.feeBps > repaymentGuard.maxFlashFeeBps) revert InvalidRequest();
        uint256 balance = _currencyBalance(tokenAddress);
        uint256 profitAfterRepay = balance > requiredRepay ? balance - requiredRepay : 0;
        emit RobinhoodProfitGuardChecked(
            tokenAddress,
            balance,
            requiredRepay,
            _max(minProfit, repaymentGuard.minProfit),
            _max(gasReserve, repaymentGuard.gasReserve),
            requiredBalance,
            profitAfterRepay
        );
        if (repaymentGuard.enforceBalance) {
            if (balance < requiredBalance) revert BadFlashRepayment(balance, requiredBalance);
        }

        uint256 paid = 0;
        if (tokenAddress == address(0)) {
            paid = poolManager.settle{value: requiredRepay}();
        } else {
            poolManager.sync(tokenAddress);
            if (!IERC20RobinhoodMinimal(tokenAddress).transfer(adapter.poolManager, requiredRepay)) revert TransferFailed();
            paid = poolManager.settle();
        }
        if (paid < amount) revert BadFlashRepayment(paid, amount);
        emit RobinhoodV4FlashAccountingSettled(tokenAddress, requiredRepay, paid);
        emit RobinhoodFlashRepaid(tokenAddress, requiredRepay, requiredBalance);

        return abi.encode(tokenAddress, amount, requiredRepay, paid);
    }

    /// @notice 直接调用 PoolManager::swap 的逐跳执行器，并结算每跳输入 delta、take 输出 delta。
    function _executeV4DirectRouteSwaps(
        address[] memory swapTargets,
        bytes[] memory swapCalldatas,
        uint256[] memory swapValues,
        address poolManager
    ) private {
        for (uint256 i = 0; i < swapTargets.length; i++) {
            uint256 swapValue = swapTargets[i] == poolManager ? 0 : swapValues[i];
            bytes memory swapCalldata = swapTargets[i] == poolManager
                ? _v4DirectSwapCalldataWithSpendableInput(swapCalldatas[i])
                : swapCalldatas[i];
            (int256 delta0, int256 delta1) = _executeSwap(swapTargets[i], swapCalldata, swapValue, i + 1);
            if (swapTargets[i] == poolManager) {
                _settleV4SwapDelta(swapCalldata, delta0, delta1, poolManager);
            }
        }
    }

    /// @notice 当直接 PoolManager swap 请求输入超过当前余额时，改用实际 spendable 余额作为 amountSpecified。
    function _v4DirectSwapCalldataWithSpendableInput(bytes memory data) private view returns (bytes memory) {
        (PoolKey memory key, IV4PoolManagerSwapRobinhood.SwapParams memory params, bytes memory hookData) =
            _decodeV4PoolManagerSwap(data);
        if (params.amountSpecified >= 0) return data;

        uint256 requestedInput = uint256(-params.amountSpecified);
        address inputCurrency = params.zeroForOne ? key.currency0 : key.currency1;
        uint256 spendableInput = _currencyBalance(inputCurrency);
        if (spendableInput >= requestedInput) return data;
        if (spendableInput == 0) revert BadFlashRepayment(0, requestedInput);

        params.amountSpecified = -int256(spendableInput);
        return abi.encodeWithSelector(
            IV4PoolManagerSwapRobinhood.swap.selector,
            IV4PoolManagerSwapRobinhood.PoolKey({
                currency0: key.currency0,
                currency1: key.currency1,
                fee: key.fee,
                tickSpacing: key.tickSpacing,
                hooks: key.hooks
            }),
            params,
            hookData
        );
    }

    /// @notice 校验 V4 exact-input-single 参数是否合法。
    function _validateV4ExactInputSingle(V4ExactInputSingle calldata swap, uint256 deadline) private view {
        if (deadline <= block.timestamp || swap.amountIn == 0 || swap.amountOutMinimum == 0) revert InvalidRequest();
        if (swap.poolKey.currency0 == swap.poolKey.currency1) revert InvalidRequest();
        if (swap.poolKey.currency0 != address(0) && swap.poolKey.currency1 != address(0)) {
            if (uint160(swap.poolKey.currency0) >= uint160(swap.poolKey.currency1)) revert InvalidRequest();
        }
        if (swap.poolKey.fee > 1_000_000 && swap.poolKey.fee != 0x800000) revert InvalidRequest();
        if (swap.poolKey.tickSpacing <= 0) revert InvalidRequest();
    }

    /// @notice 内部构建 Universal Router V4 exact-input-single calldata。
    function _buildV4ExactInputSingle(
        address universalRouter,
        V4ExactInputSingle calldata swap,
        uint256 deadline
    ) private pure returns (address target, bytes memory data, bytes memory commands, bytes[] memory inputs) {
        bytes memory actions = abi.encodePacked(
            V4_ACTION_SWAP_EXACT_IN_SINGLE,
            V4_ACTION_SETTLE_ALL,
            V4_ACTION_TAKE_ALL
        );
        bytes[] memory params = new bytes[](3);
        params[0] = abi.encode(swap);
        params[1] = abi.encode(_currencyIn(swap), swap.amountIn);
        params[2] = abi.encode(_currencyOut(swap), swap.amountOutMinimum);

        commands = abi.encodePacked(UNIVERSAL_ROUTER_COMMAND_V4_SWAP);
        inputs = new bytes[](1);
        inputs[0] = abi.encode(actions, params);
        target = universalRouter;
        data = abi.encodeWithSelector(IUniversalRouterRobinhood.execute.selector, commands, inputs, deadline);
    }

    /// @notice 内部构建直接 PoolManager::swap 的 exact-input-single calldata。
    function _buildV4PoolManagerExactInputSingle(address poolManager, V4ExactInputSingle calldata swap)
        private
        pure
        returns (address target, bytes memory data)
    {
        IV4PoolManagerSwapRobinhood.SwapParams memory params = IV4PoolManagerSwapRobinhood.SwapParams({
            zeroForOne: swap.zeroForOne,
            amountSpecified: -int256(uint256(swap.amountIn)),
            sqrtPriceLimitX96: swap.zeroForOne ? V4_MIN_SQRT_PRICE_LIMIT : V4_MAX_SQRT_PRICE_LIMIT
        });
        target = poolManager;
        data = abi.encodeWithSelector(
            IV4PoolManagerSwapRobinhood.swap.selector,
            IV4PoolManagerSwapRobinhood.PoolKey({
                currency0: swap.poolKey.currency0,
                currency1: swap.poolKey.currency1,
                fee: swap.poolKey.fee,
                tickSpacing: swap.poolKey.tickSpacing,
                hooks: swap.poolKey.hooks
            }),
            params,
            swap.hookData
        );
    }

    /// @notice 内部构建 V3 exact-input-single calldata。
    function _buildV3ExactInputSingle(V3ExactInputSingle calldata swap, uint256 deadline)
        private
        view
        returns (address target, bytes memory data, address tokenIn, address tokenOut)
    {
        UniswapV3Adapter memory adapter = uniswapV3Adapter;
        if (!adapter.enabled || adapter.swapRouter == address(0)) revert InvalidRequest();
        if (
            deadline <= block.timestamp
                || swap.tokenIn == address(0)
                || swap.tokenOut == address(0)
                || swap.tokenIn == swap.tokenOut
                || swap.fee == 0
                || swap.amountIn == 0
                || swap.amountOutMinimum == 0
        ) {
            revert InvalidRequest();
        }
        IV3RouterRobinhood.ExactInputSingleParams memory params = IV3RouterRobinhood.ExactInputSingleParams({
            tokenIn: swap.tokenIn,
            tokenOut: swap.tokenOut,
            fee: swap.fee,
            recipient: address(this),
            deadline: deadline,
            amountIn: swap.amountIn,
            amountOutMinimum: swap.amountOutMinimum,
            sqrtPriceLimitX96: swap.sqrtPriceLimitX96
        });
        target = adapter.swapRouter;
        data = abi.encodeWithSelector(IV3RouterRobinhood.exactInputSingle.selector, params);
        tokenIn = swap.tokenIn;
        tokenOut = swap.tokenOut;
    }

    /// @notice 根据 hop.adapterKind 构建 V3 或 V4 单跳 calldata。
    function _buildMixedRouteHopCalldata(MixedRouteHop calldata hop, uint256 deadline)
        private
        view
        returns (address target, bytes memory data, address tokenIn, address tokenOut)
    {
        if (hop.adapterKind == SWAP_ADAPTER_UNISWAP_V3) {
            return _buildV3ExactInputSingle(hop.v3, deadline);
        }
        if (hop.adapterKind == SWAP_ADAPTER_UNISWAP_V4) {
            UniswapV4Adapter memory adapter = uniswapV4Adapter;
            if (!adapter.enabled) revert V4AdapterMissing();
            _validateV4ExactInputSingle(hop.v4, deadline);
            tokenIn = _currencyIn(hop.v4);
            tokenOut = _currencyOut(hop.v4);
            (target, data,,) = _buildV4ExactInputSingle(adapter.universalRouter, hop.v4, deadline);
            return (target, data, tokenIn, tokenOut);
        }
        revert InvalidRequest();
    }

    /// @notice 校验 Morpho 顺序路由请求的 token、finalToken、hop 数量和 calldata。
    function _validateFlashRouteRequest(FlashRouteRequest calldata request) private view {
        if (request.token == address(0) || request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (hopCount == 0 || hopCount > MAX_ROUTE_HOPS || hopCount != request.swapCalldatas.length) revert InvalidRequest();
        for (uint256 i = 0; i < hopCount; i++) {
            if (!_isAllowedSwapTarget(request.swapTargets[i]) || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    /// @notice 处理 Morpho 单笔回调：校验借款金额并执行单个 swap。
    function _handleSingleFlashCallback(
        uint256 assets,
        bytes calldata data,
        FlashSourceAdapter memory source
    ) private {
        FlashRequest memory request = abi.decode(data, (FlashRequest));
        if (assets != request.amount || request.token == address(0)) revert BadFlashCallback();

        if (request.swapCalldata.length != 0) {
            if (request.swapTarget != uniswapV4Adapter.universalRouter) revert InvalidRequest();
            _executeSwap(request.swapTarget, request.swapCalldata, 0, 0);
        }

        _enforceRepayment(request.token, request.amount, request.minProfit, request.gasReserve, source);
    }

    /// @notice 处理 Morpho 顺序路由回调：按序执行所有 swap 后强制还款。
    function _handleRouteFlashCallback(
        uint256 assets,
        bytes calldata data,
        FlashSourceAdapter memory source
    ) private {
        FlashRouteRequest memory request = abi.decode(data, (FlashRouteRequest));
        if (assets != request.amount) revert BadFlashCallback();
        _validateFlashRouteRequestMemory(request);
        for (uint256 i = 0; i < request.swapTargets.length; i++) {
            _executeSwap(request.swapTargets[i], request.swapCalldatas[i], 0, i + 1);
        }
        _enforceRepayment(request.token, request.amount, request.minProfit, request.gasReserve, source);
    }

    /// @notice memory 版本的顺序路由校验，用于 callback 内解码后的再次校验。
    function _validateFlashRouteRequestMemory(FlashRouteRequest memory request) private view {
        if (request.token == address(0) || request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (hopCount == 0 || hopCount > MAX_ROUTE_HOPS || hopCount != request.swapCalldatas.length) revert InvalidRequest();
        for (uint256 i = 0; i < hopCount; i++) {
            if (!_isAllowedSwapTarget(request.swapTargets[i]) || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    /// @notice 校验 V4 Flash Accounting 请求结构。
    function _validateV4FlashAccountingRequest(V4FlashAccountingRequest calldata request) private view {
        if (request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (hopCount > MAX_ROUTE_HOPS || hopCount != request.swapCalldatas.length) revert InvalidRequest();
        for (uint256 i = 0; i < hopCount; i++) {
            if (!_isAllowedSwapTarget(request.swapTargets[i]) || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    /// @notice memory 版本的 V4 Flash Accounting 请求校验。
    function _validateV4FlashAccountingRequestMemory(V4FlashAccountingRequest memory request) private view {
        if (request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (hopCount > MAX_ROUTE_HOPS || hopCount != request.swapCalldatas.length) revert InvalidRequest();
        for (uint256 i = 0; i < hopCount; i++) {
            if (!_isAllowedSwapTarget(request.swapTargets[i]) || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    /// @notice 校验带 swapValues 的 V4 请求，并确保数组长度一致。
    function _validateV4FlashAccountingValueRequest(V4FlashAccountingValueRequest calldata request) private view {
        if (request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (
            hopCount > MAX_ROUTE_HOPS
                || hopCount != request.swapCalldatas.length
                || hopCount != request.swapValues.length
        ) {
            revert InvalidRequest();
        }
        for (uint256 i = 0; i < hopCount; i++) {
            if (!_isAllowedSwapTarget(request.swapTargets[i]) || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    /// @notice memory 版本的带值 V4 请求校验。
    function _validateV4FlashAccountingValueRequestMemory(V4FlashAccountingValueRequest memory request) private view {
        if (request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (
            hopCount > MAX_ROUTE_HOPS
                || hopCount != request.swapCalldatas.length
                || hopCount != request.swapValues.length
        ) {
            revert InvalidRequest();
        }
        for (uint256 i = 0; i < hopCount; i++) {
            if (!_isAllowedSwapTarget(request.swapTargets[i]) || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    /// @notice 仅允许调用已配置的 PoolManager、Universal Router 或 V3 SwapRouter。
    function _isAllowedSwapTarget(address target) private view returns (bool) {
        if (target == address(0)) return false;
        UniswapV4Adapter memory v4 = uniswapV4Adapter;
        if (v4.enabled && target == v4.poolManager) return true;
        if (v4.enabled && target == v4.universalRouter) return true;
        UniswapV3Adapter memory v3 = uniswapV3Adapter;
        if (v3.enabled && target == v3.swapRouter) return true;
        return false;
    }

    /// @notice 对 swap target 发起调用；失败时提取 revert selector 用于链下诊断。
    function _executeSwap(address target, bytes memory data, uint256 value, uint256 hopIndex)
        private
        returns (int256 amount0, int256 amount1)
    {
        (bool ok, bytes memory returndata) = target.call{value: value}(data);
        if (!ok) revert RouterCallFailed(_revertSelector(returndata));
        emit RobinhoodRouteHopExecuted(hopIndex, target);
        if (returndata.length >= 64) {
            (amount0, amount1) = abi.decode(returndata, (int256, int256));
        } else if (returndata.length >= 32) {
            int256 packed = abi.decode(returndata, (int256));
            amount0 = int128(packed >> 128);
            amount1 = int128(packed);
        }
    }

    /// @notice 对直接 PoolManager swap 返回的 delta 做结算：输入为负则 settle，输出为正则 take。
    function _settleV4SwapDelta(
        bytes memory data,
        int256 amount0,
        int256 amount1,
        address poolManager
    ) private {
        (PoolKey memory key, IV4PoolManagerSwapRobinhood.SwapParams memory params,) = _decodeV4PoolManagerSwap(data);
        address inputCurrency = params.zeroForOne ? key.currency0 : key.currency1;
        address outputCurrency = params.zeroForOne ? key.currency1 : key.currency0;
        int256 inputDelta = params.zeroForOne ? amount0 : amount1;
        int256 outputDelta = params.zeroForOne ? amount1 : amount0;
        if (inputDelta < 0) {
            uint256 inputAmount = uint256(-inputDelta);
            if (inputCurrency == address(0)) {
                IV4PoolManagerRobinhood(poolManager).settle{value: inputAmount}();
            } else {
                IV4PoolManagerRobinhood(poolManager).sync(inputCurrency);
                if (!IERC20RobinhoodMinimal(inputCurrency).transfer(poolManager, inputAmount)) revert TransferFailed();
                IV4PoolManagerRobinhood(poolManager).settle();
            }
        }
        if (outputDelta > 0) {
            IV4PoolManagerRobinhood(poolManager).take(outputCurrency, address(this), uint256(outputDelta));
        }
    }

    /// @notice 去掉 selector 后解码 PoolManager swap calldata，还原 PoolKey、SwapParams、hookData。
    function _decodeV4PoolManagerSwap(bytes memory data)
        private
        pure
        returns (PoolKey memory key, IV4PoolManagerSwapRobinhood.SwapParams memory params, bytes memory hookData)
    {
        if (data.length <= 4) revert InvalidRequest();
        bytes memory payload = new bytes(data.length - 4);
        for (uint256 i = 4; i < data.length; i++) {
            payload[i - 4] = data[i];
        }
        (key, params, hookData) = abi.decode(payload, (PoolKey, IV4PoolManagerSwapRobinhood.SwapParams, bytes));
    }

    /// @notice 读取当前合约在指定 currency 上的余额；address(0) 表示原生 ETH。
    function _currencyBalance(address currency) private view returns (uint256) {
        if (currency == address(0)) return address(this).balance;
        return IERC20RobinhoodMinimal(currency).balanceOf(address(this));
    }

    /// @notice 通用还款守卫：计算 requiredBalance，校验余额，并为 flash source 授权还款。
    function _enforceRepayment(
        address token,
        uint256 amount,
        uint256 minProfit,
        uint256 gasReserve,
        FlashSourceAdapter memory source
    ) private {
        if (source.feeBps > repaymentGuard.maxFlashFeeBps) revert InvalidRequest();
        uint256 requiredRepay = _requiredRepay(amount, source.feeBps);
        uint256 requiredBalance = requiredRepay + _max(minProfit, repaymentGuard.minProfit)
            + _max(gasReserve, repaymentGuard.gasReserve);
        uint256 balance = IERC20RobinhoodMinimal(token).balanceOf(address(this));
        uint256 profitAfterRepay = balance > requiredRepay ? balance - requiredRepay : 0;
        emit RobinhoodProfitGuardChecked(
            token,
            balance,
            requiredRepay,
            _max(minProfit, repaymentGuard.minProfit),
            _max(gasReserve, repaymentGuard.gasReserve),
            requiredBalance,
            profitAfterRepay
        );
        if (repaymentGuard.enforceBalance) {
            if (balance < requiredBalance) revert BadFlashRepayment(balance, requiredBalance);
        }
        _forceApprove(token, source.source, requiredRepay);
        emit RobinhoodFlashRepaid(token, requiredRepay, requiredBalance);
    }

    /// @notice 根据 zeroForOne 返回输入代币。
    function _currencyIn(V4ExactInputSingle calldata swap) private pure returns (address) {
        return swap.zeroForOne ? swap.poolKey.currency0 : swap.poolKey.currency1;
    }

    /// @notice 根据 zeroForOne 返回输出代币。
    function _currencyOut(V4ExactInputSingle calldata swap) private pure returns (address) {
        return swap.zeroForOne ? swap.poolKey.currency1 : swap.poolKey.currency0;
    }

    /// @notice 按 bps 向上取整计算 flash fee。
    function _fee(uint256 amount, uint16 feeBps) private pure returns (uint256) {
        if (feeBps == 0) return 0;
        return (amount * uint256(feeBps) + 9999) / 10000;
    }

    /// @notice 计算应还本金加手续费。
    function _requiredRepay(uint256 amount, uint16 feeBps) private pure returns (uint256) {
        return amount + _fee(amount, feeBps);
    }

    /// @notice 返回两者中的较大值。
    function _max(uint256 left, uint256 right) private pure returns (uint256) {
        return left >= right ? left : right;
    }

    /// @notice 强制先 approve 0 再 approve 目标金额，兼容旧式 ERC20。
    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20RobinhoodMinimal(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20RobinhoodMinimal(token).approve(spender, amount)) revert ApproveFailed();
    }

    /// @notice 从 revert data 中提取 selector。
    function _revertSelector(bytes memory reason) private pure returns (bytes4 selector) {
        if (reason.length < 4) return bytes4(0);
        assembly {
            selector := mload(add(reason, 32))
        }
    }
}
