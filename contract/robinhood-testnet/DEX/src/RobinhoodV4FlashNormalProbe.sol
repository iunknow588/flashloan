// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 探针使用的最小 ERC20 接口。
interface IERC20RobinhoodV4Probe {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

/// @notice 探针使用的 V4 PoolManager 最小接口。
interface IRobinhoodV4PoolManagerMinimal {
    function unlock(bytes calldata data) external returns (bytes memory);
    function take(address currency, address to, uint256 amount) external;
    function sync(address currency) external;
    function settle() external payable returns (uint256 paid);
}

/// @title RobinhoodV4FlashNormalProbe
/// @notice Robinhood Testnet V4 PoolManager flash accounting 的正常路径探针。
/// 不执行套利，只验证 unlock -> take -> sync -> settle 这一条 flash 记账回路。
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

    /// @notice 防止探针重入。
    modifier nonReentrant() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    /// @notice 构造函数固定 PoolManager 地址，不接受 address(0)。
    constructor(address poolManager_) {
        if (poolManager_ == address(0)) revert InvalidNormalFlashRequest();
        poolManager = IRobinhoodV4PoolManagerMinimal(poolManager_);
    }

    receive() external payable {}

    /// @notice 发起一笔最小正常路径 flash：调用 poolManager.unlock 并返回结果。
    function runNormalFlash(address token, uint256 amount) external nonReentrant returns (bytes memory result) {
        if (amount == 0) revert InvalidNormalFlashRequest();
        emit NormalV4FlashRequested(token, amount);
        result = poolManager.unlock(abi.encode(token, amount));
        emit NormalV4FlashCompleted(token, amount);
    }

    /// @notice PoolManager unlock 回调：take 借款后立即归还，用于验证 flash accounting 债务归零。
    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert BadPoolManagerCallback();
        (address token, uint256 amount) = abi.decode(data, (address, uint256));
        if (amount == 0) revert InvalidNormalFlashRequest();

        uint256 balanceBefore = _currencyBalance(token);
        poolManager.take(token, address(this), amount);
        uint256 balanceAfterTake = _currencyBalance(token);
        if (balanceAfterTake < balanceBefore + amount) revert InvalidNormalFlashRequest();
        emit NormalV4FlashBorrowed(token, amount, balanceAfterTake);

        poolManager.sync(token);
        uint256 paid;
        if (token == address(0)) {
            paid = poolManager.settle{value: amount}();
        } else {
            if (!IERC20RobinhoodV4Probe(token).transfer(address(poolManager), amount)) revert TokenTransferFailed();
            paid = poolManager.settle();
        }
        if (paid < amount) revert InvalidNormalFlashRequest();
        emit NormalV4FlashSettled(token, amount, paid);

        return abi.encode(token, amount, paid);
    }

    /// @notice 返回指定 currency 的余额；address(0) 表示原生 ETH。
    function _currencyBalance(address token) private view returns (uint256) {
        if (token == address(0)) return address(this).balance;
        return IERC20RobinhoodV4Probe(token).balanceOf(address(this));
    }
}
