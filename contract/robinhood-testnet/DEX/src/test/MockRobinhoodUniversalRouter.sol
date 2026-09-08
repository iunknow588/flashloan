// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20MockRobinhoodUniversalRouter {
    function transfer(address to, uint256 value) external returns (bool);
}

contract MockRobinhoodUniversalRouter {
    error DeadlineExpired();
    error ScriptedTransferFailed();

    event Executed(bytes commands, uint256 inputCount, uint256 deadline);
    event TransferScriptSet(address indexed token, uint256 amount);
    event ScriptedTransfer(address indexed token, address indexed to, uint256 amount);
    event NativeTransferScriptSet(uint256 amount);
    event NativeValueReceived(address indexed sender, uint256 amount);
    event ScriptedNativeTransfer(address indexed to, uint256 amount);

    address public scriptedToken;
    uint256 public scriptedAmount;
    uint256 public scriptedNativeAmount;

    receive() external payable {
        emit NativeValueReceived(msg.sender, msg.value);
    }

    function setTransferOnNextExecute(address token, uint256 amount) external {
        scriptedToken = token;
        scriptedAmount = amount;
        emit TransferScriptSet(token, amount);
    }

    function setNativeTransferOnNextExecute(uint256 amount) external {
        scriptedNativeAmount = amount;
        emit NativeTransferScriptSet(amount);
    }

    function execute(bytes calldata commands, bytes[] calldata inputs, uint256 deadline) external payable {
        if (deadline < block.timestamp) revert DeadlineExpired();
        if (msg.value > 0) {
            emit NativeValueReceived(msg.sender, msg.value);
        }
        address token = scriptedToken;
        uint256 amount = scriptedAmount;
        if (token != address(0) && amount > 0) {
            scriptedToken = address(0);
            scriptedAmount = 0;
            if (!IERC20MockRobinhoodUniversalRouter(token).transfer(msg.sender, amount)) {
                revert ScriptedTransferFailed();
            }
            emit ScriptedTransfer(token, msg.sender, amount);
        }
        uint256 nativeAmount = scriptedNativeAmount;
        if (nativeAmount > 0) {
            scriptedNativeAmount = 0;
            (bool ok,) = payable(msg.sender).call{value: nativeAmount}("");
            if (!ok) revert ScriptedTransferFailed();
            emit ScriptedNativeTransfer(msg.sender, nativeAmount);
        }
        emit Executed(commands, inputs.length, deadline);
    }
}
