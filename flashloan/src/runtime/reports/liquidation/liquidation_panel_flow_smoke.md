# Liquidation Panel Flow Smoke

- Generated: `2026-08-17T11:29:16+00:00`
- Market: `bnb-venus-core`
- Chain ID: `56`
- Account: `0x017c5e471b5A84d924e89c8AB2a1a64A145a9356`
- OK: `True`

| Check | OK | Detail |
|---|---:|---|
| page_load | `True` | GET /liquidation -> 200 |
| borrow_pool_api | `True` | status=200 source=file_observation |
| current_pool_progress | `True` | current=3864 scanned=358 pending=3506 |
| accounts_api_progress | `True` | total=3864 health=358 pending=3506 |
| account_cached_api | `True` | account=0x017c5e471b5A84d924e89c8AB2a1a64A145a9356 status=200 |
| bnb_payload_observation_block | `True` | status=400 reasons=['venus_core_observation_only'] |
