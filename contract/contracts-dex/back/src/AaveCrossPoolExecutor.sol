// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20CrossPool {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IAavePoolCrossPool {
    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IRouterCrossPoolLike {
    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut);
}

interface IFlashLoanSimpleReceiverCrossPool {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract AaveCrossPoolExecutor is IFlashLoanSimpleReceiverCrossPool {
    error NotOwner();
    error NotController();
    error NotPool();
    error BadInitiator();
    error Paused();
    error InvalidRequest();
    error ExecutionConstraintFailed(
        uint256 minFinalTokenX,
        uint256 repaymentRequiredTokenX,
        uint256 finalTokenX,
        uint256 actualBalance,
        uint256 requiredBalance
    );
    error RouterSwapFailed(bytes4 selector);
    error SwapPathInvalid(uint256 pathLength);
    error ApproveFailed();
    error TransferFailed();
    error Reentrancy();

    struct CrossPoolExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        bytes swapPath;
        address buyPool;
        address sellPool;
        uint256 amount;
        uint256 deadline;
        uint256 minFinalTokenX;
    }

    struct CallbackPlan {
        address tokenX;
        address tokenY;
        address router;
        bytes swapPath;
        address buyPool;
        address sellPool;
        uint256 deadline;
        uint256 minFinalTokenX;
        uint256 startingTokenXBalance;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ControllerSet(address indexed previousController, address indexed newController);
    event PausedSet(bool paused);
    event FlashLoanRequested(address indexed controller, address indexed tokenX, uint256 amount);
    event CrossPoolRouteExecuted(
        address indexed controller,
        address indexed buyPool,
        address indexed sellPool,
        uint256 amount,
        uint256 premium,
        uint256 finalTokenX,
        uint256 profitTokenX
    );
    event ProfitSwept(address indexed recipient, address indexed token, uint256 amount);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable pool;
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

    constructor(address poolAddress, address initialOwner) {
        if (poolAddress == address(0)) revert InvalidRequest();
        pool = poolAddress;
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

    function flashLoanPremiumBps() external view returns (uint256) {
        return IAavePoolCrossPool(pool).FLASHLOAN_PREMIUM_TOTAL();
    }

    function execute(CrossPoolExecutionRequest calldata request)
        external
        onlyController
        whenNotPaused
        nonReentrantEntry
        returns (uint256 profitSwept)
    {
        _validateRequest(request);
        uint256 startingTokenXBalance = IERC20CrossPool(request.tokenX).balanceOf(address(this));
        CallbackPlan memory plan = CallbackPlan({
            tokenX: request.tokenX,
            tokenY: request.tokenY,
            router: request.router,
            swapPath: request.swapPath,
            buyPool: request.buyPool,
            sellPool: request.sellPool,
            deadline: request.deadline,
            minFinalTokenX: request.minFinalTokenX,
            startingTokenXBalance: startingTokenXBalance
        });

        IAavePoolCrossPool(pool).flashLoanSimple(address(this), request.tokenX, request.amount, abi.encode(plan), 0);
        emit FlashLoanRequested(msg.sender, request.tokenX, request.amount);

        uint256 endingTokenXBalance = IERC20CrossPool(request.tokenX).balanceOf(address(this));
        if (endingTokenXBalance > startingTokenXBalance) {
            profitSwept = endingTokenXBalance - startingTokenXBalance;
            if (!IERC20CrossPool(request.tokenX).transfer(owner, profitSwept)) revert TransferFailed();
            emit ProfitSwept(owner, request.tokenX, profitSwept);
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
        if (asset == address(0) || amount == 0) revert InvalidRequest();

        CallbackPlan memory plan = abi.decode(params, (CallbackPlan));
        if (asset != plan.tokenX || plan.deadline < block.timestamp) revert InvalidRequest();
        _validateTokens(plan.tokenX, plan.tokenY);
        _validateV3CrossPoolPath(plan.swapPath, plan.tokenX, plan.tokenY);

        uint256 owed = amount + premium;

        _forceApprove(plan.tokenX, plan.router, amount);
        uint256 finalTokenX;
        try IRouterCrossPoolLike(plan.router).exactInput(
            IRouterCrossPoolLike.ExactInputParams({
                path: plan.swapPath,
                recipient: address(this),
                deadline: plan.deadline,
                amountIn: amount,
                amountOutMinimum: plan.minFinalTokenX
            })
        ) returns (uint256 result) {
            finalTokenX = result;
        } catch (bytes memory reason) {
            revert RouterSwapFailed(_revertSelector(reason));
        }
        _forceApprove(plan.tokenX, plan.router, 0);

        uint256 actualBalance = IERC20CrossPool(plan.tokenX).balanceOf(address(this));
        uint256 requiredBalance = plan.startingTokenXBalance + owed;
        if (actualBalance < requiredBalance || finalTokenX < plan.minFinalTokenX) {
            revert ExecutionConstraintFailed(plan.minFinalTokenX, owed, finalTokenX, actualBalance, requiredBalance);
        }

        _forceApprove(plan.tokenX, pool, owed);
        emit CrossPoolRouteExecuted(
            controller,
            plan.buyPool,
            plan.sellPool,
            amount,
            premium,
            finalTokenX,
            actualBalance - plan.startingTokenXBalance - owed
        );
        return true;
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        if (!IERC20CrossPool(token).transfer(to, amount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, amount);
    }

    function _validateRequest(CrossPoolExecutionRequest calldata request) private view {
        if (
            request.tokenX == address(0)
                || request.tokenY == address(0)
                || request.tokenX == request.tokenY
                || request.router == address(0)
                || request.swapPath.length == 0
                || request.buyPool == address(0)
                || request.sellPool == address(0)
                || request.buyPool == request.sellPool
                || request.amount == 0
                || request.minFinalTokenX == 0
                || request.deadline < block.timestamp
        ) {
            revert InvalidRequest();
        }
    }

    function _validateTokens(address tokenX, address tokenY) private pure {
        if (tokenX == address(0) || tokenY == address(0) || tokenX == tokenY) revert InvalidRequest();
    }

    function _validateV3CrossPoolPath(bytes memory path, address tokenX, address tokenY) private pure {
        if (path.length != 66) revert SwapPathInvalid(path.length);
        if (
            _pathAddress(path, 0) != tokenX
                || _pathAddress(path, 23) != tokenY
                || _pathAddress(path, 46) != tokenX
                || _pathFee(path, 20) == 0
                || _pathFee(path, 43) == 0
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
        if (!IERC20CrossPool(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20CrossPool(token).approve(spender, amount)) revert ApproveFailed();
    }

    function _revertSelector(bytes memory reason) private pure returns (bytes4 selector) {
        if (reason.length < 4) return bytes4(0);
        assembly {
            selector := mload(add(reason, 32))
        }
    }
}
