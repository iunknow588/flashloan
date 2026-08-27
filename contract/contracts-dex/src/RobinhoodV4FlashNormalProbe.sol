// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20RobinhoodV4Probe {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IRobinhoodV4PoolManagerMinimal {
    function unlock(bytes calldata data) external returns (bytes memory);
    function take(address currency, address to, uint256 amount) external;
    function sync(address currency) external;
    function settle() external payable returns (uint256 paid);
}

contract RobinhoodV4FlashNormalProbe {
    error BadPoolManagerCallback();
    error InvalidNormalFlashRequest();
    error TokenTransferFailed();
    error Reentrancy();

    event NormalV4FlashRequested(address indexed token, uint256 amount);
    event NormalV4FlashBorrowed(address indexed token, uint256 amount, uint256 balanceAfterTake);
    event NormalV4FlashSettled(address indexed token, uint256 amount, uint256 paid);
    event NormalV4FlashCompleted(address indexed token, uint256 amount);

    IRobinhoodV4PoolManagerMinimal public immutable poolManager;
    bool private locked;

    modifier nonReentrant() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(address poolManager_) {
        if (poolManager_ == address(0)) revert InvalidNormalFlashRequest();
        poolManager = IRobinhoodV4PoolManagerMinimal(poolManager_);
    }

    function runNormalFlash(address token, uint256 amount) external nonReentrant returns (bytes memory result) {
        if (token == address(0) || amount == 0) revert InvalidNormalFlashRequest();
        emit NormalV4FlashRequested(token, amount);
        result = poolManager.unlock(abi.encode(token, amount));
        emit NormalV4FlashCompleted(token, amount);
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert BadPoolManagerCallback();
        (address token, uint256 amount) = abi.decode(data, (address, uint256));
        if (token == address(0) || amount == 0) revert InvalidNormalFlashRequest();

        IERC20RobinhoodV4Probe erc20 = IERC20RobinhoodV4Probe(token);
        uint256 balanceBefore = erc20.balanceOf(address(this));
        poolManager.take(token, address(this), amount);
        uint256 balanceAfterTake = erc20.balanceOf(address(this));
        if (balanceAfterTake < balanceBefore + amount) revert InvalidNormalFlashRequest();
        emit NormalV4FlashBorrowed(token, amount, balanceAfterTake);

        poolManager.sync(token);
        if (!erc20.transfer(address(poolManager), amount)) revert TokenTransferFailed();
        uint256 paid = poolManager.settle();
        if (paid < amount) revert InvalidNormalFlashRequest();
        emit NormalV4FlashSettled(token, amount, paid);

        return abi.encode(token, amount, paid);
    }
}
