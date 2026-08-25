// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract MockRobinhoodUniversalRouter {
    error DeadlineExpired();

    event Executed(bytes commands, uint256 inputCount, uint256 deadline);

    function execute(bytes calldata commands, bytes[] calldata inputs, uint256 deadline) external payable {
        if (deadline < block.timestamp) revert DeadlineExpired();
        emit Executed(commands, inputs.length, deadline);
    }
}

