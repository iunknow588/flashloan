// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface ITestERC20Like {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract MockSwapRouter {
    error NotOwner();
    error InvalidPath();
    error DeadlineExpired();
    error RateNotSet();
    error AmountTooLarge();
    error AmountOutTooLow(uint256 amountOut, uint256 amountOutMin);
    error TransferFailed();

    event RateSet(address indexed tokenIn, address indexed tokenOut, uint256 numerator, uint256 denominator);
    event Swap(
        address indexed sender,
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut
    );

    struct Rate {
        uint256 numerator;
        uint256 denominator;
    }

    address public owner;
    mapping(address => mapping(address => Rate)) public rates;
    mapping(address => mapping(address => uint256)) public maxAmountIn;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address initialOwner) {
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
    }

    function setRate(address tokenIn, address tokenOut, uint256 numerator, uint256 denominator) external onlyOwner {
        if (tokenIn == address(0) || tokenOut == address(0) || numerator == 0 || denominator == 0) {
            revert RateNotSet();
        }
        rates[tokenIn][tokenOut] = Rate(numerator, denominator);
        emit RateSet(tokenIn, tokenOut, numerator, denominator);
    }

    function setMaxAmountIn(address tokenIn, address tokenOut, uint256 amount) external onlyOwner {
        maxAmountIn[tokenIn][tokenOut] = amount;
    }

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts) {
        if (deadline < block.timestamp) revert DeadlineExpired();
        if (path.length < 2 || to == address(0)) revert InvalidPath();

        amounts = getAmountsOut(amountIn, path);
        address tokenIn = path[0];
        address tokenOut = path[path.length - 1];
        uint256 amountOut = amounts[amounts.length - 1];
        if (amountOut < amountOutMin) revert AmountOutTooLow(amountOut, amountOutMin);

        if (!ITestERC20Like(tokenIn).transferFrom(msg.sender, address(this), amountIn)) revert TransferFailed();
        if (!ITestERC20Like(tokenOut).transfer(to, amountOut)) revert TransferFailed();

        amounts[path.length - 1] = amountOut;
        emit Swap(msg.sender, tokenIn, tokenOut, amountIn, amountOut);
    }

    function getAmountsOut(uint256 amountIn, address[] memory path) public view returns (uint256[] memory amounts) {
        if (path.length < 2) revert InvalidPath();
        amounts = new uint256[](path.length);
        amounts[0] = amountIn;
        for (uint256 i = 0; i < path.length - 1; i++) {
            Rate memory rate = rates[path[i]][path[i + 1]];
            if (rate.numerator == 0 || rate.denominator == 0) revert RateNotSet();
            uint256 maxIn = maxAmountIn[path[i]][path[i + 1]];
            if (maxIn != 0 && amounts[i] > maxIn) revert AmountTooLarge();
            amounts[i + 1] = (amounts[i] * rate.numerator) / rate.denominator;
        }
    }

    function getAmountsIn(uint256 amountOut, address[] memory path) public view returns (uint256[] memory amounts) {
        if (path.length < 2) revert InvalidPath();
        amounts = new uint256[](path.length);
        amounts[path.length - 1] = amountOut;
        for (uint256 i = path.length - 1; i > 0; i--) {
            Rate memory rate = rates[path[i - 1]][path[i]];
            if (rate.numerator == 0 || rate.denominator == 0) revert RateNotSet();
            amounts[i - 1] = (amounts[i] * rate.denominator + rate.numerator - 1) / rate.numerator;
            uint256 maxIn = maxAmountIn[path[i - 1]][path[i]];
            if (maxIn != 0 && amounts[i - 1] > maxIn) revert AmountTooLarge();
        }
    }
}
