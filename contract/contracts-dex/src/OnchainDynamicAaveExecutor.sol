// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Dyn {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IAavePoolDyn {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IRouterDyn {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);
    function getAmountsIn(uint256 amountOut, address[] calldata path) external view returns (uint256[] memory amounts);
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface IFlashLoanSimpleReceiverDyn {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract OnchainDynamicAaveExecutor is IFlashLoanSimpleReceiverDyn {
    error NotOwner();
    error NotPool();
    error BadInitiator();
    error Paused();
    error InvalidPlan();
    error NoViableRoute();
    error ApproveFailed();
    error TransferFailed();

    enum Strategy {
        S1Forward,
        S1Reverse,
        S2Forward,
        S2Reverse
    }

    struct DynamicRequest {
        address xToken;
        address yToken;
        address usdc;
        address router;
        uint256 amountX;
        uint256 amountY;
        uint256 premiumBps;
        uint256 minProfitValueUsdc;
        uint256 deadline;
        uint256 slippageBps;
    }

    struct CallbackPlan {
        address xToken;
        address yToken;
        address usdc;
        address router;
        Strategy strategy;
        uint256 minProfitValueUsdc;
        uint256 deadline;
        uint256 slippageBps;
    }

    struct QuoteResult {
        Strategy strategy;
        address borrowToken;
        uint256 borrowAmount;
        address profitToken;
        uint256 profitAmount;
        uint256 profitValueUsdc;
        bool viable;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event DynamicFlashLoanRequested(
        address indexed borrowToken,
        address indexed xToken,
        address indexed yToken,
        Strategy strategy,
        uint256 amount,
        uint256 quotedProfitUsdc
    );
    event DynamicFlashLoanExecuted(address indexed asset, Strategy strategy, uint256 amount, uint256 premium);

    address public owner;
    address public immutable pool;
    bool public paused;
    uint256 private constant AMOUNT_SCALE_DENOMINATOR = 10000;
    uint256 private constant MIN_PROFIT_IMPROVEMENT_BPS = 1000;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert Paused();
        _;
    }

    constructor(address poolAddress, address initialOwner) {
        if (poolAddress == address(0)) revert InvalidPlan();
        pool = poolAddress;
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
        emit OwnershipTransferred(address(0), owner);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidPlan();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function setPaused(bool value) external onlyOwner {
        paused = value;
        emit PausedSet(value);
    }

    function requestDynamicFlashLoan(DynamicRequest calldata request) external onlyOwner whenNotPaused {
        _validateRequest(request);
        QuoteResult memory best = _bestQuote(request);
        if (!best.viable || best.profitValueUsdc < request.minProfitValueUsdc) revert NoViableRoute();

        CallbackPlan memory plan = CallbackPlan({
            xToken: request.xToken,
            yToken: request.yToken,
            usdc: request.usdc,
            router: request.router,
            strategy: best.strategy,
            minProfitValueUsdc: request.minProfitValueUsdc,
            deadline: request.deadline,
            slippageBps: request.slippageBps
        });

        IAavePoolDyn(pool).flashLoanSimple(address(this), best.borrowToken, best.borrowAmount, abi.encode(plan), 0);
        emit DynamicFlashLoanRequested(
            best.borrowToken,
            request.xToken,
            request.yToken,
            best.strategy,
            best.borrowAmount,
            best.profitValueUsdc
        );
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override whenNotPaused returns (bool) {
        if (msg.sender != pool) revert NotPool();
        if (initiator != address(this)) revert BadInitiator();

        CallbackPlan memory plan = abi.decode(params, (CallbackPlan));
        if (plan.deadline < block.timestamp || asset != _borrowToken(plan.strategy, plan.xToken, plan.yToken)) {
            revert InvalidPlan();
        }

        _executeStrategy(plan, amount, amount + premium);
        _forceApprove(asset, pool, amount + premium);
        emit DynamicFlashLoanExecuted(asset, plan.strategy, amount, premium);
        return true;
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidPlan();
        if (!IERC20Dyn(token).transfer(to, amount)) revert TransferFailed();
    }

    function _bestQuote(DynamicRequest calldata request) private view returns (QuoteResult memory best) {
        for (uint256 i = 0; i < 4; i++) {
            Strategy strategy = Strategy(i);
            QuoteResult memory strategyBest = _bestIncrementalQuote(request, strategy);
            if (strategyBest.viable && (!best.viable || strategyBest.profitValueUsdc > best.profitValueUsdc)) {
                best = strategyBest;
            }
        }
    }

    function _bestIncrementalQuote(
        DynamicRequest calldata request,
        Strategy strategy
    ) private view returns (QuoteResult memory best) {
        uint256 baseAmount = _baseAmount(request, strategy);
        for (uint256 j = 0; j < 4; j++) {
            uint256 amount = (baseAmount * _amountScaleBps(j)) / AMOUNT_SCALE_DENOMINATOR;
            QuoteResult memory quote = _quoteStrategy(request, strategy, amount);
            if (!quote.viable) {
                break;
            }
            if (!best.viable) {
                best = quote;
                continue;
            }
            if (!_isSignificantProfitImprovement(best.profitValueUsdc, quote.profitValueUsdc)) {
                break;
            }
            best = quote;
        }
    }

    function _quoteStrategy(
        DynamicRequest calldata request,
        Strategy strategy,
        uint256 amount
    ) private view returns (QuoteResult memory quote) {
        address borrowToken = _borrowToken(strategy, request.xToken, request.yToken);
        if (amount == 0) return quote;

        address[] memory tokens = _routeTokens(strategy, request.xToken, request.yToken, request.usdc);
        uint256 owed = amount + (amount * request.premiumBps) / 10000;
        (bool firstOk, uint256 firstOut) = _tryAmountOut(request.router, amount, tokens[0], tokens[1]);
        if (!firstOk) return quote;
        (bool secondOk, uint256 secondOut) = _tryAmountOut(request.router, firstOut, tokens[1], tokens[2]);
        if (!secondOk) return quote;
        (bool inputOk, uint256 requiredInput) = _tryAmountIn(request.router, owed, tokens[2], tokens[3]);
        if (!inputOk) return quote;
        if (secondOut <= requiredInput) return quote;

        uint256 profitAmount = secondOut - requiredInput;
        (bool valueOk, uint256 profitValueUsdc) = _tryValueUsdc(request.router, tokens[2], request.usdc, profitAmount);
        if (!valueOk) return quote;
        quote = QuoteResult({
            strategy: strategy,
            borrowToken: borrowToken,
            borrowAmount: amount,
            profitToken: tokens[2],
            profitAmount: profitAmount,
            profitValueUsdc: profitValueUsdc,
            viable: profitValueUsdc > 0
        });
    }

    function _baseAmount(DynamicRequest calldata request, Strategy strategy) private pure returns (uint256) {
        address borrowToken = _borrowToken(strategy, request.xToken, request.yToken);
        return borrowToken == request.xToken ? request.amountX : request.amountY;
    }

    function _amountScaleBps(uint256 index) private pure returns (uint256) {
        if (index == 0) return 2500;
        if (index == 1) return 5000;
        if (index == 2) return 7500;
        return 10000;
    }

    function _isSignificantProfitImprovement(uint256 previousProfit, uint256 nextProfit) private pure returns (bool) {
        if (nextProfit <= previousProfit) return false;
        return (nextProfit - previousProfit) * AMOUNT_SCALE_DENOMINATOR >= previousProfit * MIN_PROFIT_IMPROVEMENT_BPS;
    }

    function _executeStrategy(CallbackPlan memory plan, uint256 amount, uint256 owed) private {
        address[] memory tokens = _routeTokens(plan.strategy, plan.xToken, plan.yToken, plan.usdc);
        uint256 firstOut = _swapExact(plan.router, tokens[0], tokens[1], amount, plan.deadline, plan.slippageBps);
        uint256 secondOut = _swapExact(plan.router, tokens[1], tokens[2], firstOut, plan.deadline, plan.slippageBps);
        uint256 requiredInput = _amountIn(plan.router, owed, tokens[2], tokens[3]);
        if (secondOut <= requiredInput) revert NoViableRoute();

        uint256 profitValueUsdc = _valueUsdc(plan.router, tokens[2], plan.usdc, secondOut - requiredInput);
        if (profitValueUsdc < plan.minProfitValueUsdc) revert NoViableRoute();

        _swapExact(plan.router, tokens[2], tokens[3], requiredInput, plan.deadline, plan.slippageBps);
    }

    function _swapExact(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 deadline,
        uint256 slippageBps
    ) private returns (uint256 amountOut) {
        address[] memory path = _path(tokenIn, tokenOut);
        uint256 quotedOut = _amountOut(router, amountIn, tokenIn, tokenOut);
        uint256 minOut = (quotedOut * (10000 - slippageBps)) / 10000;
        _forceApprove(tokenIn, router, amountIn);
        uint256[] memory amounts = IRouterDyn(router).swapExactTokensForTokens(
            amountIn,
            minOut,
            path,
            address(this),
            deadline
        );
        _forceApprove(tokenIn, router, 0);
        amountOut = amounts[amounts.length - 1];
    }

    function _amountOut(address router, uint256 amountIn, address tokenIn, address tokenOut) private view returns (uint256) {
        uint256[] memory amounts = IRouterDyn(router).getAmountsOut(amountIn, _path(tokenIn, tokenOut));
        return amounts[amounts.length - 1];
    }

    function _amountIn(address router, uint256 amountOut, address tokenIn, address tokenOut) private view returns (uint256) {
        uint256[] memory amounts = IRouterDyn(router).getAmountsIn(amountOut, _path(tokenIn, tokenOut));
        return amounts[0];
    }

    function _valueUsdc(address router, address token, address usdc, uint256 amount) private view returns (uint256) {
        if (amount == 0) return 0;
        if (token == usdc) return amount;
        return _amountOut(router, amount, token, usdc);
    }

    function _tryValueUsdc(
        address router,
        address token,
        address usdc,
        uint256 amount
    ) private view returns (bool ok, uint256 value) {
        if (amount == 0) return (true, 0);
        if (token == usdc) return (true, amount);
        return _tryAmountOut(router, amount, token, usdc);
    }

    function _tryAmountOut(
        address router,
        uint256 amountIn,
        address tokenIn,
        address tokenOut
    ) private view returns (bool ok, uint256 amountOut) {
        (ok, amountOut) = _tryRouterQuote(
            router,
            abi.encodeWithSelector(IRouterDyn.getAmountsOut.selector, amountIn, _path(tokenIn, tokenOut)),
            true
        );
    }

    function _tryAmountIn(
        address router,
        uint256 amountOut,
        address tokenIn,
        address tokenOut
    ) private view returns (bool ok, uint256 amountIn) {
        (ok, amountIn) = _tryRouterQuote(
            router,
            abi.encodeWithSelector(IRouterDyn.getAmountsIn.selector, amountOut, _path(tokenIn, tokenOut)),
            false
        );
    }

    function _tryRouterQuote(
        address router,
        bytes memory data,
        bool useLast
    ) private view returns (bool ok, uint256 amount) {
        (bool success, bytes memory result) = router.staticcall(data);
        if (!success || result.length == 0) return (false, 0);
        uint256[] memory amounts = abi.decode(result, (uint256[]));
        if (amounts.length == 0) return (false, 0);
        amount = useLast ? amounts[amounts.length - 1] : amounts[0];
        ok = amount > 0;
    }

    function _routeTokens(
        Strategy strategy,
        address xToken,
        address yToken,
        address usdc
    ) private pure returns (address[] memory tokens) {
        tokens = new address[](4);
        if (strategy == Strategy.S1Forward) {
            (tokens[0], tokens[1], tokens[2], tokens[3]) = (xToken, usdc, yToken, xToken);
        } else if (strategy == Strategy.S1Reverse) {
            (tokens[0], tokens[1], tokens[2], tokens[3]) = (yToken, xToken, usdc, yToken);
        } else if (strategy == Strategy.S2Forward) {
            (tokens[0], tokens[1], tokens[2], tokens[3]) = (xToken, yToken, usdc, xToken);
        } else {
            (tokens[0], tokens[1], tokens[2], tokens[3]) = (yToken, usdc, xToken, yToken);
        }
    }

    function _borrowToken(Strategy strategy, address xToken, address yToken) private pure returns (address) {
        return strategy == Strategy.S1Forward || strategy == Strategy.S2Forward ? xToken : yToken;
    }

    function _path(address tokenIn, address tokenOut) private pure returns (address[] memory path) {
        path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;
    }

    function _validateRequest(DynamicRequest calldata request) private view {
        if (
            request.xToken == address(0)
                || request.yToken == address(0)
                || request.usdc == address(0)
                || request.router == address(0)
                || request.xToken == request.yToken
                || request.deadline < block.timestamp
                || request.slippageBps > 5000
        ) {
            revert InvalidPlan();
        }
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20Dyn(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20Dyn(token).approve(spender, amount)) revert ApproveFailed();
    }
}
