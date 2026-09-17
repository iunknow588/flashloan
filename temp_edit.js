const fs = require("fs");
const p = "E:/2026OPC大赛/flashLoan/contract/robinhood-testnet/DEX/src/test/ArbitrageComposer.sol";
let src = fs.readFileSync(p, "utf8");
const startMarker = "    function runBest(";
const endMarker = "    /// @notice Computes the triangular deviation";
const start = src.indexOf(startMarker);
const end = src.indexOf(endMarker);
if (start === -1 || end === -1 || end < start) throw new Error("markers not found: " + start + " " + end);
const replacement = `    function runBest(
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
            (bool ok, uint256 p, bytes memory rd, uint256 unusedRoute) = _tryCandidate(
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

    /// @notice Resolves the deviation for every anchored candidate and orders
    /// them from largest to smallest deviation. The Robinhood network has no
    /// MEV competition, so the composer picks the candidate with the largest
    /// price deviation instead of executing the first candidate that happens
    /// to succeed.
    function _selectionOrder(
        Candidate[] calldata candidates,
        address tokenZ,
        uint256 minDeviationBps
    ) private returns (uint256[] memory order, uint256 orderLen, bool sawInvalidAnchor) {
        uint256 n = candidates.length;
        uint256[] memory deviation = new uint256[](n);
        bool[] memory eligible = new bool[](n);
        CachedPool[] memory cache = new CachedPool[](64);
        uint256 cacheLen = 0;
        sawInvalidAnchor = false;

        for (uint256 i = 0; i < n; i++) {
            if (!_hasAnchoredRoute(candidates[i], tokenZ)) {
                sawInvalidAnchor = true;
                continue;
            }
            (uint256 d, uint256 newCacheLen) = _onchainDeviationBps(candidates[i], cache, cacheLen);
            cacheLen = newCacheLen;
            if (d == type(uint256).max) {
                d = _deviationBps(candidates[i]);
            }
            deviation[i] = d;
            if (d >= minDeviationBps) {
                eligible[i] = true;
            }
        }

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
        bytes memory callData = abi.encodeWithSelector(
            RUN_ARBITRAGE_BEST_SELECTOR,
            tokenZ,
            candidate.routePools,
            candidate.routeDirections,
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
        resultData = abi.encode(outTb, outTa, outTz, p, ri);
        return (true, p, resultData, ri);
    }

`;
src = src.slice(0, start) + replacement + src.slice(end);
fs.writeFileSync(p, src);
console.log("contract updated, new length", src.length);
