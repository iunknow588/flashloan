const hre = require("hardhat");
const { reserveConfigFlags } = require("./preflight-unified-flashloan");

const AAVE_POOL = "0x8B9b2AF4afB389b4a70A474dfD4AdCD4a302bb40";
const USDC = "0x5425890298aed601595a70AB815c96711a31Bc65";
const FUJI_V2_ROUTER = "0xd7f655E3376cE2D7A2b08fF01Eb3B1023191A901";

async function main() {
  const network = await hre.ethers.provider.getNetwork();
  const pool = await hre.ethers.getContractAt(
    [
      "function getReservesList() view returns (address[])",
      "function getReserveData(address) view returns (uint256 configuration,uint128 liquidityIndex,uint128 currentLiquidityRate,uint128 variableBorrowIndex,uint128 currentVariableBorrowRate,uint128 currentStableBorrowRate,uint40 lastUpdateTimestamp,uint16 id,address aTokenAddress,address stableDebtTokenAddress,address variableDebtTokenAddress,address interestRateStrategyAddress,uint128 accruedToTreasury,uint128 unbacked,uint128 isolationModeTotalDebt)",
      "function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)",
    ],
    AAVE_POOL,
  );
  const tokenAbi = [
    "function symbol() view returns (string)",
    "function decimals() view returns (uint8)",
    "function balanceOf(address) view returns (uint256)",
  ];
  const reserves = [];
  for (const asset of await pool.getReservesList()) {
    const token = await hre.ethers.getContractAt(tokenAbi, asset);
    const data = await pool.getReserveData(asset);
    const [symbol, decimals, liquidity] = await Promise.all([
      token.symbol().catch(() => ""),
      token.decimals().catch(() => 0),
      token.balanceOf(data.aTokenAddress).catch(() => 0n),
    ]);
    reserves.push({
      asset,
      symbol,
      decimals: Number(decimals),
      availableLiquidity: liquidity.toString(),
      ...reserveConfigFlags(data.configuration),
    });
  }

  const router = await hre.ethers.getContractAt(
    ["function factory() view returns (address)"],
    FUJI_V2_ROUTER,
  );
  const factoryAddress = await router.factory();
  const factory = await hre.ethers.getContractAt(
    ["function getPair(address,address) view returns (address)"],
    factoryAddress,
  );
  const pairs = [];
  for (const reserve of reserves) {
    if (reserve.asset.toLowerCase() === USDC.toLowerCase()) continue;
    const pair = await factory.getPair(USDC, reserve.asset).catch(() => hre.ethers.ZeroAddress);
    pairs.push({ symbol: reserve.symbol, token: reserve.asset, pair, exists: pair !== hre.ethers.ZeroAddress });
  }

  console.log(JSON.stringify({
    network: hre.network.name,
    chainId: Number(network.chainId),
    aavePool: AAVE_POOL,
    usdc: USDC,
    flashloanPremiumBps: (await pool.FLASHLOAN_PREMIUM_TOTAL()).toString(),
    reserves,
    fujiV2: { router: FUJI_V2_ROUTER, factory: factoryAddress, usdcPairs: pairs },
    conclusion: {
      currentExecutorAdapter: "uniswap_v3_only",
      currentFujiRouterFamily: "lfj_v2_or_liquidity_book",
      deployableWithCurrentConfig: false,
      reason: "A valid Fuji V3 factory/router/quoter and a non-empty Fuji V3 pool cache are required before deployment",
    },
  }, null, 2));
}

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
