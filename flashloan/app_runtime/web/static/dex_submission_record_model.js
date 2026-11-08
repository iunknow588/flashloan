/* Pure record-field interpretation for DEX submission pages. */
(function () {
  "use strict";

    const numberValue = value => {
      if (value === null || value === undefined || value === "") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const humanTokenValue = value => {
      const parsed = numberValue(value);
      if (parsed === null) return null;
      const abs = Math.abs(parsed);
      if (abs >= 1e18 && Number.isInteger(parsed)) return parsed / 1e18;
      return parsed;
    };
    const money = value => {
      const parsed = numberValue(value);
      if (parsed === null) return "-";
      return "$" + parsed.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    };
    const fmtAmount = (value, digits = 6) => {
      const parsed = numberValue(value);
      return parsed === null ? "-" : parsed.toLocaleString("zh-CN", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
    };
    const fmtSignedAmount = (value, digits = 6) => {
      const parsed = numberValue(value);
      return parsed === null ? "-" : (parsed >= 0 ? "+" : "") + fmtAmount(parsed, digits);
    };
    const fmtTime = value => window.formatLocalDisplayTime ? window.formatLocalDisplayTime(value) : String(value || "-");
    const recordTimeValue = row => {
      const raw = row.created_at || row.observed_at || row.updated_at || "";
      const parsed = raw ? new Date(raw).getTime() : NaN;
      return Number.isFinite(parsed) ? parsed : -1;
    };
    const evidence = row => row.review_summary && row.review_summary.onchain_evidence
      ? row.review_summary.onchain_evidence
      : (row.quote && row.quote.live_onchain_evidence) || {};
    const RPC_BUSY_MARKERS = [
      "network is busy",
      "please try again in a moment",
      "too many requests",
      "rate limit",
      "rate limited",
      "request rate",
      "429",
      "service unavailable",
      "temporarily unavailable",
      "overloaded",
      "server busy",
      "busy, please",
    ];
    const RPC_OFFLINE_MARKERS = [
      "rpc is disabled because the chain link is dynamically offline",
      "automatic review cannot run",
      "chain link is dynamically offline",
    ];
    const looksReviewDeferred = row => {
      const ev = evidence(row);
      const status = String(ev.status || ev.executionTrust || "").trim().toLowerCase();
      const kind = String(ev.errorKind || ev.reviewFailureKind || "").trim().toLowerCase();
      if (["rpc_busy", "live_evidence_deferred"].includes(status)) return true;
      if (kind === "rpc_busy") return true;
      const auto = row.auto_execute && typeof row.auto_execute === "object" ? row.auto_execute : {};
      const autoReason = String(auto.reason || (auto.auto_execute && typeof auto.auto_execute === "object" ? auto.auto_execute.reason : "") || "").trim().toLowerCase();
      if (autoReason === "rpc_busy") return true;
      const lower = reasonParts(row).join(" ").toLowerCase();
      return RPC_BUSY_MARKERS.some(marker => lower.includes(marker))
        || RPC_OFFLINE_MARKERS.some(marker => lower.includes(marker));
    };
    const reviewConsistentOf = row => {
      const ev = evidence(row);
      if (!ev || typeof ev !== "object") return null;
      if (String(ev.status || "").trim().toLowerCase() !== "live_verified") return false;
      if (!ev.ready_for_transaction_preflight && !ev.readyForTransactionPreflight) return false;
      const blockers = Array.isArray(ev.blockers) ? ev.blockers.filter(value => String(value || "").trim()) : [];
      if (blockers.length) return false;
      const costs = ev.costModel && typeof ev.costModel === "object" ? ev.costModel : {};
      const profit = Number(
        costs.netProfitAmount ?? ev.referenceNetProfitAmount ?? ev.profitableAmount ?? ev.profitHuman ?? ev.profit ?? 0
      );
      return Number.isFinite(profit) && profit > 0;
    };
    const routeIdentity = row => {
      const ev = evidence(row);
      return ev.routeIdentity || ev.route_identity || (row.review_summary && row.review_summary.route_identity) || {};
    };
    const shortAddress = value => {
      const text = String(value || "").trim();
      return text.length > 12 ? text.slice(0, 6) + "..." + text.slice(-4) : text;
    };
    const routeAddresses = row => {
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      const route = quote.route && typeof quote.route === "object" ? quote.route : {};
      const identity = routeIdentity(row);
      const ev = evidence(row);
      return identity.routeAddresses || identity.route_addresses || ev.routeAddresses || ev.route_addresses
        || row.route_addresses || row.routeAddresses || quote.routeAddresses || quote.route_addresses
        || route.pathAddresses || [];
    };
    const routePathText = row => {
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      const route = quote.route && typeof quote.route === "object" ? quote.route : {};
      const path = row.route_path || row.route_token_path || quote.path || route.path || [];
      if (Array.isArray(path) && path.length) return path.map(String).join(" -> ");
      const addresses = routeAddresses(row);
      if (Array.isArray(addresses) && addresses.length) return addresses.join(" -> ");
      const pair = String(row.pair || quote.pair || "-");
      return pair.includes(" -> ") ? pair : pair;
    };
    const txHashOf = row => {
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      const auto = row.auto_execute && typeof row.auto_execute === "object" ? row.auto_execute : {};
      const result = auto.result && typeof auto.result === "object" ? auto.result : {};
      const nested = result.broadcast && typeof result.broadcast === "object" ? result.broadcast : {};
      return String(
        row.tx_hash || row.transaction_hash || quote.tx_hash || quote.transaction_hash
        || result.txHash || result.transactionHash || result.transaction_hash
        || nested.txHash || nested.transactionHash || nested.transaction_hash || ""
      ).trim();
    };
    const statusKey = row => {
      const raw = String(
        row.review_category || row.category || row.status || row.state || row.submission_status || ""
      ).trim().toLowerCase();
      if (raw) return raw;
      const ev = evidence(row);
      return String(ev.status || ev.executionTrust || row.quote && row.quote.quote_status || "").trim().toLowerCase();
    };
    const statusMeta = (row, kind) => {
      const ev = evidence(row);
      const key = statusKey(row);
      const evStatus = String(ev.status || ev.executionTrust || "").toLowerCase();
      if (kind !== "execution-failed" && kind !== "execution-success" && looksReviewDeferred(row)) {
        return {label: "链上复核延后", cls: "warn"};
      }
      if (kind === "review-failed") {
        return {label: "复核失败", cls: "fail"};
      }
      if (kind === "execution-failed") {
        return {label: "提交执行失败", cls: "fail"};
      }
      if (kind === "execution-success") {
        const confirmation = confirmationStatusOf(row);
        if (confirmation === "failed") return {label: "链上确认失败/已回滚", cls: "fail"};
        if (confirmation === "pending") return {label: "已提交/待确认", cls: "static"};
        if (confirmation === "confirmed") return {label: "执行成功/已确认", cls: "live"};
      }
      if (kind === "review-success") {
        if (key === "ready_not_submitted" || key === "ready_to_submit" || key === "checks_passed_order_disabled") {
          return {label: "复核通过/待提交", cls: "live"};
        }
        return {label: "复核完成", cls: "live"};
      }
      if (["confirmed_success", "settled", "executed", "success"].includes(key) || key.includes("confirmed")) {
        return {label: "执行成功/已确认", cls: "live"};
      }
      if (["submitted_success", "broadcast_success", "broadcasted", "submission_success", "broadcast_complete"].includes(key)) {
        return {label: "已提交/待确认", cls: "static"};
      }
      if (evStatus === "live_verified" || ev.trusted_for_execution || ev.trustedForExecution) {
        return {label: "已通过链上复核", cls: "live"};
      }
      return {label: "执行成功", cls: "live"};
    };
    const analysisOf = row => {
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      return quote.quote_analysis || quote.analysis || {};
    };
    const costModelOf = row => {
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      const ev = evidence(row);
      const reviewCosts = ev.costModel && typeof ev.costModel === "object" ? ev.costModel : {};
      const quoteCosts = quote.live_onchain_evidence && typeof quote.live_onchain_evidence === "object"
        && quote.live_onchain_evidence.costModel && typeof quote.live_onchain_evidence.costModel === "object"
        ? quote.live_onchain_evidence.costModel : {};
      return Object.keys(reviewCosts).length ? reviewCosts : quoteCosts;
    };
    const broadcastResultOf = row => {
      const auto = row.auto_execute && typeof row.auto_execute === "object" ? row.auto_execute : {};
      const result = auto.result && typeof auto.result === "object" ? auto.result : {};
      const nested = result.broadcast && typeof result.broadcast === "object" ? result.broadcast : {};
      return Object.keys(nested).length ? {...nested, ...result} : result;
    };
    const confirmationStatusOf = row => {
      const ev = evidence(row);
      const auto = row.auto_execute && typeof row.auto_execute === "object" ? row.auto_execute : {};
      const result = broadcastResultOf(row);
      const explicit = String(
        auto.confirmationStatus || result.confirmationStatus || ev.confirmationStatus || ""
      ).trim().toLowerCase();
      if (explicit) return explicit;
      const receipt = String(
        result.receiptStatus || ev.receiptStatus || ev.receipt_status || ""
      ).trim().toLowerCase();
      if (["0x1", "1", "confirmed", "success"].includes(receipt)) return "confirmed";
      if (["0x0", "0", "failed", "reverted"].includes(receipt)) return "failed";
      const key = statusKey(row);
      if (["confirmed_success", "settled", "executed", "success"].includes(key) || key.includes("confirmed")) {
        return "confirmed";
      }
      if (["submitted_success", "broadcast_success", "broadcasted", "submission_success", "broadcast_complete"].includes(key)) {
        return "pending";
      }
      return txHashOf(row) ? "pending" : "none";
    };
    const profitEstimate = row => {
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      const analysis = analysisOf(row);
      const costs = costModelOf(row);
      const result = broadcastResultOf(row);
      const plan = row.review_summary && row.review_summary.plan ? row.review_summary.plan : {};
      const symbol = String(
        costs.netProfitAmountSymbol || plan.finalOutputSymbol || plan.inputSymbol
        || result.profitSymbol || result.amountSymbol
        || analysis.referenceFinalOutputSymbol || analysis.referenceInputSymbol
        || quote.final_symbol || quote.input_symbol || costs.finalOutputSymbol
        || costs.borrowInputSymbol || row.final_symbol || ""
      ).trim();
      const amount = humanTokenValue(
        plan.finalDeltaAmount ?? plan.estimatedProfitAmount ?? plan.netProfitAmount ?? plan.profit_amount
        ?? quote.reference_token_delta_amount ?? quote.live_token_profit_amount
        ?? quote.profit_amount ?? quote.final_delta_amount
        ?? row.reference_token_delta_amount ?? row.final_delta_amount
        ?? costs.netProfitAmount ?? costs.netProfitAmountHuman
        ?? result.netProfitAmount ?? result.profitHuman ?? result.profit ?? result.finalDeltaAmount
        ?? analysis.referenceNetProfitAmount ?? analysis.referenceNetProfitUsd
        ?? analysis.netProfitUsd ?? analysis.netProfitAmount ?? analysis.grossProfitUsd
        ?? analysis.profit_amount ?? costs.netProfitUsd
      );
      const finalAmount = humanTokenValue(
        plan.finalOutputAmount ?? plan.finalAmount
        ?? quote.final_amount ?? quote.reference_final_amount
        ?? costs.finalOutputAmount ?? costs.finalOutputAmountHuman
        ?? result.finalOutputAmount ?? result.outAmountHuman ?? result.outAmount
        ?? analysis.referenceFinalOutputAmount ?? analysis.referenceFinalOutputUsd
        ?? analysis.finalOutputUsd ?? costs.finalOutputUsd
      );
      const notional = numberValue(
        plan.notionalUsd ?? quote.input_amount
        ?? costs.requestedBorrowInputAmount ?? costs.borrowInputAmount ?? result.amount
        ?? analysis.referenceInputAmount ?? analysis.notionalUsd
      );
      const bps = numberValue(
        costs.netProfitBps ?? costs.netProfitAmountBps
        ?? result.netProfitAmountBps ?? result.referenceNetReturnBps
        ?? analysis.referenceNetReturnBps ?? quote.referenceNetReturnBps ?? quote.live_profit_bps
      );
      const percent = numberValue(
        plan.profitPercent ?? plan.profit_percent ?? quote.live_profit_percent
        ?? quote.profit_percent ?? result.profitPercent
      );
      const positive = amount !== null
        ? amount > 0
        : bps !== null ? bps > 0
        : percent !== null ? percent > 0
        : null;
      const missingReason = String(
        costs.netProfitAmountMissingReason || costs.referenceNetProfitAmountMissingReason
        || quote.netProfitAmountMissingReason || row.netProfitAmountMissingReason || ""
      ).trim();
      const gasSymbol = String(
        costs.gasSymbol || costs.gasCostEthSymbol || result.gasSymbol || "ETH"
      ).trim() || "ETH";
      const gas = numberValue(
        costs.gasCostEth ?? costs.gasCostToken ?? result.gasCostEth
        ?? costs.gasUsed ?? result.gasUsed ?? row.gasUsed
      );
      const netAfterGas = numberValue(
        costs.netAfterGasEth ?? costs.netAfterGasToken ?? costs.netAfterGas
        ?? result.netAfterGasEth ?? result.netAfterGasToken ?? result.netProfitAfterGas
      );
      const confirmation = confirmationStatusOf(row);
      const confirmed = confirmation === "confirmed";
      const source = confirmed
        ? "链上确认"
        : confirmation === "pending"
          ? "已提交/待确认（参考估算）"
          : "参考估算";
      return {amount, symbol, finalAmount, notional, bps, percent, positive, source, missingReason, gas, gasSymbol, netAfterGas};
    };
    const fmtProfit = estimate => {
      if (!estimate) return "-";
      const parts = [];
      if (estimate.amount !== null) {
        parts.push(`${fmtSignedAmount(estimate.amount, 6)} ${estimate.symbol || ""}`.trim());
      } else if (estimate.missingReason) {
        parts.push(`净利未记录：${estimate.missingReason}`);
      } else {
        parts.push("未记录净利换算");
      }
      if (estimate.bps !== null) parts.push(`净利BPS=${fmtAmount(estimate.bps, 2)}`);
      if (estimate.percent !== null) {
        parts.push(`比例=${estimate.percent >= 0 ? "+" : ""}${fmtAmount(estimate.percent, 4)}%`);
      }
      if (estimate.notional !== null) {
        parts.push(`输入=${fmtAmount(estimate.notional, 6)} ${estimate.symbol || ""}`.trim());
      }
      if (estimate.finalAmount !== null) {
        parts.push(`期末=${fmtAmount(estimate.finalAmount, 6)} ${estimate.symbol || ""}`.trim());
      }
      if (estimate.gas !== null) parts.push(`Gas=${fmtAmount(estimate.gas, 6)} ${estimate.gasSymbol || ""}`.trim());
      if (estimate.netAfterGas !== null) {
        parts.push(`扣Gas后=${fmtSignedAmount(estimate.netAfterGas, 6)} ${estimate.gasSymbol || estimate.symbol || ""}`.trim());
      }
      parts.push(`来源=${estimate.source}`);
      return parts.filter(Boolean).join(" | ");
    };
    const bestAmountText = row => {
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      const analysis = analysisOf(row);
      const costs = costModelOf(row);
      const calc = row.best_amount_calculation || row.bestAmountCalculation || quote.best_amount_calculation
        || quote.bestAmountCalculation || analysis.best_amount_calculation || analysis.bestAmountCalculation || {};
      const bestAmountMissing = costs.bestAmountMissing === true
        || calc.bestAmountMissing === true
        || analysis.bestAmountMissing === true
        || evidence(row).bestAmountMissing === true;
      if (bestAmountMissing) return "最佳交易量=未计算";
      const bestAmount = numberValue(
        row.best_amount ?? quote.best_amount ?? costs.selectedBorrowInputAmount
        ?? costs.borrowInputAmount ?? calc.bestAmount ?? calc.recommendedBorrowInputAmount
        ?? calc.selectedBorrowInputAmount ?? analysis.bestAmount ?? analysis.best_amount
      );
      const symbol = analysis.referenceInputSymbol || analysis.referenceFinalOutputSymbol || quote.input_symbol
        || quote.final_symbol || costs.borrowInputSymbol || "";
      if (bestAmount === null) return "最佳交易量=未计算";
      return `最佳交易量=${fmtAmount(bestAmount, 8)} ${symbol || ""}`.trim();
    };
    const reasonParts = row => {
      const parts = [];
      const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
      const ev = evidence(row);
      const precheck = row.precheck && typeof row.precheck === "object" ? row.precheck : {};
      const auto = row.auto_execute && typeof row.auto_execute === "object" ? row.auto_execute : {};
      const autoResult = auto.result && typeof auto.result === "object" ? auto.result : {};
      const gate = auto.broadcastGate && typeof auto.broadcastGate === "object" ? auto.broadcastGate : {};
      const push = value => {
        if (value === null || value === undefined || value === "") return;
        if (Array.isArray(value)) {
          for (const item of value) {
            const text = typeof item === "object" && item !== null ? (item.message || JSON.stringify(item)) : item;
            if (String(text || "").trim()) parts.push(String(text).trim());
          }
          return;
        }
        if (typeof value === "object") {
          if (value.message) parts.push(String(value.message).trim());
          return;
        }
        if (String(value).trim()) parts.push(String(value).trim());
      };
      push(row.blocked_reasons);
      push(row.reasons);
      push(row.error);
      push(row.errorStack);
      push(row.reason);
      push(auto.reason);
      push(autoResult.error);
      push(autoResult.detail);
      push(precheck.reasons);
      push(precheck.error);
      push(ev.blockers);
      push(ev.blocked_reasons);
      push(ev.reasons);
      push(ev.reviewFailureKind);
      push(ev.reviewFailureReasons);
      push(ev.reviewFailureDetail);
      push(ev.failureReason);
      push(ev.error);
      push(ev.detail);
      push(ev.errorStack);
      for (const hop of ev.hops || []) {
        if (typeof hop !== "object" || hop === null) continue;
        push(hop.liveQuoteError);
        push(hop.error);
        push(hop.blockers);
        push(hop.reason);
      }
      if (auto.broadcastResultMissing && gate.broadcastEnabled) {
        const gateReasons = Array.isArray(gate.reasons) ? gate.reasons : [];
        parts.push("broadcast_result_missing: broadcastGate open (broadcastEnabled=true), no broadcast result in worker result");
        if (gateReasons.length) parts.push(`broadcastGate reasons: ${gateReasons.join("；")}`);
      }
      return parts;
    };
    const knownReasonLabel = value => {
      const text = String(value || "").trim();
      const lower = text.toLowerCase();
      if (!text) return "";
      if (RPC_BUSY_MARKERS.some(marker => lower.includes(marker))) {
        return "链上复核延后：RPC 繁忙/限流";
      }
      if (RPC_OFFLINE_MARKERS.some(marker => lower.includes(marker))) {
        return "链上复核延后：RPC 动态离线/复核未运行";
      }
      if (/^on-chain evidence timed out after ([0-9.]+)s$/i.test(text)) {
        const seconds = text.match(/^on-chain evidence timed out after ([0-9.]+)s$/i)[1];
        return `链上复核超时：RPC 未在期限（${seconds} 秒）内返回`;
      }
      const labels = {
        "broadcast disabled; static preflight passed": "自动扫描复核并通过，但链上广播未开启；未发起交易",
        "broadcast disabled": "自动扫描复核完成，但链上广播未开启",
        "broadcast_result_missing": "链上广播开关开启，但 worker 结果无广播结果，需查询服务器广播错误日志",
        "broadcast_failed": "自动扫描已找到利润并尝试链上广播，但提交/确认失败",
        "broadcast_error": "自动扫描已找到利润并尝试链上广播，但提交/确认失败",
        "broadcast_reverted": "交易已上链但回执失败/执行回滚，未产生实际成交",
        "broadcast_pending_confirmation": "已提交广播，等待链上确认",
        "rpc_verification_failed": "链上复核失败：RPC 不可用",
        "rpc_verification_timeout": "链上复核超时：RPC 未在期限内返回",
        "rpc_busy": "链上复核延后：RPC 繁忙/限流",
        "live_evidence_deferred": "链上验证已延后",
        "live_quote_failed": "链上报价未完成",
        "not_live_verified": "未通过链上复核",
        "no_profitable_route": "无有利润路线",
        "route_unprofitable": "路线不可盈利",
        "review_failed_quote_unavailable": "链上复核失败：报价不可用",
        "review_failed": "链上复核失败：报价/复核未完成",
        "quote_unavailable": "链上报价不可用",
        "not_profitable_after_costs": "扣费后不可盈利",
        "not_enough_liquidity": "池当前没有足够流动性和 LP 头寸",
        "pool_not_initialized": "池未初始化",
        "missing_effective_pool": "池未初始化或缺少有效池",
        "missing_route_identity": "路线缺少完整 token 地址身份，只能展示不能作为链上复核/执行依据",
        "quoter_unavailable": "Uniswap Quoter 暂不可用",
        "static_reference_pending_live_review": "当前为静态参考，需等待链上复核",
      };
      for (const [key, label] of Object.entries(labels)) {
        if (lower === key || lower.includes(key)) return label;
      }
      return text;
    };
    const failureReasonText = row => {
      const seen = new Set();
      return reasonParts(row).map(knownReasonLabel).filter(item => {
        const key = item.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }).join("；") || "无失败原因字段，请查看完整记录";
    };
    const reviewConclusionText = row => {
      const ev = evidence(row);
      const est = profitEstimate(row);
      const costs = costModelOf(row);
      const evStatus = String(ev.status || "").trim().toLowerCase();
      const deferred = looksReviewDeferred(row);
      const blockers = Array.isArray(ev.blockers) ? ev.blockers.filter(value => String(value || "").trim()) : [];
      const confirmed = evStatus === "live_verified"
        && (ev.ready_for_transaction_preflight || ev.readyForTransactionPreflight)
        && !blockers.length
        && est.amount !== null
        && est.amount > 0;
      const referenceSource = String(
        costs.netProfitReferenceSource || costs.netProfitAmountReferenceSource || ev.referenceNetProfitMethod || ""
      ).trim();
      const parts = [];
      if (est.amount !== null) {
        const label = confirmed
          ? "有利润"
          : (est.positive && (referenceSource || blockers.length || evStatus === "route_unprofitable")
              ? "参考利润（未确认）"
              : est.positive ? "有利润" : "无利润");
        parts.push(`${label}：${fmtSignedAmount(est.amount, 6)} ${est.symbol || ""}`.trim());
      } else {
        parts.push("利润估算未记录");
      }
      const reason = failureReasonText(row);
      if (reason && reason !== "无失败原因字段，请查看完整记录") parts.push(reason);
      parts.push(bestAmountText(row));
      if (deferred) parts.push("链上复核结论=延后（RPC 繁忙/限流或复核未运行，将重试）");
      else if (confirmed) parts.push("链上复核结论=一致");
      else if (evStatus === "review_failed" || evStatus === "quote_unavailable" || ev.reviewFailureKind === "quote_unavailable") {
        parts.push("链上复核结论=未通过（报价不可用）");
      }
      else if (evStatus === "route_unprofitable" || blockers.length) parts.push("链上复核结论=未通过（不可执行）");
      else if (evStatus === "live_verified") parts.push("链上复核结论=通过，但净利未满足可执行条件");
      else if (evStatus) parts.push("链上复核结论=未通过");
      if (!confirmed && referenceSource) {
        parts.push(`参考净利来源=${referenceSource === "offline_v4_sqlite_route_snapshot" ? "历史参考快照（未确认）" : referenceSource}`);
      }
      const selectedProfitText = selectedProfitReviewText(row);
      if (selectedProfitText) parts.push(selectedProfitText);
      return parts.filter(Boolean).join(" | ");
    };
    const selectedProfitReviewText = row => {
      const quote = row.quote || {};
      const analysis = quote.quote_analysis || quote.analysis || {};
      const live = evidence(row);
      const selectedProfitAmount = humanTokenValue(
        row.selectedProfitAmount ?? analysis.selectedProfitAmount ?? live.selectedProfitAmount
      );
      const selectedProfitSymbol = row.selectedProfitAmountSymbol
        ?? analysis.selectedProfitAmountSymbol
        ?? live.selectedProfitAmountSymbol
        ?? "";
      const selectedProfitSource = row.selectedProfitSource
        ?? analysis.selectedProfitSource
        ?? live.selectedProfitSource
        ?? "";
      const selectedProfitMissingReason = row.selectedProfitMissingReason
        ?? analysis.selectedProfitMissingReason
        ?? live.selectedProfitMissingReason
        ?? "no_native_signed_profit";
      const referenceAmount = humanTokenValue(
        analysis.referenceNetProfitAmount
        ?? live.referenceNetProfitAmount
        ?? quote.referenceNetProfitAmount
        ?? row.referenceNetProfitAmount
      );
      const referenceText = referenceAmount === null
        ? "未记录"
        : `${fmtSignedAmount(referenceAmount, 6)} ${selectedProfitSymbol || ""}`.trimEnd();
      if (selectedProfitAmount !== null && selectedProfitAmount !== undefined) {
        const delta = referenceAmount !== null && referenceAmount !== undefined
          ? selectedProfitAmount - referenceAmount
          : null;
        const deltaText = delta !== null ? ` | delta=${fmtSignedAmount(delta, 6)} ${selectedProfitSymbol || ""}`.trimEnd() : "";
        return `链上复核值=${fmtSignedAmount(selectedProfitAmount, 6)} ${selectedProfitSymbol || ""}`.trimEnd()
          + ` | 参考执行估算=${referenceText}`
          + deltaText
          + ` | 来源=${selectedProfitReviewSourceLabel(selectedProfitSource)}`;
      }
      return `无链上 signed 净值；参考执行估算=${referenceText} | 缺失原因=${selectedProfitMissingReason}`;
    };
    const selectedProfitReviewSourceLabel = source => {
      const normalized = String(source || "").trim();
      if (!normalized) return "未记录";
      if (normalized.toLowerCase() === "native_rust_contract_preview") return "原生合约预览（链上复核）";
      if (normalized.toLowerCase() === "sqlite_reference") return "管理器参考（非 eth_call）";
      return normalized;
    };
    const uniqueTxCount = rows => {
      const set = new Set(rows.map(txHashOf).filter(Boolean));
      return set.size;
    };

  window.DexSubmissionRecordModel = {
    numberValue,
    humanTokenValue,
    money,
    fmtAmount,
    fmtSignedAmount,
    fmtTime,
    recordTimeValue,
    evidence,
    looksReviewDeferred,
    reviewConsistentOf,
    routeIdentity,
    shortAddress,
    routeAddresses,
    routePathText,
    txHashOf,
    statusKey,
    statusMeta,
    analysisOf,
    costModelOf,
    broadcastResultOf,
    confirmationStatusOf,
    profitEstimate,
    fmtProfit,
    bestAmountText,
    reasonParts,
    knownReasonLabel,
    failureReasonText,
    reviewConclusionText,
    selectedProfitReviewText,
    selectedProfitReviewSourceLabel,
    uniqueTxCount
  };
})();
