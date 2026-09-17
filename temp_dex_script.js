
    const $ = id => document.getElementById(id);
    const APP_VERSION = "2026.09.11.07";
    const esc = value => String(value ?? "-").replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
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
    const amount = (value, symbol, digits = 8) => {
      if (value === null || value === undefined || value === "") return "-";
      const parsed = Number(value);
      return Number.isFinite(parsed) ? `${parsed.toLocaleString("en-US", {maximumFractionDigits: digits})} ${symbol || ""}`.trim() : "-";
    };
    const labelMap = {
      live_verified: "链上价格已验证",
      not_live_verified: "尚未完成链上复核",
      rpc_verification_failed: "链上复核失败：RPC 不可用",
      adapter_required: "链上复核失败：缺少报价适配器",
      missing_trigger_block: "链上复核未执行：缺少触发区块",
      missing_effective_pool: "链上复核：缺少有效池，无法报价",
      live_quote_failed: "链上报价失败",
      live_quote_reference_only: "仅参考报价",
      route_unprofitable: "路线不盈利",
      slippage_limit_exceeded: "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
      not_enough_liquidity: "流动性不足",
      pool_insufficient_for_profitable_execution: "池深度不足以盈利执行",
      pool_not_initialized: "V4 池未初始化",
      live_evidence_deferred: "链上验证已延后",
      not_reviewed_this_scan: "本轮未复核",
      requires_live_quoter: "需要链上报价器",
      sdk_adapter_required: "需要 V4 SDK 适配器",
      uniswap_v4_sdk_exact_input_single: "V4 SDK 单步精确输入",
      uniswap_v4_sdk_exact_input_route: "V4 SDK 路线精确输入",
      quote_request_ready: "报价请求已就绪",
    };
    const label = value => labelMap[value] || value || "-";
    const executionTrustText = value => ({
      static_reference_only: "静态参考",
      static_reference: "静态参考",
      live_quoter_verified_readonly: "实时报价器验证（只读）",
      static_live_quoter_estimate: "静态+实时报价器估算",
      live_quote_reference_only: "参考报价",
      live_verified: "链上已验证",
      live: "链上已验证",
      not_enough_liquidity: "链上复核：流动性不足",
      pool_not_initialized: "链上复核：池未初始化",
      live_quote_failed: "链上报价失败",
      rpc_verification_failed: "链上复核失败：RPC 不可用",
      deferred: "已延迟",
    }[String(value || "")] || value || "-");
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
        "on-chain step-by-step reverify failed; blocking transaction preflight": "链上逐步复核失败；正在阻止交易预检",
        "on-chain reverify complete; current state does not satisfy transaction submission conditions": "链上复核完成；当前状态不满足交易提交条件",
        "On-chain verification complete; preflight still required": "链上验证完成；仍需交易预检",
        "On-chain verification passed, but execution gates are blocked": "链上验证通过，但执行门禁未放行",
        "Pool depth insufficient for profitable execution": "池深度不足以盈利执行",
        "Per-hop price impact exceeds 10 bps": "单跳价格影响超过 10 bps",
        "Route is unprofitable": "路线不盈利",
        "On-chain quote failed": "链上报价失败",
        "Reference quote only": "仅参考报价",
        "On-chain verification deferred": "链上验证已延后",
      };
      const extra = {
        "at least one hop price impact cannot be confirmed or exceeds 10 bps": "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
        "on-chain computed profit is not positive after flash loan fees; cannot enter preflight": "链上已验证，但扣除闪电贷费用后尚未转正；不能进入预检",
        "on-chain verification did not cover all hops; cannot enter preflight": "链上验证未覆盖全部跳数；不能进入预检",
        "stable-coin route on-chain net profit is below the $6 gate; cannot enter preflight": "稳定币路线链上净收益低于 6U 门槛；不能进入预检",
        "final output is not stable coin or lacks reliable USD valuation; cannot enter automatic transaction preflight": "最终输出不是稳定币或缺少可信 USD 估值；不能进入自动交易预检",
        "current pool depth cannot support a borrow size that passes 10 bps per-hop limit and profit after flash loan fee": "当前池深度无法支持同时通过 10 bps 滑点限制并扣除闪电贷费用后仍有盈利的借贷量",
        "on-chain RPC verification failed": "链上 RPC 复核失败",
        "Robinhood Testnet RPC unavailable": "Robinhood 测试网 RPC 不可用",
        "adapter required": "缺少链上报价适配器",
        "missing trigger block context; later latest price cannot replace the trigger-time price.": "缺少触发区块上下文；后续最新价格不能替代触发时点价格",
        "V4 pool is not initialized or cannot be quoted; route cannot enter the on-chain execution gate": "V4 池未初始化或无法报价；该路线不能进入链上执行门禁",
        "historic block state unavailable; deferred on-chain reverify": "历史区块状态不可用；链上复核已延后",
        "on-chain Quoter already reverified as profitable; still need transaction calldata, slippage protection, flash loan return constraints, and fork/static-call simulation before submitting": "链上 Quoter 已复核为正收益；提交前仍需交易 calldata、滑点保护、闪电贷返还约束以及 fork/static-call 模拟",
      };
      return map[text] || extra[text] || text || "-";
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
        .flatMap(segment => segment.split(/\s*:\s*/).flatMap(chunk => chunk.split(/\s*->\s*/)))
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
      const blockerDirectMap = {
        "at least one hop price impact cannot be confirmed or exceeds 10 bps": "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
        "on-chain computed profit is not positive after flash loan fees; cannot enter preflight": "链上已验证，但扣除闪电贷费用后尚未转正；不能进入预检",
        "on-chain verification did not cover all hops; cannot enter preflight": "链上验证未覆盖全部跳数；不能进入预检",
        "stable-coin route on-chain net profit is below the $6 gate; cannot enter preflight": "稳定币路线链上净收益低于 6U 门槛；不能进入预检",
        "final output is not stable coin or lacks reliable USD valuation; cannot enter automatic transaction preflight": "最终输出不是稳定币或缺少可信 USD 估值；不能进入自动交易预检",
        "current pool depth cannot support a borrow size that passes 10 bps per-hop limit and profit after flash loan fee": "当前池深度无法支持同时通过 10 bps 滑点限制并扣除闪电贷费用后仍有盈利的借贷量",
        "V4 pool is not initialized or cannot be quoted; route cannot enter the on-chain execution gate": "V4 池未初始化或无法报价；该路线不能进入链上执行门禁",
        "historic block state unavailable; deferred on-chain reverify": "历史区块状态不可用；链上复核已延后",
        "on-chain Quoter already reverified as profitable; still need transaction calldata, slippage protection, flash loan return constraints, and fork/static-call simulation before submitting": "链上 Quoter 已复核为正收益；提交前仍需交易 calldata、滑点保护、闪电贷返还约束以及 fork/static-call 模拟",
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
    const routeEvidence = route => route?.live_onchain_evidence || route?.quoteAnalysis?.live_onchain_evidence || route?.quote?.live_onchain_evidence || {};
    const routeKey = route => {
      const symbols = Array.isArray(route?.routeSymbols) ? route.routeSymbols : [];
      return String(route?.symbolPair || (symbols.length ? symbols.join(" -> ") : "") || "").toUpperCase();
    };
    const routesToRender = (scannerRoutes, quoteRoutesPayload) => {
      const quoteSnapshot = quoteRoutesPayload?.snapshot?.payload || {};
      const quoteList = Array.isArray(quoteRoutesPayload?.routes)
        ? quoteRoutesPayload.routes
        : Array.isArray(quoteSnapshot.routes)
          ? quoteSnapshot.routes
          : [];
      const scannerList = Array.isArray(scannerRoutes) ? scannerRoutes : [];
      if (!quoteList.length) return scannerList;
      const scannerByKey = new Map(scannerList.map(route => [routeKey(route), route]));
      const quoteByKey = new Map(quoteList.map(route => [routeKey(route), route]));
      const merged = quoteList.map(route => {
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
      return merged;
    };
    const routeAnalysis = route => {
      const base = {...(route?.quoteAnalysis || {})};
      const evidence = routeEvidence(route);
      if (!Object.keys(evidence).length) return base;
      const costs = evidence.costModel || {};
      const live = Boolean(evidence.livePriceVerified || evidence.status === "live_verified");
      const chainAttempted = Boolean(evidence.status);
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
        referenceFinalOutputAmount: live ? costs.finalOutputAmount : base.referenceFinalOutputAmount,
        referenceNetProfitAmount: live ? costs.netProfitAmount : base.referenceNetProfitAmount,
        referenceNetReturnBps: live ? costs.netProfitAmountBps : base.referenceNetReturnBps,
        referenceFinalOutputSymbol: costs.finalOutputSymbol || base.referenceFinalOutputSymbol,
        referenceInputSymbol: costs.borrowInputSymbol || base.referenceInputSymbol,
        referenceReturnBasis: costs.profitBasis || base.referenceReturnBasis,
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
            action: live
              ? (evidence.readyForTransactionPreflight ? "链上验证完成；仍需交易预检" : "链上验证通过，但执行门禁未放行")
            : (base.requiresLiveQuoter && base.referenceNetProfitAmount != null ? "链上报价未完成，当前为静态参考收益" : "链上报价未完成"),
            reasons: evidence.blockers,
        } : base.recommendation,
      };
    };
    const evidenceStatus = status => ({
      live_verified: "链上验证通过",
      live_quote_failed: "链上单跳验证失败",
      live_quote_reference_only: "仅参考报价",
      route_unprofitable: "路线不盈利",
      slippage_limit_exceeded: "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
      not_enough_liquidity: "链上复核：流动性不足",
      pool_insufficient_for_profitable_execution: "池深度不足以盈利执行",
      pool_not_initialized: "V4 池未初始化",
      live_evidence_deferred: "链上验证已延后",
      not_reviewed_this_scan: "链上复核未覆盖本轮预算",
      rpc_verification_failed: "链上复核失败：RPC 不可用",
      adapter_required: "链上复核失败：缺少 V4 报价适配器",
      missing_trigger_block: "链上复核未执行：缺少触发区块上下文",
    }[String(status || "").toLowerCase()] || "链上复核状态未确认");
    const evidenceStatusEx = status => {
      const value = String(status || "").toLowerCase();
      const blocked = {
        slippage_limit_exceeded: "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
        route_unprofitable: "链上已验证，收益未转正",
        pool_insufficient_for_profitable_execution: "链上已验证，池深度不足",
        pool_not_initialized: "链上复核失败：V4 池未初始化",
        rpc_verification_failed: "链上复核失败：RPC 不可用",
        adapter_required: "链上复核失败：缺少报价适配器",
        missing_trigger_block: "链上复核未执行：缺少触发区块",
        missing_effective_pool: "链上复核：缺少有效池，无法报价",
        live_quote_failed: "链上单跳验证失败",
        live_verified: "链上验证通过",
      };
      return blocked[value] || evidenceStatus(value);
    };
    function onchainEvidenceHtml(evidence, route) {
      if (!evidence || !Object.keys(evidence).length) return "";
      const status = String(evidence.status || "").toLowerCase();
      const failed = (evidence.hops || []).filter(hop => hop?.liveQuoteError || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity", "adapter_required"].includes(String(hop?.status || "").toLowerCase()));
      const focusFailed = failed[0] || null;
      const stackTop = focusFailed?.errorStack?.[0] || evidence.errorStack?.[0] || route?.errorStack?.[0] || null;
      const blockersToShow = focusFailed ? [] : lines(evidence.blockers);
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
        <span class="muted">区块 ${esc(evidence.blockNumber || evidence.triggerBlockNumber || "-")} | 报价区块 ${esc(quoteBlockLabel(evidence.quoteBlockSource))} | 信任级别 ${esc(executionTrustText(evidence.executionTrust))}</span>
        ${focusFailed ? `<div class="evidence-error">第 ${esc(focusFailed.hop || "-")} 步 ${esc(focusFailed.fromSymbol || "-")} -> ${esc(focusFailed.toSymbol || "-")}：${focusFailedInputText}${errorStackHtml(focusFailed.errorStack, focusFailed.liveQuoteError || "报价失败")}</div>` : ""}
        ${focusFailed?.liveQuoteError ? "" : (stackTop ? `<div class="muted">模块 ${esc(stackTop.module || "")} | code=${esc(stackTop.code ?? "")} | ${esc(stackTop.message || "")}</div>` : "")}
        ${blockersToShow.map(item => `<div class="muted">${esc(blockerText(item))}${errorChainHtml(item)}</div>`).join("")}
        ${probeHtml}</div>`;
    }
    const inputSymbol = (route, analysis) => route?.routeSymbols?.[0] || analysis?.referenceInputSymbol || analysis?.inputSymbol || "";
    const routeSizeAmount = (value, route, analysis) => amount(value, inputSymbol(route, analysis));
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
    function routeHtml(route, index) {
      const evidence = routeEvidence(route);
      const analysis = routeAnalysis(route);
      const live = Boolean(analysis.livePriceVerified || analysis.priceEvidenceMode === "live_verified");
      const steps = analysis.steps || [];
      const costs = evidence.costModel || {};
      const failedHops = (Array.isArray(evidence.hops) ? evidence.hops : steps).filter(hop => hop?.liveQuoteError || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity", "adapter_required"].includes(String(hop?.status || "").toLowerCase()));
      const failedHop = failedHops[0] || null;
      const failedStep = failedHop ? steps.find(step => Number(step?.hop) === Number(failedHop.hop)) : null;
      const focusOnError = Boolean(failedHop);
      const focusSteps = steps.filter(Boolean);
      const stackTop = failedHop?.errorStack?.[0] || evidence.errorStack?.[0] || route.errorStack?.[0] || null;
      const focusErrorText = failedHop?.liveQuoteError ? blockerText(failedHop.liveQuoteError) : (stackTop?.message ? blockerText(stackTop.message) : "链上单跳报价失败");
      const recommendation = analysis.recommendation || {};
      const requests = route.quoteInputs?.quoteRequests || [];
      const pools = route.quoteInputs?.candidatePools || [];
      const displayedSteps = focusSteps.length ? focusSteps : steps.filter(Boolean);
      const isFailedStep = step => Boolean(step?.liveQuoteError) || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity", "adapter_required"].includes(String(step?.status || "").toLowerCase());
      const stepRows = displayedSteps.map(step => `<tr><td>${esc(step.hop)}</td><td>${esc(step.fromSymbol)} -> ${esc(step.toSymbol)}</td><td>${esc(step.protocol || step.dex || "-")}</td><td>${esc(label(step.quoteSource))}${step.liveQuoteError ? `<div class="evidence-error">${errorStackHtml(step.errorStack, step.liveQuoteError)}</div>` : ""}</td><td>${esc(step.pool || "-")}</td><td>${numeric(step.feeBps, 2)}</td><td>${numeric(step.inputAmount, 8)} ${esc(step.fromSymbol)}</td><td>${isFailedStep(step) ? "报价失败" : (live ? `${numeric(step.outputAmount, 8)} ${esc(step.toSymbol)}` : `${numeric(step.referenceOutputAmount, 8)} ${esc(step.toSymbol)} (ref)`)}</td><td>${isFailedStep(step) ? "-" : (live ? numeric(step.effectiveRate, 8) : numeric(step.referenceEffectiveRate, 8))}</td><td>${isFailedStep(step) ? "-" : (step.priceImpactBps == null ? "-" : `${numeric(step.priceImpactBps, 2)} / ${numeric(step.priceImpactLimitBps ?? evidence.validation?.hopPriceImpactLimitBps ?? 10, 0)} bps`)}</td></tr>`).join("");
      const finalOutputUsd = live ? costs.finalOutputUsd : analysis.referenceFinalOutputUsd;
      const finalOutputAmount = live ? costs.finalOutputAmount : analysis.referenceFinalOutputAmount;
      const finalOutputSymbol = costs.finalOutputSymbol || analysis.referenceFinalOutputSymbol || analysis.outputSymbol || inputSymbol(route, analysis);
      const grossProfitUsd = live ? costs.grossProfitUsd : analysis.referenceGrossProfitUsd;
      const grossProfitAmount = live ? costs.grossProfitAmount : analysis.referenceGrossProfitAmount;
      const tokenNetProfitAmount = live ? (costs.netProfitAmount ?? analysis.referenceNetProfitAmount) : analysis.referenceNetProfitAmount;
      const tokenNetProfitSymbol = live ? (costs.netProfitAmountSymbol || analysis.referenceInputSymbol || inputSymbol(route, analysis)) : (analysis.referenceInputSymbol || inputSymbol(route, analysis));
      const tokenNetReturnBps = live ? (costs.netProfitAmountBps ?? analysis.referenceNetReturnBps) : analysis.referenceNetReturnBps;
      const profitBasis = (live ? costs.profitBasis : analysis.referenceReturnBasis) || analysis.referenceReturnBasis;
      const tokenProfit = profitBasis === "borrow_token" && tokenNetProfitAmount != null ? `${amount(tokenNetProfitAmount, tokenNetProfitSymbol)} / ${numeric(tokenNetReturnBps, 2)} bps` : "-";
      const tokenProfitLabel = live ? "X 本位结果" : "X 本位参考";
      const netProfitDisplay = live ? (analysis.netProfitUsd != null ? money(analysis.netProfitUsd) : tokenProfit) : tokenProfit;
      const netReturnDisplay = live ? numeric(analysis.netProfitBps ?? tokenNetReturnBps, 2) : numeric(analysis.referenceNetReturnBps, 2);
      const finalOutputDisplay = finalOutputUsd != null ? money(finalOutputUsd) : amount(finalOutputAmount, finalOutputSymbol);
      const grossProfitDisplay = grossProfitUsd != null ? money(grossProfitUsd) : amount(grossProfitAmount, finalOutputSymbol);
      const initialProbeValue = route.sizeAnalysis?.recommendedBorrowInputAmount ?? route.sizeAnalysis?.tokenAmountSteps?.[0] ?? costs.requestedBorrowInputAmount ?? costs.selectedBorrowInputAmount ?? analysis.referenceInputAmount;
      const depthStatus = String(analysis.staticDepthStatus || "unavailable").toLowerCase();
      const depthKnown = live || depthStatus === "available";
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
        : `<div class="analysis"><div><small>${live ? "最终输出" : "最终输出（参考）"}</small><strong>${depthKnown ? esc(finalOutputDisplay) : "等待深度探测"}</strong></div><div><small>${live ? "毛利润" : "毛利润（参考）"}</small><strong>${depthKnown ? esc(grossProfitDisplay) : "等待深度探测"}</strong></div><div><small>${live ? "净利润" : "净利润（参考）"}</small><strong>${depthKnown ? esc(netProfitDisplay) : "-"}</strong></div><div><small>${live ? "净回报 bps" : "净回报 bps（参考）"}</small><strong>${depthKnown ? netReturnDisplay : "-"}</strong></div><div><small>${tokenProfitLabel}</small><strong>${depthKnown ? esc(tokenProfit) : "-"}</strong></div><div><small>初始探测金额</small><strong>${routeSizeAmount(initialProbeValue, route, analysis)}</strong></div><div><small>最大可执行金额</small><strong>${routeSizeAmount(route.sizeAnalysis?.maxExecutableAmountRaw, route, analysis)}</strong></div></div>`;
      const depthLabel = live ? "链上实时深度已验证" : failedIsLiquidity ? "流动性不足，已停止继续估算" : failedIsDeferred ? "链上复核延后，不判定为路线失败" : focusOnError ? "已停止在链上报价" : depthStatus === "available" ? "离线深度可用" : depthStatus === "exceeds_10_bps" ? "请降低金额：滑点超过 10 bps" : "离线深度不可用";
      const recClass = ["pool_not_initialized", "not_enough_liquidity", "adapter_required", "live_quote_failed", "pool_insufficient_for_profitable_execution", "route_unprofitable"].includes(String(evidence.status || "").toLowerCase()) ? "bad" : (["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(evidence.status || "").toLowerCase()) ? "warn" : (live ? "good" : "warn"));
      const isDeferred = ["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(evidence.status || "").toLowerCase());
      const actionText = focusOnError
        ? failedAction
        : live && ["route_unprofitable", "pool_insufficient_for_profitable_execution"].includes(String(evidence.status || "").toLowerCase())
          ? "链上验证通过，但执行门禁未放行"
          : isDeferred && (steps.length || Object.keys(evidence).length)
            ? "本轮未集中复核；可使用链上估算"
            : recommendationAction(recommendation.action || label(route.quoteStatus));
      const referenceBadge = !live && !analysis.chainReviewed && analysis.requiresLiveQuoter ? `<span class="badge warn">静态候选（待链上报价）</span>` : "";
      const deferredReviewBadge = isDeferred ? `<span class="badge warn">可点击链上估算立即复核</span>` : "";
      const priceReviewBadge = live ? "链上价格已验证" : (analysis.chainReviewed ? `链上复核已返回：${esc(evidenceStatusEx(evidence.status || ""))}` : "链上价格复核未完成");
      const defaultTestAmount = initialProbeValue != null && Number.isFinite(Number(initialProbeValue)) ? Number(initialProbeValue) : 1;
      const routeTestAmountSymbol = analysis.referenceInputSymbol || inputSymbol(route, analysis) || (route.routeSymbols || [])[0] || "";
      return `<article class="route"><div class="route-head"><div><h2>${esc(route.symbolPair || route.routeSymbols?.join(" -> ") || "路线")}</h2><div class="badges">
        <span class="badge ${recClass}">${esc(actionText)}</span>
        ${referenceBadge}
        ${deferredReviewBadge}
        <span class="badge">路径 ${esc((route.routeSymbols || []).join(" -> "))}</span>
        <span class="badge ${live ? "good" : "warn"}">${priceReviewBadge}</span>
        <span class="badge ${Object.keys(evidence).length && evidence.status !== "live_verified" ? "bad" : "good"}">${esc(evidenceStatusEx(evidence.status || ""))}</span>
        <span class="badge ${live || depthStatus === "available" ? "good" : "warn"}">${esc(depthLabel)}</span>
      </div></div><button class="copy-btn" type="button" data-copy-route="${index}">复制本组</button></div>
        <div class="route-test"><label for="route-test-amount-${index}">链上金额</label><input id="route-test-amount-${index}" type="number" min="0" step="any" inputmode="decimal" value="${esc(defaultTestAmount)}" data-route-test-amount><button type="button" data-route-live-estimate="${index}">链上估算</button><button type="button" data-route-static-call="${index}">只读链上预检</button><button type="button" data-route-broadcast="${index}">广播测试</button><span class="muted">${routeTestAmountSymbol}</span><span class="route-test-result" data-route-test-result="${index}"></span></div>
        ${onchainEvidenceHtml(evidence, route)}
      ${analysisHtml}
      <div class="table-wrap"><table><thead><tr><th>跳</th><th>兑换</th><th>协议</th><th>报价来源</th><th>池</th><th>手续费 bps</th><th>输入</th><th>输出</th><th>汇率</th><th>价格影响 / 上限</th></tr></thead><tbody>${stepRows || '<tr><td colspan="10">暂无单跳报价数据</td></tr>'}</tbody></table></div>
      <div class="recommendation"><strong>建议：${esc(focusOnError ? actionText : recommendationAction(recommendation.action || "-"))}</strong><ol>${recommendationRows.map(item => `<li>${esc(blockerText(item))}</li>`).join("")}</ol></div>
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
    function routeSummaryHtml(rawRoute, index) {
      try {
        const route = normalizeRouteForRender(rawRoute);
        const evidence = routeEvidence(route);
        const analysis = routeAnalysis(route);
        const display = value => value === null || value === undefined || value === "" ? "-" : String(value);
        const path = route.symbolPair || (route.routeSymbols || []).join(" -> ") || "-";
        const status = route.quoteStatus || evidence.status || analysis.onchainEvidenceStatus || "quote_required";
        const evStatus = evidenceStatusEx(evidence.status);
        const trust = executionTrustText(evidence.executionTrust || analysis.executionTrust || "-");
        const block = evidence.blockNumber || evidence.triggerBlockNumber || "-";
        const hops = (Array.isArray(evidence.hops) ? evidence.hops : analysis.steps || []).filter(Boolean).map(step => `hop ${step.hop || "?"}: ${display(step.fromSymbol)} -> ${display(step.toSymbol)}`).join(" | ");
        const netProfit = analysis.netProfitUsd != null
          ? money(analysis.netProfitUsd)
          : (analysis.referenceNetProfitAmount != null ? amount(analysis.referenceNetProfitAmount, analysis.referenceInputSymbol || "") : "-");
        const metrics = [
          `quote=${label(status)}`,
          `evidence=${evStatus}`,
          `block=${display(block)}`,
          `trust=${trust}`,
          `net=${netProfit}`,
        ];
        return `<div class="route-in-group"><div class="route-in-summary"><div class="path-text">${esc(path)}</div><div class="route-actions route-test"><input data-route-test-amount value="1" inputmode="numeric"><button class="copy-btn" data-copy-route="${index}" type="button">复制</button><button data-route-live-estimate="${index}" type="button">链上估算</button><button data-route-static-call="${index}" type="button">预检</button><button data-route-broadcast="${index}" type="button">广播</button></div></div><div class="route-metric-line">${metrics.map(esc).join(" | ")}</div>${hops ? `<div class="route-hop-line">${esc(hops)}</div>` : ""}<div data-route-test-result="${index}" class="route-test-result"></div></div>`;
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
      const pools = routePoolsOf(route);
      const poolIds = pools.map(poolDirectoryId).filter(Boolean).sort().join("|");
      const start = String(route?.routeSymbols?.[0] || route?.overviewRoute?.anchorBorrowSymbol || route?.borrowSymbols?.[0] || "token").toUpperCase();
      return `${start}::${poolIds || "static"}`;
    };
    const renderPoolDirectories = routes => {
      const groups = new Map();
      routes.forEach((route, index) => {
        if (!route) return;
        const key = routeGroupKey(route);
        if (!groups.has(key)) {
          groups.set(key, {key, routes: [], pools: routePoolsOf(route), start: String(route?.routeSymbols?.[0] || route?.overviewRoute?.anchorBorrowSymbol || route?.borrowSymbols?.[0] || "token").toUpperCase()});
        }
        groups.get(key).routes.push({route, index});
      });
      const html = Array.from(groups.values()).map(group => {
        const poolRows = group.pools.map(pool => {
          const label = pool.symbolPair || pool.pairSymbol || pool.routeText || pool.pool || "-";
          const fee = pool.fee == null ? "" : `fee ${numeric(pool.fee, 0)}`;
          const depth = pool.depthStatus || pool.status || "";
          return `<div class="pool-chip">${esc(label)}${fee ? `<small>${esc(fee)}</small>` : ""}${depth ? `<small>${esc(depth)}</small>` : ""}</div>`;
        }).join("");
        const routeHtml = group.routes.map(({route, index}) => routeSummaryHtml(route, index)).join("");
        return `<section class="pool-group"><header><h2>${esc(group.start)} 资金池目录</h2><span>${esc(group.routes.length)} 条闭环路径</span></header><div class="pool-list">${poolRows || `<span class="muted">未携带池明细</span>`}</div>${routeHtml}</section>`;
      }).join("");
      return html || '<div class="empty">暂无可供报价复核的路由。</div>';
    };
    const refreshIntervalKey = "dex-quotes-refresh-interval-seconds";
    const scanIntervalMs = 3000;
    const defaultRefreshIntervalSeconds = 180;
    let refreshTimer = null;
    let refreshInFlight = false;
    let scanNowInFlight = false;
    let latestRoutes = [];
    let snapshotRefreshing = false;
    let selectionGeneration = 0;
    let activeSelectionSignature = "";
    const selectionSignature = () => {
      const query = window.ChainSelector?.currentQuery ? window.ChainSelector.currentQuery() : new URLSearchParams(window.location.search);
      return ["chain_id", "market_id", "protocols", "protocol_id", "dex_strategy"].map(key => query.get(key) || "").join("|").toLowerCase();
    };
    const beginSelectionRefresh = message => {
      selectionGeneration += 1; activeSelectionSignature = selectionSignature(); latestRoutes = [];
      $("routeList").innerHTML = `<div class="empty">${esc(message)}</div>`;
      ["routeCount", "completeCount", "readyCount"].forEach(id => $(id).textContent = "0");
    };
    const apiPath = path => {
      const scoped = window.ChainSelector?.withSelection ? window.ChainSelector.withSelection(path) : path;
      const url = new URL(scoped, window.location.origin);
      url.searchParams.set("v", APP_VERSION);
      return url.pathname + url.search;
    };
    async function fetchJson(path) {
      const response = await fetch(apiPath(path), {cache: "no-cache", headers: {Accept: "application/json"}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
      return data;
    }
    async function fetchJsonWithTimeout(path, timeoutMs = 12000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(apiPath(path), {cache: "no-cache", signal: controller.signal, headers: {Accept: "application/json"}});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
        return data;
      } catch (error) {
        if (error && error.name === "AbortError") throw new Error("请求超时，已停止等待数据");
        throw error;
      } finally {
        clearTimeout(timer);
      }
    }
    function renderScannerResponse(response, quoteRoutesPayload) {
      const responseChainId = response?.chain_id ?? response?.snapshot?.chain_id ?? "";
      const activeChainId = new URLSearchParams(window.location.search).get("chain_id") || "";
      if (responseChainId && activeChainId && String(responseChainId) !== activeChainId) return;
      const scanner = response.scanner || {}; const snapshot = response.snapshot || {}; const data = snapshot.payload || {}; const summary = data.summary || snapshot.summary || {};
      $("routeCount").textContent = summary.routeCount ?? summary.route_count ?? 0;
      $("completeCount").textContent = summary.completeStaticQuoteCount ?? summary.complete_static_quote_count ?? 0;
      $("readyCount").textContent = summary.executableCandidateCount ?? summary.executable_candidate_count ?? 0;
      $("routeSourceMeta").textContent = `${summary.pathSource || "market_observation"} | 观察路线 ${summary.marketObservationRouteCount ?? 0}`;
      const reviewed = summary.onchainEvidenceReviewedRouteCount ?? 0;
      const deferred = summary.onchainEvidenceDeferredRouteCount ?? summary.liveQuoterRequiredCount ?? 0;
      const failed = summary.onchainEvidenceFailedRouteCount ?? 0;
      $("probeMeta").textContent = `探测 ${numeric(summary.probeNotionalUsd ?? 1000, 2)}U | 已链上复核 ${reviewed} | 待处理 ${deferred}`;
      $("profitMeta").textContent = `预检 ${summary.chainSubmissionCandidateCount ?? 0} | 报价失败 ${failed} | 记录 ${snapshot.recording?.source === "disabled" ? "关闭" : "开启"}`;
      const uniswapx = data.dexSettlement?.uniswapx || {};
      $("settlement").textContent = uniswapx.supported ? "UniswapX 可用" : "优先 AMM";
      $("settlementMeta").textContent = uniswapx.supported ? "意图报价通道已开启" : "当前链未启用 UniswapX 结算";
      const scannerRoutes = Array.isArray(data.routes) ? data.routes : [];
      latestRoutes = routesToRender(scannerRoutes, quoteRoutesPayload);
      if (latestRoutes.length > scannerRoutes.length) {
        const quoteSummary = (quoteRoutesPayload && (quoteRoutesPayload.summary || quoteRoutesPayload.snapshot?.payload?.summary)) || {};
        $("routeCount").textContent = latestRoutes.length;
        $("routeSourceMeta").textContent = `${quoteSummary.pathSource || summary.pathSource || "market_observation"} | 观察路线 ${quoteSummary.marketObservationRouteCount ?? summary.marketObservationRouteCount ?? 0}`;
      }
    $("routeList").innerHTML = renderPoolDirectories(latestRoutes);
      const duration = scanner.last_duration_ms == null ? "-" : `${scanner.last_duration_ms} ms`;
      $("message").textContent = `${scanner.running ? "扫描器运行中" : "扫描器已停止"} | ${duration} | 扫描次数 ${scanner.scan_count ?? 0}${snapshot.error ? ` | ${snapshot.error}` : ""}`;
    }
    function renderClosedLoopGroups(payload) {
      const groups = Array.isArray(payload?.groups) ? payload.groups : [];
      if (!groups.length) {
        $("groupList").innerHTML = "";
        return;
      }
      const groupHtml = groups.map(group => {
        const token = group.flashLoanToken || group.startToken || {};
        const address = token.address ? `<small>${esc(token.address)}</small>` : "";
        const borrowable = group.borrowable ? "" : " <small>(not borrowable)</small>";
        return `<div class="group-card"><h3>${esc(token.symbol || "-")}${borrowable}</h3>${address}<strong>${esc(group.routeCount || 0)} routes / ${esc(group.poolDependencyCount || 0)} pools</strong></div>`;
      }).join("");
      $("groupList").innerHTML = groupHtml;
    }
    async function refreshClosedLoopGroups() {
      try {
        const payload = await fetchJsonWithTimeout("/api/dex/closed-loop-groups", 5000);
        renderClosedLoopGroups(payload);
      } catch (_) {}
    }
    async function refreshSnapshot() {
      if (snapshotRefreshing || refreshInFlight) return; snapshotRefreshing = true; refreshInFlight = true;
      const generation = selectionGeneration; const signature = activeSelectionSignature;
      try {
        refreshClosedLoopGroups();
        const response = await fetchJsonWithTimeout("/api/dex/opportunity-scanner/snapshot", 8000);
        if (generation === selectionGeneration && signature === activeSelectionSignature) renderScannerResponse(response);
        let quoteRoutesPayload = null;
        try { quoteRoutesPayload = await fetchJsonWithTimeout("/api/dex/quote-routes"); } catch (_) {}
        if (generation === selectionGeneration && signature === activeSelectionSignature && quoteRoutesPayload) renderScannerResponse(response, quoteRoutesPayload);
      }
      catch (error) { if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message); }
      finally { snapshotRefreshing = false; refreshInFlight = false; }
    }
    async function startScanner() {
      const generation = selectionGeneration; const signature = activeSelectionSignature;
      try {
        await fetchJsonWithTimeout("/api/dex/opportunity-scanner/start", 8000);
        const response = await fetchJsonWithTimeout("/api/dex/opportunity-scanner/snapshot", 8000);
        if (generation === selectionGeneration && signature === activeSelectionSignature) renderScannerResponse(response);
        if (!scanNowInFlight) {
          scanNowInFlight = true;
          fetchJsonWithTimeout("/api/dex/opportunity-scanner/scan-now", 5000)
            .then(scanResponse => {
              if (generation === selectionGeneration && signature === activeSelectionSignature) renderScannerResponse(scanResponse);
            })
            .catch(error => {
              if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message);
            })
            .finally(() => { scanNowInFlight = false; });
        }
      }
      catch (error) { if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message); }
    }
    async function refresh() {
      const generation = selectionGeneration; const signature = activeSelectionSignature; $("message").textContent = "正在后台扫描当前链，先显示当前快照...";
      try {
        refreshClosedLoopGroups();
        const snapshot = await fetchJsonWithTimeout("/api/dex/opportunity-scanner/snapshot", 8000);
        if (generation === selectionGeneration && signature === activeSelectionSignature) renderScannerResponse(snapshot);
        let quoteRoutesPayload = null;
        try { quoteRoutesPayload = await fetchJsonWithTimeout("/api/dex/quote-routes"); } catch (_) {}
        if (generation === selectionGeneration && signature === activeSelectionSignature && quoteRoutesPayload) renderScannerResponse(snapshot, quoteRoutesPayload);
        if (scanNowInFlight) return;
        scanNowInFlight = true;
        fetchJsonWithTimeout("/api/dex/opportunity-scanner/scan-now", 5000)
          .then(scanResponse => {
            if (generation === selectionGeneration && signature === activeSelectionSignature) renderScannerResponse(scanResponse, quoteRoutesPayload);
          })
          .catch(error => {
            if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message);
          })
          .finally(() => { scanNowInFlight = false; });
      }
      catch (error) { if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message); }
    }
    async function copyRoute(rawRoute, button) {
      const route = normalizeRouteForRender(rawRoute);
      const evidence = routeEvidence(route); const analysis = routeAnalysis(route);
      const sourceAnalysis = analysis;
      const size = route?.sizeAnalysis || {};
      const costs = evidence.costModel || {};
      const display = value => value === null || value === undefined || value === "" ? "-" : String(value);
      const live = Boolean(analysis.livePriceVerified || analysis.priceEvidenceMode === "live_verified");
      const gateBlocked = Object.keys(evidence).length > 0
        && live
        && ["route_unprofitable", "pool_insufficient_for_profitable_execution"].includes(String(evidence.status || "").toLowerCase());
      const hasLiveEvidence = Object.keys(evidence).length > 0 && !["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(evidence.status || "").toLowerCase());
      const staleWarningCodes = [
        "hop_1_reference_quote_requires_live_quoter",
        "hop_2_reference_quote_requires_live_quoter",
        "hop_3_reference_quote_requires_live_quoter",
        "hop_4_reference_quote_requires_live_quoter",
        "hop_1_requires_live_quoter",
        "hop_2_requires_live_quoter",
        "hop_3_requires_live_quoter",
        "hop_4_requires_live_quoter",
        "reference_price_step_requires_live_quoter",
        "reference_usd_price_missing_or_untrusted",
        "static_depth_evidence_unavailable",
        "robinhood_live_price_only_enforced",
        "v4_token_decimals_missing_or_assumed",
      ];
      const copiedWarnings = (sourceAnalysis.warnings || []).filter(warning => !hasLiveEvidence || !staleWarningCodes.some(code => String(warning).startsWith(code))).map(warningText);
      const failedHops = (Array.isArray(evidence.hops) ? evidence.hops : analysis.steps || []).filter(hop => hop?.liveQuoteError || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity"].includes(String(hop?.status || "").toLowerCase()));
      const failedHop = failedHops[0] || null;
      const failedStep = failedHop ? (analysis.steps || []).find(step => Number(step?.hop) === Number(failedHop.hop)) : null;
      const focusOnError = Boolean(failedHop);
      const failedIsLiquidity = focusOnError && String(failedHop.status || "").toLowerCase() === "not_enough_liquidity";
      const failedIsDeferred = focusOnError && ["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(failedHop.status || "").toLowerCase());
      const stackTop = failedHop?.errorStack?.[0] || route.errorStack?.[0] || evidence.errorStack?.[0] || null;
      const focusErrorText = failedHop?.liveQuoteError ? blockerText(failedHop.liveQuoteError) : (stackTop?.message ? blockerText(stackTop.message) : "链上单跳报价失败");
      if (focusOnError) {
        const probe = evidence.executionSizeProbe || null;
        const probeDecimals = Number(probe?.decimals ?? evidence.costModel?.borrowInputDecimals ?? 18);
        const probeAmount = raw => {
          if (raw === null || raw === undefined || raw === "") return null;
          const parsed = Number(raw);
          return Number.isFinite(parsed) ? parsed / (10 ** probeDecimals) : null;
        };
        const probeLines = Array.isArray(probe?.attempts) ? probe.attempts.filter(Boolean).map(row => {
          const amountText = numeric(probeAmount(row.amountRaw), 8);
          const reasonText = row.failureReason ? ` (${row.failureReason})` : "";
          return `探测 ${amountText} ${display(evidence.costModel?.borrowInputSymbol || "-")} -> ${display(row.status || "-")}${reasonText}`;
        }) : [];
        const probeHeader = probe ? [`执行规模探测=${display(probe.searchStatus || probe.status || "-")}${probe.selectedProfitableAmountRaw != null ? `，可用 ${numeric(probeAmount(probe.selectedProfitableAmountRaw), 8)} ${display(evidence.costModel?.borrowInputSymbol || "")}` : ""}`] : [];
        const text = [
          `route=${display(route.symbolPair || (route.routeSymbols || []).join(" -> "))}`,
          `path=${display((route.routeSymbols || []).join(" -> "))}`,
          `quoteStatus=${display(label(route.quoteStatus))}`,
          `evidenceStatus=${display(evidenceStatusEx(evidence.status))}`,
          `block=${display(evidence.blockNumber || evidence.triggerBlockNumber)}`,
          `executionTrust=${display(executionTrustText(evidence.executionTrust))}`,
          `失败输入=${failedIsLiquidity ? `${display(failedHop.amountIn ?? failedHop.inputAmount ?? failedStep?.inputAmount)} ${display(failedHop.fromSymbol)}` : "-"}`,
          `错误定位=第 ${display(failedHop.hop)} 步 ${display(failedHop.fromSymbol)} -> ${display(failedHop.toSymbol)}`,
          `链上错误=${display(focusErrorText)}`,
          `错误分层=\n${display(errorChainText(failedHop.liveQuoteError || focusErrorText))}`,
          `建议=${failedIsLiquidity ? "流动性不足；降低借贷金额后重试，或标记当前金额不可执行" : failedIsDeferred ? "链上复核延后；后续自动复核，不判定为路线失败" : "链上报价失败，阻止交易预检"}`,
          ...probeHeader,
          ...probeLines,
        ].filter(Boolean).join("\n");
        try { await navigator.clipboard.writeText(text); const old = button.textContent; button.textContent = "已复制"; setTimeout(() => button.textContent = old, 1200); } catch (error) { $("message").textContent = uiError(error.message); }
        return;
      }
      const netProfit = analysis.netProfitUsd ?? sourceAnalysis.netProfitUsd ?? costs.netProfitUsd ?? sourceAnalysis.referenceNetProfitAmount ?? costs.netProfitAmount;
      const netProfitSymbol = live && (analysis.netProfitUsd != null || sourceAnalysis.netProfitUsd != null || costs.netProfitUsd != null) ? "USD" : (costs.netProfitAmountSymbol || sourceAnalysis.referenceInputSymbol || "token");
      const initialProbeValue = size.recommendedBorrowInputAmount ?? size.tokenAmountSteps?.[0] ?? costs.requestedBorrowInputAmount ?? costs.selectedBorrowInputAmount ?? sourceAnalysis.referenceInputAmount;
      const initialProbeSymbol = size.recommendedBorrowInputAmountBasis || sourceAnalysis.referenceInputSymbol || costs.borrowInputSymbol || "token";
      const rows = Array.isArray(size.rows) ? size.rows : [];
      const liveDepthImpacts = (sourceAnalysis.steps || [])
        .map(step => Number(step.priceImpactBps))
        .filter(value => Number.isFinite(value));
      const liveMaxDepthImpactBps = liveDepthImpacts.length ? Math.max(...liveDepthImpacts) : null;
      const hopLines = (sourceAnalysis.steps || []).filter(Boolean).map((step, index) => {
        const output = step.outputAmount ?? step.referenceOutputAmount;
        const rate = step.effectiveRate ?? step.referenceEffectiveRate;
        return `hop ${step.hop ?? index + 1}: ${display(step.fromSymbol)} -> ${display(step.toSymbol)} | input=${display(step.inputAmount)} ${display(step.fromSymbol)} | output=${display(output)} ${display(step.toSymbol)} | rate=${display(rate)} | feeBps=${display(step.feeBps)} | priceImpactBps=${display(step.priceImpactBps)}`;
      });
      const ladderLines = rows.map(row => `探测 ${display(row.borrowAmount)} ${display(row.borrowSymbol || row.borrowBasis)} | 输出=${display(row.referenceFinalOutputAmount)} ${display(row.referenceFinalOutputSymbol)} | 净利=${display(row.referenceNetProfitAmount)} | bps=${display(row.referenceNetReturnBps)} | 深度=${display(row.staticDepthStatus)} | 可执行=${display(row.executableCandidate)}`);
      const text = [
        `route=${display(route.symbolPair || (route.routeSymbols || []).join(" -> "))}`,
        `path=${display((route.routeSymbols || []).join(" -> "))}`,
        `quoteStatus=${display(label(route.quoteStatus))}`,
        `evidenceStatus=${display(evidenceStatusEx(evidence.status))}`,
        `block=${display(evidence.blockNumber || evidence.triggerBlockNumber)}`,
        `executionTrust=${display(executionTrustText(evidence.executionTrust))}`,
        `referenceInput=${display(sourceAnalysis.referenceInputAmount)} ${display(sourceAnalysis.referenceInputSymbol)}`,
        `初始探测（initialProbe）=${display(initialProbeValue)} ${display(initialProbeSymbol)}`,
        `referenceOutput=${display(sourceAnalysis.referenceFinalOutputAmount)} ${display(sourceAnalysis.referenceFinalOutputSymbol)}`,
        `referenceNetProfit=${display(sourceAnalysis.referenceNetProfitAmount)} ${display(sourceAnalysis.referenceInputSymbol)}`,
        `referenceNetReturnBps=${display(sourceAnalysis.referenceNetReturnBps)}`,
        `netProfit=${live ? `${display(netProfit)} ${netProfitSymbol}` : "-"}`,
        `depthStatus=${display(live ? "链上实时深度已验证" : sourceAnalysis.staticDepthStatus)}`,
        `maxStaticDepthImpactBps=${display(live ? liveMaxDepthImpactBps : sourceAnalysis.maxStaticDepthImpactBps)}`,
        `depthImpactLimitBps=${display(live ? "不适用：已使用链上实时报价" : (sourceAnalysis.staticDepthImpactLimitBps || size.depthImpactLimitBps || 10))}`,
        `建议=${display(gateBlocked ? "链上验证通过，但执行门禁未放行" : recommendationAction((sourceAnalysis.recommendation || {}).action))}`,
        ...hopLines,
        ...ladderLines,
        ...(lines(evidence.blockers).map(item => errorChainText(item) || blockerText(item))),
        ...copiedWarnings,
      ].filter(Boolean).join("\n");
      try { await navigator.clipboard.writeText(text); const old = button.textContent; button.textContent = "已复制"; setTimeout(() => button.textContent = old, 1200); } catch (error) { $("message").textContent = uiError(error.message); }
    }
    $("refreshBtn").onclick = refresh;
    async function postJson(path, payload) {
      const response = await fetch(apiPath(path), {
        method: "POST",
        cache: "no-cache",
        headers: {"Content-Type": "application/json", Accept: "application/json"},
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok && data?.ok !== true) {
        const detail = data?.detail || data?.error || `请求失败（HTTP ${response.status}）`;
        throw new Error(detail);
      }
      return data;
    }
    function routeTestSymbols(route) {
      if (route?.routeSymbols?.length) return route.routeSymbols;
      if (route?.quoteAnalysis?.routeSymbols?.length) return route.quoteAnalysis.routeSymbols;
      const steps = (route?.quoteAnalysis?.steps || []).filter(step => step && step.fromSymbol);
      if (!steps.length) return [];
      const symbols = steps.map(step => step.fromSymbol);
      const last = steps[steps.length - 1]?.toSymbol;
      if (last && (symbols.length === 0 || last === symbols[0])) symbols.push(last);
      return symbols;
    }
    function routeTestSummary(result) {
      if (result?.txHash) return `广播成功：${result.txHash}`;
      if (result?.detail && !result?.steps) return String(result.detail);
      if (result?.steps && result.steps.length) {
        const hopText = result.steps.map(step => `${step.hop}: ${step.fromSymbol} -> ${step.toSymbol} ${numeric(step.inputAmount, 8)} -> ${numeric(step.outputAmount ?? step.referenceOutputAmount, 8)}`).join(" | ");
        const analysis = result.analysis || {};
        const profitText = analysis.referenceNetProfitAmount == null ? "" : `净收益 ${numeric(analysis.referenceNetProfitAmount, 8)} ${analysis.referenceInputSymbol || result.amountSymbol || ""}`;
        return `${hopText}${profitText ? ` | ${profitText}` : ""}`;
      }
      return "完成";
    }
    async function runRouteTest(route, button, mode, resultElement) {
      const container = button.closest(".route-test");
      const input = container?.querySelector("[data-route-test-amount]");
      const amount = input?.value || "1";
      const symbols = routeTestSymbols(route);
      const broadcast = mode === "broadcast";
      if (broadcast && !window.confirm("确认广播这一笔测试网上链交易？金额读取当前输入框。")) return;
      const requestMode = mode === "estimate" ? "estimate" : (mode === "static_call" ? "auto" : "auto");
      const old = button.textContent;
      button.disabled = true;
      const oldMessage = $("message").textContent;
      $("message").textContent = `正在执行 ${mode === "broadcast" ? "广播测试" : mode === "static_call" ? "只读链上预检" : "链上估算"}...`;
      if (resultElement) resultElement.textContent = "处理中...";
      try {
        const payload = await postJson("/api/dex/quote-route/test", {
          routeSymbols: symbols,
          amount,
          mode: requestMode,
          broadcast,
        });
        if (resultElement) resultElement.textContent = routeTestSummary(payload);
        $("message").textContent = `${payload?.broadcast ? "广播" : payload?.mode || "测试"}完成`;
      } catch (error) {
        if (resultElement) resultElement.textContent = uiError(error.message);
        $("message").textContent = uiError(error.message);
      } finally {
        button.textContent = old;
        button.disabled = false;
        if (!resultElement?.textContent) $("message").textContent = oldMessage;
      }
    }
    $("routeList").addEventListener("click", event => {
      const copyButton = event.target.closest("[data-copy-route]");
      if (copyButton) copyRoute(latestRoutes[Number(copyButton.dataset.copyRoute)], copyButton);
      const button = event.target.closest("[data-route-live-estimate], [data-route-static-call], [data-route-broadcast]");
      if (!button) return;
      const index = Number(button.dataset.routeIndex ?? button.dataset.routeLiveEstimate ?? button.dataset.routeStaticCall ?? button.dataset.routeBroadcast);
      const mode = button.hasAttribute("data-route-broadcast") ? "broadcast" : button.hasAttribute("data-route-static-call") ? "static_call" : "estimate";
      const resultElement = document.querySelector(`[data-route-test-result="${index}"]`);
      return runRouteTest(latestRoutes[index], button, mode, resultElement);
    });
    window.addEventListener("chainselectionchanged", event => { if (String(event?.detail?.selected?.feature || "").toLowerCase() !== "dex_arbitrage") return; beginSelectionRefresh("正在加载选中的链..."); refreshSnapshot(); });
    window.addEventListener("chainselectionprotocolschanged", () => { beginSelectionRefresh("正在加载选中的 DEX..."); refreshSnapshot(); });
    beginSelectionRefresh("正在加载当前选择...");
    refreshSnapshot();
    function normalizeRefreshInterval(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? Math.min(3600, Math.max(10, Math.floor(parsed))) : defaultRefreshIntervalSeconds;
    }
    function scheduleRefresh() {
      const seconds = normalizeRefreshInterval($("refreshInterval").value);
      $("refreshInterval").value = String(seconds);
      try { window.localStorage.setItem(refreshIntervalKey, String(seconds)); } catch (_) {}
      if (refreshTimer) clearInterval(refreshTimer);
      refreshTimer = setInterval(refreshSnapshot, seconds * 1000);
    }
    try {
      $("refreshInterval").value = normalizeRefreshInterval(window.localStorage.getItem(refreshIntervalKey) || defaultRefreshIntervalSeconds);
    } catch (_) {}
    $("refreshInterval").addEventListener("change", scheduleRefresh);
    scheduleRefresh();
  
