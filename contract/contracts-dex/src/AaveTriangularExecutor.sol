// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20AaveTriangular {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IAavePoolAaveTriangular {
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IRouterAaveTriangular {
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
    error ExecutionConstraintFailed(
        uint256 failureCode,
        uint256 amountOutMinUsdc,
        uint256 repaymentRequiredUsdc,
        uint256 finalUsdc,
        uint256 actualBalance,
        uint256 requiredBalance
    );
    error RouterSwapFailed(bytes4 selector);
    error RouterSwapResultInvalid(uint256 resultLength);
    error ApproveFailed();
    error TransferFailed();
    error Reentrancy();

    uint256 public constant FAIL_POST_SWAP_BALANCE_BELOW_REPAYMENT = 1;
    uint256 public constant DEFAULT_PROFIT_SWEEP_THRESHOLD_USDC = 100_000_000;

    struct ExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        uint256 amount;
        uint256 deadline;
        uint256 amountOutMinUsdc;
    }

    struct CallbackPlan {
        address tokenX;
        address tokenY;
        address router;
        uint256 deadline;
        uint256 amountOutMinUsdc;
        uint256 startingUsdcBalance;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ControllerSet(address indexed previousController, address indexed newController);
    event PausedSet(bool paused);
    event ProfitSweepThresholdSet(uint256 previousThreshold, uint256 newThreshold);
    event FlashLoanRequested(address indexed controller, uint256 amount);
    event RouteExecuted(
        address indexed controller,
        address indexed router,
        uint256 amount,
        uint256 premium,
        uint256 finalUsdc,
        uint256 profitUsdc
    );
    event ProfitSwept(address indexed recipient, uint256 amount, uint256 threshold);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable pool;
    address public immutable usdc;
    address public owner;
    address public controller;
    bool public paused;
    bool private locked;
    uint256 public profitSweepThresholdUsdc;

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
        profitSweepThresholdUsdc = DEFAULT_PROFIT_SWEEP_THRESHOLD_USDC;
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

    function setProfitSweepThresholdUsdc(uint256 newThreshold) external onlyOwner {
        emit ProfitSweepThresholdSet(profitSweepThresholdUsdc, newThreshold);
        profitSweepThresholdUsdc = newThreshold;
    }

    function flashLoanPremiumBps() external view returns (uint256) {
        return IAavePoolAaveTriangular(pool).FLASHLOAN_PREMIUM_TOTAL();
    }

    function execute(ExecutionRequest calldata request)
        external
        onlyController
        whenNotPaused
        nonReentrantEntry
        returns (uint256 profitSwept)
    {
        _validateRequest(request);
        uint256 startingUsdcBalance = IERC20AaveTriangular(usdc).balanceOf(address(this));
        CallbackPlan memory plan = CallbackPlan({
            tokenX: request.tokenX,
            tokenY: request.tokenY,
            router: request.router,
            deadline: request.deadline,
            amountOutMinUsdc: request.amountOutMinUsdc,
            startingUsdcBalance: startingUsdcBalance
        });

        IAavePoolAaveTriangular(pool).flashLoanSimple(address(this), usdc, request.amount, abi.encode(plan), 0);
        emit FlashLoanRequested(msg.sender, request.amount);

        uint256 endingUsdcBalance = IERC20AaveTriangular(usdc).balanceOf(address(this));
        uint256 threshold = profitSweepThresholdUsdc;
        if (endingUsdcBalance > threshold) {
            profitSwept = endingUsdcBalance;
            if (!IERC20AaveTriangular(usdc).transfer(owner, profitSwept)) revert TransferFailed();
            emit ProfitSwept(owner, profitSwept, threshold);
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

        _forceApprove(usdc, plan.router, amount);
        uint256[] memory amounts;
        try IRouterAaveTriangular(plan.router).swapExactTokensForTokens(
            amount,
            plan.amountOutMinUsdc,
            path,
            address(this),
            plan.deadline
        ) returns (uint256[] memory result) {
            amounts = result;
        } catch (bytes memory reason) {
            revert RouterSwapFailed(_revertSelector(reason));
        }
        if (amounts.length != path.length) revert RouterSwapResultInvalid(amounts.length);
        _forceApprove(usdc, plan.router, 0);

        uint256 finalUsdc = amounts[amounts.length - 1];
        uint256 actualBalance = IERC20AaveTriangular(usdc).balanceOf(address(this));
        uint256 requiredBalance = plan.startingUsdcBalance + owed;
        if (actualBalance < requiredBalance) {
            revert ExecutionConstraintFailed(
                FAIL_POST_SWAP_BALANCE_BELOW_REPAYMENT,
                plan.amountOutMinUsdc,
                owed,
                finalUsdc,
                actualBalance,
                requiredBalance
            );
        }

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

    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20AaveTriangular(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20AaveTriangular(token).approve(spender, amount)) revert ApproveFailed();
    }

    function _revertSelector(bytes memory reason) private pure returns (bytes4 selector) {
        if (reason.length < 4) return bytes4(0);
        assembly {
            selector := mload(add(reason, 32))
        }
    }
}
