// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface ITestERC20V3RouterLike {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract MockV3SwapRouter {
    error NotOwner();
    error InvalidPath();
    error DeadlineExpired();
    error RateNotSet();
    error AmountTooLarge();
    error AmountOutTooLow(uint256 amountOut, uint256 amountOutMin);
    error TransferFailed();

    event RateSet(address indexed tokenIn, address indexed tokenOut, uint256 numerator, uint256 denominator);
    event ExactInputSwap(
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

    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }

    address public owner;
    mapping(address => mapping(address => Rate)) public rates;
    mapping(address => mapping(address => uint256)) public maxAmountIn;
    mapping(address => mapping(address => uint256)) public impactBpsPerUnit;
    mapping(address => mapping(address => uint256)) public quoteMinGas;

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

    function setImpactBpsPerUnit(address tokenIn, address tokenOut, uint256 impactBps) external onlyOwner {
        impactBpsPerUnit[tokenIn][tokenOut] = impactBps;
    }

    function setQuoteMinGas(address tokenIn, address tokenOut, uint256 minGas) external onlyOwner {
        quoteMinGas[tokenIn][tokenOut] = minGas;
    }

    function quoteExactInput(bytes calldata path, uint256 amountIn)
        external
        view
        returns (uint256 amountOut, uint160[] memory, uint32[] memory, uint256 gasEstimate)
    {
        address[] memory tokens = _decodeTokenPath(path);
        amountOut = amountIn;
        for (uint256 i = 0; i < tokens.length - 1; i++) {
            amountOut = _quoteHop(tokens[i], tokens[i + 1], amountOut);
        }
        return (amountOut, new uint160[](0), new uint32[](0), 0);
    }

    function exactInput(ExactInputParams calldata params) external returns (uint256 amountOut) {
        if (params.recipient == address(0)) revert InvalidPath();

        address[] memory tokens = _decodeTokenPath(params.path);
        amountOut = params.amountIn;
        for (uint256 i = 0; i < tokens.length - 1; i++) {
            amountOut = _quoteHop(tokens[i], tokens[i + 1], amountOut);
        }
        if (amountOut < params.amountOutMinimum) revert AmountOutTooLow(amountOut, params.amountOutMinimum);

        if (!ITestERC20V3RouterLike(tokens[0]).transferFrom(msg.sender, address(this), params.amountIn)) {
            revert TransferFailed();
        }
        if (!ITestERC20V3RouterLike(tokens[tokens.length - 1]).transfer(params.recipient, amountOut)) {
            revert TransferFailed();
        }

        emit ExactInputSwap(msg.sender, tokens[0], tokens[tokens.length - 1], params.amountIn, amountOut);
    }

    function _quoteHop(address tokenIn, address tokenOut, uint256 amountIn) private view returns (uint256 amountOut) {
        uint256 minGas = quoteMinGas[tokenIn][tokenOut];
        if (minGas != 0 && gasleft() < minGas) revert RateNotSet();
        Rate memory rate = rates[tokenIn][tokenOut];
        if (rate.numerator == 0 || rate.denominator == 0) revert RateNotSet();
        uint256 maxIn = maxAmountIn[tokenIn][tokenOut];
        if (maxIn != 0 && amountIn > maxIn) revert AmountTooLarge();
        amountOut = (amountIn * rate.numerator) / rate.denominator;
        uint256 impactBps = (amountIn * impactBpsPerUnit[tokenIn][tokenOut]) / 1_000_000;
        if (impactBps >= 10000) revert AmountTooLarge();
        amountOut = (amountOut * (10000 - impactBps)) / 10000;
    }

    function _decodeTokenPath(bytes calldata path) private pure returns (address[] memory tokens) {
        if (path.length < 43 || (path.length - 20) % 23 != 0) revert InvalidPath();
        uint256 tokenCount = ((path.length - 20) / 23) + 1;
        tokens = new address[](tokenCount);
        for (uint256 i = 0; i < tokenCount; i++) {
            tokens[i] = _pathAddress(path, i * 23);
        }
    }

    function _pathAddress(bytes calldata path, uint256 offset) private pure returns (address token) {
        assembly {
            token := shr(96, calldataload(add(path.offset, offset)))
        }
    }
}
