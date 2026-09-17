// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Generated from flashloan/src/core/error_registry.py.
// Do not edit by hand: regenerate with flashloan/src/core/generate_contract_dex_error_codes.py.

library ContractDexErrorCodes {
    uint256 internal constant MODULE_OFFSET = 40_000_000;

    // flow id
    uint256 internal constant FLOW_VALIDATION = 1;
    uint256 internal constant FLOW_QUOTE = 2;
    uint256 internal constant FLOW_EXECUTION = 3;
    uint256 internal constant FLOW_REPAYMENT = 4;
    uint256 internal constant FLOW_CALLBACK = 5;
    uint256 internal constant FLOW_SWEEP = 6;
    uint256 internal constant FLOW_INTERNAL = 9;

    // node id
    uint256 internal constant NODE_REQUEST_VALIDATE = 1;
    uint256 internal constant NODE_FLASH_SOURCE_RESOLVE = 2;
    uint256 internal constant NODE_ADAPTER_RESOLVE = 3;
    uint256 internal constant NODE_ROUTE_VALIDATE = 4;
    uint256 internal constant NODE_HOP_QUOTE = 5;
    uint256 internal constant NODE_SWAP_EXECUTE = 6;
    uint256 internal constant NODE_CALLBACK_VERIFY = 7;
    uint256 internal constant NODE_REPAYMENT_CHECK = 8;
    uint256 internal constant NODE_TRANSFER = 9;
    uint256 internal constant NODE_APPROVE = 10;
    uint256 internal constant NODE_REENTRANCY_GUARD = 11;
    uint256 internal constant NODE_OWNER_GUARD = 12;
    uint256 internal constant NODE_POOL_GUARD = 13;
    uint256 internal constant NODE_INITIATOR_GUARD = 14;
    uint256 internal constant NODE_PAUSED_GUARD = 15;
    uint256 internal constant NODE_INTERNAL = 99;

    // error codes
    uint256 internal constant VALIDATION_REQUEST_INVALID = 40_010_101;
    uint256 internal constant VALIDATION_FLASH_SOURCE_MISSING = 40_010_201;
    uint256 internal constant VALIDATION_ROUTE_INVALID = 40_010_401;
    uint256 internal constant QUOTE_ADAPTER_MISSING = 40_020_301;
    uint256 internal constant QUOTE_HOP_QUOTE_POOL_NOT_INITIALIZED = 40_020_501;
    uint256 internal constant QUOTE_HOP_QUOTE_NOT_ENOUGH_LIQUIDITY = 40_020_502;
    uint256 internal constant QUOTE_HOP_QUOTE_QUOTER_FAILED = 40_020_503;
    uint256 internal constant EXECUTION_ROUTER_CALL_FAILED = 40_030_601;
    uint256 internal constant EXECUTION_APPROVE_FAILED = 40_031_001;
    uint256 internal constant EXECUTION_TRANSFER_FAILED = 40_030_901;
    uint256 internal constant EXECUTION_REENTRANCY = 40_031_101;
    uint256 internal constant EXECUTION_NOT_OWNER = 40_031_201;
    uint256 internal constant EXECUTION_PAUSED = 40_031_501;
    uint256 internal constant CALLBACK_BAD_FLASH_CALLBACK = 40_050_701;
    uint256 internal constant CALLBACK_NOT_POOL = 40_051_301;
    uint256 internal constant CALLBACK_BAD_INITIATOR = 40_051_401;
    uint256 internal constant REPAYMENT_INSUFFICIENT = 40_040_801;
    uint256 internal constant INTERNAL = 40_099_901;
}
