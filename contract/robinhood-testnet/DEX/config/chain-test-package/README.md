# Robinhood Contract Notes

- Chain: `robinhood-testnet`
- Chain ID: `46630`
- Market: `dex-arbitrage-46630-uniswap-v4`
- Current state: read-only evidence and preflight preparation only
- Current route preflight status: `blocked_route_execution_size_probe`
- Route execution mode: `pool_manager_direct_swap_settle_take`
- Default V4 quoter: `0x8dc178efb8111bb0973dd9d722ebeff267c98f94`
- Deployment gate: blocked until token continuity, calldata, repayment, and fork/static-call pass
- Deployment: no dedicated broadcast executor is added by this package
- Route test scope: calldata, slippage, repayment, and fork/static-call verification
