// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract MockPoolManagerExtsload {
    mapping(bytes32 => bytes32) public slots;

    function setSlot(bytes32 slot, bytes32 value) external {
        slots[slot] = value;
    }

    function extsload(bytes32 slot) external view returns (bytes32 value) {
        value = slots[slot];
    }
}
