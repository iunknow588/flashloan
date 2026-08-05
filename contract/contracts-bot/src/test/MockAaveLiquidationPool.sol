// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20PoolToken {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract MockAaveLiquidationPool {
    error TransferFailed();
    error CallbackFailed();
    error LiquidationNotConfigured();
    error NotRepaid(uint256 expectedBalance, uint256 actualBalance);

    struct LiquidationQuote {
        uint256 collateralToSeize;
        bool configured;
    }

    uint256 public premiumBps;
    mapping(address => mapping(address => mapping(address => LiquidationQuote))) public quotes;

    event FlashLoan(address indexed receiver, address indexed asset, uint256 amount, uint256 premium);
    event LiquidationCall(
        address indexed liquidator,
        address indexed user,
        address indexed collateralAsset,
        address debtAsset,
        uint256 debtToCover,
        uint256 collateralToSeize
    );

    constructor(uint256 initialPremiumBps) {
        premiumBps = initialPremiumBps;
    }

    function setLiquidationQuote(
        address user,
        address collateralAsset,
        address debtAsset,
        uint256 collateralToSeize
    ) external {
        quotes[user][collateralAsset][debtAsset] = LiquidationQuote({
            collateralToSeize: collateralToSeize,
            configured: true
        });
    }

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16
    ) external {
        uint256 premium = (amount * premiumBps) / 10000;
        uint256 balanceBefore = IERC20PoolToken(asset).balanceOf(address(this));
        if (!IERC20PoolToken(asset).transfer(receiverAddress, amount)) revert TransferFailed();

        bool ok = IFlashLoanSimpleReceiver(receiverAddress).executeOperation(
            asset,
            amount,
            premium,
            receiverAddress,
            params
        );
        if (!ok) revert CallbackFailed();

        if (!IERC20PoolToken(asset).transferFrom(receiverAddress, address(this), amount + premium)) {
            revert TransferFailed();
        }
        uint256 expectedBalance = balanceBefore + premium;
        uint256 balanceAfter = IERC20PoolToken(asset).balanceOf(address(this));
        if (balanceAfter < expectedBalance) revert NotRepaid(expectedBalance, balanceAfter);
        emit FlashLoan(receiverAddress, asset, amount, premium);
    }

    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool
    ) external {
        LiquidationQuote memory quote = quotes[user][collateralAsset][debtAsset];
        if (!quote.configured || quote.collateralToSeize == 0) revert LiquidationNotConfigured();
        if (!IERC20PoolToken(debtAsset).transferFrom(msg.sender, address(this), debtToCover)) revert TransferFailed();
        if (!IERC20PoolToken(collateralAsset).transfer(msg.sender, quote.collateralToSeize)) revert TransferFailed();
        emit LiquidationCall(msg.sender, user, collateralAsset, debtAsset, debtToCover, quote.collateralToSeize);
    }
}
