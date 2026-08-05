# `src_bot/tests` 娴嬭瘯璇存槑

杩欎唤鏂囨。鐢ㄦ潵缁欏悗缁祴璇曘€佽ˉ娴嬪拰鍥炲綊鎻愪緵缁熶竴鍙傜収銆傝繖閲岀殑娴嬭瘯浠ユ湰鍦扮绾垮崟娴嬩负涓伙紝鏍稿績鍘熷垯鏄細

1. 灏介噺鐢?`monkeypatch`銆乫ake銆侀潤鎬佹牱鏈鐩栧垎鏀€?
2. 娑夊強閾句笂鐘舵€佹椂锛屼紭鍏堥獙璇佲€滄祦绋嬫槸鍚︿細缁х画璋冪敤閾句笂澶嶆牳鈥濓紝涓嶈鎶婂閮ㄧ储寮曟垨缂撳瓨褰撴垚鏈€缁堢粨璁恒€?
3. 椤甸潰鐘舵€併€佹墽琛岀姸鎬併€佸け璐ユ牱鏈€佽鍒嗗埆娴嬶紝閬垮厤鍙祴涓昏矾寰勩€?

## 鐩綍缁撴瀯

```text
src_bot/tests/
  conftest.py                  pytest 璺緞娉ㄥ叆
  test_*.py                    涓氬姟鍗曟祴
  README.md                    鏈鏄?
```

`conftest.py` 鍙仛浜嗕竴浠朵簨锛氭妸 `src_bot/` 鍔犲叆 `sys.path`锛屾柟渚跨洿鎺?`python -m pytest flashloan/src_bot/tests`銆?

## 娴嬭瘯鍒嗗眰

### 1. 鍩虹涓庢灦鏋勫畧鍗?

鍏虫敞閰嶇疆銆佷緷璧栬竟鐣屻€佺洰褰曠害鏉熴€佸惎鍔ㄨ涓烘槸鍚︾ǔ瀹氥€?

甯歌鏂囦欢锛?

- `test_architecture_guards.py`
- `test_config_schema.py`
- `test_database_schema_status.py`
- `test_startup_behavior.py`
- `test_run_logging.py`
- `test_secret_leakage_guards.py`

閫傚悎楠岃瘉锛?

- 鏂板鏂囦欢鏄惁钀藉湪姝ｇ‘鐩綍
- 鐜鍙橀噺鏄惁缂哄け鎴栧啿绐?
- Schema / 閰嶇疆鍋ュ悍搴︽槸鍚﹀彲璇?
- 鍚姩鍜屽悗鍙板垵濮嬪寲寮傚父鏄惁鑴辨晱
- 鐪熷疄 `.env` 鏄惁琚?Git 蹇界暐涓旀湭琚窡韪?
- 鍙楃増鏈帶鍒剁殑婧愮爜銆佺ず渚嬮厤缃槸鍚︽贩鍏ョ閽ャ€佸姪璁拌瘝鎴?PEM 绉侀挜

### 2. 鎺у埗鍙伴〉闈笌璺敱

鍏虫敞 `/`銆乣/liquidation`銆乣/account-scan`銆乣/execution`銆乣/audit`銆乣/config` 绛夐〉闈紝浠ュ強 API 鏄惁杩斿洖绋冲畾鐘舵€併€?

甯歌鏂囦欢锛?

- `test_web_app_assembly.py`
- `test_navigation_flow.py`
- `test_control_panel_status.py`
- `test_exchange_matrix_page.py`
- `test_page_state_service.py`
- `test_control_panel_liquidation_actions.py`

閫傚悎楠岃瘉锛?

- 椤甸潰璺宠浆鏄惁瀛樺湪闂幆
- 椤甸潰 embedded / 闈?embedded 妯″紡鏄惁涓€鑷?
- `/api/status`銆乣/api/liquidation/*` 鐨勭姸鎬佸瓧娈垫槸鍚﹀洖褰?
- 鎵ц閾捐矾涓殑 `submission_failed`銆乣static_call_failed`銆乣confirmed_failed` 鏄惁褰掍竴

### 3. 娓呯畻鍙戠幇涓庢壂鎻?

杩欐槸褰撳墠鏈€閲嶈鐨勪竴灞傦紝璐熻矗鎶娾€滃€欓€夎处鍙封€濆彉鎴愨€滃彲鎵ц鍓嶇殑閾句笂澶嶆牳缁撴灉鈥濄€?

甯歌鏂囦欢锛?

- `test_liquidation_discovery_service.py`
- `test_liquidation_discovery_workflow.py`
- `test_liquidation_scan.py`
- `test_liquidation_scan_modules.py`
- `test_external_liquidation_index.py`
- `test_liquidation_priority.py`
- `test_liquidation_amounts.py`

鎺ㄨ崘鍏虫敞鐨勯摼璺『搴忥細

```text
澶栭儴绮楃瓫 -> 閾句笂 Borrow 鏃ュ織鍙戠幇 -> 璐︽埛鍋ュ悍搴︽壂鎻?-> 鍊欓€夋帓搴?-> 鎵ц鍓嶇疆鏍￠獙
```

杩欓噷鐨勬祴璇曢噸鐐逛笉鏄€滃閮ㄧ储寮曞噯涓嶅噯鈥濓紝鑰屾槸锛?

- 澶栭儴绱㈠紩鏄惁鍙綔涓哄€欓€夋潵婧?
- 鍊欓€夋槸鍚︿細鍘婚噸鍚堝苟
- 鏈€缁堟槸鍚︿粛璧?`scan_account_health()`
- `Multicall3` 澶辫触鏃舵槸鍚﹁兘閫€鍥炲崟璐︽埛 RPC
- 璐︽埛鏁颁笂闄愩€佹壒娆°€佸苟鍙戞槸鍚︿繚鎸佺ǔ瀹?

### 4. 鎵ц銆侀妫€銆佺泩鍒╁厹搴?

鍏虫敞浠?report 鍒?payload锛屽啀鍒?static call / submit / receipt / failure sample 鐨勫畬鏁撮棴鐜€?

甯歌鏂囦欢锛?

- `test_liquidation_preflight.py`
- `test_liquidation_execution_service.py`
- `test_liquidation_engine.py`
- `test_liquidation_pause_guard.py`
- `test_profit_guard.py`
- `test_nonce_manager.py`
- `test_parallel_submitter.py`
- `test_private_tx.py`

閫傚悎楠岃瘉锛?

- `minProfitAmount` 鏄惁涓ユ牸鐢熸晥
- `ProfitTooLow` 鏄惁鑳介樆鏂簭鎹熸墽琛?
- `static_call_required`銆乣static_call_passed`銆乣execution_phase` 鏄惁涓€鑷?
- `receipt.status == 0` 鏄惁杩涘叆澶辫触鏍锋湰鎬?
- 寮哄埗鎵ц鏄惁鍙粫杩囪蒋闃绘柇锛屼笉缁曡繃纭樆鏂?

### 5. 甯傚満涓庤瀵?

鍏虫敞琛屾儏鐩戞帶銆侀槇鍊艰Е鍙戙€佽瀵熷櫒杩愯鎬併€?

甯歌鏂囦欢锛?

- `test_observer_config.py`
- `test_observer_runtime_service.py`
- `test_observer_window_extremes.py`
- `test_market_volatility_event_service.py`
- `test_trigger_signal.py`
- `test_arbitrage_strategy.py`
- `test_dynamic_quote.py`
- `test_gas_estimator.py`

### 6. 宸ュ叿鍜屽垎鏋?

鐢ㄤ簬缁撴灉澶嶇洏銆佺獥鍙ｅ垎鏋愩€侀槇鍊煎垎鏋愩€佸彲鎵ц淇″彿鏋勫缓銆?

甯歌鏂囦欢锛?

- `test_analyze_thresholds.py`
- `test_analyze_trade_results.py`
- `test_build_executable_signal.py`
- `test_collect_liquidation_history.py`
- `test_check_manual_prereqs.py`
- `test_aave_hit_stats.py`

## 甯哥敤鍛戒护

鎵€鏈夊懡浠ら粯璁ゅ湪浠撳簱鏍圭洰褰?`E:\2026OPC澶ц禌\flashLoan` 鎵ц锛岄櫎闈炲懡浠ゅ潡閲屾樉寮?`cd` 鍒板瓙鐩綍銆?

```powershell
python -m pytest flashloan/src_bot/tests -q
python -m pytest flashloan/src_bot/tests/test_liquidation_scan.py -q
python -m pytest flashloan/src_bot/tests/test_control_panel_status.py -q
python -m pytest flashloan/src_bot/tests/test_secret_leakage_guards.py -q
python -m pytest flashloan/src_bot/tests -k "liquidation and not network" -q
python -m pytest flashloan/src_bot/tests -x --maxfail=1
```

DEX Python 鍥炲綊锛?

```powershell
python -m pytest flashloan/srcs_dex/tests -q
```

鍚堢害渚ц仈鍔ㄥ洖褰掑崟鐙窇锛?

```powershell
cd contract/contracts-dex
npm test
npx hardhat test test/MockFundedExecutor.test.js --grep "minProfit"
npx hardhat test test/AaveSequentialFlashLoanExecutor.test.js
npx hardhat test test/OnchainDynamicAaveExecutor.test.js
```

濡傛灉闇€瑕佸洖褰掓棫鐨?`contract/contracts-bot` 娓呯畻鎵ц鍚堢害锛屽崟鐙湪 `contract/contracts-bot` 鐩綍鎵ц锛?

```powershell
cd contract/contracts-bot
npm test
npm run test:fork
```

Fuji 棰勬鍙厑璁告墽琛屼笉骞挎挱鍛戒护锛屼笖杈撳嚭蹇呴』鑴辨晱锛?

```powershell
cd contract/contracts-dex
npm run preflight:fuji
```

`preflight:fuji` 鍙兘鐢ㄤ簬 ready 鐘舵€佹鏌ワ紱鏈畬鎴?static call 璇佹嵁銆乷wner 鏍￠獙銆乧hain ID 鍜屾墽琛屽紑鍏崇‘璁ゅ墠锛屼笉寰楁妸 `readyForBroadcast=true` 褰撴垚鍏佽骞挎挱銆?

## 缁熶竴鍥炲綊鍏ュ彛

姣忚疆绂荤嚎鍥炲綊浼樺厛鎸変笅闈㈤『搴忔墽琛岋紝骞舵妸缁撴灉鍐欏叆鍥炲綊鎶ュ憡锛?

```powershell
python -m pytest flashloan/src_bot/tests -q
python -m pytest flashloan/srcs_dex/tests -q
cd contract/contracts-dex
npm test
```

鍙€変絾鎺ㄨ崘鐨勫畧鍗懡浠わ細

```powershell
python -m pytest flashloan/src_bot/tests/test_secret_leakage_guards.py flashloan/src_bot/tests/test_run_logging.py -q
rg -n "int\(os\.getenv|float\(os\.getenv" flashloan/src_bot -g "*.py"
rg -n "str\(exc\)|str\(e\)" flashloan/src_bot -g "*.py"
git diff --check
```

`rg` 鎵弿缁撴灉闇€瑕佷汉宸ュ垎绫伙細

- `int/float(os.getenv(...))`锛氬簲涓烘棤鍛戒腑锛涘鏈夊懡涓紝浼樺厛杩佺Щ鍒?`parse_env_int` / `parse_env_float`銆?
- `str(exc)`锛氬厑璁镐繚鐣欏湪鍐呴儴鍏煎鍒ゆ柇銆乺evert 鍒嗙被鎴栨祴璇曟柇瑷€锛涚敤鎴峰彲瑙併€佹棩蹇椼€丄PI銆乤ttempt銆乫ailure sample 鍜屾姤鍛婂瓧娈靛繀椤讳娇鐢?`redact_sensitive_text`銆?
- `git diff --check`锛氬厑璁稿嚭鐜?LF/CRLF warning锛涗笉鍏佽鍑虹幇 whitespace error銆?

## 鍥炲綊鎶ュ憡褰掓。

鍥炲綊鎶ュ憡缁熶竴褰掓。鍒帮細

```text
docs/娓呯悊鏈哄櫒浜?evidence/regression/
```

鎺ㄨ崘鍛藉悕锛?

```text
YYYYMMDD-HHMMSS_regression_<scope>.md
```

绀轰緥锛?

```text
20260802-153000_regression_src_bot_318_passed.md
20260802-153500_regression_full_offline.md
```

鎶ュ憡鑷冲皯璁板綍锛?

- 鎵ц鏃堕棿鍜屾墽琛屼汉銆?
- 宸ヤ綔鍖虹増鏈細`git rev-parse --short HEAD`锛屼互鍙?`git status --short` 鏄惁涓虹┖銆?
- 鏄惁鑱旂綉銆佹槸鍚﹁繛鎺ョ湡瀹?RPC/鏁版嵁搴撱€佹槸鍚︽墽琛?Fuji 鍛戒护銆?
- 姣忔潯鍛戒护銆佸伐浣滅洰褰曘€侀€氳繃鏁般€佸け璐ユ暟銆侀€€鍑虹爜銆?
- 鏁忔劅淇℃伅妫€鏌ョ粨鏋滐細绉侀挜銆乼oken銆佸畬鏁?RPC URL銆佹暟鎹簱瀵嗙爜鏄惁娉勯湶銆?
- 鏈繍琛岄」鍜屽師鍥犮€?
- 缁撹锛歚pass` / `fail` / `blocked`锛屼互鍙婃槸鍚﹀厑璁歌繘鍏ヤ笅涓€闃舵銆?

## 鍙樻洿閫熸煡

鏀硅繖浜涘湴鏂规椂锛屼紭鍏堣窇瀵瑰簲娴嬭瘯锛?

1. 璺敱銆侀〉闈㈣烦杞€佸煁鍏ユ€?
   - `test_navigation_flow.py`
   - `test_web_app_assembly.py`
   - `test_exchange_matrix_page.py`
2. 鎺у埗鍙扮姸鎬併€佹墽琛屾€併€佸け璐ユ牱鏈€?
   - `test_control_panel_status.py`
   - `test_control_panel_liquidation_actions.py`
   - `test_liquidation_audit_service.py`
3. 娓呯畻鍙戠幇銆佸閮ㄧ储寮曘€侀摼涓婂鏍?
   - `test_external_liquidation_index.py`
   - `test_liquidation_discovery_service.py`
   - `test_liquidation_discovery_workflow.py`
   - `test_liquidation_scan.py`
4. 棰勬銆佹姤浠枫€乸ayload銆佺泩鍒╁厹搴?
   - `test_liquidation_preflight.py`
   - `test_execution_payload.py`
   - `test_profit_guard.py`
   - `test_liquidation_amounts.py`
5. 鍚堢害鐩堝埄淇濇姢鍜屽洖鎵ч棴鐜?
   - `contract/contracts-dex/test/MockFundedExecutor.test.js`
   - `contract/contracts-dex/test/AaveSequentialFlashLoanExecutor.test.js`
   - `contract/contracts-dex/test/OnchainDynamicAaveExecutor.test.js`
   - `contract/contracts-bot/test/AaveV3LiquidationExecutor.test.js`

## 鏂板娴嬭瘯寤鸿

鏂板娴嬭瘯鏃讹紝浼樺厛琛ヤ笅闈㈠嚑绫伙細

1. 鐘舵€佸綊涓€娴嬭瘯
   - 渚嬪 `route_failure_state()`銆乣execution_phase`銆乣receipt.status`
2. 澶辫触鏍锋湰娴嬭瘯
   - 渚嬪鎻愪氦澶辫触銆侀摼涓婂洖鎵уけ璐ャ€侀潤鎬佽皟鐢ㄥけ璐?
3. 閫€鍖栬矾寰勬祴璇?
   - 渚嬪 Multicall 涓嶅彲鐢ㄣ€佸閮ㄧ储寮曚笉鍙敤銆丷PC 鍗曠偣澶辫触
4. 闂幆娴嬭瘯
   - 渚嬪鍙戠幇 -> 澶嶆牳 -> 鎺掑簭 -> payload -> preflight -> submit

## 缂栧啓绾﹀畾

- 浼樺厛浣跨敤 `monkeypatch` 鎴?fake 瀵硅薄锛屼笉鐩存帴鎵撶湡瀹?RPC銆?
- 姣忎釜娴嬭瘯鍙獙璇佷竴涓叧閿涓恒€?
- 鍚嶇О灏介噺鎶婂満鏅啓娓呮銆?
- 娑夊強娓呯畻鏃讹紝浼樺厛鏂█ `health_factor`銆乣status`銆乣execution_phase`銆乣receipt.status`銆乣profit`銆乣failure_type`銆?
- 濡傛灉鏀逛簡椤甸潰璺宠浆鎴栨帴鍙ｇ姸鎬侊紝浼樺厛琛?`navigation` / `status` / `actions` 涓夌被娴嬭瘯銆?

## 鍥炲綊椤哄簭寤鸿

1. 鍏堣窇鍩虹瀹堝崼銆?
2. 鍐嶈窇椤甸潰鍜岃矾鐢便€?
3. 鍐嶈窇娓呯畻鍙戠幇涓庢壂鎻忋€?
4. 鍐嶈窇鎵ц銆侀妫€銆佺泩鍒╁厹搴曘€?
5. 鏈€鍚庤窇鍚堢害娴嬭瘯銆?

涓€涓瘮杈冪ǔ鐨勭粍鍚堟槸锛?

```powershell
python -m pytest flashloan/src_bot/tests/test_architecture_guards.py
python -m pytest flashloan/src_bot/tests/test_secret_leakage_guards.py
python -m pytest flashloan/src_bot/tests/test_control_panel_status.py flashloan/src_bot/tests/test_navigation_flow.py
python -m pytest flashloan/src_bot/tests/test_liquidation_scan.py flashloan/src_bot/tests/test_liquidation_discovery_workflow.py flashloan/src_bot/tests/test_external_liquidation_index.py
python -m pytest flashloan/src_bot/tests/test_control_panel_liquidation_actions.py
cd contract/contracts-dex
npm test
```

## 褰撳墠鏈€鍊煎緱浼樺厛缁存姢鐨勬祴璇曠偣

- `/execution` 鐨勬彁浜ゆ€併€乺eceipt 鎬併€佸け璐ユ牱鏈€?
- 娓呯畻鍙戠幇閾捐矾閲岀殑鈥滃閮ㄧ储寮曞彧鍋氱矖绛涒€?
- 閾句笂鍋ュ悍搴︽壂鎻忓繀椤绘槸鏈€缁堣鍐?
- `minProfitAmount` 涓?`ProfitTooLow`
- 椤甸潰璺敱鍜屽祵鍏ユ€佽烦杞棴鐜?
- 鎻愪氦鍓嶇殑绉侀挜娉勯湶瀹堝崼

## 鏈€杩戞柊澧?

- `test_external_liquidation_index.py`
- `test_liquidation_discovery_workflow.py`
- `test_navigation_flow.py`

