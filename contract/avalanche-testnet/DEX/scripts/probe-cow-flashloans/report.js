// Small report/summary helpers for the CoW flashloan probe CLI.

function orderToSignSummary(order) {
  if (!order || typeof order !== "object") return null;
  return {
    sellToken: order.sellToken,
    buyToken: order.buyToken,
    receiver: order.receiver,
    sellAmount: order.sellAmount,
    buyAmount: order.buyAmount,
    validTo: order.validTo,
    appData: order.appData,
    feeAmount: order.feeAmount,
    kind: order.kind,
    partiallyFillable: order.partiallyFillable,
    sellTokenBalance: order.sellTokenBalance,
    buyTokenBalance: order.buyTokenBalance,
  };
}

function quoteResponseSummary(response) {
  if (!response || typeof response !== "object") return null;
  const quote = response.quote && typeof response.quote === "object" ? response.quote : {};
  return {
    id: response.id ?? response.quoteId ?? null,
    verified: response.verified ?? quote.verified ?? null,
    solver: response.solver ?? quote.solver ?? null,
    quote: {
      sellAmount: quote.sellAmount,
      buyAmount: quote.buyAmount,
      feeAmount: quote.feeAmount,
      gasAmount: quote.gasAmount,
      validTo: quote.validTo,
      appData: quote.appData,
      kind: quote.kind,
      partiallyFillable: quote.partiallyFillable,
    },
  };
}

function appDataSummary(appDataInfo, metadata) {
  const doc = appDataInfo?.doc && typeof appDataInfo.doc === "object" ? appDataInfo.doc : {};
  const docMetadata = doc.metadata && typeof doc.metadata === "object" ? doc.metadata : {};
  return {
    appDataKeccak256: appDataInfo?.appDataKeccak256 || null,
    fullAppDataPresent: Boolean(appDataInfo?.fullAppData),
    docVersion: doc.version || null,
    orderClass: docMetadata.orderClass?.orderClass || docMetadata.orderClass || null,
    metadataKeys: Object.keys(docMetadata),
    generatedMetadataKeys: Object.keys(metadata || {}),
  };
}

function hooksSummary(hooks) {
  const result = {};
  for (const key of ["pre", "post"]) {
    const rows = Array.isArray(hooks?.[key]) ? hooks[key] : [];
    result[key] = rows.map((hook) => ({
      target: hook.target,
      gasLimit: hook.gasLimit,
      callDataBytes: typeof hook.callData === "string" ? Math.max(0, (hook.callData.length - 2) / 2) : null,
    }));
  }
  return result;
}


module.exports = {
  orderToSignSummary,
  quoteResponseSummary,
  appDataSummary,
  hooksSummary,
};
