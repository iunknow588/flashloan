// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20MockV4PoolManager {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IUnlockCallbackMockV4 {
    function unlockCallback(bytes calldata data) external returns (bytes memory);
}

contract MockV4PoolManager {
    error AlreadyUnlocked();
    error NotUnlocked();
    error DebtNotSettled(address currency, uint256 debt);
    error TokenTransferFailed();
    error SyncMissing();

    event Unlocked(address indexed caller);
    event Taken(address indexed caller, address indexed currency, address indexed to, uint256 amount);
    event Synced(address indexed caller, address indexed currency, uint256 balance);
    event Settled(address indexed caller, address indexed currency, uint256 paid, uint256 remainingDebt);

    mapping(address caller => bool unlocked) public isUnlocked;
    mapping(address caller => address currency) public activeCurrency;
    mapping(address caller => mapping(address currency => uint256 debt)) public currencyDebt;
    mapping(address caller => address currency) public syncedCurrency;
    mapping(address caller => uint256 balanceAtSync) public syncedBalance;

    function unlock(bytes calldata data) external returns (bytes memory result) {
        if (isUnlocked[msg.sender]) revert AlreadyUnlocked();
        isUnlocked[msg.sender] = true;
        emit Unlocked(msg.sender);
        result = IUnlockCallbackMockV4(msg.sender).unlockCallback(data);
        address currency = activeCurrency[msg.sender];
        uint256 debt = currencyDebt[msg.sender][currency];
        isUnlocked[msg.sender] = false;
        if (debt != 0) revert DebtNotSettled(currency, debt);
    }

    function take(address currency, address to, uint256 amount) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        if (!IERC20MockV4PoolManager(currency).transfer(to, amount)) revert TokenTransferFailed();
        activeCurrency[msg.sender] = currency;
        currencyDebt[msg.sender][currency] += amount;
        emit Taken(msg.sender, currency, to, amount);
    }

    function sync(address currency) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        syncedCurrency[msg.sender] = currency;
        syncedBalance[msg.sender] = IERC20MockV4PoolManager(currency).balanceOf(address(this));
        emit Synced(msg.sender, currency, syncedBalance[msg.sender]);
    }

    function settle() external payable returns (uint256 paid) {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        address currency = syncedCurrency[msg.sender];
        if (currency == address(0)) revert SyncMissing();
        uint256 currentBalance = IERC20MockV4PoolManager(currency).balanceOf(address(this));
        if (currentBalance > syncedBalance[msg.sender]) {
            paid = currentBalance - syncedBalance[msg.sender];
        }
        uint256 debt = currencyDebt[msg.sender][currency];
        if (paid > debt) paid = debt;
        currencyDebt[msg.sender][currency] = debt - paid;
        emit Settled(msg.sender, currency, paid, currencyDebt[msg.sender][currency]);
    }
}
