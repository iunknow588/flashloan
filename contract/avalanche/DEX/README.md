# Avalanche DEX Contract Project

This project contains the Avalanche mainnet DEX/flash-accounting contracts and tools.

Primary source:

```text
src/UnifiedFlashLoanMevExecutor.sol
```

## Networks

```text
avalanche   chainId 43114
```

Fuji-specific contracts, deployments, and tools are isolated under:

```text
../avalanche-testnet/DEX/
```

## Commands

```powershell
npx hardhat compile
npx hardhat test test\UnifiedFlashLoanMevExecutor.test.js
npm run preflight:unified:avalanche
npm run readback:unified:avalanche
```

Deployment output stays under `deployments/`. Historical legacy implementations are archived under `contract/back/legacy-single-contract/`.
