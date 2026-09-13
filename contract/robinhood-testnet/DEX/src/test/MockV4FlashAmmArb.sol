// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20MockV4AmmArb {
    function approve(address spender, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IPoolManagerMockV4AmmArb {
    function unlock(bytes calldata data) external returns (bytes memory);
    function take(address currency, address to, uint256 amount) external;
    function sync(address currency) external;
    function settle() external payable returns (uint256);
}

interface ISimpleAmmMockV4AmmArb {
    function swap(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOutMin
    ) external returns (uint256 amountOut);
}

contract MockV4FlashAmmArb {
    error BadPoolManagerCallback();
    error InvalidRequest();
    error ApproveFailed();
    error TransferFailed();
    error ArbitrageNotProfitable(uint256 received, uint256 required);
    error Reentrancy();

    IPoolManagerMockV4AmmArb public immutable poolManager;
    bool private locked;

    modifier nonReentrant() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(address poolManager_) {
        if (poolManager_ == address(0)) revert InvalidRequest();
        poolManager = IPoolManagerMockV4AmmArb(poolManager_);
    }

    receive() external payable {}

    function runArbitrage(
        address tokenZ,
        address tokenA,
        address tokenB,
        uint256 amountIn,
        uint256 minProfit
    )
        external
        nonReentrant
        returns (uint256 outTb, uint256 outTa, uint256 outTz, uint256 profit)
    {
        if (tokenZ == address(0) || tokenA == address(0) || tokenB == address(0) || amountIn == 0) {
            revert InvalidRequest();
        }
        bytes memory result = poolManager.unlock(abi.encode(tokenZ, tokenA, tokenB, amountIn, minProfit));
        (outTb, outTa, outTz, profit) = abi.decode(result, (uint256, uint256, uint256, uint256));
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert BadPoolManagerCallback();
        (
            address tokenZ,
            address tokenA,
            address tokenB,
            uint256 amountIn,
            uint256 minProfit
        ) = abi.decode(data, (address, address, address, uint256, uint256));
        if (amountIn == 0) revert InvalidRequest();

        poolManager.take(tokenZ, address(this), amountIn);
        _approve(tokenZ);
        _approve(tokenA);
        _approve(tokenB);

        ISimpleAmmMockV4AmmArb amm = ISimpleAmmMockV4AmmArb(address(poolManager));
        uint256 outTb = amm.swap(tokenZ, tokenB, amountIn, 0);
        uint256 outTa = amm.swap(tokenB, tokenA, outTb, 0);
        uint256 outTz = amm.swap(tokenA, tokenZ, outTa, 0);

        uint256 required = amountIn + minProfit;
        if (outTz < required) revert ArbitrageNotProfitable(outTz, required);

        uint256 repay = amountIn;
        poolManager.sync(tokenZ);
        if (!IERC20MockV4AmmArb(tokenZ).transfer(address(poolManager), repay)) revert TransferFailed();
        poolManager.settle();

        return abi.encode(outTb, outTa, outTz, outTz - amountIn);
    }

    function _approve(address token) private {
        if (!IERC20MockV4AmmArb(token).approve(address(poolManager), type(uint256).max)) revert ApproveFailed();
    }
}
