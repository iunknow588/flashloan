(function () {
  "use strict";

  function currentQuery() {
    return new URLSearchParams(window.location.search);
  }

  function contextQuery() {
    const query = currentQuery();
    query.set("page_path", window.location.pathname);
    return query;
  }

  function applyContext(payload) {
    const context = payload && payload.context ? payload.context : null;
    const page = context && context.page ? context.page : {};
    const body = document.body;
    if (body) {
      body.dataset.workflow = context && context.workflow || "";
      body.dataset.executionMode = context && context.execution_mode || "";
      body.dataset.chainScope = context && context.scope_type || "";
      body.dataset.settlementModel = context && context.settlement_model || "";
      body.dataset.atomicCrossChain = context && context.atomic_cross_chain ? "true" : "false";
    }
    window.WorkflowContext = Object.assign(window.WorkflowContext || {}, context || {}, {
      page: page,
      payload: payload || null,
      ready: true
    });
    window.dispatchEvent(new CustomEvent("workflowcontextready", {detail: payload || {}}));
    return window.WorkflowContext;
  }

  async function init() {
    const response = await fetch("/api/workflow-context?" + contextQuery().toString(), {cache: "no-cache"});
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || ("请求失败（HTTP " + response.status + "）"));
    }
    return applyContext(payload);
  }

  window.WorkflowContext = {
    init: init,
    query: contextQuery,
    apply: applyContext,
    ready: false
  };
})();
