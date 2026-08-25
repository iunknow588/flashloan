// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20RobinhoodMinimal {
    function approve(address spender, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
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

interface IMorphoBlueFlashLoanCallback {
    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external;
}

contract RobinhoodPausedFlashLoanExecutor is IMorphoBlueFlashLoanCallback {
    error NotOwner();
    error Paused();
    error InvalidRequest();
    error UnsupportedFlashSourceKind(uint8 kind);
    error FlashSourceMissing();
    error V4AdapterMissing();
    error BadFlashCallback();
    error BadFlashRepayment(uint256 balance, uint256 requiredBalance);
    error ApproveFailed();
    error RouterCallFailed(bytes4 selector);
    error Reentrancy();

    uint8 public constant FLASH_SOURCE_NONE = 0;
    uint8 public constant FLASH_SOURCE_MORPHO_BLUE = 1;

    uint8 public constant SWAP_ADAPTER_NONE = 0;
    uint8 public constant SWAP_ADAPTER_UNISWAP_V3 = 3;
    uint8 public constant SWAP_ADAPTER_UNISWAP_V4 = 4;

    bytes1 public constant UNIVERSAL_ROUTER_COMMAND_V4_SWAP = 0x10;
    bytes1 public constant V4_ACTION_SWAP_EXACT_IN_SINGLE = 0x06;
    bytes1 public constant V4_ACTION_SETTLE_ALL = 0x0c;
    bytes1 public constant V4_ACTION_TAKE_ALL = 0x0f;
    uint256 public constant MAX_ROUTE_HOPS = 8;

    uint8 private constant FLASH_EXECUTION_NONE = 0;
    uint8 private constant FLASH_EXECUTION_SINGLE_CALL = 1;
    uint8 private constant FLASH_EXECUTION_ORDERED_ROUTE = 2;

    struct FlashSourceAdapter {
        bool enabled;
        uint8 kind;
        address source;
        uint16 feeBps;
        bytes32 metadataHash;
    }

    struct UniswapV4Adapter {
        bool enabled;
        address universalRouter;
        address poolManager;
        address permit2;
        bytes32 metadataHash;
    }

    struct UniswapV3Adapter {
        bool enabled;
        address swapRouter;
        address factory;
        address quoter;
        bytes32 metadataHash;
    }

    struct RepaymentGuard {
        uint256 minProfit;
        uint256 gasReserve;
        uint16 maxFlashFeeBps;
        bool enforceBalance;
    }

    struct PoolKey {
        address currency0;
        address currency1;
        uint24 fee;
        int24 tickSpacing;
        address hooks;
    }

    struct V4ExactInputSingle {
        PoolKey poolKey;
        bool zeroForOne;
        uint128 amountIn;
        uint128 amountOutMinimum;
        bytes hookData;
    }

    struct V3ExactInputSingle {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    struct MixedRouteHop {
        uint8 adapterKind;
        V3ExactInputSingle v3;
        V4ExactInputSingle v4;
    }

    struct FlashRequest {
        address token;
        uint256 amount;
        address swapTarget;
        bytes swapCalldata;
        uint256 minProfit;
        uint256 gasReserve;
    }

    struct FlashRouteRequest {
        address token;
        uint256 amount;
        address[] swapTargets;
        bytes[] swapCalldatas;
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

    address public owner;
    bool public paused;
    bool private locked;
    uint8 private activeFlashExecutionKind;
    bytes32 private activeFlashHash;
    FlashSourceAdapter public flashSourceAdapter;
    UniswapV4Adapter public uniswapV4Adapter;
    UniswapV3Adapter public uniswapV3Adapter;
    RepaymentGuard public repaymentGuard;

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

    constructor(address initialOwner) {
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
        paused = true;
        repaymentGuard = RepaymentGuard({minProfit: 0, gasReserve: 0, maxFlashFeeBps: 0, enforceBalance: true});
        emit OwnershipTransferred(address(0), owner);
        emit PausedSet(true);
        emit RepaymentGuardSet(0, 0, 0, true);
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

    function setFlashSourceAdapter(
        uint8 kind,
        bool enabled,
        address source,
        uint16 feeBps,
        bytes32 metadataHash
    ) external onlyOwner {
        if (kind != FLASH_SOURCE_MORPHO_BLUE) revert UnsupportedFlashSourceKind(kind);
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

    function _validateV4ExactInputSingle(V4ExactInputSingle calldata swap, uint256 deadline) private view {
        if (deadline <= block.timestamp || swap.amountIn == 0 || swap.amountOutMinimum == 0) revert InvalidRequest();
        if (swap.poolKey.currency0 == swap.poolKey.currency1) revert InvalidRequest();
        if (swap.poolKey.currency0 != address(0) && swap.poolKey.currency1 != address(0)) {
            if (uint160(swap.poolKey.currency0) >= uint160(swap.poolKey.currency1)) revert InvalidRequest();
        }
        if (swap.poolKey.fee > 1_000_000 && swap.poolKey.fee != 0x800000) revert InvalidRequest();
        if (swap.poolKey.tickSpacing <= 0) revert InvalidRequest();
    }

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

    function _validateFlashRouteRequest(FlashRouteRequest calldata request) private view {
        if (request.token == address(0) || request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (hopCount == 0 || hopCount > MAX_ROUTE_HOPS || hopCount != request.swapCalldatas.length) revert InvalidRequest();
        address expectedTarget = uniswapV4Adapter.universalRouter;
        if (expectedTarget == address(0)) revert V4AdapterMissing();
        for (uint256 i = 0; i < hopCount; i++) {
            if (request.swapTargets[i] != expectedTarget || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    function _handleSingleFlashCallback(
        uint256 assets,
        bytes calldata data,
        FlashSourceAdapter memory source
    ) private {
        FlashRequest memory request = abi.decode(data, (FlashRequest));
        if (assets != request.amount || request.token == address(0)) revert BadFlashCallback();

        if (request.swapCalldata.length != 0) {
            if (request.swapTarget != uniswapV4Adapter.universalRouter) revert InvalidRequest();
            _executeSwap(request.swapTarget, request.swapCalldata, 0);
        }

        _enforceRepayment(request.token, request.amount, request.minProfit, request.gasReserve, source);
    }

    function _handleRouteFlashCallback(
        uint256 assets,
        bytes calldata data,
        FlashSourceAdapter memory source
    ) private {
        FlashRouteRequest memory request = abi.decode(data, (FlashRouteRequest));
        if (assets != request.amount) revert BadFlashCallback();
        _validateFlashRouteRequestMemory(request);
        for (uint256 i = 0; i < request.swapTargets.length; i++) {
            _executeSwap(request.swapTargets[i], request.swapCalldatas[i], i + 1);
        }
        _enforceRepayment(request.token, request.amount, request.minProfit, request.gasReserve, source);
    }

    function _validateFlashRouteRequestMemory(FlashRouteRequest memory request) private view {
        if (request.token == address(0) || request.amount == 0 || request.finalToken != request.token) revert InvalidRequest();
        uint256 hopCount = request.swapTargets.length;
        if (hopCount == 0 || hopCount > MAX_ROUTE_HOPS || hopCount != request.swapCalldatas.length) revert InvalidRequest();
        address expectedTarget = uniswapV4Adapter.universalRouter;
        if (expectedTarget == address(0)) revert V4AdapterMissing();
        for (uint256 i = 0; i < hopCount; i++) {
            if (request.swapTargets[i] != expectedTarget || request.swapCalldatas[i].length == 0) revert InvalidRequest();
        }
    }

    function _executeSwap(address target, bytes memory data, uint256 hopIndex) private {
        (bool ok, bytes memory reason) = target.call(data);
        if (!ok) revert RouterCallFailed(_revertSelector(reason));
        emit RobinhoodRouteHopExecuted(hopIndex, target);
    }

    function _enforceRepayment(
        address token,
        uint256 amount,
        uint256 minProfit,
        uint256 gasReserve,
        FlashSourceAdapter memory source
    ) private {
        if (source.feeBps > repaymentGuard.maxFlashFeeBps) revert InvalidRequest();
        uint256 requiredRepay = amount + _fee(amount, source.feeBps);
        uint256 requiredBalance = requiredRepay + _max(minProfit, repaymentGuard.minProfit)
            + _max(gasReserve, repaymentGuard.gasReserve);
        if (repaymentGuard.enforceBalance) {
            uint256 balance = IERC20RobinhoodMinimal(token).balanceOf(address(this));
            if (balance < requiredBalance) revert BadFlashRepayment(balance, requiredBalance);
        }
        _forceApprove(token, source.source, requiredRepay);
        emit RobinhoodFlashRepaid(token, requiredRepay, requiredBalance);
    }

    function _currencyIn(V4ExactInputSingle calldata swap) private pure returns (address) {
        return swap.zeroForOne ? swap.poolKey.currency0 : swap.poolKey.currency1;
    }

    function _currencyOut(V4ExactInputSingle calldata swap) private pure returns (address) {
        return swap.zeroForOne ? swap.poolKey.currency1 : swap.poolKey.currency0;
    }

    function _fee(uint256 amount, uint16 feeBps) private pure returns (uint256) {
        if (feeBps == 0) return 0;
        return (amount * uint256(feeBps) + 9999) / 10000;
    }

    function _max(uint256 left, uint256 right) private pure returns (uint256) {
        return left >= right ? left : right;
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20RobinhoodMinimal(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20RobinhoodMinimal(token).approve(spender, amount)) revert ApproveFailed();
    }

    function _revertSelector(bytes memory reason) private pure returns (bytes4 selector) {
        if (reason.length < 4) return bytes4(0);
        assembly {
            selector := mload(add(reason, 32))
        }
    }
}
