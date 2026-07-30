// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function approve(address spender, uint256 value) external returns (bool);
}

interface IJoeRouterLike {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

contract MockFundedExecutor {
    error NotOwner();
    error Paused();
    error InvalidStep();
    error InvalidPath();
    error DeadlineExpired();
    error TransferFailed();
    error ApproveFailed();
    error ProfitTooLow(uint256 actualProfit, uint256 minProfit);

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event StepExecuted(
        uint256 indexed index,
        address indexed router,
        address indexed tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOutMin,
        uint256 amountOut
    );
    event PlanExecuted(address indexed profitToken, uint256 startBalance, uint256 endBalance, uint256 profit);
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    struct SwapStep {
        address router;
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 amountOutMin;
        address[] path;
    }

    uint256 public constant USE_FULL_BALANCE = type(uint256).max;

    address public owner;
    bool public paused;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert Paused();
        _;
    }

    constructor(address initialOwner) {
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
        emit OwnershipTransferred(address(0), owner);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidStep();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function setPaused(bool value) external onlyOwner {
        paused = value;
        emit PausedSet(value);
    }

    function executePlan(
        SwapStep[] calldata steps,
        address profitToken,
        uint256 minProfit,
        uint256 deadline
    ) external onlyOwner whenNotPaused returns (uint256 profit) {
        if (deadline < block.timestamp) revert DeadlineExpired();
        if (steps.length == 0 || profitToken == address(0)) revert InvalidStep();

        uint256 startBalance = IERC20(profitToken).balanceOf(address(this));

        for (uint256 i = 0; i < steps.length; i++) {
            _executeStep(i, steps[i], deadline);
        }

        uint256 endBalance = IERC20(profitToken).balanceOf(address(this));
        profit = endBalance > startBalance ? endBalance - startBalance : 0;
        if (profit < minProfit) revert ProfitTooLow(profit, minProfit);

        emit PlanExecuted(profitToken, startBalance, endBalance, profit);
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidStep();
        if (!IERC20(token).transfer(to, amount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, amount);
    }

    function _executeStep(uint256 index, SwapStep calldata step, uint256 deadline) private {
        if (step.router == address(0) || step.tokenIn == address(0) || step.tokenOut == address(0)) {
            revert InvalidStep();
        }
        if (step.path.length < 2 || step.path[0] != step.tokenIn || step.path[step.path.length - 1] != step.tokenOut) {
            revert InvalidPath();
        }

        uint256 amountIn = step.amountIn == USE_FULL_BALANCE
            ? IERC20(step.tokenIn).balanceOf(address(this))
            : step.amountIn;
        if (amountIn == 0) revert InvalidStep();

        _forceApprove(step.tokenIn, step.router, amountIn);
        uint256[] memory amounts = IJoeRouterLike(step.router).swapExactTokensForTokens(
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
        if (!IERC20(token).approve(spender, 0)) revert ApproveFailed();
        if (amount > 0 && !IERC20(token).approve(spender, amount)) revert ApproveFailed();
    }
}
