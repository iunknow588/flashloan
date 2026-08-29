# Robinhood Contract Notes

- Chain: `robinhood-testnet`
- Chain ID: `46630`
- Market: `robinhood-testnet-dex-paused`
- Current state: read-only evidence and preflight preparation only
- Current route preflight status: `ready_for_fork_staticcall`
- Deployment gate: blocked until token continuity, calldata, repayment, and fork/static-call pass
- Deployment: no dedicated broadcast executor is added by this package
- Route test scope: calldata, slippage, repayment, and fork/static-call verification
