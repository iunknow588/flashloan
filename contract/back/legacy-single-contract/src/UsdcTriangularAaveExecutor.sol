// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Triangular {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IAavePoolTriangular {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IRouterTriangular {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface IFlashLoanSimpleReceiverTriangular {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract UsdcTriangularAaveExecutor is IFlashLoanSimpleReceiverTriangular {
    error NotOwner();
    error NotPool();
    error BadInitiator();
    error Paused();
    error InvalidRequest();
    error NoViableRoute();
    error ApproveFailed();
    error TransferFailed();
    error Reentrancy();

    struct ArbRequest {
        address tokenX;
        address tokenY;
        address router;
        uint256 amount;
        uint256 premiumBps;
        uint256 minProfitUsdc;
        uint256 deadline;
        uint256 slippageBps;
        bool allowReverse;
    }

    struct CallbackPlan {
        address tokenX;
        address tokenY;
        address router;
        uint256 minProfitUsdc;
        uint256 deadline;
        uint256 slippageBps;
        bool allowReverse;
        uint256 startingUsdcBalance;
    }

    struct RouteQuote {
        bool viable;
        bool reverse;
        uint256 finalUsdc;
        uint256 profitUsdc;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event FlashLoanRequested(address indexed usdc, address indexed tokenX, address indexed tokenY, uint256 amount);
    event TriangularArbitrageExecuted(
        bool indexed reverse,
        address indexed tokenX,
        address indexed tokenY,
        uint256 amount,
        uint256 premium,
        uint256 finalUsdc,
        uint256 profitUsdc
    );
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable pool;
    address public immutable usdc;
    address public owner;
    bool public paused;
    bool private locked;

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

    constructor(address poolAddress, address usdcAddress, address initialOwner) {
        if (poolAddress == address(0) || usdcAddress == address(0)) revert InvalidRequest();
        pool = poolAddress;
        usdc = usdcAddress;
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

    function requestArbitrage(ArbRequest calldata request) external onlyOwner whenNotPaused nonReentrantEntry {
        _validateRequest(request);
        if (!previewBestRoute(request).viable) revert NoViableRoute();

        CallbackPlan memory plan = CallbackPlan({
            tokenX: request.tokenX,
            tokenY: request.tokenY,
            router: request.router,
            minProfitUsdc: request.minProfitUsdc,
            deadline: request.deadline,
            slippageBps: request.slippageBps,
            allowReverse: request.allowReverse,
            startingUsdcBalance: IERC20Triangular(usdc).balanceOf(address(this))
        });

        IAavePoolTriangular(pool).flashLoanSimple(address(this), usdc, request.amount, abi.encode(plan), 0);
        emit FlashLoanRequested(usdc, request.tokenX, request.tokenY, request.amount);
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
        if (asset != usdc || amount == 0) revert InvalidRequest();

        CallbackPlan memory plan = abi.decode(params, (CallbackPlan));
        if (plan.deadline < block.timestamp) revert InvalidRequest();

        uint256 owed = amount + premium;
        RouteQuote memory best = _bestRoute(plan, amount, owed);
        if (!best.viable) revert NoViableRoute();

        address[] memory path = _routePath(plan.tokenX, plan.tokenY, best.reverse);
        uint256 minOut = _max(owed + plan.minProfitUsdc, (best.finalUsdc * (10000 - plan.slippageBps)) / 10000);
        _forceApprove(usdc, plan.router, amount);
        uint256[] memory amounts = IRouterTriangular(plan.router).swapExactTokensForTokens(
            amount,
            minOut,
            path,
            address(this),
            plan.deadline
        );
        _forceApprove(usdc, plan.router, 0);

        uint256 finalUsdc = amounts[amounts.length - 1];
        uint256 requiredBalance = plan.startingUsdcBalance + owed + plan.minProfitUsdc;
        uint256 actualBalance = IERC20Triangular(usdc).balanceOf(address(this));
        if (actualBalance < requiredBalance) revert NoViableRoute();

        _forceApprove(usdc, pool, owed);
        emit TriangularArbitrageExecuted(
            best.reverse,
            plan.tokenX,
            plan.tokenY,
            amount,
            premium,
            finalUsdc,
            actualBalance - plan.startingUsdcBalance - owed
        );
        return true;
    }

    function previewBestRoute(ArbRequest calldata request) public view returns (RouteQuote memory best) {
        _validateRequest(request);
        uint256 premiumEstimate = (request.amount * request.premiumBps) / 10000;
        return _bestRoute(
            CallbackPlan({
                tokenX: request.tokenX,
                tokenY: request.tokenY,
                router: request.router,
                minProfitUsdc: request.minProfitUsdc,
                deadline: request.deadline,
                slippageBps: request.slippageBps,
                allowReverse: request.allowReverse,
                startingUsdcBalance: 0
            }),
            request.amount,
            request.amount + premiumEstimate
        );
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        if (!IERC20Triangular(token).transfer(to, amount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, amount);
    }

    function _bestRoute(
        CallbackPlan memory plan,
        uint256 amount,
        uint256 owed
    ) private view returns (RouteQuote memory best) {
        RouteQuote memory forward = _quoteRoute(plan, amount, owed, false);
        if (forward.viable) best = forward;
        if (plan.allowReverse) {
            RouteQuote memory reverse = _quoteRoute(plan, amount, owed, true);
            if (reverse.viable && (!best.viable || reverse.profitUsdc > best.profitUsdc)) {
                best = reverse;
            }
        }
    }

    function _quoteRoute(
        CallbackPlan memory plan,
        uint256 amount,
        uint256 owed,
        bool reverse
    ) private view returns (RouteQuote memory quote) {
        address[] memory path = _routePath(plan.tokenX, plan.tokenY, reverse);
        (bool ok, uint256 finalUsdc) = _tryAmountsOut(plan.router, amount, path);
        if (!ok || finalUsdc < owed + plan.minProfitUsdc) return quote;
        uint256 minAfterSlippage = (finalUsdc * (10000 - plan.slippageBps)) / 10000;
        if (minAfterSlippage < owed + plan.minProfitUsdc) return quote;
        quote = RouteQuote({
            viable: true,
            reverse: reverse,
            finalUsdc: finalUsdc,
            profitUsdc: finalUsdc - owed
        });
    }

    function _tryAmountsOut(
        address router,
        uint256 amount,
        address[] memory path
    ) private view returns (bool ok, uint256 finalAmount) {
        (bool success, bytes memory result) = router.staticcall(
            abi.encodeWithSelector(IRouterTriangular.getAmountsOut.selector, amount, path)
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

    function _validateRequest(ArbRequest calldata request) private view {
        if (
            request.tokenX == address(0)
                || request.tokenY == address(0)
                || request.router == address(0)
                || request.tokenX == request.tokenY
                || request.tokenX == usdc
                || request.tokenY == usdc
                || request.amount == 0
                || request.premiumBps > 10000
                || request.deadline < block.timestamp
                || request.slippageBps > 5000
        ) {
            revert InvalidRequest();
        }
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20Triangular(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20Triangular(token).approve(spender, amount)) revert ApproveFailed();
    }

    function _max(uint256 a, uint256 b) private pure returns (uint256) {
        return a > b ? a : b;
    }
}
