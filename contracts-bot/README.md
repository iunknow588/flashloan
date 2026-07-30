# Liquidation Bot Contracts

This package contains the on-chain executor used by the liquidation scanner.

The bot should discover unhealthy Aave accounts, quote the collateral-to-debt swap off-chain, then call `requestLiquidation`. The contract:

1. Borrows the debt asset from Aave with `flashLoanSimple`.
2. Calls Aave V3 `liquidationCall`.
3. Swaps seized collateral back to the debt asset through the configured router.
4. Approves Aave to pull `amount + premium`.
5. Reverts unless the remaining debt-asset balance is at least `minProfitAmount`.

## Commands

```powershell
npm install
npm test
npm run test:fork
npm run simulate:liquidation
npm run deploy:avalanche
```

Copy `.env.example` to `.env` and set `DEPLOYER_PRIVATE_KEY` before deploying.

To simulate from a generated payload file:

```powershell
npm run simulate:liquidation -- --payload payload.json
```

## Pre-Execution Rule

Before sending a real liquidation transaction, the bot must run:

```text
requestLiquidation.staticCall(request)
```

The real transaction is allowed only when the static call succeeds and the off-chain model still satisfies:

```text
expected profit > premium + gas + slippage buffer + MEV buffer + rounding buffer
```

`debtToCover` must come from Aave-aware liquidation math or a successful fork/static simulation. Do not hard-code a 50% close factor.

The executor also expects the configured `USDC_ADDRESS`/`FUJI_USDC_ADDRESS` so it can sweep profits back into the base asset before withdrawal.
