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
    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);
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
    error SwapPathInvalid(uint256 pathLength);
    error ApproveFailed();
    error TransferFailed();
    error Reentrancy();

    uint256 public constant FAIL_POST_SWAP_BALANCE_BELOW_REPAYMENT = 1;
    uint256 public constant DEFAULT_PROFIT_SWEEP_THRESHOLD_USDC = 0;
    uint256 public constant DEFAULT_PROFIT_RESERVE_USDC = 0;

    struct ExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        bytes swapPath;
        uint256 amount;
        uint256 deadline;
        uint256 amountOutMinUsdc;
    }

    struct CallbackPlan {
        address tokenX;
        address tokenY;
        address router;
        bytes swapPath;
        uint256 deadline;
        uint256 amountOutMinUsdc;
        uint256 startingUsdcBalance;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ControllerSet(address indexed previousController, address indexed newController);
    event PausedSet(bool paused);
    event ProfitSweepEnabledSet(bool previousValue, bool newValue);
    event ProfitSweepThresholdSet(uint256 previousThreshold, uint256 newThreshold);
    event ProfitReserveSet(uint256 previousReserve, uint256 newReserve);
    event FlashLoanRequested(address indexed controller, uint256 amount);
    event RouteExecuted(
        address indexed controller,
        address indexed router,
        uint256 amount,
        uint256 premium,
        uint256 finalUsdc,
        uint256 profitUsdc
    );
    event ProfitSwept(address indexed recipient, uint256 amount, uint256 reserveUsdc);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable pool;
    address public immutable usdc;
    address public owner;
    address public controller;
    bool public paused;
    bool private locked;
    bool public profitSweepEnabled;
    uint256 public profitSweepThresholdUsdc;
    uint256 public profitReserveUsdc;

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
        profitSweepEnabled = true;
        profitSweepThresholdUsdc = DEFAULT_PROFIT_SWEEP_THRESHOLD_USDC;
        profitReserveUsdc = DEFAULT_PROFIT_RESERVE_USDC;
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

    function setProfitSweepEnabled(bool value) external onlyOwner {
        emit ProfitSweepEnabledSet(profitSweepEnabled, value);
        profitSweepEnabled = value;
    }

    function setProfitSweepThresholdUsdc(uint256 newThreshold) external onlyOwner {
        emit ProfitSweepThresholdSet(profitSweepThresholdUsdc, newThreshold);
        profitSweepThresholdUsdc = newThreshold;
    }

    function setProfitReserveUsdc(uint256 newReserve) external onlyOwner {
        emit ProfitReserveSet(profitReserveUsdc, newReserve);
        profitReserveUsdc = newReserve;
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
            swapPath: request.swapPath,
            deadline: request.deadline,
            amountOutMinUsdc: request.amountOutMinUsdc,
            startingUsdcBalance: startingUsdcBalance
        });

        IAavePoolAaveTriangular(pool).flashLoanSimple(address(this), usdc, request.amount, abi.encode(plan), 0);
        emit FlashLoanRequested(msg.sender, request.amount);

        uint256 endingUsdcBalance = IERC20AaveTriangular(usdc).balanceOf(address(this));
        uint256 reserve = profitReserveUsdc;
        if (profitSweepEnabled && endingUsdcBalance > reserve) {
            profitSwept = endingUsdcBalance - reserve;
            if (profitSwept >= profitSweepThresholdUsdc) {
                if (!IERC20AaveTriangular(usdc).transfer(owner, profitSwept)) revert TransferFailed();
                emit ProfitSwept(owner, profitSwept, reserve);
            } else {
                profitSwept = 0;
            }
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
        _validateV3TriangularPath(plan.swapPath, plan.tokenX, plan.tokenY);

        uint256 owed = amount + premium;

        _forceApprove(usdc, plan.router, amount);
        uint256 finalUsdc;
        try IRouterAaveTriangular(plan.router).exactInput(
            IRouterAaveTriangular.ExactInputParams({
                path: plan.swapPath,
                recipient: address(this),
                deadline: plan.deadline,
                amountIn: amount,
                amountOutMinimum: plan.amountOutMinUsdc
            })
        ) returns (uint256 result) {
            finalUsdc = result;
        } catch (bytes memory reason) {
            revert RouterSwapFailed(_revertSelector(reason));
        }
        _forceApprove(usdc, plan.router, 0);

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
                || request.swapPath.length == 0
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

    function _validateV3TriangularPath(bytes memory path, address tokenX, address tokenY) private view {
        if (path.length != 89) revert SwapPathInvalid(path.length);
        if (
            _pathAddress(path, 0) != usdc
                || _pathAddress(path, 23) != tokenX
                || _pathAddress(path, 46) != tokenY
                || _pathAddress(path, 69) != usdc
                || _pathFee(path, 20) == 0
                || _pathFee(path, 43) == 0
                || _pathFee(path, 66) == 0
        ) {
            revert InvalidRequest();
        }
    }

    function _pathAddress(bytes memory path, uint256 offset) private pure returns (address token) {
        assembly {
            token := shr(96, mload(add(add(path, 32), offset)))
        }
    }

    function _pathFee(bytes memory path, uint256 offset) private pure returns (uint24 feeValue) {
        assembly {
            feeValue := shr(232, mload(add(add(path, 32), offset)))
        }
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
