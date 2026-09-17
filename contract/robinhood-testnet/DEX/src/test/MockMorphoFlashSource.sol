// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Mock Morpho Blue flash source，用于本地验证 Morpho 兼容路径。

interface IERC20MockMorpho {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

interface IMorphoFlashReceiverMock {
    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external;
}

contract MockMorphoFlashSource {
    error TransferFailed();

    event FlashLoan(address indexed receiver, address indexed token, uint256 amount);

    function flashLoan(address token, uint256 assets, bytes calldata data) external {
        if (!IERC20MockMorpho(token).transfer(msg.sender, assets)) revert TransferFailed();
        IMorphoFlashReceiverMock(msg.sender).onMorphoFlashLoan(assets, data);
        if (!IERC20MockMorpho(token).transferFrom(msg.sender, address(this), assets)) revert TransferFailed();
        emit FlashLoan(msg.sender, token, assets);
    }
}

