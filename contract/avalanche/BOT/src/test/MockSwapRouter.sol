// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20RouterToken {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract MockSwapRouter {
    error InvalidPath();
    error DeadlineExpired();
    error RateNotSet();
    error AmountOutTooLow(uint256 amountOut, uint256 amountOutMin);
    error TransferFailed();

    struct Rate {
        uint256 numerator;
        uint256 denominator;
    }

    mapping(address => mapping(address => Rate)) public rates;

    function setRate(address tokenIn, address tokenOut, uint256 numerator, uint256 denominator) external {
        if (tokenIn == address(0) || tokenOut == address(0) || numerator == 0 || denominator == 0) {
            revert RateNotSet();
        }
        rates[tokenIn][tokenOut] = Rate(numerator, denominator);
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

        amounts = new uint256[](path.length);
        amounts[0] = amountIn;
        for (uint256 i = 0; i < path.length - 1; i++) {
            Rate memory rate = rates[path[i]][path[i + 1]];
            if (rate.numerator == 0 || rate.denominator == 0) revert RateNotSet();
            amounts[i + 1] = (amounts[i] * rate.numerator) / rate.denominator;
        }

        uint256 amountOut = amounts[path.length - 1];
        if (amountOut < amountOutMin) revert AmountOutTooLow(amountOut, amountOutMin);
        if (!IERC20RouterToken(path[0]).transferFrom(msg.sender, address(this), amountIn)) revert TransferFailed();
        if (!IERC20RouterToken(path[path.length - 1]).transfer(to, amountOut)) revert TransferFailed();
    }
}
