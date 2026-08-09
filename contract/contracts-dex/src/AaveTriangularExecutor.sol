// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20AaveTriangular {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IAavePoolAaveTriangular {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IRouterAaveTriangular {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface IFlashLoanSimpleReceiverAaveTriangular {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract AaveTriangularExecutor is IFlashLoanSimpleReceiverAaveTriangular {
    error NotOwner();
    error NotController();
    error NotPool();
    error BadInitiator();
    error Paused();
    error InvalidRequest();
    error NoViableRoute();
    error ApproveFailed();
    error TransferFailed();
    error Reentrancy();

    struct ExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        uint256 amount;
        uint256 minProfitUsdc;
        uint256 deadline;
        uint256 slippageBps;
    }

    struct CallbackPlan {
        address tokenX;
        address tokenY;
        address router;
        uint256 minProfitUsdc;
        uint256 deadline;
        uint256 slippageBps;
        uint256 startingUsdcBalance;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ControllerSet(address indexed previousController, address indexed newController);
    event PausedSet(bool paused);
    event FlashLoanRequested(address indexed controller, uint256 amount);
    event RouteExecuted(
        address indexed controller,
        address indexed router,
        uint256 amount,
        uint256 premium,
        uint256 finalUsdc,
        uint256 profitUsdc
    );
    event ProfitReturned(address indexed controller, uint256 amount);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable pool;
    address public immutable usdc;
    address public owner;
    address public controller;
    bool public paused;
    bool private locked;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyController() {
        if (msg.sender != controller) revert NotController();
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

    function setController(address newController) external onlyOwner {
        if (newController == address(0)) revert InvalidRequest();
        emit ControllerSet(controller, newController);
        controller = newController;
    }

    function setPaused(bool value) external onlyOwner {
        paused = value;
        emit PausedSet(value);
    }

    function execute(ExecutionRequest calldata request)
        external
        onlyController
        whenNotPaused
        nonReentrantEntry
        returns (uint256 profitReturned)
    {
        _validateRequest(request);
        uint256 startingUsdcBalance = IERC20AaveTriangular(usdc).balanceOf(address(this));
        CallbackPlan memory plan = CallbackPlan({
            tokenX: request.tokenX,
            tokenY: request.tokenY,
            router: request.router,
            minProfitUsdc: request.minProfitUsdc,
            deadline: request.deadline,
            slippageBps: request.slippageBps,
            startingUsdcBalance: startingUsdcBalance
        });

        IAavePoolAaveTriangular(pool).flashLoanSimple(address(this), usdc, request.amount, abi.encode(plan), 0);
        emit FlashLoanRequested(msg.sender, request.amount);

        uint256 endingUsdcBalance = IERC20AaveTriangular(usdc).balanceOf(address(this));
        if (endingUsdcBalance > startingUsdcBalance) {
            profitReturned = endingUsdcBalance - startingUsdcBalance;
            if (!IERC20AaveTriangular(usdc).transfer(controller, profitReturned)) revert TransferFailed();
            emit ProfitReturned(controller, profitReturned);
        }
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
        _validateTokens(plan.tokenX, plan.tokenY);
        address[] memory path = _routePath(plan.tokenX, plan.tokenY);

        uint256 owed = amount + premium;
        uint256 quotedFinal = _amountOut(plan.router, amount, path);
        uint256 requiredFinal = owed + plan.minProfitUsdc;
        uint256 minAfterSlippage = (quotedFinal * (10000 - plan.slippageBps)) / 10000;
        if (quotedFinal < requiredFinal || minAfterSlippage < requiredFinal) revert NoViableRoute();

        _forceApprove(usdc, plan.router, amount);
        uint256[] memory amounts = IRouterAaveTriangular(plan.router).swapExactTokensForTokens(
            amount,
            _max(requiredFinal, minAfterSlippage),
            path,
            address(this),
            plan.deadline
        );
        _forceApprove(usdc, plan.router, 0);

        uint256 finalUsdc = amounts[amounts.length - 1];
        uint256 actualBalance = IERC20AaveTriangular(usdc).balanceOf(address(this));
        uint256 requiredBalance = plan.startingUsdcBalance + requiredFinal;
        if (actualBalance < requiredBalance) revert NoViableRoute();

        _forceApprove(usdc, pool, owed);
        emit RouteExecuted(controller, plan.router, amount, premium, finalUsdc, actualBalance - plan.startingUsdcBalance - owed);
        return true;
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        if (!IERC20AaveTriangular(token).transfer(to, amount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, amount);
    }

    function _validateRequest(ExecutionRequest calldata request) private view {
        if (
            request.tokenX == address(0)
                || request.tokenY == address(0)
                || request.tokenX == request.tokenY
                || request.tokenX == usdc
                || request.tokenY == usdc
                || request.router == address(0)
                || request.amount == 0
                || request.deadline < block.timestamp
                || request.slippageBps > 5000
        ) {
            revert InvalidRequest();
        }
    }

    function _validateTokens(address tokenX, address tokenY) private view {
        if (
            tokenX == address(0)
                || tokenY == address(0)
                || tokenX == tokenY
                || tokenX == usdc
                || tokenY == usdc
        ) {
            revert InvalidRequest();
        }
    }

    function _routePath(address tokenX, address tokenY) private view returns (address[] memory path) {
        _validateTokens(tokenX, tokenY);
        path = new address[](4);
        path[0] = usdc;
        path[1] = tokenX;
        path[2] = tokenY;
        path[3] = usdc;
    }

    function _amountOut(address router, uint256 amount, address[] memory path) private view returns (uint256) {
        uint256[] memory amounts = IRouterAaveTriangular(router).getAmountsOut(amount, path);
        return amounts[amounts.length - 1];
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20AaveTriangular(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20AaveTriangular(token).approve(spender, amount)) revert ApproveFailed();
    }

    function _max(uint256 a, uint256 b) private pure returns (uint256) {
        return a > b ? a : b;
    }
}
