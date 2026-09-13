// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20TzInjectionHook {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract TzTieredInjectionHook {
    error ZeroAddress();
    error TransferFailed();
    error NotOperator();
    error NotPoolManager();

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

    struct ModifyLiquidityParams {
        int24 tickLower;
        int24 tickUpper;
        int256 liquidityDelta;
        bytes32 salt;
    }

    struct HookPermissions {
        bool beforeInitialize;
        bool afterInitialize;
        bool beforeAddLiquidity;
        bool afterAddLiquidity;
        bool beforeRemoveLiquidity;
        bool afterRemoveLiquidity;
        bool beforeSwap;
        bool afterSwap;
        bool beforeDonate;
        bool afterDonate;
        bool beforeSwapReturnDelta;
        bool afterSwapReturnDelta;
        bool afterAddLiquidityReturnDelta;
        bool afterRemoveLiquidityReturnDelta;
    }

    event Injected(
        address indexed tzToken,
        address indexed target,
        uint256 swappedAmount,
        uint256 injectedAmount
    );

    uint256 public constant DECIMALS = 18;
    uint256 public constant HIGH_THRESHOLD = 100 * 10 ** DECIMALS;
    uint256 public constant LARGE_INJECTION = 100 * 10 ** DECIMALS;
    uint256 public constant SMALL_INJECTION = 50 * 10 ** DECIMALS;

    address public immutable tzToken;
    address public immutable poolManager;
    address public operator;
    address public injectTarget;

    uint256 public lastSwappedAmount;
    uint256 public pendingReplenishAmount;
    uint256 public totalObservedSwappedAmount;

    receive() external payable {}

    constructor(address tzToken_, address poolManager_, address operator_) {
        if (tzToken_ == address(0) || poolManager_ == address(0) || operator_ == address(0)) revert ZeroAddress();
        tzToken = tzToken_;
        poolManager = poolManager_;
        operator = operator_;
    }

    modifier onlyOperator() {
        if (msg.sender != operator) revert NotOperator();
        _;
    }

    function setInjectTarget(address target) external onlyOperator {
        if (target == address(0)) revert ZeroAddress();
        injectTarget = target;
    }

    function fundTz(uint256 amount) external {
        if (!IERC20TzInjectionHook(tzToken).transferFrom(msg.sender, address(this), amount)) revert TransferFailed();
    }

    function markReplenished() external onlyOperator {
        pendingReplenishAmount = 0;
    }

    function getHookPermissions() external pure returns (HookPermissions memory permissions) {
        permissions.afterSwap = true;
    }

    function afterInitialize(
        address, /* sender */
        PoolKey calldata, /* key */
        uint160, /* sqrtPriceX96 */
        int24 /* tick */
    ) external pure returns (bytes4) {
        return TzTieredInjectionHook.afterInitialize.selector;
    }

    function beforeInitialize(
        address, /* sender */
        PoolKey calldata, /* key */
        uint160 /* sqrtPriceX96 */
    ) external pure returns (bytes4) {
        return TzTieredInjectionHook.beforeInitialize.selector;
    }

    function beforeAddLiquidity(
        address, /* sender */
        PoolKey calldata, /* key */
        ModifyLiquidityParams calldata, /* params */
        bytes calldata /* hookData */
    ) external pure returns (bytes4) {
        return TzTieredInjectionHook.beforeAddLiquidity.selector;
    }

    function afterAddLiquidity(
        address, /* sender */
        PoolKey calldata, /* key */
        ModifyLiquidityParams calldata, /* params */
        int256, /* delta */
        int256, /* feesAccrued */
        bytes calldata /* hookData */
    ) external pure returns (bytes4, int256) {
        return (TzTieredInjectionHook.afterAddLiquidity.selector, 0);
    }

    function beforeRemoveLiquidity(
        address, /* sender */
        PoolKey calldata, /* key */
        ModifyLiquidityParams calldata, /* params */
        bytes calldata /* hookData */
    ) external pure returns (bytes4) {
        return TzTieredInjectionHook.beforeRemoveLiquidity.selector;
    }

    function afterRemoveLiquidity(
        address, /* sender */
        PoolKey calldata, /* key */
        ModifyLiquidityParams calldata, /* params */
        int256, /* delta */
        int256, /* feesAccrued */
        bytes calldata /* hookData */
    ) external pure returns (bytes4, int256) {
        return (TzTieredInjectionHook.afterRemoveLiquidity.selector, 0);
    }

    function beforeSwap(
        address, /* sender */
        PoolKey calldata, /* key */
        SwapParams calldata, /* params */
        bytes calldata /* hookData */
    ) external pure returns (bytes4, int256, uint24) {
        return (TzTieredInjectionHook.beforeSwap.selector, 0, 0);
    }

    function afterDonate(
        address, /* sender */
        PoolKey calldata, /* key */
        uint256, /* amount0 */
        uint256, /* amount1 */
        bytes calldata /* hookData */
    ) external pure returns (bytes4) {
        return TzTieredInjectionHook.afterDonate.selector;
    }

    function beforeDonate(
        address, /* sender */
        PoolKey calldata, /* key */
        uint256, /* amount0 */
        uint256, /* amount1 */
        bytes calldata /* hookData */
    ) external pure returns (bytes4) {
        return TzTieredInjectionHook.beforeDonate.selector;
    }

    function withdrawTz(uint256 amount) external onlyOperator {
        if (!IERC20TzInjectionHook(tzToken).transfer(operator, amount)) revert TransferFailed();
    }

    function afterSwap(
        address /* sender */,
        PoolKey calldata key,
        SwapParams calldata /* params */,
        int256 delta,
        bytes calldata /* hookData */
    ) external returns (bytes4, int128) {
        if (msg.sender != poolManager) revert NotPoolManager();
        uint256 swappedAmount = _tzSwappedAmount(key, delta);
        if (swappedAmount > 0) {
            lastSwappedAmount = swappedAmount;
            pendingReplenishAmount += swappedAmount;
            totalObservedSwappedAmount += swappedAmount;
        }
        return (TzTieredInjectionHook.afterSwap.selector, 0);
    }

    function _tzSwappedAmount(PoolKey calldata key, int256 delta) private view returns (uint256) {
        if (key.currency0 != tzToken && key.currency1 != tzToken) return 0;
        int256 amount0 = delta >> 128;
        uint256 lower = uint256(delta) & ((1 << 128) - 1);
        int256 amount1 = lower >= (1 << 127)
            ? -int256((1 << 128) - lower)
            : int256(lower);
        if (key.currency0 == tzToken) return _abs(amount0);
        return _abs(amount1);
    }

    function _abs(int256 value) private pure returns (uint256) {
        return value >= 0 ? uint256(value) : uint256(-value);
    }
}
