// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 官方风格 V4 失衡池制造合约，用于本地生成套利机会。

interface IERC20ImbalanceMaker {
    function transfer(address to, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IOfficialV4PoolManagerImbalanceMaker {
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

    function unlock(bytes calldata data) external returns (bytes memory);
    function take(address currency, address to, uint256 amount) external;
    function sync(address currency) external;
    function settle() external payable returns (uint256 paid);
    function swap(
        PoolKey calldata key,
        SwapParams calldata params,
        bytes calldata hookData
    ) external returns (int256 packedDelta);
}

contract OfficialV4ImbalanceMaker {
    error BadPoolManagerCallback();
    error InvalidRequest();
    error SwapNotResolved();
    error TransferFailed();
    error InsufficientBalance();
    error Reentrancy();
    error NotOperator();

    uint160 private constant MIN_SQRT_PRICE = 4295128740;
    uint160 private constant MAX_SQRT_PRICE = 1461446703485210103287273052203988822378723970341;

    IOfficialV4PoolManagerImbalanceMaker public immutable poolManager;
    address public operator;
    bool private locked;

    modifier nonReentrant() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    modifier onlyOperator() {
        if (msg.sender != operator) revert NotOperator();
        _;
    }

    constructor(address poolManager_) {
        if (poolManager_ == address(0)) revert InvalidRequest();
        poolManager = IOfficialV4PoolManagerImbalanceMaker(poolManager_);
        operator = msg.sender;
    }

    receive() external payable {}

    function setOperator(address newOperator) external onlyOperator {
        if (newOperator == address(0)) revert InvalidRequest();
        operator = newOperator;
    }

    function withdraw(address token, uint256 amount) external onlyOperator {
        if (!IERC20ImbalanceMaker(token).transfer(operator, amount)) revert TransferFailed();
    }

    function runImbalance(
        address tokenZ,
        IOfficialV4PoolManagerImbalanceMaker.PoolKey[3] calldata keys,
        bool[3] calldata zeroForOne,
        uint256 amountIn
    )
        external
        nonReentrant
        returns (uint256 outB, uint256 outC, uint256 outZ, uint256 spent)
    {
        if (tokenZ == address(0) || amountIn == 0) revert InvalidRequest();
        if (IERC20ImbalanceMaker(tokenZ).balanceOf(address(this)) < amountIn) revert InsufficientBalance();
        bytes memory result = poolManager.unlock(abi.encode(tokenZ, amountIn, keys, zeroForOne));
        (outB, outC, outZ, spent) = abi.decode(result, (uint256, uint256, uint256, uint256));
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert BadPoolManagerCallback();
        (
            address tokenZ,
            uint256 amountIn,
            IOfficialV4PoolManagerImbalanceMaker.PoolKey[3] memory keys,
            bool[3] memory zeroForOne
        ) = abi.decode(data, (address, uint256, IOfficialV4PoolManagerImbalanceMaker.PoolKey[3], bool[3]));

        uint256 outB = _swapAndResolve(keys[0], zeroForOne[0], amountIn);
        uint256 outC = _swapAndResolve(keys[1], zeroForOne[1], outB);
        uint256 outZ = _swapAndResolve(keys[2], zeroForOne[2], outC);
        uint256 spent = amountIn > outZ ? amountIn - outZ : 0;
        return abi.encode(outB, outC, outZ, spent);
    }

    function _swapAndResolve(
        IOfficialV4PoolManagerImbalanceMaker.PoolKey memory key,
        bool zeroForOne,
        uint256 amountIn
    ) private returns (uint256 amountOut) {
        int256 packedDelta = poolManager.swap(
            key,
            IOfficialV4PoolManagerImbalanceMaker.SwapParams({
                zeroForOne: zeroForOne,
                amountSpecified: -int256(amountIn),
                sqrtPriceLimitX96: zeroForOne ? MIN_SQRT_PRICE : MAX_SQRT_PRICE
            }),
            ""
        );
        int256 amount0 = packedDelta >> 128;
        int256 amount1 = int256(int128(packedDelta));

        address inputCurrency = zeroForOne ? key.currency0 : key.currency1;
        address outputCurrency = zeroForOne ? key.currency1 : key.currency0;
        int256 inputDelta = zeroForOne ? amount0 : amount1;
        int256 outputDelta = zeroForOne ? amount1 : amount0;

        if (inputDelta < 0) {
            uint256 inputAmount = uint256(-inputDelta);
            if (inputAmount != amountIn) revert SwapNotResolved();
            if (IERC20ImbalanceMaker(inputCurrency).balanceOf(address(this)) < inputAmount) revert InsufficientBalance();
            poolManager.sync(inputCurrency);
            if (!IERC20ImbalanceMaker(inputCurrency).transfer(address(poolManager), inputAmount)) revert TransferFailed();
            poolManager.settle();
        } else {
            revert SwapNotResolved();
        }

        if (outputDelta > 0) {
            amountOut = uint256(outputDelta);
            poolManager.take(outputCurrency, address(this), amountOut);
        } else {
            revert SwapNotResolved();
        }
    }
}
