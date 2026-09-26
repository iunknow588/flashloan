// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 本地套利 composer 测试合约，用于组织多跳路由并在本地 AMM 环境验证套利路径。

import {ContractDexErrorCodes} from "../ContractDexErrorCodes.sol";

interface IPoolManagerExtsload {
    function extsload(bytes32 slot) external returns (bytes32 value);
}

contract ArbitrageComposer {
    struct PoolKey {
        address currency0;
        address currency1;
        uint24 fee;
        int24 tickSpacing;
        address hooks;
    }

    struct Candidate {
        PoolKey[3][] routePools;
        bool[3][] routeDirections;
        uint256 deviationBps;
        uint256 mQuote;
        uint256 xQuote;
        uint256 yQuote;
    }

    struct CachedPool {
        bytes32 poolId;
        uint160 sqrtPriceX96;
    }

    error InvalidRequest();
    error NoProfitableCandidate();
    error DexError(uint256 code);

    bytes4 private constant RUN_ARBITRAGE_BEST_SELECTOR = 0xde150c83;

    address public immutable poolManager;
    address public immutable executor;

    constructor(address poolManager_, address executor_) {
        if (poolManager_ == address(0) || executor_ == address(0)) revert InvalidRequest();
        poolManager = poolManager_;
        executor = executor_;
    }

    function runBest(
        address tokenZ,
        Candidate[] calldata candidates,
        uint256 amountIn,
        uint256 minProfit,
        uint256 minDeviationBps
    ) external returns (bool executed, uint256 candidateIndex, uint256 profit, bytes memory resultData) {
        if (tokenZ == address(0) || amountIn == 0) revert InvalidRequest();
        (uint256[] memory order, uint256 orderLen, bool sawInvalidAnchor) = _selectionOrder(
            candidates, tokenZ, minDeviationBps
        );
        for (uint256 k = 0; k < orderLen; k++) {
            uint256 i = order[k];
            (bool ok, uint256 p, bytes memory rd, ) = _tryCandidate(
                candidates[i], tokenZ, amountIn, minProfit
            );
            if (ok) {
                return (true, i, p, rd);
            }
        }
        if (sawInvalidAnchor) {
            revert DexError(ContractDexErrorCodes.VALIDATION_ROUTE_INVALID);
        }
        revert NoProfitableCandidate();
    }

    /// @notice Detailed variant of runBest that returns the selected route
    /// pools and directions alongside the decoded executor result.
    function runBestDetailed(
        address tokenZ,
        Candidate[] calldata candidates,
        uint256 amountIn,
        uint256 minProfit,
        uint256 minDeviationBps
    ) external returns (bool executed, uint256 candidateIndex, uint256 profit, bytes memory resultData, PoolKey[3] memory selectedKeys, bool[3] memory selectedDirections) {
        if (tokenZ == address(0) || amountIn == 0) revert InvalidRequest();
        (uint256[] memory order, uint256 orderLen, bool sawInvalidAnchor) = _selectionOrder(
            candidates, tokenZ, minDeviationBps
        );
        for (uint256 k = 0; k < orderLen; k++) {
            uint256 i = order[k];
            (bool ok, uint256 p, bytes memory rd, uint256 routeIndex) = _tryCandidate(
                candidates[i], tokenZ, amountIn, minProfit
            );
            if (!ok) {
                continue;
            }
            if (routeIndex >= candidates[i].routePools.length) {
                continue;
            }
            selectedKeys = candidates[i].routePools[routeIndex];
            selectedDirections = candidates[i].routeDirections[routeIndex];
            resultData = rd;
            return (true, i, p, rd, selectedKeys, selectedDirections);
        }
        if (sawInvalidAnchor) {
            revert DexError(ContractDexErrorCodes.VALIDATION_ROUTE_INVALID);
        }
        revert NoProfitableCandidate();
    }

    /// @notice Read-only adaptive peak search from a caller-provided
    /// initialAmount. The initial amount is only the search origin. The contract
    /// doubles a profitable origin until the first non-improving probe is found,
    /// then searches the interval with midpoints. It returns the first midpoint
    /// that improves the profitable base instead of scanning every fixed ladder
    /// amount to completion.
    function previewBestDetailedWithInitialAmount(
        address tokenZ,
        Candidate[] calldata candidates,
        uint256 initialAmount,
        uint256 minProfit,
        uint256 minDeviationBps
    )
        external
        returns (
            bool executed,
            uint256 selectedAmount,
            uint256 candidateIndex,
            uint256 profit,
            uint256 routeIndex,
            bytes memory resultData
        )
    {
        if (tokenZ == address(0) || initialAmount == 0) revert InvalidRequest();
        (uint256[] memory order, uint256 orderLen, bool sawInvalidAnchor) = _selectionOrder(
            candidates, tokenZ, minDeviationBps
        );
        uint256 selectedCandidateIndex;
        uint256 selectedProfit;
        uint256 selectedRouteIndex;
        bytes memory selectedResultData;
        uint256 baseAmount;
        uint256 upperAmount;
        bool hasUpperAmount;

        (bool initialExecuted, uint256 initialCandidateIndex, uint256 initialProfit, uint256 initialRouteIndex, bytes memory initialResultData) =
            _probeAmountForCandidates(tokenZ, candidates, order, orderLen, initialAmount, minProfit);
        if (initialExecuted) {
            executed = true;
            selectedAmount = initialAmount;
            selectedCandidateIndex = initialCandidateIndex;
            selectedProfit = initialProfit;
            selectedRouteIndex = initialRouteIndex;
            selectedResultData = initialResultData;
            baseAmount = initialAmount;

            uint256 candidate = _doubleAmount(baseAmount);
            for (uint256 step = 0; step < MAX_AMOUNT_SEARCH_STEPS && candidate > 0; step++) {
                (bool ok, uint256 p, bytes memory rd, ) =
                    _probeCandidateRouteFixed(candidates[selectedCandidateIndex], tokenZ, candidate, minProfit, selectedRouteIndex);
                if (ok && p > selectedProfit) {
                    selectedAmount = candidate;
                    selectedProfit = p;
                    selectedResultData = rd;
                    baseAmount = candidate;
                    hasUpperAmount = false;
                    upperAmount = 0;
                    candidate = _doubleAmount(baseAmount);
                    continue;
                }
                upperAmount = candidate;
                hasUpperAmount = true;
                break;
            }
        } else {
            upperAmount = initialAmount;
            uint256 candidate = initialAmount / 2;
            for (uint256 step = 0; step < MAX_AMOUNT_SEARCH_STEPS && candidate > 0; step++) {
                (bool ok, uint256 p, bytes memory rd, ) =
                    _probeCandidateRouteFixed(candidates[initialCandidateIndex], tokenZ, candidate, minProfit, initialRouteIndex);
                if (ok) {
                    executed = true;
                    selectedAmount = candidate;
                    selectedProfit = p;
                    selectedCandidateIndex = initialCandidateIndex;
                    selectedRouteIndex = initialRouteIndex;
                    selectedResultData = rd;
                    baseAmount = candidate;
                    hasUpperAmount = true;
                    break;
                }
                upperAmount = candidate;
                candidate /= 2;
            }
        }

        if (hasUpperAmount && executed) {
            for (uint256 step = 0; step < MAX_AMOUNT_SEARCH_STEPS; step++) {
                if (upperAmount <= baseAmount + 1) break;
                uint256 midpoint = baseAmount + (upperAmount - baseAmount) / 2;
                if (midpoint <= baseAmount || midpoint >= upperAmount) break;

                (bool ok, uint256 p, bytes memory rd, ) =
                    _probeCandidateRouteFixed(candidates[selectedCandidateIndex], tokenZ, midpoint, minProfit, selectedRouteIndex);
                if (ok && p > selectedProfit) {
                    selectedAmount = midpoint;
                    selectedProfit = p;
                    selectedResultData = rd;
                    break;
                }
                upperAmount = midpoint;
            }
        }

        candidateIndex = selectedCandidateIndex;
        profit = selectedProfit;
        routeIndex = selectedRouteIndex;
        resultData = selectedResultData;
        if (!executed) {
            if (sawInvalidAnchor) {
                revert DexError(ContractDexErrorCodes.VALIDATION_ROUTE_INVALID);
            }
            revert NoProfitableCandidate();
        }
    }

    /// @notice Broadcast wrapper for the preselected amount produced by
    /// previewBestDetailedWithInitialAmount. Runs the normal single-amount
    /// best-route selection once, then returns the selected route details.
    function runBestDetailedWithSelectedAmount(
        address tokenZ,
        Candidate[] calldata candidates,
        uint256 selectedAmount,
        uint256 minProfit,
        uint256 minDeviationBps
    ) external returns (bool executed, uint256 candidateIndex, uint256 profit, bytes memory resultData, PoolKey[3] memory selectedKeys, bool[3] memory selectedDirections) {
        if (selectedAmount == 0) revert InvalidRequest();
        (executed, candidateIndex, profit, resultData, selectedKeys, selectedDirections) = this.runBestDetailed(
            tokenZ,
            candidates,
            selectedAmount,
            minProfit,
            minDeviationBps
        );
    }

    /// @notice Resolves the deviation for every anchored candidate and orders
    /// them from largest to smallest deviation. The Robinhood network has no
    /// MEV competition, so the composer picks the candidate with the largest
    /// pool-embedded price deviation instead of executing the first candidate
    /// that happens to succeed.
    function _selectionOrder(
        Candidate[] calldata candidates,
        address tokenZ,
        uint256 minDeviationBps
    ) private view returns (uint256[] memory order, uint256 orderLen, bool sawInvalidAnchor) {
        uint256 n = candidates.length;
        uint256[] memory deviation = new uint256[](n);
        bool[] memory eligible = new bool[](n);
        CachedPool[] memory cache = new CachedPool[](64);
        uint256 cacheLen = 0;
        sawInvalidAnchor = false;
        bool foundAnchored = false;

        for (uint256 i = 0; i < n; i++) {
            if (!_hasAnchoredRoute(candidates[i], tokenZ)) {
                sawInvalidAnchor = true;
                continue;
            }
            foundAnchored = true;
            (uint256 d, uint256 newCacheLen) = _onchainDeviationBps(candidates[i], tokenZ, cache, cacheLen);
            cacheLen = newCacheLen;
            if (d == type(uint256).max) {
                d = _deviationBps(candidates[i]);
            }
            if (d == type(uint256).max) {
                continue;
            }
            deviation[i] = d;
            if (d >= minDeviationBps) {
                eligible[i] = true;
            }
        }

        // A stray invalid candidate must not mask the real no-profit case when
        // at least one anchored candidate was provided.
        sawInvalidAnchor = sawInvalidAnchor && !foundAnchored;

        order = new uint256[](n);
        for (uint256 round = 0; round < n; round++) {
            int256 best = -1;
            for (uint256 j = 0; j < n; j++) {
                if (eligible[j] && (best < 0 || deviation[j] > deviation[uint256(best)])) {
                    best = int256(j);
                }
            }
            if (best < 0) {
                break;
            }
            eligible[uint256(best)] = false;
            order[orderLen++] = uint256(best);
        }
    }

    /// @notice Calls the executor's runArbitrageBest for one candidate and decodes
    /// the result. A failing call does not revert the composer; the caller moves
    /// on to the next candidate in the selection order.
    function _tryCandidate(
        Candidate calldata candidate,
        address tokenZ,
        uint256 amountIn,
        uint256 minProfit
    ) private returns (bool ok, uint256 profit, bytes memory resultData, uint256 routeIndex) {
        uint256 routeCount = candidate.routePools.length;
        PoolKey[3][] memory sortedPools = new PoolKey[3][](routeCount);
        bool[3][] memory sortedDirections = new bool[3][](routeCount);
        uint256[] memory deviationScore = new uint256[](routeCount);
        uint256[] memory originalIndex = new uint256[](routeCount);
        CachedPool[] memory cache = new CachedPool[](64);
        uint256 cacheLen = 0;
        uint256 sortedLen = 0;

        for (uint256 i = 0; i < routeCount; i++) {
            if (
                i >= candidate.routeDirections.length ||
                !_routeIsAnchored(candidate.routePools[i], candidate.routeDirections[i], tokenZ)
            ) {
                continue;
            }
            (uint256 d, uint256 newCacheLen) = _routeDeviationBps(
                candidate.routePools[i],
                candidate.routeDirections[i],
                tokenZ,
                cache,
                cacheLen
            );
            cacheLen = newCacheLen;
            sortedPools[sortedLen] = candidate.routePools[i];
            sortedDirections[sortedLen] = candidate.routeDirections[i];
            deviationScore[sortedLen] = d == type(uint256).max ? 0 : d;
            originalIndex[sortedLen] = i;
            sortedLen++;
        }

        for (uint256 round = 0; round < sortedLen; round++) {
            uint256 best = round;
            for (uint256 j = round + 1; j < sortedLen; j++) {
                if (deviationScore[j] > deviationScore[best]) {
                    best = j;
                }
            }
            if (best != round) {
                uint256 tmpScore = deviationScore[round];
                deviationScore[round] = deviationScore[best];
                deviationScore[best] = tmpScore;

                PoolKey[3] memory tmpPools = sortedPools[round];
                sortedPools[round] = sortedPools[best];
                sortedPools[best] = tmpPools;

                bool[3] memory tmpDirections = sortedDirections[round];
                sortedDirections[round] = sortedDirections[best];
                sortedDirections[best] = tmpDirections;

                uint256 tmpOriginal = originalIndex[round];
                originalIndex[round] = originalIndex[best];
                originalIndex[best] = tmpOriginal;
            }
        }

        bytes memory callData = abi.encodeWithSelector(
            RUN_ARBITRAGE_BEST_SELECTOR,
            tokenZ,
            sortedPools,
            sortedDirections,
            amountIn,
            minProfit
        );
        (bool success, bytes memory ret) = executor.call(callData);
        if (!success || ret.length < 5 * 32) {
            return (false, 0, "", 0);
        }
        (uint256 outTb, uint256 outTa, uint256 outTz, uint256 p, uint256 ri) = abi.decode(
            ret,
            (uint256, uint256, uint256, uint256, uint256)
        );
        uint256 selectedRoute = ri < sortedLen ? originalIndex[ri] : type(uint256).max;
        resultData = abi.encode(outTb, outTa, outTz, p, selectedRoute);
        return (true, p, resultData, selectedRoute);
    }

    /// @notice Computes the triangular deviation on-chain from scaled quotes.
    /// @dev m = TB/TA direct quote, x = TB/TZ quote, y = TA/TZ quote.
    /// n = x/y, deviation = |m/n - 1| converted to basis points.
    /// When quotes are not supplied, falls back to the off-chain candidate
    /// deviation only to keep the first phase usable.
    function _deviationBps(Candidate calldata candidate) internal pure returns (uint256) {
        if (candidate.mQuote == 0 || candidate.xQuote == 0 || candidate.yQuote == 0) {
            return candidate.deviationBps;
        }
        uint256 n = (candidate.xQuote * 1e18) / candidate.yQuote;
        if (n == 0) {
            return candidate.deviationBps;
        }
        uint256 ratio = (candidate.mQuote * 1e18) / n;
        uint256 absDelta = ratio >= 1e18 ? ratio - 1e18 : 1e18 - ratio;
        return (absDelta * 10_000) / 1e18;
    }

    /// @notice Reads PoolManager slot0 for every route inside a candidate and
    /// keeps the largest triangular deviation found.
    function _onchainDeviationBps(
        Candidate calldata candidate,
        address tokenZ,
        CachedPool[] memory cache,
        uint256 cacheLen
    ) internal view returns (uint256, uint256) {
        uint256 routeCount = candidate.routePools.length;
        if (routeCount == 0 || routeCount != candidate.routeDirections.length) {
            return (type(uint256).max, cacheLen);
        }
        uint256 best = type(uint256).max;
        for (uint256 i = 0; i < routeCount; i++) {
            (uint256 d, uint256 newCacheLen) = _routeDeviationBps(
                candidate.routePools[i],
                candidate.routeDirections[i],
                tokenZ,
                cache,
                cacheLen
            );
            cacheLen = newCacheLen;
            if (d != type(uint256).max && (best == type(uint256).max || d > best)) {
                best = d;
            }
        }
        return (best, cacheLen);
    }

    function _routeDeviationBps(
        PoolKey[3] calldata routePools,
        bool[3] calldata routeDirections,
        address tokenZ,
        CachedPool[] memory cache,
        uint256 cacheLen
    ) internal view returns (uint256, uint256) {
        if (!_routeIsAnchored(routePools, routeDirections, tokenZ)) {
            return (type(uint256).max, cacheLen);
        }
        (uint256 q0, uint256 len0) = _rate18(routePools[0], routeDirections[0], cache, cacheLen);
        (uint256 q1, uint256 len1) = _rate18(routePools[1], routeDirections[1], cache, len0);
        (uint256 q2, uint256 len2) = _rate18(routePools[2], routeDirections[2], cache, len1);
        if (q0 == 0 || q1 == 0 || q2 == 0) {
            return (type(uint256).max, len2);
        }
        uint256 product = _mulDiv(_mulDiv(q0, q1, 1e18), q2, 1e18);
        uint256 netProduct = _feeAdjustedProduct(product, routePools);
        if (netProduct <= 1e18) {
            return (0, len2);
        }
        uint256 absDelta = netProduct - 1e18;
        return (_mulDiv(absDelta, 10_000, 1e18), len2);
    }

    function _feeAdjustedProduct(
        uint256 product,
        PoolKey[3] calldata routePools
    ) internal pure returns (uint256) {
        uint256 net = product;
        for (uint256 i = 0; i < 3; i++) {
            uint256 fee = uint256(routePools[i].fee);
            uint256 factor = fee > 1_000_000 ? 0 : 1e18 - fee * 1e12;
            net = _mulDiv(net, factor, 1e18);
        }
        return net;
    }

    function _rate18(
        PoolKey calldata key,
        bool zeroForOne,
        CachedPool[] memory cache,
        uint256 cacheLen
    ) internal view returns (uint256, uint256) {
        (uint160 sqrtPriceX96, uint256 newCacheLen) = _cachedSqrtPriceX96(key, cache, cacheLen);
        if (sqrtPriceX96 == 0) return (0, newCacheLen);
        uint256 price18 = _mulDiv(_mulDiv(uint256(sqrtPriceX96), uint256(sqrtPriceX96), 1 << 192), 1e18, 1);
        if (zeroForOne) return (price18, newCacheLen);
        return (price18 == 0 ? 0 : 1e36 / price18, newCacheLen);
    }

    function _cachedSqrtPriceX96(
        PoolKey calldata key,
        CachedPool[] memory cache,
        uint256 cacheLen
    ) internal view returns (uint160, uint256) {
        bytes32 id = _poolId(key);
        for (uint256 i = 0; i < cacheLen; i++) {
            if (cache[i].poolId == id) {
                return (cache[i].sqrtPriceX96, cacheLen);
            }
        }
        uint160 value = _sqrtPriceX96(key);
        if (cacheLen < cache.length) {
            cache[cacheLen] = CachedPool(id, value);
            return (value, cacheLen + 1);
        }
        return (value, cacheLen);
    }

    function _sqrtPriceX96(PoolKey calldata key) internal view returns (uint160) {
        bytes32 id = _poolId(key);
        bytes32 stateSlot = keccak256(abi.encodePacked(id, uint256(6)));
        (bool ok, bytes memory ret) = address(poolManager).staticcall(
            abi.encodeWithSignature("extsload(bytes32)", stateSlot)
        );
        if (!ok || ret.length < 32) return 0;
        return uint160(uint256(bytes32(ret)));
    }

    function _poolId(PoolKey calldata key) private pure returns (bytes32) {
        return keccak256(abi.encode(key));
    }

    function _routeTokenInput(PoolKey calldata key, bool zeroForOne) private pure returns (address) {
        return zeroForOne ? key.currency0 : key.currency1;
    }

    function _routeTokenOutput(PoolKey calldata key, bool zeroForOne) private pure returns (address) {
        return zeroForOne ? key.currency1 : key.currency0;
    }

    function _hasAnchoredRoute(
        Candidate calldata candidate,
        address tokenZ
    ) private pure returns (bool) {
        if (
            candidate.routePools.length == 0 ||
            candidate.routePools.length != candidate.routeDirections.length ||
            tokenZ == address(0)
        ) {
            return false;
        }
        for (uint256 i = 0; i < candidate.routePools.length; i++) {
            if (_routeIsAnchored(candidate.routePools[i], candidate.routeDirections[i], tokenZ)) {
                return true;
            }
        }
        return false;
    }

    function _routeIsAnchored(
        PoolKey[3] calldata routePools,
        bool[3] calldata routeDirections,
        address tokenZ
    ) private pure returns (bool) {
        if (tokenZ == address(0)) {
            return false;
        }
        if (_routeTokenInput(routePools[0], routeDirections[0]) != tokenZ) {
            return false;
        }
        if (_routeTokenOutput(routePools[2], routeDirections[2]) != tokenZ) {
            return false;
        }
        if (_routeTokenOutput(routePools[0], routeDirections[0]) != _routeTokenInput(routePools[1], routeDirections[1])) {
            return false;
        }
        if (_routeTokenOutput(routePools[1], routeDirections[1]) != _routeTokenInput(routePools[2], routeDirections[2])) {
            return false;
        }
        return true;
    }

    uint256 private constant MAX_AMOUNT_SEARCH_STEPS = 24;

    function _doubleAmount(uint256 amount) private pure returns (uint256) {
        if (amount > type(uint256).max / 2) return 0;
        return amount * 2;
    }

    function _probeCandidateRouteFixed(
        Candidate calldata candidate,
        address tokenZ,
        uint256 amountIn,
        uint256 minProfit,
        uint256 routeIndex
    )
        private
        returns (bool ok, uint256 profit, bytes memory resultData, uint256 selectedRoute)
    {
        if (routeIndex >= candidate.routePools.length || routeIndex >= candidate.routeDirections.length) {
            return (false, 0, "", routeIndex);
        }
        PoolKey[3][] memory pools = new PoolKey[3][](1);
        bool[3][] memory directions = new bool[3][](1);
        pools[0] = candidate.routePools[routeIndex];
        directions[0] = candidate.routeDirections[routeIndex];

        bytes memory callData = abi.encodeWithSelector(
            RUN_ARBITRAGE_BEST_SELECTOR,
            tokenZ,
            pools,
            directions,
            amountIn,
            minProfit
        );
        (bool success, bytes memory ret) = executor.call(callData);
        if (!success || ret.length < 5 * 32) {
            return (false, 0, "", routeIndex);
        }
        (uint256 outTb, uint256 outTa, uint256 outTz, uint256 p, uint256 ri) = abi.decode(
            ret,
            (uint256, uint256, uint256, uint256, uint256)
        );
        outTb; outTa; outTz; ri;
        resultData = ret;
        return (true, p, resultData, routeIndex);
    }

    function _probeAmountForCandidates(
        address tokenZ,
        Candidate[] calldata candidates,
        uint256[] memory order,
        uint256 orderLen,
        uint256 amount,
        uint256 minProfit
    )
        private
        returns (bool executed, uint256 candidateIndex, uint256 profit, uint256 routeIndex, bytes memory resultData)
    {
        for (uint256 k = 0; k < orderLen; k++) {
            uint256 i = order[k];
            (bool ok, uint256 p, bytes memory rd, uint256 ri) = _tryCandidate(
                candidates[i], tokenZ, amount, minProfit
            );
            if (!ok) {
                continue;
            }
            if (!executed || p > profit) {
                executed = true;
                candidateIndex = i;
                profit = p;
                routeIndex = ri;
                resultData = rd;
            }
        }
    }

    function _mulDiv(uint256 x, uint256 y, uint256 denominator) internal pure returns (uint256 result) {
        unchecked {
            uint256 prod0;
            uint256 prod1;
            assembly {
                let mm := mulmod(x, y, not(0))
                prod0 := mul(x, y)
                prod1 := sub(sub(mm, prod0), lt(mm, prod0))
            }
            if (prod1 == 0) {
                require(denominator > 0);
                return prod0 / denominator;
            }
            require(denominator > prod1);
            uint256 remainder;
            assembly {
                remainder := mulmod(x, y, denominator)
                prod1 := sub(prod1, gt(remainder, prod0))
                prod0 := sub(prod0, remainder)
            }
            uint256 twos = denominator & (~denominator + 1);
            assembly {
                denominator := div(denominator, twos)
                prod0 := div(prod0, twos)
                twos := add(div(sub(0, twos), twos), 1)
            }
            prod0 |= prod1 * twos;
            uint256 inv = (3 * denominator) ^ 2;
            inv *= 2 - denominator * inv;
            inv *= 2 - denominator * inv;
            inv *= 2 - denominator * inv;
            inv *= 2 - denominator * inv;
            inv *= 2 - denominator * inv;
            inv *= 2 - denominator * inv;
            result = prod0 * inv;
            require(result / prod0 == inv, "mulDiv overflow");
        }
    }
}
