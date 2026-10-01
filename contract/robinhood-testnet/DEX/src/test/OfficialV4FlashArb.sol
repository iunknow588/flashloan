// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 官方风格 V4 flash 套利路径测试合约，用于本地模拟真实 PoolManager 套利。

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
        if (amountIn == 0) revert InvalidRequest();
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
        if (amountIn == 0) revert InvalidRequest();
        if (keysets.length == 0 || keysets.length != directionsets.length) {
            revert DexError(ContractDexErrorCodes.VALIDATION_ROUTE_INVALID);
        }
        bool found;
        uint256 bestTb;
        uint256 bestTa;
        uint256 bestTz;
        uint256 bestProfit;
        uint256 bestIndex;
        for (uint256 i = 0; i < keysets.length; i++) {
            try this.runArbitrage(tokenZ, keysets[i], directionsets[i], amountIn, minProfit)
                returns (uint256 b, uint256 a, uint256 z, uint256 p)
            {
                if (!found || p > bestProfit) {
                    found = true;
                    bestTb = b;
                    bestTa = a;
                    bestTz = z;
                    bestProfit = p;
                    bestIndex = i;
                }
            } catch {}
        }
        if (found) {
            return (bestTb, bestTa, bestTz, bestProfit, bestIndex);
        }
        revert NoProfitableRoute();
    }

    /// @notice Executes the best profitable X -> Y -> Z -> X route from a list
    /// of Y-Z bridge pool pairs, keeping one entry and one exit pool fixed.
    /// @dev This is the bridge-pool array variant the off-chain discovery can
    /// use when it has resolved fixed anchor entry/exit pools but wants the
    /// contract to scan every Y-Z pair. Each bridge pool is attempted as an
    /// isolated call so a revert on one does not undo the next attempt.
    function runArbitrageBestBridgePairs(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey calldata entryKey,
        IOfficialV4PoolManagerArb.PoolKey calldata exitKey,
        bool entryZeroForOne,
        bool exitZeroForOne,
        IOfficialV4PoolManagerArb.PoolKey[] calldata bridgeKeys,
        bool[] calldata bridgeZeroForOne,
        uint256 amountIn,
        uint256 minProfit
    )
        external
        returns (uint256 outY, uint256 outZ, uint256 outTokenZ, uint256 profit, uint256 routeIndex)
    {
        if (amountIn == 0) revert InvalidRequest();
        if (bridgeKeys.length == 0 || bridgeKeys.length != bridgeZeroForOne.length) {
            revert DexError(ContractDexErrorCodes.VALIDATION_ROUTE_INVALID);
        }
        bool found;
        uint256 bestY;
        uint256 bestZ;
        uint256 bestTokenZ;
        uint256 bestProfit;
        uint256 bestIndex;
        for (uint256 i = 0; i < bridgeKeys.length; i++) {
            if (!_bridgePairRouteIsAnchored(
                tokenZ,
                entryKey,
                exitKey,
                entryZeroForOne,
                exitZeroForOne,
                bridgeKeys[i],
                bridgeZeroForOne[i]
            )) {
                continue;
            }
            IOfficialV4PoolManagerArb.PoolKey[3] memory keys = [entryKey, bridgeKeys[i], exitKey];
            bool[3] memory zeroForOne = [entryZeroForOne, bridgeZeroForOne[i], exitZeroForOne];
            try this.runArbitrage(tokenZ, keys, zeroForOne, amountIn, minProfit)
                returns (uint256 b, uint256 a, uint256 z, uint256 p)
            {
                if (!found || p > bestProfit) {
                    found = true;
                    bestY = b;
                    bestZ = a;
                    bestTokenZ = z;
                    bestProfit = p;
                    bestIndex = i;
                }
            } catch {}
        }
        if (!found) revert NoProfitableRoute();
        return (bestY, bestZ, bestTokenZ, bestProfit, bestIndex);
    }

    /// @notice Reads-only adaptive peak search from `initialAmount`.
    /// @dev The initial amount is only the search origin. A profitable origin
    /// is doubled until the first non-improving or failed probe is found. An
    /// unprofitable origin is halved until a profitable base is found. The
    /// interval around that base is then searched by probing the midpoint on
    /// both the lower and upper side. The better midpoint selects which side
    /// to refine; this keeps eth_call bounded while preserving the
    /// absolute-profit objective.
    function previewArbitrageBestAmount(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey[3][] calldata keysets,
        bool[3][] calldata directionsets,
        uint256 initialAmount,
        uint256 minProfit,
        bool isBestAmount
    )
        external
        returns (
            uint256 selectedAmount,
            uint256 selectedRouteIndex,
            uint256 selectedProfit,
            uint256 attemptedCount
        )
    {
        if (initialAmount == 0) revert InvalidRequest();
        if (keysets.length == 0 || keysets.length != directionsets.length) {
            revert DexError(ContractDexErrorCodes.VALIDATION_ROUTE_INVALID);
        }
        uint256 baseAmount;
        uint256 lowerAmount;
        uint256 upperAmount;
        bool hasUpperAmount;

        if (isBestAmount) {
            (bool executable, uint256 profit, uint256 routeIndex, uint256 attempts) =
                _probeAmount(tokenZ, keysets, directionsets, initialAmount, minProfit);
            attemptedCount += attempts;
            if (executable) {
                selectedAmount = initialAmount;
                selectedRouteIndex = routeIndex;
                selectedProfit = profit;
            }
            if (selectedAmount == 0) revert NoProfitableRoute();
            return (selectedAmount, selectedRouteIndex, selectedProfit, attemptedCount);
        }

        (bool initialExecutable, uint256 initialProfit, uint256 initialRouteIndex, uint256 initialAttempts) =
            _probeAmount(tokenZ, keysets, directionsets, initialAmount, minProfit);
        attemptedCount += initialAttempts;

        if (initialExecutable) {
            selectedAmount = initialAmount;
            selectedRouteIndex = initialRouteIndex;
            selectedProfit = initialProfit;
            baseAmount = initialAmount;
            lowerAmount = initialAmount / 2;

            uint256 candidate = _doubleAmount(baseAmount);
            for (uint256 step = 0; step < MAX_AMOUNT_SEARCH_STEPS && candidate > 0; step++) {
                (bool executable, uint256 profit, uint256 routeIndex, uint256 attempts) =
                    _probeAmount(tokenZ, keysets, directionsets, candidate, minProfit);
                attemptedCount += attempts;
                if (executable && profit > selectedProfit) {
                    selectedAmount = candidate;
                    selectedProfit = profit;
                    selectedRouteIndex = routeIndex;
                    baseAmount = candidate;
                    lowerAmount = candidate / 2;
                }
                else {
                    upperAmount = candidate;
                    hasUpperAmount = true;
                    break;
                }
                candidate = _doubleAmount(candidate);
            }
            if (!hasUpperAmount && selectedAmount > 0) {
                upperAmount = _doubleAmount(selectedAmount);
                baseAmount = selectedAmount;
                lowerAmount = selectedAmount / 2;
                hasUpperAmount = upperAmount > 0;
            }
        } else {
            upperAmount = initialAmount;
            uint256 candidate = initialAmount / 2;
            for (uint256 step = 0; step < MAX_AMOUNT_SEARCH_STEPS && candidate > 0; step++) {
                (bool executable, uint256 profit, uint256 routeIndex, uint256 attempts) =
                    _probeAmount(tokenZ, keysets, directionsets, candidate, minProfit);
                attemptedCount += attempts;
                if (executable) {
                    selectedAmount = candidate;
                    selectedRouteIndex = routeIndex;
                    selectedProfit = profit;
                    baseAmount = candidate;
                    lowerAmount = candidate / 2;
                    hasUpperAmount = true;
                    break;
                }
                upperAmount = candidate;
                candidate /= 2;
            }
        }

        if (hasUpperAmount && selectedAmount > 0) {
            for (uint256 step = 0; step < MAX_AMOUNT_SEARCH_STEPS; step++) {
                if (upperAmount <= baseAmount + 1 || lowerAmount >= baseAmount) break;
                if (upperAmount <= lowerAmount) break;

                // Probe both sides around the current best amount. The first
                // direction that improves the selected profit also chooses the
                // search interval: toward the lower neighbor or toward the
                // upper non-improving probe.
                uint256 leftMid = lowerAmount + (baseAmount - lowerAmount) / 2;
                uint256 rightMid = baseAmount + (upperAmount - baseAmount) / 2;
                if (leftMid <= lowerAmount || leftMid >= baseAmount) break;
                if (rightMid <= baseAmount || rightMid >= upperAmount) break;

                (bool leftExecutable, uint256 leftProfit, uint256 leftRouteIndex, uint256 leftAttempts) =
                    _probeAmount(tokenZ, keysets, directionsets, leftMid, minProfit);
                attemptedCount += leftAttempts;
                (bool rightExecutable, uint256 rightProfit, uint256 rightRouteIndex, uint256 rightAttempts) =
                    _probeAmount(tokenZ, keysets, directionsets, rightMid, minProfit);
                attemptedCount += rightAttempts;
                if (!leftExecutable && !rightExecutable) break;

                bool leftImproves = leftExecutable && leftProfit > selectedProfit;
                bool rightImproves = rightExecutable && rightProfit > selectedProfit;
                if (!leftImproves && !rightImproves) break;

                if (leftExecutable && rightExecutable) {
                    if (leftProfit >= rightProfit) {
                        if (leftImproves) {
                            selectedAmount = leftMid;
                            selectedProfit = leftProfit;
                            selectedRouteIndex = leftRouteIndex;
                        }
                    } else {
                        if (rightImproves) {
                            selectedAmount = rightMid;
                            selectedProfit = rightProfit;
                            selectedRouteIndex = rightRouteIndex;
                        }
                    }
                } else if (leftExecutable) {
                    if (leftImproves) {
                        selectedAmount = leftMid;
                        selectedProfit = leftProfit;
                        selectedRouteIndex = leftRouteIndex;
                    }
                } else if (rightExecutable) {
                    if (rightImproves) {
                        selectedAmount = rightMid;
                        selectedProfit = rightProfit;
                        selectedRouteIndex = rightRouteIndex;
                    }
                }

                uint256 oldBase = baseAmount;
                if (leftExecutable && rightExecutable) {
                    if (leftProfit >= rightProfit) {
                        upperAmount = oldBase;
                        baseAmount = leftMid;
                    } else {
                        lowerAmount = oldBase;
                        baseAmount = rightMid;
                    }
                } else if (leftExecutable) {
                    upperAmount = oldBase;
                    baseAmount = leftMid;
                } else {
                    lowerAmount = oldBase;
                    baseAmount = rightMid;
                }
            }
        }

        if (selectedAmount == 0) revert NoProfitableRoute();
        return (selectedAmount, selectedRouteIndex, selectedProfit, attemptedCount);
    }

    /// @notice Broadcast wrapper for the official V4 executor. Takes an already
    /// preselected amount (for example produced by previewArbitrageBestAmount)
    /// and reuses the original direction-selection logic once, without scanning
    /// multiple flash-loan rounds inside a real transaction.
    function runArbitrageBestWithSelectedAmount(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey[3][] calldata keysets,
        bool[3][] calldata directionsets,
        uint256 selectedAmount,
        uint256 minProfit,
        bool isBestAmount,
        uint256 selectedRouteIndex
    )
        external
        returns (uint256 outTb, uint256 outTa, uint256 outTz, uint256 profit, uint256 routeIndex)
    {
        if (selectedAmount == 0 || !isBestAmount || selectedRouteIndex >= keysets.length || selectedRouteIndex >= directionsets.length) {
            revert InvalidRequest();
        }
        (outTb, outTa, outTz, profit) = this.runArbitrage(
            tokenZ,
            keysets[selectedRouteIndex],
            directionsets[selectedRouteIndex],
            selectedAmount,
            minProfit
        );
        routeIndex = selectedRouteIndex;
    }

    /// @notice Convenience execution entry that reviews first, then broadcasts
    /// the newly selected amount. The initial amount is only a search origin;
    /// it is never treated as a confirmed best amount.
    /// @dev This entry intentionally re-runs the full best-amount search inside
    /// the broadcast transaction, so it consumes more gas than the preselected
    /// amount wrapper. Use it when the caller wants the contract to decide the
    /// amount atomically and accepts the higher gas cost.
    function runArbitrageBestFromInitialAmount(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey[3][] calldata keysets,
        bool[3][] calldata directionsets,
        uint256 initialAmount,
        uint256 minProfit
    )
        external
        returns (uint256 outTb, uint256 outTa, uint256 outTz, uint256 profit, uint256 routeIndex)
    {
        (uint256 selectedAmount, uint256 selectedRouteIndex, , ) = this.previewArbitrageBestAmount(
            tokenZ,
            keysets,
            directionsets,
            initialAmount,
            minProfit,
            false
        );
        if (selectedAmount == 0) revert NoProfitableRoute();
        (outTb, outTa, outTz, profit, routeIndex) = this.runArbitrageBestWithSelectedAmount(
            tokenZ,
            keysets,
            directionsets,
            selectedAmount,
            minProfit,
            true,
            selectedRouteIndex
        );
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

    function _routeTokenInput(
        IOfficialV4PoolManagerArb.PoolKey calldata key,
        bool zeroForOne
    ) private pure returns (address) {
        return zeroForOne ? key.currency0 : key.currency1;
    }

    function _routeTokenOutput(
        IOfficialV4PoolManagerArb.PoolKey calldata key,
        bool zeroForOne
    ) private pure returns (address) {
        return zeroForOne ? key.currency1 : key.currency0;
    }

    function _bridgePairRouteIsAnchored(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey calldata entryKey,
        IOfficialV4PoolManagerArb.PoolKey calldata exitKey,
        bool entryZeroForOne,
        bool exitZeroForOne,
        IOfficialV4PoolManagerArb.PoolKey calldata bridgeKey,
        bool bridgeZeroForOne
    ) private pure returns (bool) {
        if (_routeTokenInput(entryKey, entryZeroForOne) != tokenZ) return false;
        if (_routeTokenOutput(exitKey, exitZeroForOne) != tokenZ) return false;
        if (_routeTokenOutput(entryKey, entryZeroForOne) != _routeTokenInput(bridgeKey, bridgeZeroForOne)) return false;
        if (_routeTokenOutput(bridgeKey, bridgeZeroForOne) != _routeTokenInput(exitKey, exitZeroForOne)) return false;
        return true;
    }

    uint256 private constant MAX_AMOUNT_SEARCH_STEPS = 24;

    function _doubleAmount(uint256 amount) private pure returns (uint256) {
        if (amount > type(uint256).max / 2) return 0;
        return amount * 2;
    }

    function _probeAmountForRoute(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey[3][] calldata keysets,
        bool[3][] calldata directionsets,
        uint256 routeIndex,
        uint256 amount,
        uint256 minProfit
    )
        private
        returns (bool executable, uint256 profit, uint256 attempts)
    {
        if (routeIndex >= keysets.length || routeIndex >= directionsets.length) return (false, 0, 0);
        attempts = 1;
        try this.runArbitrage(tokenZ, keysets[routeIndex], directionsets[routeIndex], amount, minProfit)
            returns (uint256 outTb, uint256 outTa, uint256 outTz, uint256 candidateProfit)
        {
            outTb; outTa; outTz;
            executable = candidateProfit > 0;
            profit = candidateProfit;
        } catch {}
    }

    function _probeAmount(
        address tokenZ,
        IOfficialV4PoolManagerArb.PoolKey[3][] calldata keysets,
        bool[3][] calldata directionsets,
        uint256 amount,
        uint256 minProfit
    )
        private
        returns (bool executable, uint256 profit, uint256 routeIndex, uint256 attempts)
    {
        for (uint256 i = 0; i < keysets.length; i++) {
            attempts++;
            try this.runArbitrage(tokenZ, keysets[i], directionsets[i], amount, minProfit)
                returns (uint256 outTb, uint256 outTa, uint256 outTz, uint256 candidateProfit)
            {
                outTb; outTa; outTz;
                if (candidateProfit > profit) {
                    profit = candidateProfit;
                    routeIndex = i;
                }
            } catch {}
        }
        executable = profit > 0;
    }
}
