// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract MockV3Pool {
    address public factory;
    address public token0;
    address public token1;
    uint24 public fee;
    uint128 public liquidity;
    uint160 private sqrtPriceX96Value;
    int24 private tickValue;

    constructor(
        address factoryAddress,
        address token0Address,
        address token1Address,
        uint24 feeValue,
        uint128 liquidityValue,
        uint160 sqrtPriceX96,
        int24 tick
    ) {
        factory = factoryAddress;
        token0 = token0Address;
        token1 = token1Address;
        fee = feeValue;
        liquidity = liquidityValue;
        sqrtPriceX96Value = sqrtPriceX96;
        tickValue = tick;
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
}
