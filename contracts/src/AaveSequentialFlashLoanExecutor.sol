// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Minimal {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IAavePoolLike {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;

    function flashLoan(
        address receiverAddress,
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata interestRateModes,
        address onBehalfOf,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IAaveFlashLoanReceiverLike {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);

    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

interface ISwapRouterLike {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

contract AaveSequentialFlashLoanExecutor is IAaveFlashLoanReceiverLike {
    error NotOwner();
    error NotPool();
    error BadInitiator();
    error Paused();
    error InvalidPlan();
    error InvalidStep();
    error InvalidPath();
    error DeadlineExpired();
    error ApproveFailed();
    error TransferFailed();

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event FlashLoanRequested(address indexed asset, address indexed swapToken, uint256 amount);
    event BatchFlashLoanRequested(uint256 assetCount);
    event StepExecuted(
        uint256 indexed index,
        address indexed router,
        address indexed tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOutMin,
        uint256 amountOut
    );
    event FlashLoanExecuted(address indexed asset, uint256 amount, uint256 premium);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    struct SwapStep {
        address router;
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 amountOutMin;
        address[] path;
    }

    struct ExecutionPlan {
        SwapStep[] steps;
        uint256 deadline;
    }

    uint256 public constant USE_FULL_BALANCE = type(uint256).max;

    address public owner;
    address public immutable pool;
    bool public paused;

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

    function requestFlashLoan(
        address asset,
        uint256 amount,
        ExecutionPlan calldata plan
    ) external onlyOwner whenNotPaused {
        _requestPair(asset, address(0), amount, plan);
    }

    function requestPairFlashLoan(
        address borrowToken,
        address swapToken,
        uint256 borrowAmount,
        ExecutionPlan calldata plan
    ) external onlyOwner whenNotPaused {
        _requestPair(borrowToken, swapToken, borrowAmount, plan);
    }

    function requestBatchFlashLoan(
        address[] calldata borrowTokens,
        address[] calldata swapTokens,
        uint256[] calldata borrowAmounts,
        ExecutionPlan calldata plan
    ) external onlyOwner whenNotPaused {
        if (
            borrowTokens.length == 0
                || borrowTokens.length != swapTokens.length
                || borrowTokens.length != borrowAmounts.length
                || plan.deadline < block.timestamp
        ) {
            revert InvalidPlan();
        }

        uint256[] memory interestRateModes = new uint256[](borrowTokens.length);
        for (uint256 i = 0; i < borrowTokens.length; i++) {
            if (borrowTokens[i] == address(0) || swapTokens[i] == address(0) || borrowAmounts[i] == 0) {
                revert InvalidPlan();
            }
            interestRateModes[i] = 0;
        }

        bytes memory params = abi.encode(plan);
        IAavePoolLike(pool).flashLoan(
            address(this),
            borrowTokens,
            borrowAmounts,
            interestRateModes,
            address(this),
            params,
            0
        );
        emit BatchFlashLoanRequested(borrowTokens.length);
    }

    function _requestPair(address asset, address swapToken, uint256 amount, ExecutionPlan calldata plan) private {
        if (asset == address(0) || amount == 0 || plan.deadline < block.timestamp) revert InvalidPlan();
        bytes memory params = abi.encode(plan);
        IAavePoolLike(pool).flashLoanSimple(address(this), asset, amount, params, 0);
        emit FlashLoanRequested(asset, swapToken, amount);
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
        if (asset == address(0) || amount == 0) revert InvalidPlan();

        ExecutionPlan memory plan = abi.decode(params, (ExecutionPlan));
        if (plan.deadline < block.timestamp) revert InvalidPlan();

        uint256 amountOwed = amount + premium;

        for (uint256 i = 0; i < plan.steps.length; i++) {
            _executeStep(i, plan.steps[i], plan.deadline);
        }

        _forceApprove(asset, pool, amountOwed);
        emit FlashLoanExecuted(asset, amount, premium);

        return true;
    }

    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external override whenNotPaused returns (bool) {
        if (msg.sender != pool) revert NotPool();
        if (initiator != address(this)) revert BadInitiator();
        if (assets.length == 0 || assets.length != amounts.length || assets.length != premiums.length) {
            revert InvalidPlan();
        }

        ExecutionPlan memory plan = abi.decode(params, (ExecutionPlan));
        if (plan.deadline < block.timestamp) revert InvalidPlan();

        for (uint256 i = 0; i < plan.steps.length; i++) {
            _executeStep(i, plan.steps[i], plan.deadline);
        }

        for (uint256 i = 0; i < assets.length; i++) {
            if (assets[i] == address(0) || amounts[i] == 0) revert InvalidPlan();
            _forceApprove(assets[i], pool, amounts[i] + premiums[i]);
        }

        emit BatchFlashLoanRequested(assets.length);
        return true;
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidPlan();
        if (!IERC20Minimal(token).transfer(to, amount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, amount);
    }

    function _executeStep(uint256 index, SwapStep memory step, uint256 deadline) private {
        if (step.router == address(0) || step.tokenIn == address(0) || step.tokenOut == address(0)) {
            revert InvalidStep();
        }
        if (step.path.length < 2 || step.path[0] != step.tokenIn || step.path[step.path.length - 1] != step.tokenOut) {
            revert InvalidPath();
        }
        if (deadline < block.timestamp) revert DeadlineExpired();

        uint256 amountIn = step.amountIn == USE_FULL_BALANCE
            ? IERC20Minimal(step.tokenIn).balanceOf(address(this))
            : step.amountIn;
        if (amountIn == 0) revert InvalidStep();

        _forceApprove(step.tokenIn, step.router, amountIn);
        uint256[] memory amounts = ISwapRouterLike(step.router).swapExactTokensForTokens(
            amountIn,
            step.amountOutMin,
            step.path,
            address(this),
            deadline
        );
        _forceApprove(step.tokenIn, step.router, 0);

        emit StepExecuted(
            index,
            step.router,
            step.tokenIn,
            step.tokenOut,
            amountIn,
            step.amountOutMin,
            amounts[amounts.length - 1]
        );
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        if (!IERC20Minimal(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20Minimal(token).approve(spender, amount)) revert ApproveFailed();
    }
}
