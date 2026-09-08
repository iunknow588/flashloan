# 娴嬭瘯鐩綍鏁寸悊瀹屾垚鎶ュ憡

## 浠诲姟姒傝堪

瀹屾垚鏃堕棿锛?026-08-08 06:40 GMT+8

鍚庣画鏁寸悊锛?026-08-08

璺ㄩ〉闈㈠姛鑳芥祴璇曞凡缁х画鎸夊姛鑳藉綊妗ｏ細

- `unit/intent_trade/test_builder.py`
- `unit/config/test_intent_trade_config.py`
- `unit/account_pool/test_account_pool_state.py`
- `unit/debt_pool/test_debt_pool_workflow.py`
- `unit/market_events/test_volatility.py`
- `unit/market_events/test_store.py`
- `integration/liquidation/test_liquidation_discovery_service.py`
- `integration/liquidation/test_liquidation_discovery_workflow.py`
- `integration/liquidation/test_liquidation_execution_service.py`
- `functional/liquidation/test_liquidation_web_helpers.py`
- `functional/page_state/test_page_state_service.py`
- `functional/observer_runtime/test_observer_runtime_service.py`

灏?`flashloan/app_runtime/tests` 鐩綍涓嬬殑 78 涓祴璇曟枃浠舵寜 **鍗曞厓娴嬭瘯銆侀泦鎴愭祴璇曘€佸姛鑳芥祴璇?* 涓夌被閲嶆柊鍒嗙被鏁寸悊銆?
## 鐩綍缁撴瀯

娉細涓嬮潰鐨勫師濮嬫爲褰㈢粨鏋勮褰曠殑鏄娆℃寜 unit/integration/functional 鏁寸悊鏃剁殑蹇収锛涘悗缁法椤甸潰鍔熻兘宸茬户缁縼鍏ュ悓鍚嶅姛鑳界洰褰曪紝瑙佷笂鏂光€滆法椤甸潰鍔熻兘娴嬭瘯宸茬户缁寜鍔熻兘褰掓。鈥濆垪琛ㄣ€?
```
tests/
鈹溾攢鈹€ conftest.py              # pytest閰嶇疆锛堜繚鐣欐牴鐩綍锛?鈹溾攢鈹€ __init__.py              # 绌烘枃浠讹紙淇濈暀鏍圭洰褰曪級
鈹溾攢鈹€ README.md                # 娴嬭瘯鐩綍璇存槑鏂囨。
鈹溾攢鈹€ unit/                    # 58涓祴璇曟枃浠?鈹?  鈹溾攢鈹€ test_liquidation_amounts.py
鈹?  鈹溾攢鈹€ test_liquidation_priority.py
鈹?  鈹溾攢鈹€ test_liquidation_preflight.py
鈹?  鈹溾攢鈹€ test_profit_guard.py
鈹?  鈹溾攢鈹€ test_gas_estimator.py
鈹?  鈹溾攢鈹€ test_nonce_manager.py
鈹?  鈹溾攢鈹€ test_private_tx.py
鈹?  鈹溾攢鈹€ test_parallel_submitter.py
鈹?  鈹溾攢鈹€ cow_flashloan/test_routes.py
鈹?  鈹溾攢鈹€ cow_flashloan/test_capabilities.py
鈹?  鈹溾攢鈹€ test_market_config.py
鈹?  鈹溾攢鈹€ test_config_schema.py
鈹?  鈹溾攢鈹€ test_observer_window_extremes.py
鈹?  鈹溾攢鈹€ test_liquidation_samples.py
鈹?  鈹溾攢鈹€ test_liquidation_evidence_audit.py
鈹?  鈹溾攢鈹€ test_architecture_guards.py
鈹?  鈹溾攢鈹€ test_secret_leakage_guards.py
鈹?  鈹斺攢鈹€ ... (41 more files)
鈹溾攢鈹€ integration/             # 10涓祴璇曟枃浠?鈹?  鈹溾攢鈹€ test_liquidation_engine.py
鈹?  鈹溾攢鈹€ test_liquidation_scan.py
鈹?  鈹溾攢鈹€ test_liquidation_discovery_service.py
鈹?  鈹溾攢鈹€ test_liquidation_discovery_workflow.py
鈹?  鈹溾攢鈹€ test_external_liquidation_index.py
鈹?  鈹溾攢鈹€ test_liquidation_execution_service.py
鈹?  鈹溾攢鈹€ test_liquidation_audit_service.py
鈹?  鈹溾攢鈹€ cow_flashloan/test_candidate_queue.py
鈹?  鈹溾攢鈹€ test_market_observer_daemon.py
鈹?  鈹斺攢鈹€ test_binance_market_snapshot_daemon.py
鈹溾攢鈹€ functional/              # 10涓祴璇曟枃浠?鈹?  鈹溾攢鈹€ test_web_app_assembly.py
鈹?  鈹溾攢鈹€ test_navigation_flow.py
鈹?  鈹溾攢鈹€ test_control_panel_status.py
鈹?  鈹溾攢鈹€ test_control_panel_liquidation_actions.py
鈹?  鈹溾攢鈹€ test_exchange_matrix_page.py
鈹?  鈹溾攢鈹€ test_page_state_service.py
鈹?  鈹溾攢鈹€ test_control_panel_data_extremes.py
鈹?  鈹溾攢鈹€ binance_market/test_service.py
鈹?  鈹溾攢鈹€ test_observer_runtime_service.py
鈹?  鈹斺攢鈹€ test_liquidation_web_helpers.py
鈹斺攢鈹€ e2e/                     # 绔埌绔祴璇曪紙棰勭暀绌虹洰褰曪級
```

## 鍒嗙被鏍囧噯

### 鍗曞厓娴嬭瘯 (unit/)
- **绾€昏緫娴嬭瘯**锛氫笉渚濊禆澶栭儴鏈嶅姟锛堟暟鎹簱銆佺綉缁溿€乄eb鏈嶅姟鍣級
- **鍗曚竴鑱岃矗**锛氭瘡涓祴璇曞彧楠岃瘉涓€涓嚱鏁版垨绫荤殑琛屼负
- **浣跨敤Fake/Mock**锛氬ぇ閲忎娇鐢?FakeEth銆丗akeWeb3銆丗akeContract 绛夋々瀵硅薄
- **蹇€熸墽琛?*锛氬ぇ閮ㄥ垎鍦ㄦ绉掔骇瀹屾垚

**绀轰緥**锛?- `test_liquidation_amounts.py` - token_amount鍗曚綅杞崲锛?_500_000/6=1.5锛?- `test_profit_guard.py` - 鍒╂鼎璁＄畻锛?.0 - 0.5 - 0.1 = 1.4锛?- `test_gas_estimator.py` - Gas绛栫暐姣旇緝

### 闆嗘垚娴嬭瘯 (integration/)
- **澶氱粍浠跺崗浣?*锛氭祴璇曞涓ā鍧椾箣闂寸殑浜や簰
- **渚濊禆娉ㄥ叆**锛氫娇鐢?_deps() helper 娉ㄥ叆渚濊禆
- **宸ヤ綔娴侀獙璇?*锛氶獙璇佸畬鏁翠笟鍔℃祦绋?- **涓瓑鎵ц鏃堕棿**锛氶€氬父鍑犵瀹屾垚

**绀轰緥**锛?- `test_liquidation_engine.py` - LiquidationEngine 渚濊禆娉ㄥ叆娴嬭瘯
- `test_liquidation_scan.py` - 瀹屾暣鎵弿娴佺▼锛?48琛屽ぇ鏂囦欢锛?- `cow_flashloan/test_candidate_queue.py` - CoW鍊欓€夐槦鍒楃鐞?
### 鍔熻兘娴嬭瘯 (functional/)
- **Web鐣岄潰娴嬭瘯**锛氭祴璇曡矾鐢便€侀〉闈㈡覆鏌撱€丄PI鍝嶅簲
- **鐘舵€佺鐞?*锛氭祴璇曢〉闈㈢姸鎬併€佹帶鍒剁姸鎬併€佽繍琛屾椂鐘舵€?- **绔埌绔満鏅?*锛氫粠鐢ㄦ埛瑙嗚楠岃瘉鍔熻兘
- **杈冮暱鎵ц鏃堕棿**锛氭秹鍙奆lask搴旂敤缁勮锛岄€氬父10-30绉?
**绀轰緥**锛?- `test_web_app_assembly.py` - 19涓祴璇曢獙璇佽矾鐢辨敞鍐屽拰椤甸潰娓叉煋
- `test_control_panel_status.py` - 鎺у埗闈㈡澘鐘舵€丄PI
- `test_observer_runtime_service.py` - 杩愯鏃舵湇鍔★紙鍚姩杩涘害銆佸績璺筹級

## 娴嬭瘯缁撴灉

| 娴嬭瘯绫诲瀷 | 鏂囦欢鏁?| 娴嬭瘯鏁?| 缁撴灉 | 鎵ц鏃堕棿 |
|---------|--------|--------|------|----------|
| 鍗曞厓娴嬭瘯 | 58 | 303 | 鉁?鍏ㄩ儴閫氳繃 | 72绉?|
| 闆嗘垚娴嬭瘯 | 10 | 88 | 鉁?鍏ㄩ儴閫氳繃 | 4绉?|
| 鍔熻兘娴嬭瘯 | 10 | 185 | 鉁?鍙甯告敹闆?| - |

**鎬昏锛?8涓祴璇曟枃浠讹紝576涓祴璇曠敤渚?*

## 淇璁板綍

### 璺緞淇
鐢变簬鏂囦欢琚Щ鍔ㄥ埌瀛愮洰褰曪紝浠ヤ笅娴嬭瘯涓殑 `SRC_ROOT` 璺緞闇€瑕佽皟鏁达細

1. **`unit/test_architecture_guards.py`**
   - 淇敼锛歚parents[1]` 鈫?`parents[2]`
   - 鍘熷洜锛氭枃浠朵粠 `tests/` 绉诲姩鍒?`tests/unit/`锛岄渶瑕佸悜涓婂鍥炴函涓€绾?
2. **`unit/test_liquidation_scan_modules.py`**
   - 淇敼锛歚parents[1]` 鈫?`parents[2]`
   - 鍘熷洜锛氬悓涓?
3. **`unit/test_replit_entrypoint.py`**
   - 淇敼锛歚parents[1]` 鈫?`parents[2]`
   - 鍘熷洜锛氬悓涓?
## 杩愯鍛戒护

```bash
# 杩愯鎵€鏈夋祴璇?python -m pytest flashloan/app_runtime/tests -q

# 杩愯鐗瑰畾绫诲瀷
python -m pytest flashloan/app_runtime/tests/unit -q
python -m pytest flashloan/app_runtime/tests/integration -q
python -m pytest flashloan/app_runtime/tests/functional -q

# 杩愯鐗瑰畾妯″潡
python -m pytest flashloan/app_runtime/tests -k "liquidation and not network" -q
```

## 娉ㄦ剰浜嬮」

1. **鍔熻兘娴嬭瘯鎵ц鏃堕棿杈冮暱**锛氭秹鍙奆lask搴旂敤缁勮锛屽崟涓枃浠跺彲鑳介渶瑕?0-20绉?2. **鏋舵瀯瀹堝崼娴嬭瘯**锛氭鏌ユā鍧楄鏁伴檺鍒讹紙<300琛岋級鍜屽鍏ヨ鑼?3. **鏁忔劅淇℃伅妫€鏌?*锛歚test_secret_leakage_guards.py` 楠岃瘉鏃犲瘑閽ユ硠婕?4. **缂栫爜闂**锛歊EADME.md 宸蹭娇鐢?UTF-8 缂栫爜閲嶆柊鐢熸垚

## 鍚庣画寤鸿

1. **CI/CD閰嶇疆**锛氬缓璁寜娴嬭瘯绫诲瀷鍒嗛樁娈佃繍琛岋紙鍗曞厓鈫掗泦鎴愨啋鍔熻兘锛?2. **e2e鐩綍**锛氶鐣欑敤浜庢湭鏉ョ鍒扮娴嬭瘯锛堝 Selenium/Playwright锛?3. **骞惰鎵ц**锛氬崟鍏冩祴璇曞彲骞惰鎵ц锛坄pytest -n auto`锛夊姞閫熷弽棣?