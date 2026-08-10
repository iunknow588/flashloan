// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Controller {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IRouterController {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);
}

interface IAaveTriangularExecutorLike {
    struct ExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        uint256 amount;
        uint256 deadline;
        uint256 amountOutMinUsdc;
    }

    function execute(ExecutionRequest calldata request) external returns (uint256 profitSwept);
    function flashLoanPremiumBps() external view returns (uint256);
}

contract TriangularRouteController {
    error NotOwner();
    error Paused();
    error InvalidRequest();
    error NoViableRoute(
        uint256 failureCode,
        uint256 edgeBps,
        uint256 requiredEdgeBps,
        uint256 quotedFinalUsdc,
        uint256 requiredFinalUsdc,
        uint256 minAfterSlippageUsdc
    );
    error TransferFailed();
    error Reentrancy();

    uint256 public constant MAX_CANDIDATE_TOKENS = 8;
    uint256 public constant FAIL_NONE = 0;
    uint256 public constant FAIL_FIRST_HOP_QUOTE = 1;
    uint256 public constant FAIL_DIRECT_COMPARISON_QUOTE = 2;
    uint256 public constant FAIL_MIDDLE_HOP_QUOTE = 3;
    uint256 public constant FAIL_EDGE_BELOW_REQUIRED = 4;
    uint256 public constant FAIL_ROUTE_QUOTE = 5;
    uint256 public constant FAIL_FINAL_BELOW_REQUIRED = 6;
    uint256 public constant FAIL_SLIPPAGE_BELOW_REQUIRED = 7;
    uint256 public constant MAX_AMOUNT_SEARCH_STEPS = 16;

    struct QuoteContext {
        uint256 owedEstimate;
        uint256 requiredEdgeBps;
        uint256 requiredFinalUsdc;
        uint256 fundingCostUsdc;
    }

    struct SearchState {
        bool hasBest;
        uint256 routeMaxBorrow;
        bool baseQuoteSet;
        uint256 baseAmount;
        uint256 baseFinalUsdc;
        uint256 attempts;
    }

    struct RouteDecision {
        bool viable;
        bool reverse;
        uint256 quotedFinalUsdc;
        uint256 profitUsdc;
        address[] path;
        uint256 edgeBps;
        uint256 requiredEdgeBps;
        uint256 directComparableAmount;
        uint256 viaComparableAmount;
        uint256 failureCode;
        uint256 requiredFinalUsdc;
        uint256 minAfterSlippageUsdc;
        uint256 amountOutMinUsdc;
        uint256 selectedAmount;
        uint256 routeMaxBorrow;
        uint256 probeAmount;
        uint256 probeProfitUsdc;
        uint256 fundingCostUsdc;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event ExecutionConfigSet(
        address indexed router,
        uint256 amount,
        uint256 minProfitUsdc,
        uint256 deadlineSeconds,
        uint256 slippageBps
    );
    event AmountSearchConfigSet(
        uint256 minBorrowAmount,
        uint256 maxBorrowAmount,
        uint256 amountSearchSteps,
        uint256 maxRouteSlippageBps
    );
    event RouteSubmitted(
        bool indexed reverse,
        address indexed tokenX,
        address indexed tokenY,
        uint256 amount,
        uint256 quotedFinalUsdc,
        uint256 profitSwept
    );
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable usdc;
    address public immutable executor;
    address public owner;
    bool public paused;
    bool private locked;
    address public dexRouter;
    uint256 public borrowAmount;
    uint256 public minProfitUsdc;
    uint256 public deadlineSeconds;
    uint256 public slippageBps;
    uint256 public minBorrowAmount;
    uint256 public maxBorrowAmount;
    uint256 public amountSearchSteps;
    uint256 public maxRouteSlippageBps;

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

    function setExecutionConfig(
        address routerAddress,
        uint256 borrowAmountValue,
        uint256 minProfit,
        uint256 deadlineWindowSeconds,
        uint256 slippage
    ) external onlyOwner {
        _validateConfig(routerAddress, borrowAmountValue, deadlineWindowSeconds, slippage);
        dexRouter = routerAddress;
        borrowAmount = borrowAmountValue;
        minProfitUsdc = minProfit;
        deadlineSeconds = deadlineWindowSeconds;
        slippageBps = slippage;
        if (amountSearchSteps == 0) {
            minBorrowAmount = borrowAmountValue;
            maxBorrowAmount = borrowAmountValue;
            amountSearchSteps = 1;
            maxRouteSlippageBps = slippage;
            emit AmountSearchConfigSet(borrowAmountValue, borrowAmountValue, 1, slippage);
        } else {
            _validateAmountSearchConfig(minBorrowAmount, maxBorrowAmount, amountSearchSteps, maxRouteSlippageBps);
        }
        emit ExecutionConfigSet(routerAddress, borrowAmountValue, minProfit, deadlineWindowSeconds, slippage);
    }

    function setAmountSearchConfig(
        uint256 minBorrow,
        uint256 maxBorrow,
        uint256 steps,
        uint256 maxSlippage
    ) external onlyOwner {
        _validateAmountSearchConfig(minBorrow, maxBorrow, steps, maxSlippage);
        minBorrowAmount = minBorrow;
        maxBorrowAmount = maxBorrow;
        amountSearchSteps = steps;
        maxRouteSlippageBps = maxSlippage;
        emit AmountSearchConfigSet(minBorrow, maxBorrow, steps, maxSlippage);
    }

    function run(address[] calldata candidateTokens)
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (uint256 profitSwept)
    {
        RouteDecision memory decision;
        (, decision) = previewBestRoute(candidateTokens);
        if (!decision.viable) {
            revert NoViableRoute(
                decision.failureCode,
                decision.edgeBps,
                decision.requiredEdgeBps,
                decision.quotedFinalUsdc,
                decision.requiredFinalUsdc,
                decision.minAfterSlippageUsdc
            );
        }

        profitSwept = _executeDecision(decision);

        emit RouteSubmitted(
            decision.reverse,
            decision.path[1],
            decision.path[2],
            decision.selectedAmount,
            decision.quotedFinalUsdc,
            profitSwept
        );
    }

    function previewBestRoute(address[] calldata candidateTokens)
        public
        view
        returns (uint256 bestPairIndex, RouteDecision memory best)
    {
        _validateCandidateTokens(candidateTokens);
        _validateConfig(dexRouter, borrowAmount, deadlineSeconds, slippageBps);
        _validateAmountSearchConfig(minBorrowAmount, maxBorrowAmount, amountSearchSteps, maxRouteSlippageBps);

        uint256 premiumBps = IAaveTriangularExecutorLike(executor).flashLoanPremiumBps();
        bool hasBest = false;
        uint256 pairIndex = 0;
        for (uint256 i = 0; i < candidateTokens.length; i++) {
            for (uint256 j = i + 1; j < candidateTokens.length; j++) {
                RouteDecision memory decision = _previewDirection(
                    candidateTokens[i],
                    candidateTokens[j],
                    premiumBps,
                    false
                );
                if (_shouldReplaceBest(decision, best, hasBest)) {
                    best = decision;
                    bestPairIndex = pairIndex;
                    hasBest = true;
                }

                decision = _previewDirection(
                    candidateTokens[j],
                    candidateTokens[i],
                    premiumBps,
                    true
                );
                if (_shouldReplaceBest(decision, best, hasBest)) {
                    best = decision;
                    bestPairIndex = pairIndex;
                    hasBest = true;
                }
                pairIndex++;
            }
        }
    }

    function _executeDecision(RouteDecision memory decision)
        private
        returns (uint256 profitSwept)
    {
        IAaveTriangularExecutorLike.ExecutionRequest memory executionRequest = IAaveTriangularExecutorLike.ExecutionRequest({
            tokenX: decision.path[1],
            tokenY: decision.path[2],
            router: dexRouter,
            amount: decision.selectedAmount,
            deadline: block.timestamp + deadlineSeconds,
            amountOutMinUsdc: decision.amountOutMinUsdc
        });
        profitSwept = IAaveTriangularExecutorLike(executor).execute(executionRequest);
    }

    function withdrawToken(address token, address to, uint256 tokenAmount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        if (!IERC20Controller(token).transfer(to, tokenAmount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, tokenAmount);
    }

    function _previewDirection(
        address tokenIn,
        address tokenOut,
        uint256 premiumBps,
        bool reverse
    ) private view returns (RouteDecision memory decision) {
        address[] memory path = _routePath(tokenIn, tokenOut, false);
        SearchState memory state = SearchState({
            hasBest: false,
            routeMaxBorrow: 0,
            baseQuoteSet: false,
            baseAmount: 0,
            baseFinalUsdc: 0,
            attempts: 0
        });

        RouteDecision memory probe = _previewAmount(tokenIn, tokenOut, path, borrowAmount, premiumBps, reverse);
        state = _recordCapacity(probe, borrowAmount, state);
        state.attempts = 1;
        decision = probe;
        state.hasBest = probe.viable;

        if (probe.viable) {
            uint256 amount = borrowAmount * 2;
            while (state.attempts < amountSearchSteps && amount <= maxBorrowAmount) {
                RouteDecision memory candidate = _previewAmount(tokenIn, tokenOut, path, amount, premiumBps, reverse);
                state = _recordCapacity(candidate, amount, state);
                state.attempts++;

                if (!candidate.viable) {
                    break;
                }
                if (candidate.profitUsdc < decision.profitUsdc) {
                    break;
                }
                if (_shouldReplaceBest(candidate, decision, true)) {
                    decision = candidate;
                }

                uint256 nextAmount = amount * 2;
                if (nextAmount <= amount || nextAmount > maxBorrowAmount) {
                    break;
                }
                amount = nextAmount;
            }
        }

        if (state.hasBest && state.attempts < amountSearchSteps) {
            (decision, state) = _refineAboveBest(tokenIn, tokenOut, path, premiumBps, reverse, decision, state);
        }

        decision.routeMaxBorrow = state.routeMaxBorrow;
        decision.probeAmount = borrowAmount;
        decision.probeProfitUsdc = probe.viable ? probe.profitUsdc : 0;
    }

    function _refineAboveBest(
        address tokenIn,
        address tokenOut,
        address[] memory path,
        uint256 premiumBps,
        bool reverse,
        RouteDecision memory best,
        SearchState memory state
    ) private view returns (RouteDecision memory refined, SearchState memory updatedState) {
        refined = best;
        updatedState = state;
        uint256 anchor = best.selectedAmount;
        uint256[4] memory deltaNumerators = [uint256(1), uint256(1), uint256(1), uint256(5)];
        uint256[4] memory deltaDenominators = [uint256(2), uint256(4), uint256(8), uint256(100)];

        for (uint256 i = 0; i < deltaNumerators.length && updatedState.attempts < amountSearchSteps; i++) {
            uint256 delta = (anchor * deltaNumerators[i]) / deltaDenominators[i];
            if (delta == 0) continue;
            uint256 amount = anchor + delta;
            if (amount <= anchor || amount > maxBorrowAmount) continue;

            RouteDecision memory candidate = _previewAmount(tokenIn, tokenOut, path, amount, premiumBps, reverse);
            updatedState = _recordCapacity(candidate, amount, updatedState);
            if (_shouldReplaceBest(candidate, refined, true)) {
                refined = candidate;
            }
            updatedState.attempts++;
        }
    }

    function _recordCapacity(
        RouteDecision memory candidate,
        uint256 amount,
        SearchState memory state
    ) private view returns (SearchState memory updatedState) {
        updatedState = state;
        if (candidate.quotedFinalUsdc == 0) return updatedState;
        if (!updatedState.baseQuoteSet) {
            updatedState.baseQuoteSet = true;
            updatedState.baseAmount = amount;
            updatedState.baseFinalUsdc = candidate.quotedFinalUsdc;
        }
        if (
            _routeSlippageBps(
                updatedState.baseAmount,
                updatedState.baseFinalUsdc,
                amount,
                candidate.quotedFinalUsdc
            ) <= maxRouteSlippageBps
        ) {
            if (amount > updatedState.routeMaxBorrow) {
                updatedState.routeMaxBorrow = amount;
            }
        }
    }

    function _previewAmount(
        address tokenIn,
        address tokenOut,
        address[] memory path,
        uint256 amount,
        uint256 premiumBps,
        bool reverse
    ) private view returns (RouteDecision memory decision) {
        QuoteContext memory context = _quoteContext(premiumBps, amount);
        (bool firstOk, uint256 firstHopAmount) = _tryAmountsOut(dexRouter, amount, _twoTokenPath(usdc, tokenIn));
        (bool directOk, uint256 directComparisonAmount) = _tryAmountsOut(dexRouter, amount, _twoTokenPath(usdc, tokenOut));
        if (!firstOk) {
            return _decision(
                false,
                reverse,
                path,
                0,
                0,
                0,
                context.requiredEdgeBps,
                directComparisonAmount,
                0,
                FAIL_FIRST_HOP_QUOTE,
                context.requiredFinalUsdc,
                0,
                0,
                amount,
                0,
                context.fundingCostUsdc
            );
        }
        if (!directOk || directComparisonAmount == 0) {
            return _decision(
                false,
                reverse,
                path,
                0,
                0,
                0,
                context.requiredEdgeBps,
                directComparisonAmount,
                0,
                FAIL_DIRECT_COMPARISON_QUOTE,
                context.requiredFinalUsdc,
                0,
                0,
                amount,
                0,
                context.fundingCostUsdc
            );
        }

        (bool viaOk, uint256 viaComparableAmount) =
            _tryAmountsOut(dexRouter, firstHopAmount, _twoTokenPath(tokenIn, tokenOut));
        if (!viaOk) {
            return _decision(
                false,
                reverse,
                path,
                0,
                0,
                0,
                context.requiredEdgeBps,
                directComparisonAmount,
                viaComparableAmount,
                FAIL_MIDDLE_HOP_QUOTE,
                context.requiredFinalUsdc,
                0,
                0,
                amount,
                0,
                context.fundingCostUsdc
            );
        }

        uint256 edgeBps = _edgeBps(viaComparableAmount, directComparisonAmount);
        if (edgeBps < context.requiredEdgeBps) {
            return _decision(
                false,
                reverse,
                path,
                0,
                0,
                edgeBps,
                context.requiredEdgeBps,
                directComparisonAmount,
                viaComparableAmount,
                FAIL_EDGE_BELOW_REQUIRED,
                context.requiredFinalUsdc,
                0,
                0,
                amount,
                0,
                context.fundingCostUsdc
            );
        }

        (bool quoteOk, uint256 finalUsdc) = _tryAmountsOut(dexRouter, amount, path);
        if (!quoteOk) {
            return _decision(
                false,
                reverse,
                path,
                0,
                0,
                edgeBps,
                context.requiredEdgeBps,
                directComparisonAmount,
                viaComparableAmount,
                FAIL_ROUTE_QUOTE,
                context.requiredFinalUsdc,
                0,
                0,
                amount,
                0,
                context.fundingCostUsdc
            );
        }
        if (finalUsdc < context.requiredFinalUsdc) {
            return _decision(
                false,
                reverse,
                path,
                finalUsdc,
                0,
                edgeBps,
                context.requiredEdgeBps,
                directComparisonAmount,
                viaComparableAmount,
                FAIL_FINAL_BELOW_REQUIRED,
                context.requiredFinalUsdc,
                0,
                0,
                amount,
                0,
                context.fundingCostUsdc
            );
        }
        uint256 minAfterSlippage = (finalUsdc * (10000 - slippageBps)) / 10000;
        if (minAfterSlippage < context.requiredFinalUsdc) {
            return _decision(
                false,
                reverse,
                path,
                finalUsdc,
                0,
                edgeBps,
                context.requiredEdgeBps,
                directComparisonAmount,
                viaComparableAmount,
                FAIL_SLIPPAGE_BELOW_REQUIRED,
                context.requiredFinalUsdc,
                minAfterSlippage,
                0,
                amount,
                0,
                context.fundingCostUsdc
            );
        }
        decision = _decision(
            true,
            reverse,
            path,
            finalUsdc,
            finalUsdc - context.owedEstimate,
            edgeBps,
            context.requiredEdgeBps,
            directComparisonAmount,
            viaComparableAmount,
            FAIL_NONE,
            context.requiredFinalUsdc,
            minAfterSlippage,
            _max(context.requiredFinalUsdc, minAfterSlippage),
            amount,
            0,
            context.fundingCostUsdc
        );
    }

    function _decision(
        bool viable,
        bool reverse,
        address[] memory path,
        uint256 quotedFinalUsdc,
        uint256 profitUsdc,
        uint256 edgeBps,
        uint256 requiredEdgeBps,
        uint256 directComparableAmount,
        uint256 viaComparableAmount,
        uint256 failureCode,
        uint256 requiredFinalUsdc,
        uint256 minAfterSlippageUsdc,
        uint256 amountOutMinUsdc,
        uint256 selectedAmount,
        uint256 routeMaxBorrow,
        uint256 fundingCostUsdc
    ) private pure returns (RouteDecision memory decision) {
        decision = RouteDecision({
            viable: viable,
            reverse: reverse,
            quotedFinalUsdc: quotedFinalUsdc,
            profitUsdc: profitUsdc,
            path: path,
            edgeBps: edgeBps,
            requiredEdgeBps: requiredEdgeBps,
            directComparableAmount: directComparableAmount,
            viaComparableAmount: viaComparableAmount,
            failureCode: failureCode,
            requiredFinalUsdc: requiredFinalUsdc,
            minAfterSlippageUsdc: minAfterSlippageUsdc,
            amountOutMinUsdc: amountOutMinUsdc,
            selectedAmount: selectedAmount,
            routeMaxBorrow: routeMaxBorrow,
            probeAmount: 0,
            probeProfitUsdc: 0,
            fundingCostUsdc: fundingCostUsdc
        });
    }

    function _failureRank(uint256 failureCode) private pure returns (uint256) {
        if (failureCode == FAIL_ROUTE_QUOTE) return 3;
        if (failureCode == FAIL_FINAL_BELOW_REQUIRED || failureCode == FAIL_SLIPPAGE_BELOW_REQUIRED) return 2;
        if (failureCode == FAIL_EDGE_BELOW_REQUIRED) return 1;
        return 0;
    }

    function _shouldReplaceBest(
        RouteDecision memory candidate,
        RouteDecision memory current,
        bool hasCurrent
    ) private pure returns (bool) {
        if (!hasCurrent) return true;
        if (candidate.viable && !current.viable) return true;
        if (candidate.viable && current.viable) {
            if (candidate.profitUsdc > current.profitUsdc) return true;
            if (candidate.profitUsdc == current.profitUsdc && candidate.edgeBps > current.edgeBps) return true;
        }
        if (!candidate.viable && !current.viable && _failureRank(candidate.failureCode) > _failureRank(current.failureCode)) {
            return true;
        }
        return false;
    }

    function _max(uint256 a, uint256 b) private pure returns (uint256) {
        return a > b ? a : b;
    }

    function _quoteContext(uint256 premiumBps, uint256 amount) private view returns (QuoteContext memory context) {
        uint256 fundingCostUsdc = (amount * premiumBps) / 10000;
        uint256 owedEstimate = amount + fundingCostUsdc;
        context = QuoteContext({
            owedEstimate: owedEstimate,
            requiredEdgeBps: _requiredEdgeBpsFor(premiumBps, amount, minProfitUsdc, slippageBps),
            requiredFinalUsdc: owedEstimate + _requiredProfitUsdc(minProfitUsdc),
            fundingCostUsdc: fundingCostUsdc
        });
    }

    function _routeSlippageBps(
        uint256 baseAmount,
        uint256 baseFinalUsdc,
        uint256 amount,
        uint256 finalUsdc
    ) private pure returns (uint256) {
        if (baseAmount == 0 || baseFinalUsdc == 0 || amount == 0 || finalUsdc == 0) return type(uint256).max;
        uint256 expectedFinal = (baseFinalUsdc * amount) / baseAmount;
        if (finalUsdc >= expectedFinal) return 0;
        return ((expectedFinal - finalUsdc) * 10000) / expectedFinal;
    }

    function _tryAmountsOut(
        address router,
        uint256 amount,
        address[] memory path
    ) private view returns (bool ok, uint256 finalAmount) {
        (bool success, bytes memory result) = router.staticcall(
            abi.encodeWithSelector(IRouterController.getAmountsOut.selector, amount, path)
        );
        if (!success || result.length == 0) return (false, 0);
        uint256[] memory amounts = abi.decode(result, (uint256[]));
        if (amounts.length != path.length || amounts.length == 0) return (false, 0);
        finalAmount = amounts[amounts.length - 1];
        ok = finalAmount > 0;
    }

    function _routePath(address tokenX, address tokenY, bool reverse) private view returns (address[] memory path) {
        path = new address[](4);
        path[0] = usdc;
        if (reverse) {
            path[1] = tokenY;
            path[2] = tokenX;
        } else {
            path[1] = tokenX;
            path[2] = tokenY;
        }
        path[3] = usdc;
    }

    function _twoTokenPath(address tokenIn, address tokenOut) private pure returns (address[] memory path) {
        path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;
    }

    function _requiredEdgeBpsFor(
        uint256 premiumBps,
        uint256 amount,
        uint256 requestedMinProfitUsdc,
        uint256 slippage
    ) private pure returns (uint256) {
        return premiumBps + slippage + _ceilDiv(_requiredProfitUsdc(requestedMinProfitUsdc) * 10000, amount);
    }

    function _requiredProfitUsdc(uint256 requestedMinProfitUsdc) private pure returns (uint256) {
        return requestedMinProfitUsdc == 0 ? 1 : requestedMinProfitUsdc;
    }

    function _edgeBps(uint256 viaAmount, uint256 directAmount) private pure returns (uint256) {
        if (viaAmount <= directAmount) return 0;
        return ((viaAmount - directAmount) * 10000) / directAmount;
    }

    function _ceilDiv(uint256 value, uint256 divisor) private pure returns (uint256) {
        return value == 0 ? 0 : ((value - 1) / divisor) + 1;
    }

    function _validateConfig(
        address routerAddress,
        uint256 borrowAmountValue,
        uint256 deadlineWindowSeconds,
        uint256 slippage
    ) private pure {
        if (routerAddress == address(0) || borrowAmountValue == 0 || deadlineWindowSeconds == 0 || slippage > 5000) {
            revert InvalidRequest();
        }
    }

    function _validateAmountSearchConfig(
        uint256 minBorrow,
        uint256 maxBorrow,
        uint256 steps,
        uint256 maxSlippage
    ) private view {
        if (
            minBorrow == 0
                || maxBorrow < minBorrow
                || borrowAmount == 0
                || borrowAmount < minBorrow
                || borrowAmount > maxBorrow
                || steps == 0
                || steps > MAX_AMOUNT_SEARCH_STEPS
                || maxSlippage > 5000
        ) {
            revert InvalidRequest();
        }
    }

    function _validateCandidateTokens(address[] calldata candidateTokens) private view {
        if (candidateTokens.length < 2 || candidateTokens.length > MAX_CANDIDATE_TOKENS) revert InvalidRequest();
        for (uint256 i = 0; i < candidateTokens.length; i++) {
            if (candidateTokens[i] == address(0) || candidateTokens[i] == usdc) revert InvalidRequest();
            for (uint256 j = i + 1; j < candidateTokens.length; j++) {
                if (candidateTokens[i] == candidateTokens[j]) revert InvalidRequest();
            }
        }
    }

}
