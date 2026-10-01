// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice composer 测试用的 Mock 执行器，模拟真实执行器的调用边界。

contract MockComposerExecutor {
    uint256 public calls;
    bool public shouldRevert;
    bool public revertNext;
    bool public profitScale;
    bool public leftPeakMode;
    uint256 public leftPeakAmount;
    address public revertMarkerCurrency0;
    address public failMarkerCurrency0;
    address public lastFailMarkerTried;

    struct PoolKey {
        address currency0;
        address currency1;
        uint24 fee;
        int24 tickSpacing;
        address hooks;
    }

    function setShouldRevert(bool value_) external {
        shouldRevert = value_;
    }

    function setRevertNext(bool value_) external {
        revertNext = value_;
    }

    function setProfitScale(bool value_) external {
        profitScale = value_;
    }

    function setLeftPeakMode(bool value_) external {
        leftPeakMode = value_;
    }

    function setLeftPeakAmount(uint256 value_) external {
        leftPeakAmount = value_;
    }

    function setRevertMarkerCurrency0(address value_) external {
        revertMarkerCurrency0 = value_;
    }

    function setFailMarkerCurrency0(address value_) external {
        failMarkerCurrency0 = value_;
    }

    fallback(bytes calldata data) external returns (bytes memory) {
        calls++;
        if (failMarkerCurrency0 != address(0) && _containsMarker(data, failMarkerCurrency0)) {
            lastFailMarkerTried = failMarkerCurrency0;
            return bytes("");
        }
        if (revertMarkerCurrency0 != address(0) && _containsMarker(data, revertMarkerCurrency0)) {
            revert("mockExecutorRevert");
        }
        if (revertNext) {
            revertNext = false;
            revert("mockExecutorRevert");
        }
        if (shouldRevert) {
            revert("mockExecutorRevert");
        }
        if (profitScale && data.length > 4) {
            uint256 amountIn = _decodeRunArbitrageBestAmount(data);
            return abi.encode(uint256(111), uint256(222), uint256(333), amountIn / 1000, uint256(0));
        }
        if (leftPeakMode && data.length > 4) {
            uint256 amountIn = _decodeRunArbitrageBestAmount(data);
            uint256 profit;
            uint256 tolerance = leftPeakAmount / 1000;
            if (leftPeakAmount > 0 && amountIn + tolerance >= leftPeakAmount && amountIn <= leftPeakAmount + tolerance) {
                profit = 10_000;
            } else if (amountIn < leftPeakAmount) {
                profit = amountIn;
            } else {
                profit = 1;
            }
            return abi.encode(uint256(111), uint256(222), uint256(333), profit, uint256(0));
        }
        return abi.encode(uint256(111), uint256(222), uint256(333), uint256(999), uint256(0));
    }

    function _decodeRunArbitrageBestAmount(bytes calldata data) private pure returns (uint256 amountIn) {
        if (data.length < 4 + 32 * 4) return 0;
        uint256 decodedAmount;
        (, , , decodedAmount,) = abi.decode(
            data[4:],
            (address, PoolKey[3][], bool[3][], uint256, uint256)
        );
        amountIn = decodedAmount;
    }

    function _containsMarker(bytes calldata data, address marker) private pure returns (bool) {
        bytes32 target = bytes32(uint256(uint160(marker)));
        uint256 end = 4;
        if (data.length < 32 + end) return false;
        for (uint256 i = end; i + 32 <= data.length; i++) {
            bytes32 chunk;
            assembly {
                chunk := calldataload(add(data.offset, i))
            }
            if (chunk == target) return true;
        }
        return false;
    }
}
