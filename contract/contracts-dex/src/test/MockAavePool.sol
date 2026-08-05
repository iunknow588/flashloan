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

    function executeOperation(
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata premiums,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract MockAavePool {
    error TransferFailed();
    error CallbackFailed();
    error NotRepaid(uint256 expectedBalance, uint256 actualBalance);

    event FlashLoan(address indexed receiver, address indexed asset, uint256 amount, uint256 premium);

    uint256 public premiumBps;

    constructor(uint256 initialPremiumBps) {
        premiumBps = initialPremiumBps;
    }

    function setPremiumBps(uint256 value) external {
        premiumBps = value;
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

        uint256 expectedBalance = balanceBefore + premium;
        if (!IERC20PoolToken(asset).transferFrom(receiverAddress, address(this), amount + premium)) {
            revert TransferFailed();
        }

        uint256 balanceAfter = IERC20PoolToken(asset).balanceOf(address(this));
        if (balanceAfter < expectedBalance) revert NotRepaid(expectedBalance, balanceAfter);

        emit FlashLoan(receiverAddress, asset, amount, premium);
    }

    function flashLoan(
        address receiverAddress,
        address[] calldata assets,
        uint256[] calldata amounts,
        uint256[] calldata,
        address,
        bytes calldata params,
        uint16
    ) external {
        if (assets.length == 0 || assets.length != amounts.length) revert CallbackFailed();

        uint256[] memory premiums = new uint256[](assets.length);
        uint256[] memory expectedBalances = new uint256[](assets.length);

        for (uint256 i = 0; i < assets.length; i++) {
            premiums[i] = (amounts[i] * premiumBps) / 10000;
            expectedBalances[i] = IERC20PoolToken(assets[i]).balanceOf(address(this)) + premiums[i];
            if (!IERC20PoolToken(assets[i]).transfer(receiverAddress, amounts[i])) revert TransferFailed();
        }

        bool ok = IFlashLoanSimpleReceiver(receiverAddress).executeOperation(
            assets,
            amounts,
            premiums,
            receiverAddress,
            params
        );
        if (!ok) revert CallbackFailed();

        for (uint256 i = 0; i < assets.length; i++) {
            if (!IERC20PoolToken(assets[i]).transferFrom(receiverAddress, address(this), amounts[i] + premiums[i])) {
                revert TransferFailed();
            }
            uint256 balanceAfter = IERC20PoolToken(assets[i]).balanceOf(address(this));
            if (balanceAfter < expectedBalances[i]) revert NotRepaid(expectedBalances[i], balanceAfter);
            emit FlashLoan(receiverAddress, assets[i], amounts[i], premiums[i]);
        }
    }
}
