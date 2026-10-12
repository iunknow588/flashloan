    const $ = id => document.getElementById(id);
    const APP_VERSION = "2026.10.06.01";
    const esc = value => String(value ?? "-").replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
    const shortPoolId = value => {
      const text = String(value || "");
      return text.length > 18 ? `${text.slice(0, 14)}...` : text;
    };
    const lines = items => (Array.isArray(items) ? items : []).filter(item => item !== null && item !== undefined && String(item).trim() !== "");
    const numeric = (value, digits = 4) => {
      if (value === null || value === undefined || value === "") return "-";
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed.toLocaleString("en-US", {maximumFractionDigits: digits}) : "-";
    };
    const money = value => {
      if (value === null || value === undefined || value === "") return "-";
      const parsed = Number(value);
      return Number.isFinite(parsed) ? `${parsed < 0 ? "-" : ""}$${Math.abs(parsed).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : "-";
    };
    const snapshotAgeSeconds = (snapshotPayload) => {
      const updatedAt = snapshotPayload?.snapshot?.updated_at || snapshotPayload?.scanner?.snapshot_updated_at || "";
      if (!updatedAt) return Number.MAX_SAFE_INTEGER;
      const parsed = new Date(updatedAt);
      return Number.isNaN(parsed.getTime()) ? Number.MAX_SAFE_INTEGER : (Date.now() - parsed.getTime()) / 1000;
    };
    const amount = (value, symbol, digits = 8) => {
      if (value === null || value === undefined || value === "") return "-";
      const parsed = Number(value);
      return Number.isFinite(parsed) ? `${parsed.toLocaleString("en-US", {maximumFractionDigits: digits})} ${symbol || ""}`.trim() : "-";
    };
    const labelMap = {
      live_verified: "链上价格已验证",
      not_live_verified: "尚未完成链上复核",
      rpc_verification_failed: "链上复核失败：RPC 不可用",
      rpc_verification_timeout: "链上复核超时：RPC 未在期限内返回",
      adapter_required: "链上复核失败：缺少报价适配器",
      missing_trigger_block: "链上复核未执行：缺少触发区块",
      missing_effective_pool: "链上复核：缺少有效池，无法报价",
      missing_route_pool_identity: "链上复核未通过：缺少中间桥池 PoolKey",
      missing_route_identity: "链上复核未通过：缺少路线身份",
      missing_configuration: "链上复核未通过：缺少链上配置",
      live_quote_failed: "链上报价失败",
      live_quote_reference_only: "仅参考报价",
      route_unprofitable: "路线不盈利",
      slippage_limit_exceeded: "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
      not_enough_liquidity: "池当前没有 LP 流动性",
      pool_insufficient_for_profitable_execution: "池深度不足以盈利执行",
      pool_not_initialized: "V4 池未初始化",
      missing_effective_pool: "链上复核：池未初始化或缺少有效池",
      live_evidence_deferred: "链上验证已延后",
      not_reviewed_this_scan: "本轮未复核",
      stale_refresh_pending: "快照过期，等待刷新",
      requires_live_quoter: "需要链上报价器",
      sdk_adapter_required: "需要 V4 SDK 适配器",
      uniswap_v4_sdk_exact_input_single: "V4 SDK 单步精确输入",
      uniswap_v4_sdk_exact_input_route: "V4 SDK 路线精确输入",
      quote_request_ready: "报价请求已就绪",
      uniswapx_fallback_failed: "UniswapX 第二跳回退失败",
    };
    const label = value => labelMap[value] || value || "-";
    labelMap.static_reference_pending_live_review = "静态参考，待链上逐 hop 复核";
    const executionTrustText = value => {
      if (String(value || "").toLowerCase() === "static_reference_pending_live_review") return "静态参考，待链上逐 hop 复核";
      return ({
      static_reference_only: "静态参考",
      static_reference: "静态参考",
      live_quoter_verified_readonly: "实时报价器验证（只读）",
      static_live_quoter_estimate: "静态+实时报价器复核",
      live_quote_reference_only: "参考报价",
      live_verified: "链上已验证",
      live: "链上已验证",
      not_enough_liquidity: "链上复核：池当前没有 LP 流动性",
      pool_not_initialized: "链上复核：池未初始化",
      missing_effective_pool: "链上复核：池未初始化或缺少有效池",
      live_quote_failed: "链上报价失败",
      rpc_verification_failed: "链上复核失败：RPC 不可用",
      rpc_verification_timeout: "链上复核超时：RPC 未在期限内返回",
      deferred: "已延迟",
      })[String(value || "")] || value || "-";
    };
    const terminalEvidenceStatus = status => {
      const lower = String(status || "").toLowerCase();
      return [
        "route_unprofitable",
        "slippage_limit_exceeded",
        "live_quote_failed",
        "quote_failed",
        "not_enough_liquidity",
        "pool_not_initialized",
        "pool_insufficient_for_profitable_execution",
        "missing_effective_pool",
        "missing_route_pool_identity",
        "missing_route_identity",
        "missing_configuration",
        "route_lacks_complete_token_address_identity",
        "rpc_verification_failed",
        "rpc_verification_timeout",
        "adapter_required",
        "missing_trigger_block",
        "uniswapx_fallback_failed",
      ].includes(lower);
    };
    const estimateStageStatus = value => terminalEvidenceStatus(value) ? evidenceStatusEx(value) : "链下估算完成";
    const quoteBlockLabel = value => {
      const text = String(value || "");
      if (text === "latest_fallback_missing_trigger" || text === "latest_fallback_historical_state_unavailable") return "最新区块回退（历史状态不可用）";
      if (text === "trigger_block") return "触发区块";
      return text || "-";
    };
    const warningZh = {
      hop_1_reference_quote_requires_live_quoter: "第 1 步参考报价需要链上实时报价",
      hop_2_reference_quote_requires_live_quoter: "第 2 步参考报价需要链上实时报价",
      hop_3_reference_quote_requires_live_quoter: "第 3 步参考报价需要链上实时报价",
      reference_price_step_requires_live_quoter: "参考价步骤需要链上实时报价",
      reference_usd_price_missing_or_untrusted: "美元参考价缺失或不可信",
      static_depth_evidence_unavailable: "离线深度证据不可用",
      robinhood_testnet_live_price_only_enforced: "Robinhood 测试网强制使用链上实时价格",
      uniswap_v4_live_quote_failed: "Uniswap V4 链上报价失败",
    };
    const warningText = value => {
      const text = String(value || "");
      if (warningZh[text]) return warningZh[text];
      if (text === "robinhood_live_price_only_enforced") return "Robinhood 强制使用链上实时价格";
      if (/[\u4e00-\u9fff]/.test(text)) return text;
      const blocker = blockerText(text);
      if (/[\u4e00-\u9fff]/.test(blocker)) return blocker;
      return `接口提示（${blocker}）`;
    };
    const uiError = value => {
      const text = String(value || "请求失败");
      if (text === "amount must be positive") return "金额必须大于 0";
      if (/^HTTP \d+$/.test(text)) return `请求失败（${text}）`;
      const friendly = blockerText(text);
      if (/[\u4e00-\u9fff]/.test(friendly)) return friendly;
      return `接口错误（${friendly}）`;
    };
    const recommendationAction = value => {
      const text = String(value || "");
      const map = {
        "on-chain step-by-step reverify failed; blocking transaction preflight": "链上逐步复核失败；正在阻止链上执行",
        "on-chain reverify complete; current state does not satisfy transaction submission conditions": "链上复核完成；当前状态不满足交易提交条件",
        "On-chain verification complete; preflight still required": "链上复核完成；仍需要链上复核确认可执行性",
        "On-chain verification passed, but execution gates are blocked": "链上复核通过，但链上执行条件尚未满足",
        "Pool depth insufficient for profitable execution": "池深度不足以盈利执行",
        "Per-hop price impact exceeds 10 bps": "单跳价格影响超过 10 bps",
        "Route is unprofitable": "路线不盈利",
        "On-chain quote failed": "链上报价失败",
        "on-chain quote failed; route cannot enter transaction preflight": "链上报价失败，无法进入链上执行",
        "route cannot enter transaction preflight": "链上报价失败，无法进入链上执行",
        "on-chain best amount unconfirmed; cannot enter transaction preflight": "链上复核未通过：最佳量未计算成功，不能进入链上执行",
        "Reference quote only": "仅参考报价",
        "On-chain verification deferred": "链上验证已延后",
      };
      const extra = {
        "at least one hop price impact cannot be confirmed or exceeds 10 bps": "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
        "on-chain computed profit is not positive after flash loan fees; cannot enter preflight": "链上复核已确认，但扣除闪电贷费用后尚未转正；不能进入链上执行",
        "on-chain verification did not cover all hops; cannot enter preflight": "链上复核未覆盖全部跳数；不能进入链上执行",
        "stable-coin route on-chain net profit is below the $6 gate; cannot enter preflight": "稳定币路线链上净收益低于 6U 门槛；不能进入链上执行",
        "final output is not stable coin or lacks reliable USD valuation; cannot enter automatic transaction preflight": "最终输出不是稳定币或缺少可信 USD 估值；不能进入自动链上执行",
        "current pool depth cannot support a borrow size that passes 10 bps per-hop limit and profit after flash loan fee": "当前池深度无法支持同时通过 10 bps 滑点限制并扣除闪电贷费用后仍有盈利的借贷量",
        "on-chain RPC verification failed": "链上 RPC 复核失败",
        "Robinhood Testnet RPC unavailable": "Robinhood 测试网 RPC 不可用",
        "adapter required": "缺少链上报价适配器",
        "missing trigger block context; later latest price cannot replace the trigger-time price.": "缺少触发区块上下文；后续最新价格不能替代触发时点价格",
        "V4 pool is not initialized or cannot be quoted; route cannot enter the on-chain execution gate": "V4 池未初始化或无法报价；该路线不能进入链上执行",
        "historic block state unavailable; deferred on-chain reverify": "历史区块状态不可用；链上复核已延后",
        "on-chain Quoter already reverified as profitable; still need transaction calldata, slippage protection, flash loan return constraints, and fork/static-call simulation before submitting": "链上 Quoter 已复核为正收益；当前复核结论已完成",
      };
      return map[text] || extra[text] || text || "-";
    };
    const terminalFailureAdvice = status => {
      const lower = String(status || "").toLowerCase();
      if (lower === "rpc_verification_timeout") return "链上复核超时：RPC 未在期限内返回；不能作为已确认最佳量，不建议直接链上执行，请稍后重试或检查 RPC 配置。";
      if (lower === "rpc_verification_failed") return "链上复核失败：RPC 不可用；不能进入链上执行，请稍后重试或检查 RPC 配置。";
      if (lower === "fetch_network_error" || lower === "backend_unreachable") return "请求服务不可达：无法连接后端，请检查服务是否启动/端口是否正确，稍后重试";
      if (lower === "route_unprofitable") return "链上报价已验证，但净收益未转正；禁止链上执行";
      if (lower === "slippage_limit_exceeded") return "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认；禁止链上执行";
      if (lower === "not_enough_liquidity") return "链上复核：池当前没有 LP 流动性；降低金额后重试或标记当前金额不可执行";
      if (lower === "pool_not_initialized") return "V4 池未初始化；无法完成链上复核";
      if (lower === "pool_insufficient_for_profitable_execution") return "池深度不足以盈利执行；禁止链上执行";
      if (lower === "missing_effective_pool") return "链上复核：池未初始化或缺少有效池；无法报价";
      if (lower === "missing_route_pool_identity") return "链上复核未通过：缺少中间桥池 PoolKey；请先补全市场观察中的中间桥池身份后重试，禁止链上执行";
      if (lower === "missing_route_identity") return "链上复核未通过：缺少路线身份；请先补全路由地址/池身份后重试，禁止链上执行";
      if (lower === "missing_configuration") return "链上复核未通过：缺少链上配置；请检查官方 V4 仲裁合约和链级配置后重试";
      if (lower === "missing_trigger_block") return "链上复核未执行：缺少触发区块上下文；无法确认";
      if (lower === "adapter_required") return "链上复核失败：缺少 V4 报价适配器；无法完成链上复核";
      if (lower === "live_quote_failed" || lower === "quote_failed") return "链上报价失败，无法进入链上执行";
      if (lower === "uniswapx_fallback_failed") return "UniswapX 第二跳回退失败：无合适中间池、参数或三跳净收益未达到 1%；按失败终止，不再寻找其他 AMM 池";
      return "链上报价失败，无法进入链上执行";
    };
    const errorChainParts = value => {
      const raw = String(value || "").trim();
      if (!raw) return null;
      const hopArrowProtected = raw.replace(/hop\s+(\d+)\s+([A-Za-z0-9]+)\s*->\s*([A-Za-z0-9]+)/gi, "hop $1 $2_HOPTO_$3");
      const hopMatch = hopArrowProtected.match(/^(hop\s+\d+\s+[A-Za-z0-9]+\s+verification\s+failed):\s*(.*)$/i);
      const outer = hopMatch ? blockerText(hopMatch[1]) : "";
      const errPart = hopMatch ? hopMatch[2] : hopArrowProtected;
      const looksNested = /[;:]/.test(errPart)
        || /verification\s+failed/i.test(errPart)
        || /robinhood\s+(?:testnet|mainnet)\s+v4\s+quoter\s+(exact-input\s+call\s+failed|reverted)/i.test(errPart)
        || /UnexpectedRevertBytes|NotEnoughLiquidity/.test(errPart);
      if (!looksNested) return null;
      const parts = errPart
        .split(/\s*;\s*/)
        .flatMap(segment => segment.split(/\s*:\s*(?![\\/])/).flatMap(chunk => chunk.split(/\s*->\s*/)))
        .map(part => part.trim())
        .map(part => part.replace(/_HOPTO_/g, " -> "))
        .filter(Boolean);
      if (parts.length < 2) return null;
      const zhParts = parts.map(part => String(blockerText(part)).replace(/^原因[:：]?\s*/, ""));
      return { outer, layers: zhParts };
    };
    const errorChainText = value => {
      const parsed = errorChainParts(value);
      if (!parsed) return blockerText(value);
      return [
        parsed.outer || "链上报价失败",
        ...parsed.layers.map((text, index) => `  ${index + 1}. ${text}`),
      ].join("\n");
    };
    const errorChainHtml = value => {
      const parsed = errorChainParts(value);
      if (!parsed) return `<div class="error-chain"><div>${esc(blockerText(value))}</div></div>`;
      return `<div class="error-chain"><div>${esc(parsed.outer || "链上报价失败")}</div><ol>${parsed.layers.map((text, index) => `<li>${esc(text)}</li>`).join("")}</ol></div>`;
    };
    const errorStackHtml = (stack, fallback) => {
      const rows = Array.isArray(stack) ? stack.filter(row => row && (row.message || row.code)) : [];
      if (!rows.length) return errorChainHtml(fallback);
      const layers = rows.map(row => blockerText(row.message || row.code || "链上错误"));
      return `<div class="error-chain"><div>${esc(layers[0] || blockerText(fallback))}</div><ol>${layers.slice(1).map(text => `<li>${esc(text)}</li>`).join("")}</ol></div>`;
    };
    const nestedBlockerText = value => {
      const parsed = errorChainParts(value);
      if (!parsed) return "";
      const finalCause = parsed.layers[parsed.layers.length - 1];
      const errorChain = parsed.layers.join(" -> ");
      return `${parsed.outer || "链上报价失败"}：最终原因 ${finalCause}；错误链 ${errorChain}`;
    };
    const blockerText = value => {
      const text = String(value || "");
      const nested = nestedBlockerText(text);
      if (nested) return nested;
      if (text === "fetch_network_error" || text === "backend_unreachable") return "请求服务不可达：无法连接后端，请检查服务是否启动/端口是否正确，稍后重试";
      const evidenceTimeout = text.match(/^on-chain evidence timed out after ([0-9.]+)s$/i);
      if (evidenceTimeout) return `链上复核超时：RPC 未在期限内返回（${evidenceTimeout[1]} 秒）`;
      const blockerDirectMap = {
        "at least one hop price impact cannot be confirmed or exceeds 10 bps": "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
        "on-chain computed profit is not positive after flash loan fees; cannot enter preflight": "链上复核已确认，但扣除闪电贷费用后尚未转正；不能进入链上执行",
        "on-chain verification did not cover all hops; cannot enter preflight": "链上复核未覆盖全部跳数；不能进入链上执行",
        "stable-coin route on-chain net profit is below the $6 gate; cannot enter preflight": "稳定币路线链上净收益低于 6U 门槛；不能进入链上执行",
        "final output is not stable coin or lacks reliable USD valuation; cannot enter automatic transaction preflight": "最终输出不是稳定币或缺少可信 USD 估值；不能进入自动链上执行",
        "current pool depth cannot support a borrow size that passes 10 bps per-hop limit and profit after flash loan fee": "当前池深度无法支持同时通过 10 bps 滑点限制并扣除闪电贷费用后仍有盈利的借贷量",
        "V4 pool is not initialized or cannot be quoted; route cannot enter the on-chain execution gate": "V4 池未初始化或无法报价；该路线不能进入链上执行",
        "historic block state unavailable; deferred on-chain reverify": "历史区块状态不可用；链上复核已延后",
        "on-chain Quoter already reverified as profitable; still need transaction calldata, slippage protection, flash loan return constraints, and fork/static-call simulation before submitting": "链上 Quoter 已复核为正收益；当前复核结论已完成",
      };
      if (blockerDirectMap[text]) return blockerDirectMap[text];
      const translated = text
        .replace(/hop (\d+) ([A-Za-z0-9]+)->([A-Za-z0-9]+) verification failed/g, "链上第 $1 步 $2 -> $3 验证失败")
        .replace(/on-chain hop (\d+) verification failed/g, "链上第 $1 步验证失败")
        .replace(/robinhood testnet v4 quoter exact-input call failed/g, "Robinhood 测试网 V4 报价器精确输入调用失败")
        .replace(/robinhood testnet v4 quoter reverted/g, "Robinhood 测试网 V4 报价器回滚")
        .replace(/robinhood mainnet v4 quoter exact-input call failed/g, "Robinhood 主网 V4 报价器精确输入调用失败")
        .replace(/robinhood mainnet v4 quoter reverted/g, "Robinhood 主网 V4 报价器回滚")
        .replace(/UnexpectedRevertBytes\(bytes\)/g, "意外回滚字节(bytes)")
        .replace(/NotEnoughLiquidity\(bytes32\)/g, "流动性不足(bytes32)")
        .replace(/metadata is not found[^\n]*/gi, "历史区块状态缺失（metadata）")
        .replace(/0x486aa307/g, "PoolNotInitialized()")
        .replace(/PoolNotInitialized\(\)/g, "V4 池未初始化")
        .replace(/意外回滚字节\(bytes\) -> V4 池未初始化/g, "V4 池未初始化")
        .replace(/robinhood testnet v4 quoter/g, "Robinhood 测试网 V4 报价器");
      const extra = {
        "on-chain RPC verification failed": "链上 RPC 复核失败",
        "Robinhood Testnet RPC unavailable": "Robinhood 测试网 RPC 不可用",
        "Robinhood Mainnet RPC unavailable": "Robinhood 主网 RPC 不可用",
      };
      if (extra[text]) return extra[text];
      if (translated && !/[\u4e00-\u9fff]/.test(translated)) return `原因：${translated}`;
      return translated;
    };
    const pathFromSteps = (steps, fallback = "") => {
      const rows = Array.isArray(steps) ? steps.filter(step => step && typeof step === "object") : [];
      if (!rows.length) return fallback || "";
      const symbols = [];
      for (const step of rows) {
        const from = String(step.fromSymbol || step.tokenInSymbol || "").trim().toUpperCase();
        const to = String(step.toSymbol || step.tokenOutSymbol || "").trim().toUpperCase();
        if (from && (!symbols.length || symbols[symbols.length - 1] !== from)) symbols.push(from);
        if (to) symbols.push(to);
      }
      return symbols.join(" -> ");
    };
    const routeKey = route => {
      const symbols = Array.isArray(route?.routeSymbols) ? route.routeSymbols : [];
      const symbolKey = symbols.filter(Boolean).map(item => String(item).trim().toUpperCase()).filter(Boolean);
      if (symbolKey.length >= 3 && symbolKey[0] === symbolKey[symbolKey.length - 1]) {
        const middle = symbolKey.slice(1, -1).sort().join("->");
        return `loop|${symbolKey[0]}|${middle}`;
      }
      const raw = route?.symbolPair || (symbolKey.length ? symbolKey.join(" -> ") : "") || "";
      return `path|${String(raw).toUpperCase()}`;
    };
    const v4PoolHasCompleteIdentity = pool => {
      const key = pool?.poolKey;
      if (!key || typeof key !== "object") return false;
      return Boolean(
        String(key.currency0 || key.token0 || "").trim()
        && String(key.currency1 || key.token1 || "").trim()
        && key.fee != null
        && key.tickSpacing != null
        && String(key.hooks || "").trim()
      );
    };
    const routeHasCompleteV4PoolIdentity = route => {
      const pools = Array.isArray(route?.routePools)
        ? route.routePools.filter(pool => pool && typeof pool === "object")
        : [];
      if (!pools.length) return false;
      const symbols = Array.isArray(route?.routeSymbols)
        ? route.routeSymbols.map(item => String(item || "").trim().toUpperCase()).filter(Boolean)
        : [];
      const effectivePools = symbols.length ? pools.slice(0, symbols.length - 1) : pools;
      return effectivePools.length > 0 && effectivePools.every(v4PoolHasCompleteIdentity);
    };
    const poolExplicitlyUsable = pool => {
      if (!pool || typeof pool !== "object") return false;
      if (pool.usableForObservation != null) return pool.usableForObservation === true;
      if (pool.dynamicFee || pool.specialFee || String(pool.feeLabel || "").toLowerCase() === "special") return false;
      if (pool.hasLiquidity != null) return pool.hasLiquidity === true;
      return Number(pool.liquidityIndex ?? pool.liquidity ?? 0) > 0;
    };
    const poolExplicitlyUnusable = pool => {
      if (!pool || typeof pool !== "object") return false;
      if (pool.usableForObservation != null) return pool.usableForObservation === false;
      if (pool.hasLiquidity != null) return pool.hasLiquidity === false;
      if (pool.dynamicFee || pool.specialFee || String(pool.feeLabel || "").toLowerCase() === "special") return true;
      return Number(pool.liquidityIndex ?? pool.liquidity ?? 0) === 0 && (pool.liquidityIndex != null || (pool.liquidity != null && String(pool.liquidity).trim() !== ""));
    };
    const poolLiquidityEvidenceMissing = pool => !poolExplicitlyUsable(pool) && !poolExplicitlyUnusable(pool);
    const routeExplicitlyUnusable = route => {
      if (!route || typeof route !== "object") return true;
      if (route.routeUsableForObservation === false) return true;
      const pools = Array.isArray(route.routePools)
        ? route.routePools.filter(pool => pool && typeof pool === "object")
        : Array.isArray(route.quoteInputs?.candidatePools)
          ? route.quoteInputs.candidatePools.filter(pool => pool && typeof pool === "object")
          : [];
      if (!pools.length) return true;
      return pools.length > 0 && pools.every(pool => pool.usableForObservation === false || pool.hasLiquidity === false);
    };
    const filterCompleteV4Routes = routes => Array.isArray(routes)
      ? routes.filter(route => route && typeof route === "object" && routeHasCompleteV4PoolIdentity(route) && !routeExplicitlyUnusable(route))
      : [];
    const routeQuality = route => {
      const evidence = routeEvidence(route);
      const status = String(evidence.status || route?.quoteStatus || "").toLowerCase();
      let score = 0;
      if (status && status !== "requires_live_quoter" && status !== "live_evidence_deferred" && status !== "not_reviewed_this_scan") score += 10;
      if (["live_verified", "route_unprofitable", "slippage_limit_exceeded", "pool_insufficient_for_profitable_execution"].includes(status)) score += 25;
      score += Array.isArray(evidence.hops) ? evidence.hops.length : 0;
      score += Array.isArray(route?.quoteAnalysis?.steps) ? Math.min(route.quoteAnalysis.steps.length, 5) : 0;
      return score;
    };
    const routesToRender = (scannerRoutes, quoteRoutesPayload) => {
      const quoteSnapshot = quoteRoutesPayload?.snapshot?.payload || {};
      const normalizeSource = route => ({
        ...route,
        tableSource: route?.tableSource || "dex_table",
        referencedTable: route?.referencedTable || "ob_table",
        pathSource: route?.pathSource || "market_observation",
      });
      const quoteList = Array.isArray(quoteRoutesPayload?.routes)
        ? quoteRoutesPayload.routes
        : Array.isArray(quoteSnapshot.routes)
          ? quoteSnapshot.routes
          : [];
      const scannerList = filterCompleteV4Routes(scannerRoutes).map(normalizeSource);
      const normalizedQuoteList = filterCompleteV4Routes(quoteList).map(normalizeSource);
      const dedupe = routes => {
        const dedupedRoutes = new Map();
        for (const route of routes) {
          const key = routeKey(route);
          const existing = dedupedRoutes.get(key);
          if (!existing || routeQuality(route) > routeQuality(existing)) dedupedRoutes.set(key, route);
        }
        return Array.from(dedupedRoutes.values());
      };
      if (!normalizedQuoteList.length) return dedupe(scannerList);
      const scannerByKey = new Map(scannerList.map(route => [routeKey(route), route]));
      const quoteByKey = new Map(normalizedQuoteList.map(route => [routeKey(route), route]));
      const merged = normalizedQuoteList.map(route => {
        const quoteStatus = route.live_onchain_evidence?.status;
        const quoteImproved = Boolean(quoteStatus) && !["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(quoteStatus).toLowerCase());
        const base = scannerByKey.get(routeKey(route));
        const baseEvidence = (base?.live_onchain_evidence || base?.quoteAnalysis?.live_onchain_evidence) || {};
        const scannerHasEvidence = Object.keys(baseEvidence).length > 0;
        return quoteImproved || (!scannerHasEvidence && Boolean(route?.quoteAnalysis?.steps?.length)) ? route : base;
      });
      for (const route of scannerList) {
        if (!quoteByKey.has(routeKey(route))) merged.push(route);
      }
      const deduped = new Map();
      for (const route of merged) {
        const key = routeKey(route);
        const existing = deduped.get(key);
        if (!existing || routeQuality(route) > routeQuality(existing)) deduped.set(key, route);
      }
      return Array.from(deduped.values());
    };
    const routeAnalysis = route => {
      const base = {...(route?.quoteAnalysis || {})};
      const evidence = routeEvidence(route);
      if (!Object.keys(evidence).length) return base;
      const costs = evidence.costModel || {};
      const bestAmount = costs.selectedBorrowInputAmount ?? costs.borrowInputAmount ?? base.bestAmount ?? base.best_amount ?? null;
      const status = String(evidence.status || "").toLowerCase();
      const trust = String(evidence.executionTrust || "").toLowerCase();
      const liveTrust = ["live_v4_adapter", "live_quoter_verified_readonly", "live_verified"].includes(trust);
      const liveStatus = ["live_verified", "route_unprofitable", "slippage_limit_exceeded", "pool_insufficient_for_profitable_execution"].includes(status);
      const hardEvidenceFailure = [
        "rpc_verification_failed",
        "rpc_verification_timeout",
        "fetch_network_error",
        "backend_unreachable",
        "adapter_required",
        "missing_trigger_block",
        "missing_route_identity",
        "missing_route_pool_identity",
        "missing_configuration",
        "live_quote_failed",
        "quote_failed",
        "pool_not_initialized",
        "not_enough_liquidity",
        "missing_effective_pool",
      ].includes(status);
      const live = Boolean(!hardEvidenceFailure && (evidence.livePriceVerified || liveStatus || liveTrust));
      const chainAttempted = Boolean(evidence.status);
      const bestAmountUnconfirmed = bestAmountMissingOf(route, evidence, base);
      const rawHuman = value => {
        if (value === null || value === undefined || value === "") return null;
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return null;
        const abs = Math.abs(parsed);
        return abs >= 1e18 && Number.isInteger(parsed) ? parsed / 1e18 : parsed;
      };
      const staticSteps = Array.isArray(base.steps) ? base.steps.filter(Boolean) : [];
      const firstStaticInput = rawHuman(staticSteps.find(step => step.inputAmount != null || step.amountIn != null || step.referenceInputAmount != null)?.inputAmount ?? staticSteps[0]?.inputAmount ?? staticSteps[0]?.amountIn ?? staticSteps[0]?.referenceInputAmount);
      const lastStaticStep = staticSteps[staticSteps.length - 1] || null;
      const lastStaticOutput = rawHuman(lastStaticStep?.outputAmount ?? lastStaticStep?.referenceOutputAmount ?? lastStaticStep?.amountOut);
      const staticNetProfit = firstStaticInput != null && lastStaticOutput != null ? lastStaticOutput - firstStaticInput : null;
      const staticNetBps = firstStaticInput != null && lastStaticOutput != null && firstStaticInput > 0
        ? (lastStaticOutput - firstStaticInput) / firstStaticInput * 10000
        : null;
      return {
        ...base,
        live_onchain_evidence: evidence,
        livePriceVerified: live,
        chainReviewed: chainAttempted,
        priceEvidenceMode: live ? "live_verified" : "live_required",
        priceEvidenceSource: evidence.priceEvidenceSource || (live ? "chain_live_quote" : "chain_live_quote_required"),
        finalOutputUsd: live ? costs.finalOutputUsd : null,
        grossProfitUsd: live ? costs.grossProfitUsd : null,
        netProfitUsd: live ? costs.netProfitUsd : null,
        netProfitBps: live ? costs.netProfitBps : null,
        referenceInputAmount: bestAmount ?? base.referenceInputAmount ?? firstStaticInput,
        referenceFinalOutputAmount: costs.finalOutputAmount ?? base.referenceFinalOutputAmount ?? lastStaticOutput,
        referenceNetProfitAmount: humanTokenValue(costs.netProfitAmount) ?? base.referenceNetProfitAmount ?? staticNetProfit,
        referenceNetReturnBps: costs.netProfitAmountBps ?? base.referenceNetReturnBps ?? staticNetBps,
        referenceFinalOutputSymbol: costs.finalOutputSymbol || base.referenceFinalOutputSymbol || lastStaticStep?.toSymbol || base.referenceInputSymbol || (route?.routeSymbols || [])[0],
        referenceInputSymbol: costs.borrowInputSymbol || base.referenceInputSymbol || (route?.routeSymbols || [])[0],
        referenceReturnBasis: costs.profitBasis || base.referenceReturnBasis || "borrow_token",
        steps: (() => {
          const staticSteps = Array.isArray(base.steps) ? base.steps.filter(Boolean) : [];
          const liveHops = Array.isArray(evidence.hops) ? evidence.hops.filter(Boolean) : [];
          if (!liveHops.length) return staticSteps;
          const liveByHop = new Map(liveHops.map(hop => [Number(hop?.hop ?? 0), hop]));
          const used = new Set();
          let previousOutputAmount = null;
          let previousOutputSymbol = "";
          const merged = staticSteps.map((baseStep, index) => {
            const hopNumber = Number(baseStep.hop ?? index + 1);
            const live = liveByHop.get(hopNumber);
            used.add(hopNumber);
            if (!live) return baseStep;
            const liveInputAmount = live.amountIn ?? live.inputAmount ?? (live.amountInRaw != null && live.inputDecimals != null ? Number(live.amountInRaw) / (10 ** Number(live.inputDecimals)) : (previousOutputSymbol && previousOutputSymbol === String(baseStep.fromSymbol).toUpperCase() ? previousOutputAmount : undefined));
            const mergedStep = {
              ...baseStep, ...live,
              inputAmount: liveInputAmount ?? baseStep.inputAmount,
              outputAmount: live.amountOut ?? baseStep.outputAmount ?? baseStep.referenceOutputAmount,
              outputUsd: live.amountOutRaw == null ? (baseStep.outputUsd ?? baseStep.referenceOutputUsd) : (live.outputUsd ?? baseStep.outputUsd),
              effectiveRate: live.amountOutRaw == null ? (baseStep.effectiveRate ?? baseStep.referenceEffectiveRate) : (live.effectiveRate ?? baseStep.effectiveRate),
            };
            if (live.amountOut != null) {
              previousOutputAmount = Number(live.amountOut);
              previousOutputSymbol = String(live.toSymbol || "").toUpperCase();
            } else if (live.amountOutRaw != null && live.outputDecimals != null) {
              previousOutputAmount = Number(live.amountOutRaw) / (10 ** Number(live.outputDecimals));
              previousOutputSymbol = String(live.toSymbol || "").toUpperCase();
            }
            return mergedStep;
          });
          for (const live of liveHops) {
            const hopNumber = Number(live?.hop ?? 0);
            if (hopNumber && !used.has(hopNumber) && !merged.some(step => Number(step?.hop) === hopNumber)) merged.push(live);
          }
          return merged;
        })(),
        recommendation: lines(evidence.blockers).length || live ? {
            level: evidence.status || (live ? "live_verified" : ""),
            action: bestAmountUnconfirmed
              ? "链上复核未通过：最佳量未计算成功，不能进入链上执行"
              : live
              ? (evidence.readyForTransactionPreflight ? "链上复核完成；可点击链上执行确认成交" : (String(evidence.status || "").toLowerCase() === "route_unprofitable" ? "链上报价已验证，但净收益未转正；禁止链上执行" : "链上复核通过，但链上执行条件尚未满足"))
              : (base.requiresLiveQuoter && base.referenceNetProfitAmount != null ? "链上报价未完成，当前为静态参考收益" : "链上报价未完成"),
            reasons: evidence.blockers,
        } : base.recommendation,
      };
    };
    const evidenceStatus = status => {
      if (String(status || "").toLowerCase() === "static_reference_pending_live_review") return "静态参考，待链上逐 hop 复核";
      return ({
      live_verified: "链上验证通过",
      live_quote_failed: "链上单跳验证失败",
      live_quote_reference_only: "仅参考报价",
      route_unprofitable: "路线不盈利",
      slippage_limit_exceeded: "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
      not_enough_liquidity: "链上复核：池当前没有 LP 流动性",
      pool_insufficient_for_profitable_execution: "池深度不足以盈利执行",
      pool_not_initialized: "V4 池未初始化",
      missing_effective_pool: "链上复核：池未初始化或缺少有效池",
      live_evidence_deferred: "链上验证已延后",
      not_reviewed_this_scan: "链上复核未覆盖本轮预算",
      rpc_verification_failed: "链上复核失败：RPC 不可用",
      rpc_verification_timeout: "链上复核超时：RPC 未在期限内返回",
      adapter_required: "链上复核失败：缺少 V4 报价适配器",
      missing_trigger_block: "链上复核未执行：缺少触发区块上下文",
      missing_route_pool_identity: "链上复核未通过：缺少中间桥池 PoolKey",
      missing_route_identity: "链上复核未通过：缺少路线身份",
      missing_configuration: "链上复核未通过：缺少链上配置",
    }[String(status || "").toLowerCase()] || "链上复核状态未确认");
    };
    const evidenceStatusEx = status => {
      const value = String(status || "").toLowerCase();
      if (value === "static_reference_pending_live_review") return "静态参考，待链上逐 hop 复核";
      const blocked = {
        slippage_limit_exceeded: "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
        route_unprofitable: "链上报价已验证，但净收益未转正",
        pool_insufficient_for_profitable_execution: "链上已验证，池深度不足",
        pool_not_initialized: "链上复核失败：V4 池未初始化",
        rpc_verification_failed: "链上复核失败：RPC 不可用",
        rpc_verification_timeout: "链上复核超时：RPC 未在期限内返回",
        fetch_network_error: "请求服务不可达：无法连接后端，请检查服务是否启动/端口是否正确",
        backend_unreachable: "请求服务不可达：无法连接后端，请检查服务是否启动/端口是否正确",
        adapter_required: "链上复核失败：缺少报价适配器",
        missing_trigger_block: "链上复核未执行：缺少触发区块",
        missing_effective_pool: "链上复核：缺少有效池，无法报价",
        missing_route_pool_identity: "链上复核未通过：缺少中间桥池 PoolKey",
        missing_route_identity: "链上复核未通过：缺少路线身份",
        missing_configuration: "链上复核未通过：缺少链上配置",
        live_quote_failed: "链上单跳验证失败",
        live_verified: "链上验证通过",
      };
      return blocked[value] || evidenceStatus(value);
    };
    const evidenceTrustText = (evidence, analysis) => {
      const status = String((evidence && (evidence.status || evidence.evidenceStatus)) || "").toLowerCase();
      if (terminalEvidenceStatus(status)) return evidenceStatusEx(status);
      return executionTrustText(evidence?.executionTrust || analysis?.executionTrust || "-");
    };
    function onchainEvidenceHtml(evidence, route) {
      if (!evidence || !Object.keys(evidence).length) return "";
      const status = String(evidence.status || "").toLowerCase();
      const failed = (evidence.hops || []).filter(hop => hop?.liveQuoteError || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity", "adapter_required"].includes(String(hop?.status || "").toLowerCase()));
      const focusFailed = failed[0] || null;
      const stackTop = focusFailed?.errorStack?.[0] || evidence.errorStack?.[0] || route?.errorStack?.[0] || null;
      const informativeBlocker = blocker => {
        const text = String(blocker || "").toLowerCase();
        return text.includes("broadcast disabled") || text.includes("static preflight passed");
      };
      const blockersToShow = focusFailed ? [] : lines(evidence.blockers).filter(item => !informativeBlocker(item));
      const focusFailedInput = focusFailed?.amountIn ?? focusFailed?.inputAmount ?? null;
      const focusFailedInputText = String(focusFailed?.status || "").toLowerCase() === "not_enough_liquidity" && focusFailedInput != null
        ? `流动性不足：输入 ${numeric(focusFailedInput, 8)} ${esc(focusFailed.fromSymbol || "")} 被池子拒绝；`
        : "";
      const probe = evidence.executionSizeProbe || null;
      const probeDecimals = Number(probe?.decimals ?? evidence.costModel?.borrowInputDecimals ?? 18);
      const probeAmount = raw => {
        if (raw === null || raw === undefined || raw === "") return null;
        const parsed = Number(raw);
        return Number.isFinite(parsed) ? parsed / (10 ** probeDecimals) : null;
      };
      const probeRows = Array.isArray(probe?.attempts)
        ? probe.attempts.filter(Boolean).map(row => {
            const amountText = numeric(probeAmount(row.amountRaw), 8);
            const reasonText = row.failureReason ? ` (${esc(row.failureReason)})` : "";
            return `<div class="muted">探测 ${amountText} ${esc(evidence.costModel?.borrowInputSymbol || "")} -> ${esc(row.status || "-")}${reasonText}</div>`;
          }).join("")
        : "";
      const probeHtml = probe
        ? `<div class="muted">执行规模探测：${esc(probe.searchStatus || probe.status || "-")}${probe.selectedProfitableAmountRaw != null ? `，可用 ${numeric(probeAmount(probe.selectedProfitableAmountRaw), 8)} ${esc(evidence.costModel?.borrowInputSymbol || "")}` : ""}</div>${probeRows}`
        : "";
      return `<div class="onchain-evidence ${status === "live_verified" ? "good" : "bad"}"
        data-onchain-status="${esc(status)}"><strong>${esc(evidenceStatusEx(status))}</strong>
        <span class="muted">区块 ${esc(evidence.blockNumber || evidence.triggerBlockNumber || "-")} | 报价区块 ${esc(quoteBlockLabel(evidence.quoteBlockSource))} | 信任级别 ${esc(evidenceTrustText(evidence, analysis))}</span>
        ${focusFailed ? `<div class="evidence-error">第 ${esc(focusFailed.hop || "-")} 步 ${esc(focusFailed.fromSymbol || "-")} -> ${esc(focusFailed.toSymbol || "-")}：${focusFailedInputText}${errorStackHtml(focusFailed.errorStack, focusFailed.liveQuoteError || "报价失败")}</div>` : ""}
        ${focusFailed?.liveQuoteError ? "" : (stackTop ? `<div class="muted">模块 ${esc(stackTop.module || "")} | code=${esc(stackTop.code ?? "")} | ${esc(stackTop.message || "")}</div>` : "")}
        ${blockersToShow.map(item => `<div class="muted">${esc(blockerText(item))}${errorChainHtml(item)}</div>`).join("")}
        ${probeHtml}</div>`;
    }
    const inputSymbol = (route, analysis) => route?.routeSymbols?.[0] || analysis?.referenceInputSymbol || analysis?.inputSymbol || "";
    const routeSizeAmount = (value, route, analysis) => amount(value, inputSymbol(route, analysis));
    const tokenDecimalsFor = (route, evidence) => {
      const value = evidence?.costModel?.borrowInputDecimals
        ?? evidence?.validation?.borrowInputDecimals
        ?? route?.quoteAnalysis?.referenceInputDecimals
        ?? route?.quoteInputs?.routePools?.[0]?.token0Decimals
        ?? route?.quoteInputs?.routePools?.[0]?.token1Decimals
        ?? 18;
      return Number.isFinite(Number(value)) ? Number(value) : 18;
    };
    const rawToHumanAmount = (raw, decimals) => {
      const parsed = Number(raw);
      const dec = Number(decimals);
      if (!Number.isFinite(parsed) || !Number.isFinite(dec) || raw === null || raw === undefined || raw === "") return null;
      return parsed / (10 ** dec);
    };
    const offchainProbeAmount = (route, evidence) => rawToHumanAmount(evidence?.offchainInitialProbeAmountRaw, tokenDecimalsFor(route, evidence));
    const offchainProbeReasonText = reason => ({
      "stablecoin_usd_1000u_anchor": "按约 1000 USD 等价换算",
      "one_basic_unit_fallback": "使用 1 个基础代币单位",
      "onchain_review_best_amount_reused": "使用链上复核确认金额作为初始量",
      "onchain_preview_adaptive_best_amount": "链上合约自适应搜索返回最佳量",
      "liquidity_cap_pool_reserve_2pct": "受池子流动性 2% 上限约束",
      "no_offchain_initial_anchor": "暂无可用价格/流动性锚点",
      "requested_borrow_amount_fallback": "使用请求量作为初始探测起点（兼容旧数据）",
    })[String(reason || "")] || reason || "";
    const evidenceProbeReasonText = (reason, evidence) => {
      const status = String((evidence && (evidence.status || evidence.evidenceStatus)) || "").toLowerCase();
      if (terminalEvidenceStatus(status) && String(reason || "").toLowerCase() === "onchain_review_best_amount_reused") {
        return "-";
      }
      return offchainProbeReasonText(reason);
    };
    const enrichEvidenceHops = hops => {
      let previousOutputAmount = null;
      let previousOutputSymbol = "";
      let previousOutputText = null;
      return (Array.isArray(hops) ? hops : []).filter(hop => hop && typeof hop === "object").map(hop => {
        const normalized = {...hop};
        let inputAmount = normalized.amountIn ?? normalized.inputAmount;
        if (inputAmount == null || inputAmount === "") {
          if (normalized.amountInRaw != null && normalized.inputDecimals != null) {
            const derived = Number(normalized.amountInRaw) / (10 ** Number(normalized.inputDecimals));
            if (Number.isFinite(derived)) inputAmount = derived;
          }
          if ((inputAmount == null || inputAmount === "") && previousOutputAmount != null && previousOutputSymbol === String(normalized.fromSymbol || "").toUpperCase()) {
            inputAmount = previousOutputAmount;
          }
        }
        if (inputAmount != null && inputAmount !== "") {
          normalized.amountIn = inputAmount === previousOutputAmount && previousOutputText != null ? previousOutputText : String(inputAmount);
          normalized.inputAmount = inputAmount;
        }
        if (normalized.amountOut != null && normalized.amountOut !== "") {
          const parsed = Number(normalized.amountOut);
          if (Number.isFinite(parsed)) previousOutputAmount = parsed;
          previousOutputSymbol = String(normalized.toSymbol || "").toUpperCase();
          previousOutputText = String(normalized.amountOut);
        } else if (normalized.amountOutRaw != null && normalized.outputDecimals != null) {
          const parsed = Number(normalized.amountOutRaw) / (10 ** Number(normalized.outputDecimals));
          if (Number.isFinite(parsed)) previousOutputAmount = parsed;
          previousOutputSymbol = String(normalized.toSymbol || "").toUpperCase();
          previousOutputText = String(Number(normalized.amountOutRaw) / (10 ** Number(normalized.outputDecimals)));
        }
        return normalized;
      });
    };
    const normalizeRouteForRender = route => {
      if (!route || typeof route !== "object") return {};
      const quoteAnalysis = route.quoteAnalysis && typeof route.quoteAnalysis === "object" ? route.quoteAnalysis : {};
      const liveEvidence = route.live_onchain_evidence && typeof route.live_onchain_evidence === "object" ? route.live_onchain_evidence : {};
      const qaLiveEvidence = quoteAnalysis.live_onchain_evidence && typeof quoteAnalysis.live_onchain_evidence === "object" ? quoteAnalysis.live_onchain_evidence : {};
      const qaSteps = Array.isArray(quoteAnalysis.steps) ? quoteAnalysis.steps.filter(step => step && typeof step === "object") : quoteAnalysis.steps;
      const liveHops = enrichEvidenceHops(liveEvidence.hops);
      const qaLiveHops = enrichEvidenceHops(qaLiveEvidence.hops);
      return {
        ...route,
        quoteAnalysis: {
          ...quoteAnalysis,
          steps: qaSteps,
          live_onchain_evidence: {...qaLiveEvidence, hops: qaLiveHops},
        },
        live_onchain_evidence: {...liveEvidence, hops: liveHops},
      };
    };
    const hopCandidateSectionHtml = route => {
      const candidates = Array.isArray(route?.hopCandidates)
        ? route.hopCandidates.filter(candidate => candidate && typeof candidate === "object")
        : [];
      if (!candidates.length) return "";
      const selection = route.hopCandidateSelection && typeof route.hopCandidateSelection === "object"
        ? route.hopCandidateSelection
        : {};
      const preferredIds = Array.isArray(selection.preferredPoolIds)
        ? selection.preferredPoolIds.map(value => String(value || "").toLowerCase())
        : [];
      const combinationCount = Number(route.hopCandidateCombinationCount || candidates.reduce((total, candidate) => total * Math.max(1, Number(candidate.poolCount) || 0), 1));
      const summary = candidates.map(candidate => {
        const usable = Number(candidate.usablePoolCount ?? candidate.poolCount ?? 0);
        return `<span class="badge">${esc(candidate.leg || "-")} ${esc(candidate.symbolPair || "-")} / ${numeric(candidate.poolCount ?? 0, 0)} 池 / 可用 ${numeric(usable, 0)}</span>`;
      }).join("");
      const replacement = route.hopCandidateReplacement && typeof route.hopCandidateReplacement === "object"
        ? route.hopCandidateReplacement
        : {};
      const temporaryPolicy = route.temporaryRoutePolicy && typeof route.temporaryRoutePolicy === "object"
        ? route.temporaryRoutePolicy
        : {};
      const temporaryRoute = Boolean(route.temporaryRoute || temporaryPolicy.temporaryRoute || temporaryPolicy.executeLast);
      const temporaryBadge = temporaryRoute
        ? '<span class="badge warn">临时路径 · 不写回默认扫描 · 执行顺序最后 · 执行后仅入历史</span>'
        : "";
      const replacementReasonLabels = {
        better_candidate_found: "已替换候选池",
        no_candidate_variants: "无可替换候选",
        no_better_candidate: "无更优候选",
        no_replacement_trigger: "当前路径无需替换",
        uniswapx_middle_hop_fallback: "中间第二跳改用 UniswapX",
        uniswapx_unsupported: "UniswapX 不可用，失败终止",
        uniswapx_middle_hop_missing: "缺少中间第二跳，失败终止",
        uniswapx_middle_hop_unavailable: "UniswapX 无合适中间池，失败终止",
        uniswapx_middle_hop_parameters_unavailable: "UniswapX 中间跳参数不可用，失败终止",
        uniswapx_fallback_profit_below_minimum: "三跳净收益未达 1%，失败终止",
        uniswapx_fallback_quote_required: "UniswapX 报价缺失，失败终止",
      };
      const replacementText = replacement.attempted
        ? (replacementReasonLabels[replacement.reason] || String(replacement.reason || "已评估候选替换"))
        : "";
      const replacementBadge = replacement.attempted
        ? `<span class="badge ${replacement.reason === "better_candidate_found" || replacement.reason === "uniswapx_middle_hop_fallback" ? "good" : "fail"}">候选替换：${esc(replacementText)}${Array.isArray(replacement.changedLegs) && replacement.changedLegs.length ? ` / 更换 ${esc(replacement.changedLegs.join("、"))}` : ""}</span>`
        : "";
      const uniswapxFallback = route.uniswapxFallback && typeof route.uniswapxFallback === "object"
        ? route.uniswapxFallback
        : {};
      const uniswapxMiddleHop = uniswapxFallback.middleHop && typeof uniswapxFallback.middleHop === "object"
        ? uniswapxFallback.middleHop
        : {};
      const uniswapxParams = uniswapxFallback.quoteParameters && typeof uniswapxFallback.quoteParameters === "object"
        ? uniswapxFallback.quoteParameters
        : {};
      const uniswapxBadge = uniswapxFallback.status === "selected"
        ? `<span class="badge good">UniswapX 中间第二跳：${esc(uniswapxMiddleHop.fromSymbol || "-")} -> ${esc(uniswapxMiddleHop.toSymbol || "-")} / 三跳净收益目标 ≥ 100 bps${uniswapxParams.sellAmount != null ? ` / y=${numeric(uniswapxParams.sellAmount, 8)}` : ""}${uniswapxParams.minBuyAmount != null ? ` / z≥${numeric(uniswapxParams.minBuyAmount, 8)}` : ""}${uniswapxParams.limitPrice != null ? ` / 限价 ${numeric(uniswapxParams.limitPrice, 8)}` : ""}</span>`
        : uniswapxFallback.status === "failed"
          ? `<span class="badge fail">UniswapX 第二跳失败：${esc(replacementText)}</span>`
          : "";
      const pricePreferredLabel = selection.selectionBasis === "best_candidate_net_profit" || temporaryRoute
        ? " / 价格/净利首选"
        : " / 深度首选";
      const body = candidates.map(candidate => {
        const pools = Array.isArray(candidate.pools)
          ? candidate.pools.filter(pool => pool && typeof pool === "object")
          : [];
        const chips = pools.slice(0, 6).map(pool => {
          const id = String(pool?.pool || pool?.poolId || "-").toLowerCase();
          const preferred = preferredIds.includes(id) ? pricePreferredLabel : "";
          const liquidity = poolExplicitlyUsable(pool) ? "有流动性" : (poolExplicitlyUnusable(pool) ? "无流动性" : "未标注");
        const tvl = Number((pool?.balances || {}).tvlUsd ?? pool?.estimatedTvlUsd ?? 0);
        return `<span class="badge ${poolExplicitlyUsable(pool) ? "good" : "warn"}" title="${esc(pool?.pool || pool?.poolId || "-")}">${esc(shortPoolId(pool?.pool || pool?.poolId || "-"))}${esc(preferred)} / ${esc(liquidity)}${tvl > 0 ? ` / $${numeric(tvl, 0)}` : ""}</span>`;
      }).join("");
        const venueBadge = candidate.venue === "uniswapx" ? '<span class="badge good">UniswapX</span>' : "";
        return `<tr>
          <td>${esc(candidate.leg || "-")}${venueBadge}</td>
          <td>${esc(candidate.direction || "-")}</td>
          <td>${esc(candidate.symbolPair || "-")}</td>
          <td>${numeric(candidate.poolCount ?? pools.length, 0)}</td>
          <td>${numeric(candidate.usablePoolCount ?? "-", 0)}</td>
          <td>${esc(candidate.preferredPoolId || "-")}</td>
          <td>${chips || '<span class="muted">无候选池明细</span>'}</td>
        </tr>`;
      }).join("");
      return `<div class="table-wrap hop-candidates"><h4>每跳候选池（路径组合 ${numeric(combinationCount, 0)}，当前选择 ${esc(selection.selectionBasis || "tvl_depth_desc")}）</h4><div class="badges">${summary}${temporaryBadge}${replacementBadge}${uniswapxBadge}</div><table>
        <thead><tr><th>跳</th><th>方向</th><th>交易对</th><th>候选池</th><th>可用池</th><th>当前首选</th><th>池列表</th></tr></thead>
        <tbody>${body}</tbody>
      </table></div>`;
    };
    function routeHtml(route, index) {
      const evidence = routeEvidence(route);
      const analysis = routeAnalysis(route);
      const routeUiState = uiStateOf(route);
      const live = Boolean(analysis.livePriceVerified || analysis.priceEvidenceMode === "live_verified");
      const latestBroadcast = latestBroadcastAdvice(route);
      const activeExecutionRoute = Boolean(activeExecution?.routeKey && routeExecutionKey(route) === activeExecution.routeKey);
      const activeBroadcast = activeExecutionRoute
        ? {
            text: activeExecution?.broadcastDisabled || route?.__broadcastDisabled
              ? "链上执行未提交：自动扫描复核并链上广播未开启，本次未发起链上交易。"
              : activeExecution?.state === ROUTE_UI_STATE.EXECUTION_PENDING
              ? "链上执行提交中，请稍候查看本次链上结果。"
              : activeExecution?.state === ROUTE_UI_STATE.EXECUTION_FAILED
                ? (route?.__broadcastError ? `链上执行失败/未提交：${route.__broadcastError}` : "链上执行失败/未提交；请检查网络、RPC 或开关后重试。")
                : "链上执行已提交；请到提交记录或区块浏览器确认交易哈希与链上验证结果。",
            txHash: activeExecution?.txHash || "",
            txHashes: activeExecution?.txHash ? [activeExecution.txHash] : [],
          }
        : null;
      const latestBroadcastForStage = activeBroadcast || latestBroadcast;
      const reviewStage = normalizedReviewStage(route, latestBroadcastForStage, activeExecution);
      const execPending = reviewStage === "execution_pending";
      const execFailed = reviewStage === "execution_failed";
      const preflightFailed = reviewStage === "preflight_failed";
      const steps = analysis.steps || [];
      const actualDirection = pathFromSteps(steps, (route.routeSymbols || []).join(" -> "));
      const costs = evidence.costModel || {};
      const failedHops = (Array.isArray(evidence.hops) ? evidence.hops : steps).filter(hop => hop?.liveQuoteError || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity", "adapter_required"].includes(String(hop?.status || "").toLowerCase()));
      const failedHop = failedHops[0] || null;
      const failedStep = failedHop ? steps.find(step => Number(step?.hop) === Number(failedHop.hop)) : null;
      const focusOnError = Boolean(failedHop);
      const focusSteps = steps.filter(Boolean);
      const stackTop = failedHop?.errorStack?.[0] || evidence.errorStack?.[0] || route.errorStack?.[0] || null;
      const focusErrorText = failedHop?.liveQuoteError ? blockerText(failedHop.liveQuoteError) : (stackTop?.message ? blockerText(stackTop.message) : "链上单跳报价失败");
      const recommendation = analysis.recommendation || {};
      const negativeEvidence = terminalEvidenceStatus(evidence.status);
      const requests = route.quoteInputs?.quoteRequests || [];
      const pools = route.quoteInputs?.candidatePools || [];
      const displayedSteps = focusSteps.length ? focusSteps : steps.filter(Boolean);
      const isFailedStep = step => Boolean(step?.liveQuoteError) || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity", "adapter_required"].includes(String(step?.status || "").toLowerCase());
      const stepRows = displayedSteps.map(step => `<tr><td>${esc(step.hop)}</td><td>${esc(step.fromSymbol)} -> ${esc(step.toSymbol)}</td><td>${esc(step.protocol || step.dex || "-")}</td><td>${esc(label(step.quoteSource))}${step.liveQuoteError ? `<div class="evidence-error">${errorStackHtml(step.errorStack, step.liveQuoteError)}</div>` : ""}</td><td>${esc(step.pool || "-")}</td><td>${numeric(step.feeBps, 2)}</td><td>${numeric(step.inputAmount, 8)} ${esc(step.fromSymbol)}</td><td>${isFailedStep(step) ? "报价失败" : (live ? `${numeric(step.outputAmount, 8)} ${esc(step.toSymbol)}` : `${numeric(step.referenceOutputAmount, 8)} ${esc(step.toSymbol)}（参考）`)}</td><td>${isFailedStep(step) ? "-" : (live ? numeric(step.effectiveRate, 8) : numeric(step.referenceEffectiveRate, 8))}</td><td>${isFailedStep(step) ? "-" : (step.priceImpactBps == null ? "-" : `${numeric(step.priceImpactBps, 2)} / ${numeric(step.priceImpactLimitBps ?? evidence.validation?.hopPriceImpactLimitBps ?? 10, 0)} bps`)}</td></tr>`).join("");
      const finalOutputUsd = live ? costs.finalOutputUsd : analysis.referenceFinalOutputUsd;
      const finalOutputAmount = live ? costs.finalOutputAmount : analysis.referenceFinalOutputAmount;
      const finalOutputSymbol = costs.finalOutputSymbol || analysis.referenceFinalOutputSymbol || analysis.outputSymbol || inputSymbol(route, analysis);
      const grossProfitUsd = live ? costs.grossProfitUsd : analysis.referenceGrossProfitUsd;
      const grossProfitAmount = live ? costs.grossProfitAmount : analysis.referenceGrossProfitAmount;
      const tokenNetProfitAmount = live ? (costs.netProfitAmount ?? humanTokenValue(analysis.referenceNetProfitAmount)) : humanTokenValue(analysis.referenceNetProfitAmount);
      const tokenNetProfitSymbol = live ? (costs.netProfitAmountSymbol || analysis.referenceInputSymbol || inputSymbol(route, analysis)) : (analysis.referenceInputSymbol || inputSymbol(route, analysis));
      const tokenNetReturnBps = live ? (costs.netProfitAmountBps ?? analysis.referenceNetReturnBps) : analysis.referenceNetReturnBps;
      const profitBasis = (live ? costs.profitBasis : analysis.referenceReturnBasis) || analysis.referenceReturnBasis;
      const tokenProfit = profitBasis === "borrow_token" && tokenNetProfitAmount != null ? `${amount(tokenNetProfitAmount, tokenNetProfitSymbol)} / ${numeric(tokenNetReturnBps, 2)} bps` : "-";
      const tokenProfitLabel = live ? "X 本位结果" : "X 本位参考";
      const netProfitDisplay = live ? (analysis.netProfitUsd != null ? money(analysis.netProfitUsd) : tokenProfit) : tokenProfit;
      const netReturnDisplay = live ? numeric(analysis.netProfitBps ?? tokenNetReturnBps, 2) : numeric(analysis.referenceNetReturnBps, 2);
      const finalOutputDisplay = finalOutputUsd != null ? money(finalOutputUsd) : amount(finalOutputAmount, finalOutputSymbol);
      const grossProfitDisplay = grossProfitUsd != null ? money(grossProfitUsd) : amount(grossProfitAmount, finalOutputSymbol);
      const offchainProbe = offchainProbeAmount(route, evidence);
      const actionRequested = Number(route.__requestedAmount);
      const useActionRequest = Number.isFinite(actionRequested) && actionRequested > 0 && reviewStage !== "estimate";
      const requestedAmountValue = useActionRequest
        ? actionRequested
        : costs.requestedBorrowInputAmount ?? analysis.referenceInputAmount ?? analysis.requestedAmount ?? analysis.amountOptimizedFrom ?? offchainProbe ?? analysis.referenceInputAmount;
      const initialProbeValue = useActionRequest
        ? actionRequested
        : (costs.requestedBorrowInputAmount ?? offchainProbe ?? costs.selectedBorrowInputAmount ?? route.sizeAnalysis?.recommendedBorrowInputAmount ?? route.sizeAnalysis?.tokenAmountSteps?.[0] ?? analysis.referenceInputAmount);
      const initialProbeLabel = reviewStage === "execution" || execPending || execFailed
        ? "执行初始量/起点"
        : reviewStage === "preflight" || preflightFailed
          ? "初始测试量"
          : "链下初始探测";
      const rawBestAmountValue = routeBestAmountPick(route, evidence, analysis);
      const bestAmountMissing = bestAmountMissingOf(route, evidence, analysis) || costs.bestAmountMissing === true;
      const initialOnlyEstimateValue = offchainProbe ?? analysis.requestedAmount ?? analysis.initialProbeAmount ?? rawBestAmountValue ?? 1;
      const bestAmountValue = reviewStage === "estimate" ? initialOnlyEstimateValue : rawBestAmountValue;
      const bestAmountDisplay = reviewStage === "estimate"
        ? routeSizeAmount(bestAmountValue, route, analysis)
        : (bestAmountValue === null || bestAmountValue === undefined || bestAmountValue === "" ? "未计算" : `${routeSizeAmount(bestAmountValue, route, analysis)}${bestAmountMissing ? "（未确认）" : ""}`);
      const bestAmountLabel = bestAmountDisplayLabel({evidence, analysis, stage: reviewStage});
      const depthStatus = String(analysis.staticDepthStatus || "unavailable").toLowerCase();
      const depthKnown = live || depthStatus === "available" || negativeEvidence;
      const failedIsLiquidity = focusOnError && String(failedHop.status || "").toLowerCase() === "not_enough_liquidity";
      const failedIsDeferred = focusOnError && ["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(failedHop.status || "").toLowerCase());
      const failedIsAdapter = focusOnError && String(failedHop.status || "").toLowerCase() === "adapter_required";
      const failedInputAmount = failedHop?.amountIn ?? failedHop?.inputAmount ?? failedStep?.inputAmount ?? null;
      const failedInputSymbol = failedHop?.fromSymbol || "";
      const failedAction = !focusOnError
        ? ""
        : failedIsLiquidity
          ? `第 ${esc(failedHop.hop)} 步 ${esc(failedHop.fromSymbol)} -> ${esc(failedHop.toSymbol)} 流动性不足：输入 ${numeric(failedInputAmount, 8)} ${esc(failedInputSymbol)} 被池子拒绝`
          : failedIsAdapter
            ? `第 ${esc(failedHop.hop)} 步 ${esc(failedHop.fromSymbol)} -> ${esc(failedHop.toSymbol)} 缺少 V4 报价适配器，池子状态延后解析`
          : failedIsDeferred
            ? `第 ${esc(failedHop.hop)} 步 ${esc(failedHop.fromSymbol)} -> ${esc(failedHop.toSymbol)} 链上复核延后`
            : `第 ${esc(failedHop.hop)} 步 ${esc(failedHop.fromSymbol)} -> ${esc(failedHop.toSymbol)} 失败`;
      const recommendationRows = focusOnError ? [`${failedAction}：${esc(focusErrorText)}`] : lines(recommendation.reasons);
      const analysisHtml = focusOnError
        ? `<div class="analysis focus-error"><div><small>错误定位</small><strong>第 ${esc(failedHop.hop)} 步 ${esc(failedHop.fromSymbol)} -> ${esc(failedHop.toSymbol)}</strong></div><div><small>链上错误</small>${errorStackHtml(failedHop.errorStack, failedHop.liveQuoteError || "")}</div>${stackTop && !failedHop?.liveQuoteError ? `<div><small>模块</small><strong>${esc(stackTop.module || "")} | code=${esc(stackTop.code ?? "")}</strong></div>` : ""}</div>`
        : `<div class="analysis"><div><small>交易方向</small><strong>${esc(actualDirection)}</strong></div><div><small>${bestAmountLabel}</small><strong>${esc(bestAmountDisplay)}</strong></div><div><small>${live ? "最终输出" : "最终输出（参考）"}</small><strong>${depthKnown ? esc(finalOutputDisplay) : "等待深度探测"}</strong></div><div><small>${live ? "毛利润" : "毛利润（参考）"}</small><strong>${depthKnown ? esc(grossProfitDisplay) : "等待深度探测"}</strong></div><div><small>${live ? "净利润" : "净利润（参考）"}</small><strong>${depthKnown ? esc(netProfitDisplay) : "-"}</strong></div><div><small>${live ? "净回报 bps" : "净回报 bps（参考）"}</small><strong>${depthKnown ? netReturnDisplay : "-"}</strong></div><div><small>${tokenProfitLabel}</small><strong>${depthKnown ? esc(tokenProfit) : "-"}</strong></div><div><small>${initialProbeLabel}</small><strong>${routeSizeAmount(initialProbeValue, route, analysis)}</strong></div><div><small>初始探测依据</small><strong>${esc(evidenceProbeReasonText(evidence.offchainInitialProbeReason, evidence))}</strong></div><div><small>历史最大可执行金额</small><strong>${routeSizeAmount(route.sizeAnalysis?.maxExecutableAmountRaw, route, analysis)}</strong></div>${stackTop ? `<div><small>模块</small><strong>${esc(stackTop.module || "")} | code=${esc(stackTop.code ?? "")}</strong></div><div><small>链上错误</small>${errorStackHtml(evidence.errorStack || route.errorStack, stackTop.message || "")}</div>` : ""}</div>`;
      const depthLabel = live ? "链上实时深度已验证" : failedIsLiquidity ? "流动性不足，已停止继续复核" : failedIsDeferred ? "链上复核延后，不判定为路线失败" : focusOnError ? "已停止在链上报价" : depthStatus === "available" ? "离线深度可用" : depthStatus === "exceeds_10_bps" ? "请降低金额：滑点超过 10 bps" : "离线深度不可用";
      const recClass = ["pool_not_initialized", "not_enough_liquidity", "adapter_required", "live_quote_failed", "pool_insufficient_for_profitable_execution", "route_unprofitable"].includes(String(evidence.status || "").toLowerCase()) ? "bad" : (["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(evidence.status || "").toLowerCase()) ? "warn" : (live ? "good" : "warn"));
      const isDeferred = ["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(evidence.status || "").toLowerCase());
      const baseActionText = focusOnError
        ? failedAction
        : negativeEvidence
          ? terminalFailureAdvice(evidence.status)
        : live && ["route_unprofitable", "pool_insufficient_for_profitable_execution"].includes(String(evidence.status || "").toLowerCase())
          ? "链上复核通过，但链上执行条件尚未满足"
          : isDeferred && (steps.length || Object.keys(evidence).length)
            ? "本轮未集中复核；可查看链下估算"
            : recommendationAction(recommendation.action || label(route.quoteStatus));
      const actionText = latestBroadcastForStage?.text || baseActionText;
      const actionTextPlain = String(actionText || "").trim();
      const recommendationRowsDeduped = recommendationRows.filter(item => {
        const text = blockerText(item).trim();
        return text && text !== actionTextPlain && !(actionTextPlain && (actionTextPlain.includes(text) || text.includes(actionTextPlain)));
      });
      const referenceBadge = !live && !analysis.chainReviewed && analysis.requiresLiveQuoter ? `<span class="badge warn">静态候选（待链上报价）</span>` : "";
      const deferredReviewBadge = isDeferred ? `<span class="badge warn">可查看链下估算</span>` : "";
      const priceReviewBadge = reviewStage === "execution" || execPending || execFailed
        ? "链上执行报价"
        : reviewStage === "preflight" || preflightFailed
          ? (preflightFailed
              ? `链上复核失败：${negativeEvidence ? evidenceStatusEx(evidence.status) : (route.__broadcastError ? route.__broadcastError : "后台未返回链上结果")}`
              : negativeEvidence ? `链上复核失败：${evidenceStatusEx(evidence.status)}` : live ? "链上复核价格已验证" : "链上复核完成：等待逐 hop 证据确认")
          : routeNeedsLiveReview(route)
            ? "静态参考，待链上逐 hop 复核"
          : routeUiState === ROUTE_UI_STATE.OFFCHAIN_PENDING && !routeHasCompletedOffchainReview(route)
            ? "链下估算完成：默认初始量已生成"
            : (live ? "链下估算完成：实时报价器只读" : "链下估算完成：待链上复核");
      const autoAmount = autoRouteAmount(route);
      const routeTestAmountSymbol = analysis.referenceInputSymbol || inputSymbol(route, analysis) || (route.routeSymbols || [])[0] || "";
      const bestCalc = analysis.bestAmountCalculation || route.bestAmountCalculation || route.quoteAnalysis?.bestAmountCalculation || {};
      const calcRowSources = [
        ...(Array.isArray(bestCalc.probeRecords) ? bestCalc.probeRecords : []),
        ...(Array.isArray(bestCalc.probe_records) ? bestCalc.probe_records : []),
        ...(Array.isArray(bestCalc.rows) ? bestCalc.rows : []),
        ...(Array.isArray(route.sizeAnalysis?.rows) ? route.sizeAnalysis.rows : []),
        ...(Array.isArray(analysis.probeRecords) ? analysis.probeRecords : []),
        ...(Array.isArray(analysis.probe_records) ? analysis.probe_records : []),
      ].filter(row => row && typeof row === "object");
      const seenCalcAmounts = new Set();
      const calcRows = calcRowSources
        .map(row => {
          const parsedAmount = Number(row.borrowAmount ?? row.amount ?? row.referenceInputAmount ?? row.inputAmount);
          if (!Number.isFinite(parsedAmount) || parsedAmount <= 0) return null;
          const calcKey = parsedAmount.toFixed(12);
          if (seenCalcAmounts.has(calcKey)) return null;
          seenCalcAmounts.add(calcKey);
          const calcSymbol = row.borrowSymbol || row.referenceInputSymbol || row.symbol || routeTestAmountSymbol;
          const output = row.referenceFinalOutputAmount ?? row.finalOutputAmount ?? row.outputAmount;
          const profit = humanTokenValue(row.referenceNetProfitAmount ?? row.netProfitAmount ?? row.profitAmount ?? row.profit);
          const bps = row.referenceNetReturnBps ?? row.netProfitBps ?? row.observedNetProfitBps ?? row.profitBps;
          const mark = row.isRecommended ? "推荐" : row.isBest ? "本次最佳" : (Number.isFinite(Number(profit)) && Number(profit) > 0 ? "盈利" : "");
          return `<tr><td>${numeric(parsedAmount, 8)} ${esc(calcSymbol || "")}</td><td>${output == null || output === "" ? "-" : esc(output)}</td><td>${profit == null || profit === "" ? "-" : esc(profit)}</td><td>${bps == null || bps === "" ? "-" : numeric(bps, 2)}</td><td>${row.executableCandidate ? "可执行" : "不可执行"}</td><td>${esc(row.staticDepthStatus || row.staticDepthAvailable || "-")}</td><td>${mark ? `<span class="badge ${row.isRecommended ? "good" : "warn"}">${esc(mark)}</span>` : "-"}</td></tr>`;
        })
        .filter(Boolean);
      const bestCalcRecommended = bestCalc.recommendedBorrowInputAmount != null
        ? `推荐量 ${numeric(bestCalc.recommendedBorrowInputAmount, 8)} ${esc(routeTestAmountSymbol || "")}${bestCalc.recommendedBorrowInputAmountReason ? `（${esc(bestCalc.recommendedBorrowInputAmountReason)}）` : ""}`
        : "";
      const bestCalcHtml = calcRows.length && reviewStage !== "estimate"
        ? `<div class="table-wrap best-amount-calc"><h4>最佳量计算过程</h4>${bestCalcRecommended ? `<div class="muted">${bestCalcRecommended}</div>` : ""}<table><thead><tr><th>金额</th><th>最终输出</th><th>净利</th><th>bps</th><th>可执行</th><th>深度</th><th>标记</th></tr></thead><tbody>${calcRows.join("")}</tbody></table></div>`
        : "";
      return `<article class="route"><div class="route-head"><div><h2>${esc(route.symbolPair || route.routeSymbols?.join(" -> ") || "路线")}</h2><div class="badges">
        <span class="badge ${recClass}">${esc(actionText)}</span>
        ${referenceBadge}
        ${deferredReviewBadge}
        <span class="badge">路径 ${esc((route.routeSymbols || []).join(" -> "))}</span>
        ${activeExecutionRoute ? `<span class="badge good">本次链上执行中</span>` : ""}
        <span class="badge ${live ? "good" : "warn"}">${priceReviewBadge}</span>
        <span class="badge ${Object.keys(evidence).length && evidence.status !== "live_verified" ? "bad" : "good"}">${esc(evidenceStatusEx(evidence.status || ""))}</span>
        <span class="badge ${live || depthStatus === "available" ? "good" : "warn"}">${esc(depthLabel)}</span>
      </div></div><button class="copy-btn" type="button" data-copy-route="${index}">复制本组</button></div>
        <div class="route-test">${routeActionHtml(route, index)}<span class="route-test-result" data-route-test-result="${index}"></span></div>
        ${onchainEvidenceHtml(evidence, route)}
      ${analysisHtml}
      ${bestCalcHtml}
      <div class="table-wrap"><table><thead><tr><th>跳</th><th>兑换</th><th>协议</th><th>报价来源</th><th>池</th><th>手续费 bps</th><th>输入</th><th>输出</th><th>汇率</th><th>价格影响 / 上限</th></tr></thead><tbody>${stepRows || '<tr><td colspan="10">暂无单跳报价数据</td></tr>'}</tbody></table></div>
      <div class="recommendation" data-route-recommendation="${index}"><strong>建议：${esc(actionText)}</strong>${latestBroadcastForStage?.txHash ? `<div class="muted">最近已执行：${esc(latestBroadcastForStage.txHash)}${latestBroadcastForStage.txHashes?.length > 1 ? ` 等 ${latestBroadcastForStage.txHashes.length} 笔` : ""}</div>` : ""}<ol>${recommendationRowsDeduped.map(item => `<li>${esc(blockerText(item))}</li>`).join("")}</ol></div>
      ${hopCandidateSectionHtml(route)}
      <div class="muted">候选池：${esc(pools.map(pool => pool.pool).join(" | ") || "-")}</div><div class="badges">${requests.map(item => `<span class="badge">${esc(label(item.venue))} / ${esc(label(item.quoteMode))} / ${esc(label(item.status))}</span>`).join("")}</div></article>`;
    }
    function routeHtmlSafe(route, index) {
      try {
        return routeHtml(normalizeRouteForRender(route), index);
      } catch (error) {
        const name = route?.symbolPair || (route?.routeSymbols || []).join(" -> ") || "路线";
        if (window.console?.error) window.console.error("dex route render failed", route, error);
        return `<article class="route"><div class="route-head"><h2>${esc(name)}</h2></div><div class="recommendation"><strong>建议：该路由渲染已跳过异常展示</strong><div class="muted">接口返回了不完整的路线数据，请等待下一次扫描刷新。</div></div></article>`;
      }
    }
    function autoRouteAmount(route) {
      const size = route?.sizeAnalysis || {};
      const costs = route?.quoteCosts || {};
      const analysis = route?.quoteAnalysis || {};
      const evidence = routeEvidence(route);
      const evidenceCosts = evidence?.costModel || {};
      const probe = offchainProbeAmount(route, evidence);
      // The off-chain probe amount is the normal starting point.  When it is
      // not available, prefer an existing size-analysis recommendation or the
      // token-basis ladder peak for closed routes before using one basic unit.
      const candidate = probe
        ?? evidenceCosts.requestedBorrowInputAmount
        ?? route.__requestedAmount
        ?? size.recommendedBorrowInputAmount
        ?? (Array.isArray(size.tokenAmountSteps) && size.tokenAmountSteps.length
            ? size.tokenAmountSteps[size.tokenAmountSteps.length - 1]
            : null)
        ?? analysis.requestedAmount
        ?? analysis.initialProbeAmount
        ?? costs.requestedBorrowInputAmount
        ?? 1;
      const parsed = Number(candidate);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
    }
    function autoRouteAmountSymbol(route) {
      return String(route?.sizeAnalysis?.recommendedBorrowInputAmountBasis || route?.quoteAnalysis?.referenceInputSymbol || route?.quoteCosts?.borrowInputSymbol || (route?.routeSymbols || [])[0] || "token");
    }
    const numberValue = value => value === null || value === undefined || value === "" ? null : (Number.isFinite(Number(value)) ? Number(value) : null);
    const humanTokenValue = value => {
      const parsed = numberValue(value);
      if (parsed === null) return null;
      const abs = Math.abs(parsed);
      if (abs >= 1e18 && Number.isInteger(parsed)) return parsed / 1e18;
      return parsed;
    };
    const submissionStatusLower = row => String(row?.review_category || row?.category || row?.status || row?.state || row?.submission_status || "").toLowerCase();
    const submissionIsBroadcastSuccess = row => ["submitted_success", "broadcast_success", "broadcasted", "submission_success", "broadcast_complete", "execution_success", "confirmed_success", "settled", "executed", "success"].includes(submissionStatusLower(row));
    const submissionRouteKey = row => String(row?.direction || row?.quote?.direction || row?.pair || row?.route_key || (Array.isArray(row?.route_path) ? row.route_path.join(" -> ") : "") || "").toUpperCase();
    const broadcastSubmissionsForRoute = route => {
      if (!latestSubmissionRows.length || !route) return [];
      const routeKey = String(route.symbolPair || (route.routeSymbols || []).join(" -> ") || "").toUpperCase();
      if (!routeKey) return [];
      return latestSubmissionRows
        .filter(row => submissionIsBroadcastSuccess(row) && row.tx_hash && submissionRouteKey(row) === routeKey)
        .sort((a, b) => String(b.observed_at || b.created_at || b.updated_at || "").localeCompare(String(a.observed_at || a.created_at || a.updated_at || "")) || 0) || [];
    };
    const latestBroadcastSubmissionForRoute = route => {
      const rows = broadcastSubmissionsForRoute(route);
      return rows[0] || null;
    };
    const staleBroadcastSuppressed = route => {
      const evidence = routeEvidence(route);
      const status = String(evidence.status || route.quoteStatus || route.onchainEvidenceStatus || "").toLowerCase();
      return terminalEvidenceStatus(status) || routeNeedsLiveReview(route);
    };
    const latestBroadcastAdvice = route => {
      const rows = broadcastSubmissionsForRoute(route);
      const row = rows[0] || null;
      if (!row || staleBroadcastSuppressed(route)) return {text: "", txHash: "", txHashes: []};
      const quote = row.quote || {};
      const analysis = quote.quote_analysis || quote.analysis || {};
      const txHashes = row.tx_hash ? [row.tx_hash] : [];
      const txHash = txHashes[0] || "-";
      const profit = humanTokenValue(row.profit_amount ?? quote.profit_amount ?? analysis.referenceNetProfitAmount ?? analysis.netProfitAmount);
      const pct = numberValue(row.profit_percent ?? quote.profit_percent ?? (analysis.referenceNetReturnBps == null ? null : Number(analysis.referenceNetReturnBps) / 100));
      const symbol = quote.input_symbol || quote.final_symbol || analysis.referenceInputSymbol || analysis.referenceFinalOutputSymbol || "";
      const parts = [`已执行链上交易 ${txHash}`];
      if (profit !== null) parts.push(`盈亏=${profit >= 0 ? "+" : ""}${numeric(profit, 6)} ${symbol || ""}`.trim());
      if (pct !== null) parts.push(`比例=${pct >= 0 ? "+" : ""}${numeric(pct, 4)}%`);
      parts.push("请先到提交记录或区块浏览器确认交易结果；后续是否仍有新机会以新的链上复核为准，不需要手动判断当前建议是否仍有效");
      return {text: parts.join("；"), txHash, txHashes};
    };
    const shortHash = value => {
      const text = String(value || "").trim();
      return text.length > 18 ? `${text.slice(0, 6)}...${text.slice(-4)}` : text;
    };
    const submissionsHref = () => {
      const base = "/dex/submissions";
      return window.ChainSelector?.withSelection ? window.ChainSelector.withSelection(base) : base;
    };
    const routeActionHtml = (route, index) => {
      const diagnosticOnly = route?.diagnosticOnly === true
        || String(route?.pathSource || "").trim() === "candidate_pool_fallback"
        || String(route?.tableSource || "").trim() !== "dex_table";
      if (diagnosticOnly) {
        return `<span class="muted">仅诊断展示</span><span class="muted">不在 DEX_TABLE 路由中，不进入链上复核</span>`;
      }
      const broadcast = latestBroadcastAdvice(route);
      const activeExecutionRoute = Boolean(activeExecution?.routeKey && routeExecutionKey(route) === activeExecution.routeKey);
      if (activeExecutionRoute) {
        const state = String(activeExecution?.state || "");
        const activeLabel = activeExecution?.broadcastDisabled || route?.__broadcastDisabled
          ? "链上执行未提交"
          : state === ROUTE_UI_STATE.EXECUTION_PENDING
          ? "链上执行提交中"
          : state === ROUTE_UI_STATE.EXECUTION_SUBMITTED
            ? "链上执行已提交"
            : state === ROUTE_UI_STATE.EXECUTION_FAILED
              ? "链上执行失败/未提交"
              : "链上执行中";
        return `<span class="muted">${esc(activeLabel)}</span>`;
      }
      if (broadcast?.txHash) {
        return `<span class="muted">最近已执行：${esc(shortHash(broadcast.txHash))}${broadcast.txHashes?.length > 1 ? `（共${broadcast.txHashes.length}笔）` : ""}</span><a class="copy-btn" href="${esc(submissionsHref())}">查看提交记录</a>`;
      }
      const evidence = routeEvidence(route);
      const analysis = routeAnalysis(route);
      const staticReview = routeNeedsLiveReview(route);
      const probeAmount = offchainProbeAmount(route, evidence);
      const actionStage = uiStageFromUiState(effectiveUiState(route));
      const useBestForAction = ["preflight", "preflight_failed", "execution", "execution_pending", "execution_failed"].includes(actionStage);
      const bestAmount = useBestForAction ? routeBestAmountPick(route, evidence, analysis) : null;
      const bestAmountMissing = useBestForAction && (bestAmount === null || bestAmount === undefined || bestAmount === "");
      const bestAmountUnconfirmed = useBestForAction && bestAmountMissingOf(route, evidence, analysis);
      const testAmount = bestAmount ?? probeAmount ?? autoRouteAmount(route);
      const testAmountNumber = Number(testAmount);
      const testAmountText = Number.isFinite(testAmountNumber) && testAmountNumber > 0
        ? `${numeric(testAmount, 8)} ${esc(autoRouteAmountSymbol(route))}`
        : "未计算";
      const broadcastDisabled = bestAmountMissing || bestAmountUnconfirmed || staticReview ? " disabled" : "";
      return `<span class="muted">测试金额 ${testAmountText}</span><span class="muted">链下估算自动完成</span><button type="button" data-route-static-call="${index}">链上复核</button><button type="button" data-route-broadcast="${index}"${broadcastDisabled}>${bestAmountMissing ? (bestAmount === null || bestAmount === undefined || bestAmount === "" ? "最佳量未计算" : "最佳量未确认") : staticReview ? "待链上复核后执行" : "链上执行"}</button>`;
    };
    function routeSummaryHtml(rawRoute, index) {
      try {
        const route = normalizeRouteForRender(rawRoute);
        let evidence = routeEvidence(route);
        let analysis = routeAnalysis(route);
        const display = value => value === null || value === undefined || value === "" ? "-" : String(value);
        const path = route.symbolPair || (route.routeSymbols || []).join(" -> ") || "-";
        const actualDirection = pathFromSteps(Array.isArray(evidence.hops) && evidence.hops.length ? evidence.hops : analysis.steps, path);
        const status = route.quoteStatus || evidence.status || analysis.onchainEvidenceStatus || "quote_required";
        const pendingReview = uiStateOf(route) === ROUTE_UI_STATE.OFFCHAIN_PENDING && !routeHasCompletedOffchainReview(rawRoute);
        const trust = evidenceTrustText(evidence, analysis);
        const block = evidence.blockNumber || evidence.triggerBlockNumber || "-";
        const hops = (Array.isArray(evidence.hops) ? evidence.hops : analysis.steps || []).filter(Boolean).map(step => `第 ${step.hop || "?"} 步: ${display(step.fromSymbol)} -> ${display(step.toSymbol)}`).join(" | ");
      const liveSummary = Boolean(evidence.livePriceVerified || evidence.status === "live_verified" || ["route_unprofitable", "slippage_limit_exceeded", "pool_insufficient_for_profitable_execution"].includes(String(evidence.status || "").toLowerCase()) || ["live_v4_adapter", "live_quoter_verified_readonly", "live_verified"].includes(String(evidence.executionTrust || "").toLowerCase()));
      const recommendation = analysis.recommendation || {};
      const latestBroadcast = latestBroadcastAdvice(rawRoute);
      const activeExecutionRoute = Boolean(activeExecution?.routeKey && routeExecutionKey(route) === activeExecution.routeKey);
      const activeBroadcast = activeExecutionRoute
        ? {
            text: activeExecution?.state === ROUTE_UI_STATE.EXECUTION_PENDING
              ? "链上执行提交中，请稍候查看本次链上结果。"
              : activeExecution?.state === ROUTE_UI_STATE.EXECUTION_FAILED
                ? (route?.__broadcastError ? `链上执行失败/未提交：${route.__broadcastError}` : "链上执行失败/未提交；请检查网络、RPC 或开关后重试。")
                : "链上执行已提交；请到提交记录或区块浏览器确认交易哈希与链上验证结果。",
            txHash: activeExecution?.txHash || "",
            txHashes: activeExecution?.txHash ? [activeExecution.txHash] : [],
          }
        : null;
      const latestBroadcastForStage = activeBroadcast || latestBroadcast;
      const latestSubmission = latestBroadcastForStage?.txHash ? latestBroadcastSubmissionForRoute(rawRoute) : null;
      if (latestSubmission?.txHash && latestSubmission.quote) {
        const latestQuote = latestSubmission.quote || {};
        const latestAnalysis = latestQuote.quote_analysis || latestQuote.analysis || {};
        const latestEvidence = latestQuote.live_onchain_evidence || latestQuote.evidence || {};
        analysis = {...analysis, ...latestAnalysis};
        evidence = {
          ...evidence,
          ...latestEvidence,
          costModel: {
            ...(evidence.costModel || {}),
            ...(latestEvidence.costModel || latestQuote.costModel || {}),
          },
        };
      }
      const reviewStage = normalizedReviewStage(route, latestBroadcastForStage, activeExecution);
      const execPending = reviewStage === "execution_pending";
      const execFailed = reviewStage === "execution_failed";
      const preflightFailed = reviewStage === "preflight_failed";
      const statusLower = String(evidence.status || "").toLowerCase();
      const staticReview = routeNeedsLiveReview(route) || statusLower === "static_reference_pending_live_review" || String(evidence.executionTrust || "").toLowerCase() === "static_reference_pending_live_review";
      const negativeEvidence = terminalEvidenceStatus(evidence.status);
      const rawBestRouteAmount = routeBestAmountPick(rawRoute, evidence, analysis);
      const rawBestAmountMissing = bestAmountMissingOf(rawRoute, evidence, analysis);
      const evStatus = reviewStage === "execution" || execPending || execFailed
        ? executionStageResultStatus({failed: execFailed, pending: execPending})
        : reviewStage === "preflight" || preflightFailed
          ? preflightStageResultStatus({pending: pendingReview, terminalStatus: preflightFailed ? "preflight_failed" : negativeEvidence, evidenceStatus: evidence.status})
          : estimateStageResultStatus({pending: pendingReview, terminalStatus: negativeEvidence, evidenceStatus: evidence.status});
      const recommendationText = latestBroadcastForStage?.text || (execFailed
          ? (route.__broadcastError ? `链上执行失败/未提交：${route.__broadcastError}` : "链上执行失败/未提交；请检查网络、RPC 或开关后重试。")
          : execPending
            ? "链上执行正在提交；请稍候查看本次链上结果。"
          : reviewStage === "execution"
            ? "链上执行已提交；请到提交记录或区块浏览器确认交易哈希与链上验证结果"
          : staticReview && reviewStage === "estimate"
            ? "当前为静态参考，尚未通过链上逐 hop 复核；不能作为已确认最佳量，不建议直接链上执行。"
          : statusLower === "route_unprofitable"
            ? "链上报价已验证，但净收益未转正；禁止链上执行"
          : statusLower === "slippage_limit_exceeded"
            ? "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认；禁止链上执行"
          : negativeEvidence
            ? terminalFailureAdvice(evidence.status)
          : reviewStage === "preflight"
            ? (rawBestAmountMissing
                ? "链上复核未通过：最佳量未计算成功，不能作为已确认最佳量；请重新链上复核并完成最佳量计算后再执行。"
                : "链上复核完成；尚未执行链上交易。当前最佳交易量已通过，若要执行请点击链上执行。")
            : preflightFailed
              ? (route.__broadcastError && String(route.__broadcastError) !== "链上复核失败"
                  ? route.__broadcastError
                  : "链上复核失败：RPC 或链上报价未返回有效结果，请稍后重试或检查 RPC 配置。")
            : pendingReview
              ? "链下估算完成：默认初始量已生成；链上复核可继续确认当前最佳量"
                : liveSummary
                    ? (analysis.bestAmountConfirmed === true
                        ? "链下估算完成：当前初始量已生成；最佳量与可执行性需点击链上复核确认。"
                        : "链下估算完成：当前金额仅作为链上合约初始量，最佳量需由链上复核确认。")
                    : recommendationAction(recommendation.action || "链上报价未完成"));
        const evidenceFirst = (...values) => {
          for (const value of values) {
            if (value !== null && value !== undefined && value !== "" && String(value).trim() !== "") return value;
          }
          return null;
        };
        const costsForMetric = evidence.costModel && typeof evidence.costModel === "object" ? evidence.costModel : {};
        const stepsForMetric = (Array.isArray(evidence.hops) && evidence.hops.length)
          ? evidence.hops
          : (Array.isArray(analysis.steps) ? analysis.steps.filter(Boolean) : []);
        const firstMetricStep = stepsForMetric[0] || null;
        const lastMetricStep = stepsForMetric[stepsForMetric.length - 1] || null;
        const firstMetricAmount = Number(firstMetricStep?.inputAmount ?? firstMetricStep?.amountIn ?? firstMetricStep?.referenceInputAmount);
        const lastMetricAmount = Number(lastMetricStep?.outputAmount ?? lastMetricStep?.referenceOutputAmount ?? lastMetricStep?.amountOut);
        const derivedProfitAmount = Number.isFinite(firstMetricAmount) && Number.isFinite(lastMetricAmount)
          ? (lastMetricAmount - firstMetricAmount)
          : null;
        const referenceNetProfitRaw = evidenceFirst(
          analysis.referenceNetProfitAmount,
          costsForMetric.netProfitAmount,
          costsForMetric.submissionNetProfitAmount,
          derivedProfitAmount
        );
        const referenceNetProfitAmount = humanTokenValue(referenceNetProfitRaw);
        const referenceNetProfitMetric = referenceNetProfitAmount != null
          ? amount(referenceNetProfitAmount, evidenceFirst(analysis.referenceInputSymbol, costsForMetric.borrowInputSymbol) || "")
          : "-";
        const referenceNetReturnBps = evidenceFirst(
          analysis.referenceNetReturnBps,
          costsForMetric.netProfitAmountBps,
          costsForMetric.netProfitBps
        );
        const referenceNetProfitBpsMetric = referenceNetReturnBps != null
          ? `${numeric(referenceNetReturnBps, 2)} bps`
          : "-";
        const netProfitUsd = evidenceFirst(analysis.netProfitUsd, costsForMetric.netProfitUsd);
        const netProfitAmount = humanTokenValue(evidenceFirst(
          analysis.referenceNetProfitAmount,
          costsForMetric.netProfitAmount,
          costsForMetric.submissionNetProfitAmount,
          derivedProfitAmount
        ));
        const netProfit = netProfitUsd != null
          ? money(netProfitUsd)
          : (netProfitAmount != null ? amount(netProfitAmount, evidenceFirst(analysis.referenceInputSymbol, costsForMetric.borrowInputSymbol) || "") : "-");
        const routeAmount = autoRouteAmount(route);
        const routeAmountSymbol = autoRouteAmountSymbol(route);
        const offchainProbe = offchainProbeAmount(rawRoute, evidence);
        const estimatedInitialAmount = offchainProbe ?? analysis.requestedAmount ?? analysis.initialProbeAmount ?? routeAmount ?? 1;
        const bestRouteAmount = reviewStage === "estimate"
          ? estimatedInitialAmount
          : rawBestRouteAmount;
        const actionRequested = Number(route.__requestedAmount);
        const hasActionRequest = Number.isFinite(actionRequested) && actionRequested > 0 && reviewStage !== "estimate";
        const requestedAmountValue = hasActionRequest
          ? actionRequested
          : pendingReview ? (analysis.referenceInputAmount ?? routeAmount) : (evidence.costModel?.requestedBorrowInputAmount ?? analysis.requestedAmount ?? analysis.amountOptimizedFrom ?? analysis.referenceInputAmount ?? offchainProbe ?? bestRouteAmount);
        const initialProbeAmount = hasActionRequest
          ? actionRequested
          : pendingReview
            ? estimatedInitialAmount
            : (evidence.costModel?.requestedBorrowInputAmount ?? analysis.requestedAmount ?? analysis.amountOptimizedFrom ?? offchainProbe ?? route.sizeAnalysis?.recommendedBorrowInputAmount ?? route.sizeAnalysis?.tokenAmountSteps?.[0] ?? bestRouteAmount);
        const amountScaleHint = Number.isFinite(Number(bestRouteAmount))
          && Number(bestRouteAmount) > 0
          && Number.isFinite(Number(initialProbeAmount))
          && Number(initialProbeAmount) > 0
          ? (Math.abs(Number(bestRouteAmount) - Number(initialProbeAmount)) / Number(initialProbeAmount) > 0.01
              ? bestAmountHint({
                  evidence,
                  analysis,
                  amount: numeric(bestRouteAmount, 8),
                  symbol: display(routeAmountSymbol),
                  initialAmount: numeric(initialProbeAmount, 8),
                  initialSymbol: display(routeAmountSymbol),
                  executionCount: latestBroadcastForStage?.txHashes?.length || 0,
                  stage: reviewStage,
                })
              : "")
          : "";
        const backendAmountHint = analysis.amountOptimizationHint || route.quoteAnalysis?.amountOptimizationHint;
        const bestAmountLabel = bestAmountDisplayLabel({evidence, analysis, stage: reviewStage});
        const requestedNumber = Number(requestedAmountValue);
        const probeNumber = Number(initialProbeAmount);
        const initialAmountsSame = Number.isFinite(requestedNumber)
          && Number.isFinite(probeNumber)
          && probeNumber > 0
          && Math.abs(requestedNumber - probeNumber) <= Math.max(Math.abs(requestedNumber), Math.abs(probeNumber)) * 1e-9;
        const bestAmountMetric = reviewStage === "estimate"
          ? (Number.isFinite(Number(bestRouteAmount))
              ? `${numeric(bestRouteAmount, 8)} ${display(routeAmountSymbol)}`
              : "未计算")
          : (rawBestAmountMissing
              ? "未计算"
              : `${numeric(bestRouteAmount, 8)} ${display(routeAmountSymbol)}`);
        const initialProbeMetric = numeric(initialProbeAmount ?? estimatedInitialAmount, 8);
        const routePoolCount = routePoolsOf(route).length;
        const routeLiquidPoolCount = routePoolsOf(route).filter(poolExplicitlyUsable).length;
        const routeUnusablePoolCount = routePoolsOf(route).filter(poolExplicitlyUnusable).length;
        const routeEvidenceMissingPoolCount = routePoolsOf(route).filter(poolLiquidityEvidenceMissing).length;
        const metrics = [
          `方向=${actualDirection}`,
          `路径池=${routePoolCount} / 有流动性=${routeLiquidPoolCount} / 无流动性=${routeUnusablePoolCount} / 未标注=${routeEvidenceMissingPoolCount}`,
          `${bestAmountLabel}=${bestAmountMetric}`,
          `报价=${reviewStage === "execution" || execPending || execFailed
            ? executionStageQuoteStatus({failed: execFailed, pending: execPending})
            : reviewStage === "preflight" || preflightFailed
              ? preflightStageQuoteStatus({pending: pendingReview, terminalStatus: preflightFailed ? "preflight_failed" : negativeEvidence, evidenceStatus: evidence.status})
              : estimateStageQuoteStatus({pending: pendingReview, terminalStatus: negativeEvidence, evidenceStatus: evidence.status})}`,
          `状态=${evStatus}`,
          `区块=${display(block)}`,
          `依据=${trust}`,
          `盈亏=${netProfit}`,
          `参考净利=${referenceNetProfitMetric}`,
          `参考净利 BPS=${referenceNetProfitBpsMetric}`,
          [`${reviewStage === "preflight" ? "初始测试量" : "初始探测"}=${initialProbeMetric}${initialProbeMetric !== "-" ? ` ${display(routeAmountSymbol)}` : ""}`],
          `初始探测依据=${display(evidenceProbeReasonText(evidence.offchainInitialProbeReason, evidence))}`,
          ...(amountScaleHint ? [amountScaleHint] : []),
          ...(backendAmountHint ? [`优化提示=${display(backendAmountHint)}`] : []),
        ];
        return `<div class="route-in-group"><div class="route-in-summary"><div class="path-text">${esc(actualDirection)}</div><div class="route-actions route-test"><button class="copy-btn" data-copy-route="${index}" type="button">复制</button>${routeActionHtml(route, index)}</div></div><div class="route-metric-line">${metrics.map(esc).join(" | ")}</div>${hops ? `<div class="route-hop-line">${esc(hops)}</div>` : ""}<div class="recommendation" data-route-recommendation="${index}"><strong>建议：${esc(recommendationText)}</strong></div><div data-route-test-result="${index}" class="route-test-result"></div></div>`;
      } catch (error) {
        const name = rawRoute?.symbolPair || (rawRoute?.routeSymbols || []).join(" -> ") || "路线";
        return `<div class="route-in-group">${esc(name)}（渲染跳过）</div>`;
      }
    }
    const routePoolsOf = route => {
      const pools = Array.isArray(route?.routePools)
        ? route.routePools
        : Array.isArray(route?.quoteInputs?.candidatePools)
          ? route.quoteInputs.candidatePools
          : [];
      return pools.filter(pool => pool && (pool.pool || pool.poolId || pool.pairSymbol || pool.symbolPair));
    };
    const poolDirectoryId = pool => String(pool?.pool || pool?.poolId || pool?.pairAddress || pool?.pairSymbol || pool?.symbolPair || `${pool?.token0 || ""}${pool?.token1 || ""}`).toLowerCase();
    const routeGroupKey = route => {
      const start = String(route?.routeSymbols?.[0] || route?.overviewRoute?.anchorBorrowSymbol || route?.borrowSymbols?.[0] || "token").toUpperCase();
      return start;
    };
    const renderPoolDirectories = routes => {
      const groups = new Map();
      routes.forEach((route, index) => {
        if (!route) return;
        const key = routeGroupKey(route);
        if (!groups.has(key)) {
          groups.set(key, {key, routes: [], pools: [], start: key});
        }
        groups.get(key).routes.push({route, index});
      });
      const orderedGroups = Array.from(groups.values()).sort((a, b) => {
        return String(a.start).localeCompare(String(b.start));
      });
      orderedGroups.forEach(group => {
        group.routes.sort((a, b) => {
          const keyA = routeKey(a.route);
          const keyB = routeKey(b.route);
          return String(keyA).localeCompare(String(keyB)) || a.index - b.index;
        });
      });
      const html = orderedGroups.map(group => {
        const seenPools = new Set();
        const groupPools = [];
        group.routes.forEach(({route}) => {
          routePoolsOf(route).forEach(pool => {
            const id = poolDirectoryId(pool);
            if (id && seenPools.has(id)) return;
            if (id) seenPools.add(id);
            groupPools.push(pool);
          });
        });
        const poolRows = groupPools.map(pool => {
          const label = pool.symbolPair || pool.pairSymbol || pool.routeText || pool.pool || "-";
          const dynamic = pool.dynamicFee || String(pool.feeLabel || "").toLowerCase() === "dynamic";
          const special = pool.specialFee || String(pool.feeLabel || "").toLowerCase() === "special";
          const fee = pool.fee == null && !dynamic && !special ? "" : `fee ${dynamic ? "dynamic" : special ? "special" : numeric(pool.fee, 0)}`;
          const depth = pool.depthBand?.label || pool.depthStatus || pool.status || "";
          const liquidity = pool.liquidityIndex == null ? "" : `L ${numeric(pool.liquidityIndex, 6)}`;
          return `<div class="pool-chip">${esc(label)}${fee ? `<small>${esc(fee)}</small>` : ""}${depth ? `<small>${esc(depth)}</small>` : ""}${liquidity ? `<small>${esc(liquidity)}</small>` : ""}</div>`;
        }).join("");
        const routeHtml = group.routes.map(({route, index}) => routeSummaryHtml(route, index)).join("");
        return `<section class="pool-group"><header><h2>${esc(group.start)} 可借贷资金池目录</h2><span>${esc(group.routes.length)} 条闭环路径</span><button class="copy-btn" type="button" data-closed-loop-refresh="${esc(group.start)}">定点刷新</button></header><div class="pool-list">${poolRows || `<span class="muted">未携带池明细</span>`}</div>${routeHtml}</section>`;
      }).join("");
      if (html) return html;
      const candidateLiquid = Number(routeLiquidityContext?.candidateLiquidPoolCount ?? 0);
      const closedLoopLiquid = Number(routeLiquidityContext?.closedLoopLiquidPoolCount ?? 0);
      if (candidateLiquid > 0 && closedLoopLiquid === 0) {
        return `<div class="empty">候选池中存在 ${esc(candidateLiquid)} 个有流动性池，但当前没有由这些池组成的完整 Y-X-Z-Y 闭环，因此报价页无可交易闭环。请先在市场观察/总览补充中间桥池或扩大观察锚点集合。</div>`;
      }
      return '<div class="empty">暂无同时具备可用流动性且非特殊费率的闭环路由。请先在候选池/总览确认中间桥池的流动性标记。</div>';
    };
