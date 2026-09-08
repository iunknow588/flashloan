// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./RobinhoodPausedFlashLoanExecutorInterfaces.sol";

abstract contract RobinhoodPausedFlashLoanExecutorTypes {
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
    uint160 internal constant V4_MIN_SQRT_PRICE_LIMIT = 4295128740;
    uint160 internal constant V4_MAX_SQRT_PRICE_LIMIT = 1461446703485210103287273052203988822378723970341;
    uint256 public constant MAX_ROUTE_HOPS = 8;

    uint8 internal constant FLASH_EXECUTION_NONE = 0;
    uint8 internal constant FLASH_EXECUTION_SINGLE_CALL = 1;
    uint8 internal constant FLASH_EXECUTION_ORDERED_ROUTE = 2;
    uint8 internal constant FLASH_EXECUTION_V4_ACCOUNTING = 4;
    uint8 internal constant FLASH_EXECUTION_V4_ACCOUNTING_WITH_VALUES = 5;

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

    struct V4FlashAccountingRequest {
        address token;
        uint256 amount;
        address[] swapTargets;
        bytes[] swapCalldatas;
        address finalToken;
        uint256 minProfit;
        uint256 gasReserve;
    }

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


}
