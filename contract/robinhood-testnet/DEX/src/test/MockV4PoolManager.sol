// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Uniswap V4 PoolManager Mock，用于本地 flash accounting 正常路径和 executor 离链 readiness。

interface IERC20MockV4PoolManager {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IUnlockCallbackMockV4 {
    function unlockCallback(bytes calldata data) external returns (bytes memory);
}

struct MockV4PoolKey {
    address currency0;
    address currency1;
    uint24 fee;
    int24 tickSpacing;
    address hooks;
}

struct MockV4SwapParams {
    bool zeroForOne;
    int256 amountSpecified;
    uint160 sqrtPriceLimitX96;
}

contract MockV4PoolManager {
    error AlreadyUnlocked();
    error NotUnlocked();
    error DebtNotSettled(address currency, uint256 debt);
    error TokenTransferFailed();
    error SyncMissing();

    event Unlocked(address indexed caller);
    event SwapExecuted(address indexed caller, address indexed currency0, address indexed currency1, uint256 amountSpecified, uint256 amountOutMinimum);
    event Taken(address indexed caller, address indexed currency, address indexed to, uint256 amount);
    event Synced(address indexed caller, address indexed currency, uint256 balance);
    event Settled(address indexed caller, address indexed currency, uint256 paid, uint256 remainingDebt);
    event ScriptedSwapTransfer(address indexed token, address indexed to, uint256 amount);

    mapping(address caller => bool unlocked) public isUnlocked;
    mapping(address caller => address currency) public activeCurrency;
    mapping(address caller => mapping(address currency => uint256 debt)) public currencyDebt;
    mapping(address caller => address currency) public syncedCurrency;
    mapping(address caller => uint256 balanceAtSync) public syncedBalance;
    address public scriptedSwapToken;
    uint256 public scriptedSwapAmount;
    uint256 public scriptedNativeSwapAmount;
    address[] public scriptedSwapTokens;
    uint256[] public scriptedSwapAmounts;
    uint256 public scriptedSwapCursor;
    uint256[] public scriptedNativeSwapAmounts;
    uint256 public scriptedNativeSwapCursor;

    receive() external payable {}

    /// @notice 设置下一次 swap 时的脚本化 ERC20 转出，用于模拟交易结果。
    function setTransferOnNextSwap(address token, uint256 amount) external {
        scriptedSwapToken = token;
        scriptedSwapAmount = amount;
    }

    /// @notice 设置下一次 swap 时的脚本化原生 ETH 转出。
    function setNativeTransferOnNextSwap(uint256 amount) external {
        scriptedNativeSwapAmount = amount;
    }

    /// @notice 设置多跳 swap 的 ERC20 转出队列。
    function setSwapTransferQueue(address[] calldata tokens, uint256[] calldata amounts) external {
        delete scriptedSwapTokens;
        delete scriptedSwapAmounts;
        scriptedSwapCursor = 0;
        require(tokens.length == amounts.length, "queue_length");
        for (uint256 i = 0; i < tokens.length; i++) {
            scriptedSwapTokens.push(tokens[i]);
            scriptedSwapAmounts.push(amounts[i]);
        }
    }

    /// @notice 设置多跳 swap 的 native 转出队列。
    function setNativeSwapTransferQueue(uint256[] calldata amounts) external {
        delete scriptedNativeSwapAmounts;
        scriptedNativeSwapCursor = 0;
        for (uint256 i = 0; i < amounts.length; i++) {
            scriptedNativeSwapAmounts.push(amounts[i]);
        }
    }

    /// @notice 模拟 V4 PoolManager unlock：标记 caller 为未锁，触发回调后校验 debt 归零。
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

    /// @notice 模拟 PoolManager swap；根据脚本化配置向 caller 转出代币或 ETH。
    function swap(MockV4PoolKey calldata key, MockV4SwapParams calldata params, bytes calldata) external payable returns (int256, int256) {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        uint256 amountSpecified = uint256(params.amountSpecified >= 0 ? params.amountSpecified : -params.amountSpecified);
        emit SwapExecuted(msg.sender, key.currency0, key.currency1, amountSpecified, 0);
        address token = scriptedSwapToken;
        uint256 amount = scriptedSwapAmount;
        if (scriptedSwapCursor < scriptedSwapAmounts.length) {
            token = scriptedSwapTokens[scriptedSwapCursor];
            amount = scriptedSwapAmounts[scriptedSwapCursor];
            scriptedSwapCursor += 1;
        }
        if (token != address(0) && amount > 0) {
            scriptedSwapToken = address(0);
            scriptedSwapAmount = 0;
            if (!IERC20MockV4PoolManager(token).transfer(msg.sender, amount)) revert TokenTransferFailed();
            emit ScriptedSwapTransfer(token, msg.sender, amount);
        }
        uint256 nativeAmount = scriptedNativeSwapAmount;
        if (scriptedNativeSwapCursor < scriptedNativeSwapAmounts.length) {
            nativeAmount = scriptedNativeSwapAmounts[scriptedNativeSwapCursor];
            scriptedNativeSwapCursor += 1;
        }
        if (nativeAmount > 0) {
            scriptedNativeSwapAmount = 0;
            (bool ok,) = payable(msg.sender).call{value: nativeAmount}("");
            if (!ok) revert TokenTransferFailed();
            emit ScriptedSwapTransfer(address(0), msg.sender, nativeAmount);
        }
        return (0, 0);
    }

    /// @notice 模拟 take：向 caller 转出 currency 并累计债务。
    function take(address currency, address to, uint256 amount) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        if (currency == address(0)) {
            (bool ok,) = payable(to).call{value: amount}("");
            if (!ok) revert TokenTransferFailed();
        } else if (!IERC20MockV4PoolManager(currency).transfer(to, amount)) {
            revert TokenTransferFailed();
        }
        activeCurrency[msg.sender] = currency;
        currencyDebt[msg.sender][currency] += amount;
        emit Taken(msg.sender, currency, to, amount);
    }

    /// @notice 模拟 sync：记录调用方指定 currency 的当前池内余额。
    function sync(address currency) external {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        syncedCurrency[msg.sender] = currency;
        syncedBalance[msg.sender] = _balance(currency);
        emit Synced(msg.sender, currency, syncedBalance[msg.sender]);
    }

    /// @notice 模拟 settle：用池内余额增量抵扣债务。
    function settle() external payable returns (uint256 paid) {
        if (!isUnlocked[msg.sender]) revert NotUnlocked();
        address currency = syncedCurrency[msg.sender];
        uint256 currentBalance = _balance(currency);
        if (currentBalance > syncedBalance[msg.sender]) {
            paid = currentBalance - syncedBalance[msg.sender];
        }
        uint256 debt = currencyDebt[msg.sender][currency];
        if (paid > debt) paid = debt;
        currencyDebt[msg.sender][currency] = debt - paid;
        emit Settled(msg.sender, currency, paid, currencyDebt[msg.sender][currency]);
    }

    /// @notice 读取池内指定 currency 余额。
    function _balance(address currency) private view returns (uint256) {
        if (currency == address(0)) return address(this).balance;
        return IERC20MockV4PoolManager(currency).balanceOf(address(this));
    }
}
