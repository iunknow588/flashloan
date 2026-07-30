// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

interface IAaveV3PoolLike {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;

    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;
}

interface IJoeRouterLike {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface IERC20BalanceLike {
    function balanceOf(address account) external view returns (uint256);
}

interface IFlashLoanSimpleReceiverLike {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract AaveV3LiquidationExecutor is IFlashLoanSimpleReceiverLike {
    using SafeERC20 for IERC20;

    error NotOwner();
    error NotPool();
    error BadInitiator();
    error Paused();
    error InvalidRequest();
    error ProfitTooLow(uint256 actualProfit, uint256 minProfit);

    struct LiquidationRequest {
        address user;
        address collateralAsset;
        address debtAsset;
        uint256 debtToCover;
        uint256 minCollateralSwapOut;
        uint256 minProfitAmount;
        uint256 deadline;
        address[] swapPath;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event LiquidationRequested(
        address indexed user,
        address indexed collateralAsset,
        address indexed debtAsset,
        uint256 debtToCover
    );
    event LiquidationExecuted(
        address indexed user,
        address indexed collateralAsset,
        address indexed debtAsset,
        uint256 debtCovered,
        uint256 premium,
        uint256 profit
    );
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);
    event NativeWithdrawn(address indexed to, uint256 amount);
    event TokenSweptToUSDC(address indexed token, uint256 amountIn, uint256 amountOut);

    address public immutable pool;
    address public immutable router;
    address public immutable usdc;
    address public owner;
    bool public paused;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert Paused();
        _;
    }

    constructor(address poolAddress, address routerAddress, address usdcAddress, address initialOwner) {
        if (poolAddress == address(0) || routerAddress == address(0) || usdcAddress == address(0)) revert InvalidRequest();
        pool = poolAddress;
        router = routerAddress;
        usdc = usdcAddress;
        owner = initialOwner == address(0) ? msg.sender : initialOwner;
        emit OwnershipTransferred(address(0), owner);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert InvalidRequest();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function setPaused(bool value) external onlyOwner {
        paused = value;
        emit PausedSet(value);
    }

    function requestLiquidation(LiquidationRequest calldata request) external onlyOwner whenNotPaused {
        _validateRequest(request);
        IAaveV3PoolLike(pool).flashLoanSimple(
            address(this),
            request.debtAsset,
            request.debtToCover,
            abi.encode(request),
            0
        );
        emit LiquidationRequested(request.user, request.collateralAsset, request.debtAsset, request.debtToCover);
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override whenNotPaused returns (bool) {
        if (msg.sender != pool) revert NotPool();
        if (initiator != address(this)) revert BadInitiator();

        LiquidationRequest memory request = abi.decode(params, (LiquidationRequest));
        _validateRequest(request);
        if (asset != request.debtAsset || amount != request.debtToCover) revert InvalidRequest();

        _forceApprove(request.debtAsset, pool, amount);
        IAaveV3PoolLike(pool).liquidationCall(
            request.collateralAsset,
            request.debtAsset,
            request.user,
            amount,
            false
        );

        uint256 repayAmount = amount + premium;
        uint256 debtBalance = IERC20(request.debtAsset).balanceOf(address(this));
        if (request.collateralAsset != request.debtAsset) {
            uint256 collateralBalance = IERC20(request.collateralAsset).balanceOf(address(this));
            if (collateralBalance > 0) {
                _swapCollateral(request, collateralBalance);
                debtBalance = IERC20(request.debtAsset).balanceOf(address(this));
            }
        }

        if (debtBalance < repayAmount + request.minProfitAmount) {
            uint256 profit = debtBalance > repayAmount ? debtBalance - repayAmount : 0;
            revert ProfitTooLow(profit, request.minProfitAmount);
        }

        _forceApprove(request.debtAsset, pool, repayAmount);
        emit LiquidationExecuted(
            request.user,
            request.collateralAsset,
            request.debtAsset,
            amount,
            premium,
            debtBalance - repayAmount
        );
        return true;
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        _withdrawToken(token, to, amount);
    }

    function withdrawUSDC(address to, uint256 amount) external onlyOwner {
        _withdrawToken(usdc, to, amount);
    }

    function withdrawNative(address payable to, uint256 amount) external onlyOwner {
        if (to == address(0)) revert InvalidRequest();
        if (address(this).balance < amount) revert InvalidRequest();
        (bool ok, ) = to.call{value: amount}("");
        if (!ok) revert InvalidRequest();
        emit NativeWithdrawn(to, amount);
    }

    function sweepTokenToUSDC(
        address token,
        uint256 amountIn,
        uint256 minUsdcOut,
        address[] calldata path
    ) external onlyOwner returns (uint256 amountOut) {
        return _sweepTokenToUSDC(token, amountIn, minUsdcOut, path);
    }

    function sweepAllTokenToUSDC(
        address token,
        uint256 minUsdcOut,
        address[] calldata path
    ) external onlyOwner returns (uint256 amountOut) {
        if (token == address(0)) revert InvalidRequest();
        uint256 amountIn = IERC20BalanceLike(token).balanceOf(address(this));
        if (amountIn == 0) return 0;
        return _sweepTokenToUSDC(token, amountIn, minUsdcOut, path);
    }

    receive() external payable {}

    function _withdrawToken(address token, address to, uint256 amount) private {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        IERC20(token).safeTransfer(to, amount);
        emit TokenWithdrawn(token, to, amount);
    }

    function _sweepTokenToUSDC(
        address token,
        uint256 amountIn,
        uint256 minUsdcOut,
        address[] calldata path
    ) private returns (uint256 amountOut) {
        if (token == address(0) || amountIn == 0) revert InvalidRequest();
        if (token == usdc) {
            if (amountIn < minUsdcOut) revert ProfitTooLow(amountIn, minUsdcOut);
            emit TokenSweptToUSDC(token, amountIn, amountIn);
            return amountIn;
        }
        if (path.length < 2 || path[0] != token || path[path.length - 1] != usdc) revert InvalidRequest();
        _forceApprove(token, router, amountIn);
        uint256[] memory amounts = IJoeRouterLike(router).swapExactTokensForTokens(
            amountIn,
            minUsdcOut,
            path,
            address(this),
            block.timestamp
        );
        amountOut = amounts[amounts.length - 1];
        emit TokenSweptToUSDC(token, amountIn, amountOut);
        return amountOut;
    }

    function _validateRequest(LiquidationRequest memory request) private view {
        if (
            request.user == address(0) ||
            request.collateralAsset == address(0) ||
            request.debtAsset == address(0) ||
            request.debtToCover == 0 ||
            request.deadline < block.timestamp
        ) {
            revert InvalidRequest();
        }
        if (request.collateralAsset != request.debtAsset) {
            if (
                request.swapPath.length < 2 ||
                request.swapPath[0] != request.collateralAsset ||
                request.swapPath[request.swapPath.length - 1] != request.debtAsset
            ) {
                revert InvalidRequest();
            }
        }
    }

    function _swapCollateral(LiquidationRequest memory request, uint256 collateralAmount) private {
        _forceApprove(request.collateralAsset, router, collateralAmount);
        IJoeRouterLike(router).swapExactTokensForTokens(
            collateralAmount,
            request.minCollateralSwapOut,
            request.swapPath,
            address(this),
            request.deadline
        );
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        IERC20(token).forceApprove(spender, amount);
    }
}
