const { ethers } = require("../lib/hardhat");

function sortPair(a, b) {
  return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a];
}

function makeKey(a, b, fee, tickSpacing, hooks) {
  const [currency0, currency1] = sortPair(a, b);
  return { currency0, currency1, fee, tickSpacing, hooks };
}

function poolId(key) {
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
      [key]
    )
  );
}

function zeroForOne(key, input) {
  return input.toLowerCase() === key.currency0.toLowerCase();
}

function normalizePoolParams(pool) {
  const pk = (pool && pool.poolKey) || pool || {};
  const currency0 = String(pk.currency0 || pool.currency0 || "");
  const currency1 = String(pk.currency1 || pool.currency1 || "");
  const feeRaw = pk.fee !== undefined ? pk.fee : pool.fee;
  const tickSpacingRaw = pk.tickSpacing !== undefined ? pk.tickSpacing : pool.tickSpacing;
  const hooks = String(pk.hooks || pool.hooks || "");
  if (!currency0 || !currency1 || feeRaw === undefined || tickSpacingRaw === undefined) {
    return null;
  }
  return {
    currency0,
    currency1,
    fee: Number(feeRaw),
    tickSpacing: Number(tickSpacingRaw),
    hooks,
  };
}

function loadExplicitPoolKeys(jsonText) {
  if (!jsonText) return null;
  const parsed = JSON.parse(jsonText);
  if (!Array.isArray(parsed) || parsed.length !== 3) {
    throw new Error("official V4 pool keys json must be a 3-element array");
  }
  return parsed.map((key) => normalizePoolParams(key)).filter(Boolean);
}

function buildPoolKeys(routeAddresses, inferredParams, defaults) {
  const fallbackFee = Number(defaults.fee);
  const fallbackTickSpacing = Number(defaults.tickSpacing);
  const fallbackHooks = defaults.hooks || ethers.ZeroAddress;
  if (routeAddresses.length !== 4) {
    throw new Error("official V4 route must be a 4-address closed loop");
  }
  const pairs = [
    [routeAddresses[0], routeAddresses[1]],
    [routeAddresses[1], routeAddresses[2]],
    [routeAddresses[2], routeAddresses[3]],
  ];
  return pairs.map(([a, b], index) => {
    const param = inferredParams && inferredParams[index];
    if (!param) {
      return makeKey(a, b, fallbackFee, fallbackTickSpacing, fallbackHooks);
    }
    return makeKey(
      a,
      b,
      param.fee || fallbackFee,
      param.tickSpacing || fallbackTickSpacing,
      param.hooks || fallbackHooks
    );
  });
}

function resolveRoutePoolKeys(routeAddresses, explicitJsonText, defaults) {
  const explicitKeys = loadExplicitPoolKeys(explicitJsonText);
  if (explicitKeys && explicitKeys.length === 3) {
    return buildPoolKeys(routeAddresses, explicitKeys, defaults);
  }
  if (
    defaults &&
    routeAddresses.length === 4 &&
    routeAddresses[0].toLowerCase() === routeAddresses[3].toLowerCase()
  ) {
    const defaultParams = Array.from({ length: 3 }, () => ({
      fee: defaults.fee,
      tickSpacing: defaults.tickSpacing,
      hooks: defaults.hooks,
    }));
    return buildPoolKeys(routeAddresses, defaultParams, defaults);
  }
  return buildPoolKeys(routeAddresses, [], defaults);
}

module.exports = {
  buildPoolKeys,
  loadExplicitPoolKeys,
  makeKey,
  poolId,
  resolveRoutePoolKeys,
  sortPair,
  zeroForOne,
};
