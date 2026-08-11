// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface ITestV3PoolTokenLike {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract MockV3Pool {
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
    address public factory;
    address public token0;
    address public token1;
    uint24 public fee;
    uint128 public liquidity;
    uint160 private sqrtPriceX96Value;
    int24 private tickValue;
    mapping(address => mapping(address => Rate)) public rates;
    mapping(address => mapping(address => uint256)) public maxAmountIn;
    mapping(address => mapping(address => uint256)) public impactBpsPerUnit;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(
        address factoryAddress,
        address token0Address,
        address token1Address,
        uint24 feeValue,
        uint128 liquidityValue,
        uint160 sqrtPriceX96,
        int24 tick
    ) {
        owner = factoryAddress;
        factory = factoryAddress;
        token0 = token0Address;
        token1 = token1Address;
        fee = feeValue;
        liquidity = liquidityValue;
        sqrtPriceX96Value = sqrtPriceX96;
        tickValue = tick;
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

    function setSlot0(uint160 sqrtPriceX96, int24 tick) external {
        sqrtPriceX96Value = sqrtPriceX96;
        tickValue = tick;
    }

    function setLiquidity(uint128 value) external {
        liquidity = value;
    }

    function slot0()
        external
        view
        returns (
            uint160 sqrtPriceX96,
            int24 tick,
            uint16 observationIndex,
            uint16 observationCardinality,
            uint16 observationCardinalityNext,
            uint8 feeProtocol,
            bool unlocked
        )
    {
        sqrtPriceX96 = sqrtPriceX96Value;
        tick = tickValue;
        observationIndex = 0;
        observationCardinality = 1;
        observationCardinalityNext = 1;
        feeProtocol = 0;
        unlocked = true;
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
            uint256 amountOut = (amounts[i] * rate.numerator) / rate.denominator;
            uint256 impactBps = (amounts[i] * impactBpsPerUnit[path[i]][path[i + 1]]) / 1_000_000;
            if (impactBps >= 10000) revert AmountTooLarge();
            amounts[i + 1] = (amountOut * (10000 - impactBps)) / 10000;
        }
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

        if (!ITestV3PoolTokenLike(tokenIn).transferFrom(msg.sender, address(this), amountIn)) revert TransferFailed();
        if (!ITestV3PoolTokenLike(tokenOut).transfer(to, amountOut)) revert TransferFailed();

        amounts[path.length - 1] = amountOut;
        emit Swap(msg.sender, tokenIn, tokenOut, amountIn, amountOut);
    }
}
