const { expect } = require("chai");
const { ethers } = require("hardhat");

const FLASH_SOURCE_MORPHO_BLUE = 1n;
const FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER = 4n;
const SWAP_ADAPTER_UNISWAP_V3 = 3n;
const SWAP_ADAPTER_UNISWAP_V4 = 4n;

function addr(value) {
  return ethers.getAddress(`0x${value.toString(16).padStart(40, "0")}`);
}

async function deployFixture() {
  const [owner, other] = await ethers.getSigners();
  const Executor = await ethers.getContractFactory("RobinhoodPausedFlashLoanExecutor");
  const executor = await Executor.deploy(owner.address);
  return { owner, other, executor };
}

function v4Swap(currency0, currency1, zeroForOne, amountIn = 1_000_000n, amountOutMinimum = 990_000n) {
  return {
    poolKey: {
      currency0,
      currency1,
      fee: 3000,
      tickSpacing: 60,
      hooks: ethers.ZeroAddress,
    },
    zeroForOne,
    amountIn,
    amountOutMinimum,
    hookData: "0x",
  };
}

function v4SwapForDirection(tokenIn, tokenOut, amountIn = 1_000_000n, amountOutMinimum = 990_000n) {
  if (BigInt(tokenIn) < BigInt(tokenOut)) {
    return v4Swap(tokenIn, tokenOut, true, amountIn, amountOutMinimum);
  }
  return v4Swap(tokenOut, tokenIn, false, amountIn, amountOutMinimum);
}

function emptyV4Swap() {
  return v4Swap(addr(0x10001n), addr(0x10002n), true);
}

function v3Swap(tokenIn, tokenOut, amountIn = 1_000_000n, amountOutMinimum = 990_000n) {
  return {
    tokenIn,
    tokenOut,
    fee: 3000,
    amountIn,
    amountOutMinimum,
    sqrtPriceLimitX96: 0n,
  };
}

function emptyV3Swap() {
  return v3Swap(addr(0x10003n), addr(0x10004n));
}

async function deployRouteFixture() {
  const { owner, other, executor } = await deployFixture();
  const Token = await ethers.getContractFactory("TestERC20");
  const token = await Token.deploy("USDG", "USDG", 6, owner.address);
  const Morpho = await ethers.getContractFactory("MockMorphoFlashSource");
  const morpho = await Morpho.deploy();
  const Router = await ethers.getContractFactory("MockRobinhoodUniversalRouter");
  const router = await Router.deploy();
  await executor.setFlashSourceAdapter(
    FLASH_SOURCE_MORPHO_BLUE,
    true,
    await morpho.getAddress(),
    0,
    ethers.ZeroHash,
  );
  await executor.setUniswapV4Adapter(true, await router.getAddress(), addr(0x8001n), addr(0x8002n), ethers.ZeroHash);
  await executor.setRepaymentGuard(0, 0, 0, true);
  await executor.setPaused(false);
  return { owner, other, executor, token, morpho, router };
}

async function deployV4FlashFixture() {
  const { owner, other, executor } = await deployFixture();
  const Token = await ethers.getContractFactory("TestERC20");
  const token = await Token.deploy("USDG", "USDG", 6, owner.address);
  const PoolManager = await ethers.getContractFactory("MockV4PoolManager");
  const poolManager = await PoolManager.deploy();
  const Router = await ethers.getContractFactory("MockRobinhoodUniversalRouter");
  const router = await Router.deploy();
  await executor.setFlashSourceAdapter(
    FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER,
    true,
    await poolManager.getAddress(),
    0,
    ethers.ZeroHash,
  );
  await executor.setUniswapV4Adapter(
    true,
    await router.getAddress(),
    await poolManager.getAddress(),
    addr(0x8802n),
    ethers.ZeroHash,
  );
  await executor.setRepaymentGuard(0, 0, 0, true);
  return { owner, other, executor, token, poolManager, router };
}

describe("RobinhoodPausedFlashLoanExecutor", function () {
  it("deploys paused with Robinhood-specific adapters unset", async function () {
    const { owner, executor } = await deployFixture();

    expect(await executor.owner()).to.equal(owner.address);
    expect(await executor.paused()).to.equal(true);

    const flashSource = await executor.flashSourceAdapter();
    const v4Adapter = await executor.uniswapV4Adapter();
    expect(flashSource.enabled).to.equal(false);
    expect(flashSource.kind).to.equal(0n);
    expect(v4Adapter.enabled).to.equal(false);
  });

  it("configures Morpho Blue compatibility scaffolding and Uniswap V4 calldata adapter", async function () {
    const { executor } = await deployFixture();
    const morpho = addr(0x1001n);
    const router = addr(0x2001n);
    const poolManager = addr(0x2002n);
    const permit2 = addr(0x2003n);
    const flashHash = ethers.keccak256(ethers.toUtf8Bytes("robinhood:morpho"));
    const v4Hash = ethers.keccak256(ethers.toUtf8Bytes("robinhood:uniswap-v4"));

    await executor.setFlashSourceAdapter(FLASH_SOURCE_MORPHO_BLUE, true, morpho, 0, flashHash);
    await executor.setUniswapV4Adapter(true, router, poolManager, permit2, v4Hash);

    const flashSource = await executor.flashSourceAdapter();
    const v4Adapter = await executor.uniswapV4Adapter();
    expect(flashSource.enabled).to.equal(true);
    expect(flashSource.kind).to.equal(FLASH_SOURCE_MORPHO_BLUE);
    expect(flashSource.source).to.equal(morpho);
    expect(flashSource.feeBps).to.equal(0n);
    expect(v4Adapter.enabled).to.equal(true);
    expect(v4Adapter.universalRouter).to.equal(router);
    expect(v4Adapter.poolManager).to.equal(poolManager);
    expect(v4Adapter.permit2).to.equal(permit2);
  });

  it("configures Uniswap V4 PoolManager as a flash-accounting source", async function () {
    const { executor } = await deployFixture();
    const poolManager = addr(0x24001n);
    const flashHash = ethers.keccak256(ethers.toUtf8Bytes("robinhood:v4-poolmanager-flash-accounting"));

    await executor.setFlashSourceAdapter(FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER, true, poolManager, 0, flashHash);

    const flashSource = await executor.flashSourceAdapter();
    expect(flashSource.enabled).to.equal(true);
    expect(flashSource.kind).to.equal(FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER);
    expect(flashSource.source).to.equal(poolManager);
    expect(flashSource.feeBps).to.equal(0n);
    expect(flashSource.metadataHash).to.equal(flashHash);
  });

  it("rejects unsupported flash source kinds", async function () {
    const { executor } = await deployFixture();

    await expect(
      executor.setFlashSourceAdapter(2, true, addr(0x3001n), 30, ethers.ZeroHash),
    ).to.be.revertedWithCustomError(executor, "UnsupportedFlashSourceKind").withArgs(2);
  });

  it("keeps flash loan requests blocked while paused", async function () {
    const { executor } = await deployFixture();
    await executor.setFlashSourceAdapter(FLASH_SOURCE_MORPHO_BLUE, true, addr(0x4001n), 0, ethers.ZeroHash);

    const request = {
      token: addr(0x5001n),
      amount: 1_000_000n,
      swapTarget: ethers.ZeroAddress,
      swapCalldata: "0x",
      minProfit: 1n,
      gasReserve: 0n,
    };
    await expect(executor.requestMorphoFlashLoan(request)).to.be.revertedWithCustomError(executor, "Paused");
  });

  it("keeps V4 PoolManager flash accounting requests blocked while paused", async function () {
    const { executor, token } = await deployV4FlashFixture();
    const tokenAddress = await token.getAddress();
    const request = {
      token: tokenAddress,
      amount: 1_000n,
      swapTargets: [],
      swapCalldatas: [],
      finalToken: tokenAddress,
      minProfit: 0n,
      gasReserve: 0n,
    };

    await expect(executor.requestV4FlashLoan(request)).to.be.revertedWithCustomError(executor, "Paused");
  });

  it("builds Uniswap V4 Universal Router exact-input-single calldata", async function () {
    const { executor } = await deployFixture();
    const router = addr(0x6001n);
    await executor.setUniswapV4Adapter(true, router, addr(0x6002n), addr(0x6003n), ethers.ZeroHash);

    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const swap = {
      poolKey: {
        currency0: addr(0x7001n),
        currency1: addr(0x7002n),
        fee: 3000,
        tickSpacing: 60,
        hooks: ethers.ZeroAddress,
      },
      zeroForOne: true,
      amountIn: 1_000_000n,
      amountOutMinimum: 990_000n,
      hookData: "0x1234",
    };

    const [target, data, commands, inputs] = await executor.buildV4ExactInputSingleCalldata(swap, deadline);

    expect(target).to.equal(router);
    expect(commands).to.equal("0x10");
    expect(inputs).to.have.length(1);

    const universalRouter = new ethers.Interface([
      "function execute(bytes commands, bytes[] inputs, uint256 deadline)",
    ]);
    const decodedCall = universalRouter.decodeFunctionData("execute", data);
    expect(decodedCall.commands).to.equal("0x10");
    expect(decodedCall.deadline).to.equal(deadline);

    const coder = ethers.AbiCoder.defaultAbiCoder();
    const [actions, params] = coder.decode(["bytes", "bytes[]"], decodedCall.inputs[0]);
    expect(actions).to.equal("0x060c0f");
    expect(params).to.have.length(3);

    const [settleCurrency, settleAmount] = coder.decode(["address", "uint128"], params[1]);
    const [takeCurrency, takeAmountMinimum] = coder.decode(["address", "uint128"], params[2]);
    expect(settleCurrency).to.equal(swap.poolKey.currency0);
    expect(settleAmount).to.equal(swap.amountIn);
    expect(takeCurrency).to.equal(swap.poolKey.currency1);
    expect(takeAmountMinimum).to.equal(swap.amountOutMinimum);
  });

  it("builds multi-hop V4 route calldata only when tokens are continuous", async function () {
    const { executor } = await deployFixture();
    const router = addr(0x6101n);
    await executor.setUniswapV4Adapter(true, router, addr(0x6102n), addr(0x6103n), ethers.ZeroHash);
    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const tokenA = addr(0x7101n);
    const tokenB = addr(0x7102n);
    const tokenC = addr(0x7103n);

    const [targets, calldatas, initialToken, finalToken] = await executor.buildV4ExactInputRouteCalldata(
      [v4Swap(tokenA, tokenB, true), v4Swap(tokenB, tokenC, true), v4Swap(tokenA, tokenC, false)],
      deadline,
    );

    expect(targets).to.deep.equal([router, router, router]);
    expect(calldatas).to.have.length(3);
    expect(initialToken).to.equal(tokenA);
    expect(finalToken).to.equal(tokenA);

    await expect(
      executor.buildV4ExactInputRouteCalldata(
        [v4Swap(tokenA, tokenB, true), v4Swap(tokenA, tokenC, false)],
        deadline,
      ),
    ).to.be.revertedWithCustomError(executor, "InvalidRequest");
  });

  it("builds V3/V4 mixed route calldata with token continuity checks", async function () {
    const { executor } = await deployFixture();
    const v3Router = addr(0x6201n);
    const v4Router = addr(0x6202n);
    await executor.setUniswapV3Adapter(true, v3Router, addr(0x6203n), addr(0x6204n), ethers.ZeroHash);
    await executor.setUniswapV4Adapter(true, v4Router, addr(0x6205n), addr(0x6206n), ethers.ZeroHash);
    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const tokenA = addr(0x7201n);
    const tokenB = addr(0x7202n);
    const tokenC = addr(0x7203n);

    const [targets, calldatas, initialToken, finalToken] = await executor.buildV3V4MixedRouteCalldata(
      [
        {
          adapterKind: SWAP_ADAPTER_UNISWAP_V3,
          v3: v3Swap(tokenA, tokenB),
          v4: emptyV4Swap(),
        },
        {
          adapterKind: SWAP_ADAPTER_UNISWAP_V4,
          v3: emptyV3Swap(),
          v4: v4SwapForDirection(tokenB, tokenC),
        },
        {
          adapterKind: SWAP_ADAPTER_UNISWAP_V3,
          v3: v3Swap(tokenC, tokenA),
          v4: emptyV4Swap(),
        },
      ],
      deadline,
    );

    expect(targets).to.deep.equal([v3Router, v4Router, v3Router]);
    expect(calldatas[0].slice(0, 10)).to.equal("0x414bf389");
    expect(calldatas[1].slice(0, 10)).to.equal("0x3593564c");
    expect(initialToken).to.equal(tokenA);
    expect(finalToken).to.equal(tokenA);

    await expect(
      executor.buildV3V4MixedRouteCalldata(
        [
          {
            adapterKind: SWAP_ADAPTER_UNISWAP_V3,
            v3: v3Swap(tokenA, tokenB),
            v4: emptyV4Swap(),
          },
          {
            adapterKind: SWAP_ADAPTER_UNISWAP_V3,
            v3: v3Swap(tokenC, tokenA),
            v4: emptyV4Swap(),
          },
        ],
        deadline,
      ),
    ).to.be.revertedWithCustomError(executor, "InvalidRequest");
  });

  it("rejects direct callbacks from non-Morpho callers", async function () {
    const { executor, morpho } = await deployRouteFixture();
    expect(await morpho.getAddress()).to.not.equal(ethers.ZeroAddress);

    await expect(executor.onMorphoFlashLoan(1n, "0x")).to.be.revertedWithCustomError(
      executor,
      "BadFlashCallback",
    );
  });

  it("rejects route callbacks when final balance cannot cover repayment constraints", async function () {
    const { executor, token, morpho, router } = await deployRouteFixture();
    const amount = 1_000_000n;
    await token.mint(await morpho.getAddress(), amount);
    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const routerInterface = new ethers.Interface(["function execute(bytes commands, bytes[] inputs, uint256 deadline)"]);

    const request = {
      token: await token.getAddress(),
      amount,
      swapTargets: [await router.getAddress()],
      swapCalldatas: [routerInterface.encodeFunctionData("execute", ["0x10", ["0x"], deadline])],
      finalToken: await token.getAddress(),
      minProfit: 1n,
      gasReserve: 0n,
    };

    await expect(executor.requestMorphoFlashLoanRoute(request)).to.be.revertedWithCustomError(
      executor,
      "BadFlashRepayment",
    );
  });

  it("executes a mocked ordered flash route and approves principal repayment", async function () {
    const { executor, token, morpho, router } = await deployRouteFixture();
    const amount = 1_000_000n;
    await token.mint(await morpho.getAddress(), amount);

    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const [targets, calldatas] = await executor.buildV4ExactInputRouteCalldata(
      [
        v4SwapForDirection(await token.getAddress(), addr(0x9001n)),
        v4SwapForDirection(addr(0x9001n), await token.getAddress()),
      ],
      deadline,
    );
    const request = {
      token: await token.getAddress(),
      amount,
      swapTargets: Array.from(targets),
      swapCalldatas: Array.from(calldatas),
      finalToken: await token.getAddress(),
      minProfit: 0n,
      gasReserve: 0n,
    };

    await expect(executor.requestMorphoFlashLoanRoute(request))
      .to.emit(executor, "RobinhoodRouteFlashRequested")
      .withArgs(await token.getAddress(), amount, await morpho.getAddress(), 2n);
    expect(await token.balanceOf(await morpho.getAddress())).to.equal(amount);
    expect(await token.balanceOf(await executor.getAddress())).to.equal(0n);
    expect(await router.queryFilter(router.filters.Executed())).to.have.length(2);
  });

  it("runs a mocked V4 PoolManager unlock take settle cycle", async function () {
    const { executor, token, poolManager } = await deployV4FlashFixture();
    const amount = 1_000n;
    await token.mint(await poolManager.getAddress(), amount);
    await executor.setPaused(false);

    const tokenAddress = await token.getAddress();
    const request = {
      token: tokenAddress,
      amount,
      swapTargets: [],
      swapCalldatas: [],
      finalToken: tokenAddress,
      minProfit: 0n,
      gasReserve: 0n,
    };

    await expect(executor.requestV4FlashLoan(request))
      .to.emit(executor, "RobinhoodV4FlashAccountingBorrowed")
      .withArgs(tokenAddress, amount, amount)
      .and.to.emit(executor, "RobinhoodV4FlashAccountingSettled")
      .withArgs(tokenAddress, amount, amount)
      .and.to.emit(executor, "RobinhoodV4FlashAccountingRequested")
      .withArgs(tokenAddress, amount, await poolManager.getAddress(), 0n);

    expect(await token.balanceOf(await poolManager.getAddress())).to.equal(amount);
    expect(await token.balanceOf(await executor.getAddress())).to.equal(0n);
    expect(await poolManager.currencyDebt(await executor.getAddress(), tokenAddress)).to.equal(0n);
  });

  it("runs a mocked V4 PoolManager flash route before settling", async function () {
    const { executor, token, poolManager, router } = await deployV4FlashFixture();
    const amount = 1_000n;
    await token.mint(await poolManager.getAddress(), amount);
    await executor.setPaused(false);
    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const routerInterface = new ethers.Interface(["function execute(bytes commands, bytes[] inputs, uint256 deadline)"]);

    const tokenAddress = await token.getAddress();
    const request = {
      token: tokenAddress,
      amount,
      swapTargets: [await router.getAddress()],
      swapCalldatas: [routerInterface.encodeFunctionData("execute", ["0x10", ["0x"], deadline])],
      finalToken: tokenAddress,
      minProfit: 0n,
      gasReserve: 0n,
    };

    await expect(executor.requestV4FlashLoan(request))
      .to.emit(executor, "RobinhoodRouteHopExecuted")
      .withArgs(1n, await router.getAddress())
      .and.to.emit(executor, "RobinhoodV4FlashAccountingSettled")
      .withArgs(tokenAddress, amount, amount);
    expect(await router.queryFilter(router.filters.Executed())).to.have.length(1);
    expect(await poolManager.currencyDebt(await executor.getAddress(), tokenAddress)).to.equal(0n);
  });

  it("previews required balance in borrowed token raw units", async function () {
    const { executor } = await deployFixture();

    await executor.setRepaymentGuard(5n, 7n, 100n, true);
    const [requiredRepay, requiredBalance] = await executor.previewRequiredBalance(1_000_000n, 5, 1n, 0n);

    expect(requiredRepay).to.equal(1_000_500n);
    expect(requiredBalance).to.equal(1_000_512n);
  });

  it("accepts a non-stable V4 flash-accounting route with positive X-denominated profit", async function () {
    const { executor, poolManager, router } = await deployV4FlashFixture();
    const [owner] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("TestERC20");
    const token = await Token.deploy("Wrapped Ether", "WETH", 18, owner.address);
    const tokenAddress = await token.getAddress();
    const amount = 10n ** 18n;
    const minProfit = 1n;
    await token.mint(await poolManager.getAddress(), amount);
    await token.mint(await router.getAddress(), minProfit);
    await router.setTransferOnNextExecute(tokenAddress, minProfit);
    await executor.setPaused(false);

    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const routerInterface = new ethers.Interface(["function execute(bytes commands, bytes[] inputs, uint256 deadline)"]);
    const request = {
      token: tokenAddress,
      amount,
      swapTargets: [await router.getAddress()],
      swapCalldatas: [routerInterface.encodeFunctionData("execute", ["0x10", ["0x"], deadline])],
      finalToken: tokenAddress,
      minProfit,
      gasReserve: 0n,
    };

    await expect(executor.requestV4FlashLoan(request))
      .to.emit(executor, "RobinhoodProfitGuardChecked")
      .withArgs(tokenAddress, amount + minProfit, amount, minProfit, 0n, amount + minProfit, minProfit)
      .and.to.emit(executor, "RobinhoodV4FlashAccountingSettled")
      .withArgs(tokenAddress, amount, amount);

    expect(await token.balanceOf(await poolManager.getAddress())).to.equal(amount);
    expect(await token.balanceOf(await executor.getAddress())).to.equal(minProfit);
    expect(await poolManager.currencyDebt(await executor.getAddress(), tokenAddress)).to.equal(0n);
  });

  it("supports native ETH as the V4 flash-accounting borrowed X token", async function () {
    const { owner, executor, poolManager, router } = await deployV4FlashFixture();
    const amount = 10n ** 18n;
    const minProfit = 1n;
    await owner.sendTransaction({ to: await poolManager.getAddress(), value: amount });
    await owner.sendTransaction({ to: await router.getAddress(), value: minProfit });
    await router.setNativeTransferOnNextExecute(amount + minProfit);
    await executor.setPaused(false);

    const block = await ethers.provider.getBlock("latest");
    const deadline = BigInt(block.timestamp + 600);
    const routerInterface = new ethers.Interface(["function execute(bytes commands, bytes[] inputs, uint256 deadline)"]);
    const request = {
      token: ethers.ZeroAddress,
      amount,
      swapTargets: [await router.getAddress()],
      swapCalldatas: [routerInterface.encodeFunctionData("execute", ["0x10", ["0x"], deadline])],
      swapValues: [amount],
      finalToken: ethers.ZeroAddress,
      minProfit,
      gasReserve: 0n,
    };

    await expect(executor.requestV4FlashLoanWithValues(request))
      .to.emit(router, "NativeValueReceived")
      .withArgs(await executor.getAddress(), amount)
      .and.to.emit(executor, "RobinhoodProfitGuardChecked")
      .withArgs(ethers.ZeroAddress, amount + minProfit, amount, minProfit, 0n, amount + minProfit, minProfit)
      .and.to.emit(executor, "RobinhoodV4FlashAccountingSettled")
      .withArgs(ethers.ZeroAddress, amount, amount);

    expect(await ethers.provider.getBalance(await executor.getAddress())).to.equal(minProfit);
    expect(await poolManager.currencyDebt(await executor.getAddress(), ethers.ZeroAddress)).to.equal(0n);
  });

  it("rejects V4 PoolManager flash accounting when repayment guard needs profit", async function () {
    const { executor, token, poolManager } = await deployV4FlashFixture();
    const amount = 1_000n;
    await token.mint(await poolManager.getAddress(), amount);
    await executor.setPaused(false);

    const tokenAddress = await token.getAddress();
    const request = {
      token: tokenAddress,
      amount,
      swapTargets: [],
      swapCalldatas: [],
      finalToken: tokenAddress,
      minProfit: 1n,
      gasReserve: 0n,
    };

    await expect(executor.requestV4FlashLoan(request)).to.be.revertedWithCustomError(
      executor,
      "BadFlashRepayment",
    );
  });

  it("rejects direct V4 unlock callbacks from non-PoolManager callers", async function () {
    const { executor } = await deployV4FlashFixture();
    await executor.setPaused(false);

    await expect(executor.unlockCallback("0x")).to.be.revertedWithCustomError(executor, "BadFlashCallback");
  });
});
