// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal CREATE2 deployer for deterministic hook addresses.
contract Create2Deployer {
    event Deployed(address indexed addr, bytes32 salt, bytes initCode);

    function deploy(bytes calldata initCode, bytes32 salt) external returns (address addr) {
        bytes memory code = initCode;
        assembly ("memory-safe") {
            addr := create2(0, add(code, 0x20), mload(code), salt)
        }
        require(addr != address(0), "Create2Deployer: deployment failed");
        emit Deployed(addr, salt, initCode);
    }

    function predict(bytes calldata initCode, bytes32 salt) external view returns (address) {
        bytes32 initCodeHash = keccak256(initCode);
        return address(uint160(uint256(keccak256(abi.encodePacked(hex"ff", address(this), salt, initCodeHash)))));
    }
}
