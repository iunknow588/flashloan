// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Controller {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
}

interface IRouterController {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);
}

interface IAaveTriangularExecutorLike {
    struct ExecutionRequest {
        address tokenX;
        address tokenY;
        address router;
        uint256 amount;
        uint256 minProfitUsdc;
        uint256 deadline;
        uint256 slippageBps;
    }

    function execute(ExecutionRequest calldata request) external returns (uint256 profitReturned);
}

contract TriangularRouteController {
    error NotOwner();
    error Paused();
    error InvalidRequest();
    error NoViableRoute();
    error TransferFailed();
    error Reentrancy();

    uint256 public constant MAX_ROUTE_REQUESTS = 32;

    struct RouteRequest {
        address tokenX;
        address tokenY;
        address router;
        uint256 amount;
        uint256 premiumBps;
        uint256 minProfitUsdc;
        uint256 deadline;
        uint256 slippageBps;
        bool allowReverse;
    }

    struct RouteDecision {
        bool viable;
        bool reverse;
        uint256 quotedFinalUsdc;
        uint256 profitUsdc;
        address[] path;
        uint256 edgeBps;
        uint256 requiredEdgeBps;
        uint256 directComparableAmount;
        uint256 viaComparableAmount;
    }

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PausedSet(bool paused);
    event RouteSubmitted(
        bool indexed reverse,
        address indexed tokenX,
        address indexed tokenY,
        uint256 amount,
        uint256 quotedFinalUsdc,
        uint256 profitReturned
    );
    event BatchRouteSubmitted(
        uint256 indexed requestIndex,
        bool indexed reverse,
        address indexed tokenX,
        address tokenY,
        uint256 amount,
        uint256 quotedFinalUsdc,
        uint256 profitReturned
    );
    event TokenWithdrawn(address indexed token, address indexed to, uint256 amount);

    address public immutable usdc;
    address public immutable executor;
    address public owner;
    bool public paused;
    bool private locked;

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert Paused();
        _;
    }

    modifier nonReentrantEntry() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(address usdcAddress, address executorAddress, address initialOwner) {
        if (usdcAddress == address(0) || executorAddress == address(0)) revert InvalidRequest();
        usdc = usdcAddress;
        executor = executorAddress;
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

    function run(RouteRequest calldata request)
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (uint256 profitReturned)
    {
        RouteDecision memory decision = previewBestRoute(request);
        if (!decision.viable) revert NoViableRoute();

        profitReturned = _executeDecision(request, decision);

        emit RouteSubmitted(
            decision.reverse,
            request.tokenX,
            request.tokenY,
            request.amount,
            decision.quotedFinalUsdc,
            profitReturned
        );
    }

    function runBest(RouteRequest[] calldata requests)
        external
        onlyOwner
        whenNotPaused
        nonReentrantEntry
        returns (uint256 bestIndex, uint256 profitReturned)
    {
        RouteDecision memory decision;
        (bestIndex, decision) = previewBestRouteFrom(requests);
        if (!decision.viable) revert NoViableRoute();

        RouteRequest calldata request = requests[bestIndex];
        profitReturned = _executeDecision(request, decision);

        emit BatchRouteSubmitted(
            bestIndex,
            decision.reverse,
            request.tokenX,
            request.tokenY,
            request.amount,
            decision.quotedFinalUsdc,
            profitReturned
        );
    }

    function previewBestRouteFrom(RouteRequest[] calldata requests)
        public
        view
        returns (uint256 bestIndex, RouteDecision memory best)
    {
        if (requests.length == 0 || requests.length > MAX_ROUTE_REQUESTS) revert InvalidRequest();

        for (uint256 i = 0; i < requests.length; i++) {
            RouteDecision memory decision = previewBestRoute(requests[i]);
            if (decision.viable && (!best.viable || decision.profitUsdc > best.profitUsdc)) {
                best = decision;
                bestIndex = i;
            }
        }
    }

    function _executeDecision(RouteRequest calldata request, RouteDecision memory decision)
        private
        returns (uint256 profitReturned)
    {
        address tokenX = decision.reverse ? request.tokenY : request.tokenX;
        address tokenY = decision.reverse ? request.tokenX : request.tokenY;
        IAaveTriangularExecutorLike.ExecutionRequest memory executionRequest = IAaveTriangularExecutorLike.ExecutionRequest({
            tokenX: tokenX,
            tokenY: tokenY,
            router: request.router,
            amount: request.amount,
            minProfitUsdc: request.minProfitUsdc,
            deadline: request.deadline,
            slippageBps: request.slippageBps
        });
        profitReturned = IAaveTriangularExecutorLike(executor).execute(executionRequest);
    }

    function previewBestRoute(RouteRequest calldata request) public view returns (RouteDecision memory best) {
        _validateRequest(request);
        uint256 owedEstimate = request.amount + (request.amount * request.premiumBps) / 10000;
        uint256 requiredEdgeBps = _requiredEdgeBps(request);

        RouteDecision memory forward = _quote(request, owedEstimate, requiredEdgeBps, false);
        if (forward.viable) best = forward;
        if (request.allowReverse) {
            RouteDecision memory reverse = _quote(request, owedEstimate, requiredEdgeBps, true);
            if (reverse.viable && (!best.viable || reverse.profitUsdc > best.profitUsdc)) {
                best = reverse;
            }
        }
    }

    function withdrawToken(address token, address to, uint256 amount) external onlyOwner {
        if (token == address(0) || to == address(0)) revert InvalidRequest();
        if (!IERC20Controller(token).transfer(to, amount)) revert TransferFailed();
        emit TokenWithdrawn(token, to, amount);
    }

    function _quote(
        RouteRequest calldata request,
        uint256 owedEstimate,
        uint256 requiredEdgeBps,
        bool reverse
    ) private view returns (RouteDecision memory decision) {
        address[] memory path = _routePath(request.tokenX, request.tokenY, reverse);
        (bool edgeOk, uint256 edgeBps, uint256 directComparableAmount, uint256 viaComparableAmount) =
            _relativeEdge(request, reverse);
        if (!edgeOk || edgeBps < requiredEdgeBps) return decision;

        (bool quoteOk, uint256 finalUsdc) = _tryAmountsOut(request.router, request.amount, path);
        if (!quoteOk || finalUsdc < owedEstimate + request.minProfitUsdc) return decision;
        uint256 minAfterSlippage = (finalUsdc * (10000 - request.slippageBps)) / 10000;
        if (minAfterSlippage < owedEstimate + request.minProfitUsdc) return decision;
        decision = RouteDecision({
            viable: true,
            reverse: reverse,
            quotedFinalUsdc: finalUsdc,
            profitUsdc: finalUsdc - owedEstimate,
            path: path,
            edgeBps: edgeBps,
            requiredEdgeBps: requiredEdgeBps,
            directComparableAmount: directComparableAmount,
            viaComparableAmount: viaComparableAmount
        });
    }

    function _relativeEdge(RouteRequest calldata request, bool reverse)
        private
        view
        returns (bool ok, uint256 edgeBps, uint256 directComparableAmount, uint256 viaComparableAmount)
    {
        if (reverse) {
            (bool yOk, uint256 yAmount) =
                _tryAmountsOut(request.router, request.amount, _twoTokenPath(usdc, request.tokenY));
            (bool directXOk, uint256 directX) =
                _tryAmountsOut(request.router, request.amount, _twoTokenPath(usdc, request.tokenX));
            if (!yOk || !directXOk || directX == 0) return (false, 0, directX, 0);
            (bool viaXOk, uint256 viaX) =
                _tryAmountsOut(request.router, yAmount, _twoTokenPath(request.tokenY, request.tokenX));
            if (!viaXOk) return (false, 0, directX, viaX);
            return (true, _edgeBps(viaX, directX), directX, viaX);
        }

        (bool xOk, uint256 xAmount) =
            _tryAmountsOut(request.router, request.amount, _twoTokenPath(usdc, request.tokenX));
        (bool directYOk, uint256 directY) =
            _tryAmountsOut(request.router, request.amount, _twoTokenPath(usdc, request.tokenY));
        if (!xOk || !directYOk || directY == 0) return (false, 0, directY, 0);
        (bool viaYOk, uint256 viaY) =
            _tryAmountsOut(request.router, xAmount, _twoTokenPath(request.tokenX, request.tokenY));
        if (!viaYOk) return (false, 0, directY, viaY);
        return (true, _edgeBps(viaY, directY), directY, viaY);
    }

    function _tryAmountsOut(
        address router,
        uint256 amount,
        address[] memory path
    ) private view returns (bool ok, uint256 finalAmount) {
        (bool success, bytes memory result) = router.staticcall(
            abi.encodeWithSelector(IRouterController.getAmountsOut.selector, amount, path)
        );
        if (!success || result.length == 0) return (false, 0);
        uint256[] memory amounts = abi.decode(result, (uint256[]));
        if (amounts.length != path.length || amounts.length == 0) return (false, 0);
        finalAmount = amounts[amounts.length - 1];
        ok = finalAmount > 0;
    }

    function _routePath(address tokenX, address tokenY, bool reverse) private view returns (address[] memory path) {
        path = new address[](4);
        path[0] = usdc;
        if (reverse) {
            path[1] = tokenY;
            path[2] = tokenX;
        } else {
            path[1] = tokenX;
            path[2] = tokenY;
        }
        path[3] = usdc;
    }

    function _twoTokenPath(address tokenIn, address tokenOut) private pure returns (address[] memory path) {
        path = new address[](2);
        path[0] = tokenIn;
        path[1] = tokenOut;
    }

    function _requiredEdgeBps(RouteRequest calldata request) private pure returns (uint256) {
        return request.premiumBps + request.slippageBps + _ceilDiv(request.minProfitUsdc * 10000, request.amount);
    }

    function _edgeBps(uint256 viaAmount, uint256 directAmount) private pure returns (uint256) {
        if (viaAmount <= directAmount) return 0;
        return ((viaAmount - directAmount) * 10000) / directAmount;
    }

    function _ceilDiv(uint256 value, uint256 divisor) private pure returns (uint256) {
        return value == 0 ? 0 : ((value - 1) / divisor) + 1;
    }

    function _validateRequest(RouteRequest calldata request) private view {
        if (
            request.tokenX == address(0)
                || request.tokenY == address(0)
                || request.router == address(0)
                || request.tokenX == request.tokenY
                || request.tokenX == usdc
                || request.tokenY == usdc
                || request.amount == 0
                || request.premiumBps > 10000
                || request.deadline < block.timestamp
                || request.slippageBps > 5000
        ) {
            revert InvalidRequest();
        }
    }
}
