// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ContractDexErrorCodes} from "../ContractDexErrorCodes.sol";

interface IERC20OfficialV4Arb {
    function transfer(address to, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IOfficialV4PoolManagerArb {
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

contract OfficialV4FlashArb {
    error BadPoolManagerCallback();
    error InvalidRequest();
    error SwapNotResolved();
    error TransferFailed();
    error ArbitrageNotProfitable(uint256 received, uint256 required);
    error Reentrancy();
    error NoProfitableRoute();
    error DexError(uint256 code);

    uint160 private constant MIN_SQRT_PRICE = 4295128740;
    uint160 private constant MAX_SQRT_PRICE = 1461446703485210103287273052203988822378723970341;

    IOfficialV4PoolManagerArb public immutable poolManager;
    bool private locked;

    modifier nonReentrant() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(address poolManager_) {
        if (poolManager_ == address(0)) revert InvalidRequest();
        poolManager = IOfficialV4PoolManagerArb(poolManager_);
    }

    receive() external payable {}

    function runArbitrage(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey[3] calldata keys,
        bool[3] calldata zeroForOne,
        uint256 amountIn,
        uint256 minProfit
    )
        external
        nonReentrant
        returns (uint256 outTb, uint256 outTa, uint256 outTz, uint256 profit)
    {
        if (tokenZ == address(0) || amountIn == 0) revert InvalidRequest();
        bytes memory result = poolManager.unlock(abi.encode(tokenZ, amountIn, keys, zeroForOne, minProfit));
        (outTb, outTa, outTz, profit) = abi.decode(result, (uint256, uint256, uint256, uint256));
    }

    /// @notice Tries a batch of closed-loop route directions in one call.
    /// @dev Off-chain discovery only enumerates closed-loop opportunities; this
    /// function lets the chain compute which direction is executable. Each route
    /// is attempted as an isolated external call so a revert on one direction
    /// does not undo the next attempt.
    function runArbitrageBest(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey[3][] calldata keysets,
        bool[3][] calldata directionsets,
        uint256 amountIn,
        uint256 minProfit
    )
        external
        returns (uint256 outTb, uint256 outTa, uint256 outTz, uint256 profit, uint256 routeIndex)
    {
        if (tokenZ == address(0) || amountIn == 0) revert InvalidRequest();
        if (keysets.length == 0 || keysets.length != directionsets.length) {
            revert DexError(ContractDexErrorCodes.VALIDATION_ROUTE_INVALID);
        }
        for (uint256 i = 0; i < keysets.length; i++) {
            try this.runArbitrage(tokenZ, keysets[i], directionsets[i], amountIn, minProfit)
                returns (uint256 b, uint256 a, uint256 z, uint256 p)
            {
                return (b, a, z, p, i);
            } catch {}
        }
        revert NoProfitableRoute();
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert BadPoolManagerCallback();
        (
            address tokenZ,
            uint256 amountIn,
            IOfficialV4PoolManagerArb.PoolKey[3] memory keys,
            bool[3] memory zeroForOne,
            uint256 minProfit
        ) = abi.decode(data, (address, uint256, IOfficialV4PoolManagerArb.PoolKey[3], bool[3], uint256));

        poolManager.take(tokenZ, address(this), amountIn);
        uint256 outTb = _swapAndResolve(keys[0], zeroForOne[0], amountIn);
        uint256 outTa = _swapAndResolve(keys[1], zeroForOne[1], outTb);
        uint256 outTz = _swapAndResolve(keys[2], zeroForOne[2], outTa);

        uint256 required = amountIn + minProfit;
        if (outTz < required) revert ArbitrageNotProfitable(outTz, required);
        if (tokenZ == address(0)) {
            poolManager.settle{value: amountIn}();
        } else {
            poolManager.sync(tokenZ);
            if (!IERC20OfficialV4Arb(tokenZ).transfer(address(poolManager), amountIn)) revert TransferFailed();
            poolManager.settle();
        }
        return abi.encode(outTb, outTa, outTz, outTz - amountIn);
    }

    function _swapAndResolve(
        IOfficialV4PoolManagerArb.PoolKey memory key,
        bool zeroForOne,
        uint256 amountIn
    ) private returns (uint256 amountOut) {
        int256 packedDelta = poolManager.swap(
            key,
            IOfficialV4PoolManagerArb.SwapParams({
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
            if (inputCurrency == address(0)) {
                poolManager.settle{value: inputAmount}();
            } else {
                poolManager.sync(inputCurrency);
                if (!IERC20OfficialV4Arb(inputCurrency).transfer(address(poolManager), inputAmount)) revert TransferFailed();
                poolManager.settle();
            }
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
