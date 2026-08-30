# Robinhood Contract Packages

This directory is the Robinhood contract root.

- `mainnet/`: independent Robinhood mainnet contract package
- `testnet/`: independent Robinhood testnet contract package
- `contracts.json` here is kept as a compatibility mirror for older tooling

Each chain package should own its own contract metadata, deployment notes, and
evidence pointers so the two chains do not share a single contract surface.
