/* DOM rendering for DEX submission pages. */
(function () {
  "use strict";

  const model = window.DexSubmissionRecordModel;
  if (!model) throw new Error("DEX submission record model is not loaded");
  const {
    numberValue,
    fmtTime,
    evidence,
    looksReviewDeferred,
    routePathText,
    txHashOf,
    shortAddress,
    statusMeta,
    profitEstimate,
    fmtProfit,
    reviewConclusionText,
    routeAddresses,
    failureReasonText,
    uniqueTxCount,
    confirmationStatusOf
  } = model;

  window.DexSubmissionRecordRender = {
    create(state, PAGE_BY_KIND) {
        const $ = id => document.getElementById(id);
        const esc = value => String(value ?? "-").replace(/[&<>"']/g, ch => (
          {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch]
        ));

        const renderSummary = payload => {
          const config = PAGE_BY_KIND[state.kind];
          const rows = Array.isArray(payload.attempts) ? payload.attempts : [];
          const estimateRows = rows
            .map(profitEstimate)
            .filter(est => est && (est.amount !== null || est.bps !== null || est.percent !== null));
          const profitCount = state.kind === "execution-failed"
            ? estimateRows.length
            : estimateRows.filter(est => est.amount !== null ? est.amount > 0 : est.bps > 0 || est.percent > 0).length;
          const noProfitCount = estimateRows.filter(est => est.amount === null ? !(est.bps > 0 || est.percent > 0) : est.amount <= 0).length;
          const summary = payload.summary || {};
          const loopState = payload.loopStateSummary || {};
          const loopStates = loopState.states || {};
          const values = {
            attemptCount: numberValue(summary.attemptCount) ?? 0,
            pageAttemptCount: numberValue(summary.pageAttemptCount) ?? 0,
            profitCount,
            noProfitCount,
            uniqueTxCount: uniqueTxCount(rows) || numberValue(summary.uniqueTxHash && summary.uniqueTxHash.total) || 0,
            successCount: numberValue(summary.successCount) ?? 0,
            failedCount: numberValue(summary.failedCount) ?? rows.length,
            blockedCount: numberValue(summary.blockedCount) ?? 0,
            confirmedCount: rows.filter(row => {
              return confirmationStatusOf(row) === "confirmed";
            }).length,
            closedLoopTotal: numberValue(summary.closedLoopTotal) ?? 0,
            reviewSuccessLoops: numberValue(summary.reviewSuccessLoops) ?? 0,
            reviewFailedLoops: numberValue(summary.reviewFailedLoops) ?? 0,
            reviewDeferredLoops: numberValue(summary.reviewDeferredLoops) ?? 0,
            reviewMissingLoops: numberValue(summary.reviewMissingLoops) ?? 0,
            broadcastLoops: numberValue(summary.broadcastLoops) ?? 0,
            broadcastSuccessLoops: numberValue(summary.broadcastSuccessLoops) ?? 0,
            broadcastFailedLoops: numberValue(summary.broadcastFailedLoops) ?? 0,
            submissionSuccessTotal: numberValue(summary.submissionSuccessTotal) ?? 0,
            submissionFailedTotal: numberValue(summary.submissionFailedTotal) ?? 0,
            executionSuccessTotal: numberValue(summary.executionSuccessTotal) ?? 0,
            executionFailedTotal: numberValue(summary.executionFailedTotal) ?? 0,
            cycleTerminals: numberValue(loopState.cycleTerminals) ?? 0,
            terminalExecution: numberValue(loopState.terminalExecution) ?? 0,
            executionNotRequested: numberValue(loopState.executionNotRequested) ?? 0,
            missingBroadcast: numberValue(loopState.missingBroadcast) ?? 0,
          };
          config.summary.forEach(([key, label, hint]) => {
            const node = $(key);
            if (!node) return;
            node.textContent = String(values[key] ?? 0);
            const hintNode = node.parentElement && node.parentElement.querySelector("span");
            if (hintNode) hintNode.textContent = hint;
            const small = node.parentElement && node.parentElement.querySelector("small");
            if (small) small.textContent = label;
          });
          const loopTerminalNode = $("terminalExecution");
          if (loopTerminalNode) {
            loopTerminalNode.textContent = String(values.terminalExecution);
            const loopHint = loopTerminalNode.parentElement && loopTerminalNode.parentElement.querySelector("span");
            if (loopHint) loopHint.textContent = loopState.available ? "广播成功/执行失败均计入闭环终态" : "闭环状态摘要不可用";
          }
          const notRequestedNode = $("executionNotRequested");
          if (notRequestedNode) {
            notRequestedNode.textContent = String(values.executionNotRequested);
            const notRequestedHint = notRequestedNode.parentElement && notRequestedNode.parentElement.querySelector("span");
            if (notRequestedHint) notRequestedHint.textContent = loopState.available ? "复核完成但未请求广播的显式终态" : "闭环状态摘要不可用";
          }
          const cycleTerminalsNode = $("cycleTerminals");
          if (cycleTerminalsNode) {
            cycleTerminalsNode.textContent = String(values.cycleTerminals);
            const cycleTerminalsHint = cycleTerminalsNode.parentElement && cycleTerminalsNode.parentElement.querySelector("span");
            if (cycleTerminalsHint) cycleTerminalsHint.textContent = loopState.available ? "成功/失败/未请求广播均计入周期终态" : "闭环状态摘要不可用";
          }
          const missingNode = $("missingBroadcast");
          if (missingNode) {
            missingNode.textContent = String(values.missingBroadcast);
            const missingHint = missingNode.parentElement && missingNode.parentElement.querySelector("span");
            if (missingHint) missingHint.textContent = loopState.available ? "超时未收到广播结果的状态机事实记录" : "闭环状态摘要不可用";
          }
          $("source") && ($("source").textContent = String(payload.source || "-"));
        };

        const renderCell = (row, key) => {
          if (key === "time") return `<span title="${esc(row.created_at || row.observed_at || row.updated_at || "")}">${esc(fmtTime(row.created_at || row.observed_at || row.updated_at))}</span>`;
          if (key === "network") return esc(row.network || row.cow_network || row.chain_id || "-");
          if (key === "path") {
            const path = routePathText(row);
            return `<div class="route-cell">${esc(path)}</div>`;
          }
          if (key === "evidence") {
            const ev = evidence(row);
            const status = String(ev.status || ev.executionTrust || row.quote && row.quote.quote_status || "").trim().toLowerCase();
            const live = status === "live_verified" || ev.trusted_for_execution || ev.trustedForExecution;
            if (looksReviewDeferred(row)) {
              return '<span class="badge warn">链上复核延后</span>';
            }
            const label = live ? "链上复核已通过" : (status || "静态观察");
            return `<span class="badge ${live ? "live" : status ? "fail" : "static"}">${esc(label)}</span>`;
          }
          if (key === "block") {
            const ev = evidence(row);
            return esc(ev.block_number || ev.blockNumber || "-");
          }
          if (key === "review") return `<div class="reason">${esc(reviewConclusionText(row))}</div>`;
          if (key === "profit") {
            const est = profitEstimate(row);
            return `<div class="reason">${esc(fmtProfit(est))}</div>`;
          }
          if (key === "conclusion") return `<div class="reason">${esc(reviewConclusionText(row))}</div>`;
          if (key === "tx") {
            const hash = txHashOf(row);
            return hash ? `<code class="tx">${esc(shortAddress(hash))}</code>` : "-";
          }
          if (key === "order") {
            const hash = txHashOf(row);
            return esc(row.order_uid || row.uid || hash || "-");
          }
          if (key === "status") {
            const meta = statusMeta(row, state.kind);
            return `<span class="badge ${meta.cls}">${esc(meta.label)}</span>`;
          }
          return "-";
        };

        const renderHeaders = () => {
          const config = PAGE_BY_KIND[state.kind];
          const head = document.querySelector("#recordHead");
          if (!head || !config) return;
          const actionHeader = state.kind !== "overview" ? "<th>操作</th>" : "";
          head.innerHTML = `<tr>${config.headers.map(header => `<th>${esc(header)}</th>`).join("")}${actionHeader}</tr>`;
        };

        const renderRows = rows => {
          const config = PAGE_BY_KIND[state.kind];
          const tbody = $("rows");
          tbody.innerHTML = rows.length
            ? rows.map((row, rowIndex) => {
                const cells = config.headers
                  .map((header, cellIndex) => {
                    const key = header === "时间" ? "time"
                      : header === "网络" ? "network"
                      : header === "路径" ? "path"
                      : header === "证据来源" ? "evidence"
                      : header === "链上区块" ? "block"
                      : header === "盈亏/复盘" ? "review"
                      : header === "结果/状态" ? "status"
                      : header === "原因与复盘要点" ? "reason"
                      : header === "订单/交易" ? "order"
                      : header === "利润估算" || header === "实际盈利估算" ? "profit"
                      : header === "预期盈亏" ? "profit"
                      : header === "复核结论" ? "conclusion"
                      : header === "复核失败原因" || header === "失败原因" ? "reason"
                      : header === "交易哈希" ? "tx"
                      : header === "状态" ? "status"
                      : "";
                    const cls = cellIndex === 0 || cellIndex === 2 || header === "证据来源" || header === "盈亏/复盘"
                      || header === "原因与复盘要点" || header === "复核失败原因" || header === "失败原因" ? " class=\"left\"" : "";
                    return `<td${cls}>${renderCell(row, key)}</td>`;
                  })
                  .join("");
                const actionCell = state.kind !== "overview"
                  ? `<td class="actions"><button class="copy-btn" type="button" data-copy-record="${rowIndex}">复制记录</button></td>`
                  : "";
                return `<tr>${cells}${actionCell}</tr>`;
              }).join("")
            : `<tr><td colspan="${config.headers.length + (state.kind !== "overview" ? 1 : 0)}" class="empty">${config.emptyText}</td></tr>`;
        };

        const copyRecordText = row => {
          const ev = evidence(row);
          const meta = statusMeta(row, state.kind);
          const est = profitEstimate(row);
          const addresses = routeAddresses(row);
          const pair = row.pair || row.symbol_pair || row.route_key || row.order_uid || routePathText(row) || "-";
          return [
            `时间：${fmtTime(row.created_at || row.observed_at || row.updated_at)}`,
            `网络：${row.network || row.cow_network || row.chain_id || "-"}`,
            `路径：${routePathText(row)}`,
            `地址路径：${Array.isArray(addresses) && addresses.length ? addresses.join(" -> ") : "-"}`,
            `方向：${row.direction || row.symbol_pair || pair}`,
            `状态：${meta.label}`,
            `订单/交易：${row.order_uid || row.uid || txHashOf(row) || "-"}`,
            `链上区块：${ev.block_number || ev.blockNumber || "-"}`,
            `盈亏/复盘：${fmtProfit(est)}`,
            `复核结论：${reviewConclusionText(row)}`,
            `失败原因：${failureReasonText(row)}`,
          ].filter(Boolean).join("\n");
        };

        const copyText = async (text, button) => {
          try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              await navigator.clipboard.writeText(text);
            } else {
              const area = document.createElement("textarea");
              area.value = text;
              area.setAttribute("readonly", "");
              area.style.position = "fixed";
              area.style.left = "-9999px";
              document.body.appendChild(area);
              area.select();
              document.execCommand("copy");
              area.remove();
            }
            const old = button.textContent;
            button.textContent = "已复制";
            setTimeout(() => { button.textContent = old; }, 1200);
          } catch (error) {
            const message = $("message");
            if (message) message.textContent = `复制失败：${(error && error.message) || String(error)}`;
          }
        };

        const copyRecord = async (row, button) => copyText(copyRecordText(row), button);

      return {
        renderSummary,
        renderHeaders,
        renderRows,
        copyRecord,
        renderPagination
      };
    },
  };
})();
