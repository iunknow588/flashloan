(function () {
  "use strict";

  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  function config() {
    return window.__DEX_PROVENANCE__ || {};
  }

  function includes(values, value) {
    return Array.isArray(values) && values.map(normalize).includes(value);
  }

  function frameworkLabel(source) {
    const value = normalize(source);
    const current = config();
    if (includes(current.staticFrameworkSources, value)) return current.staticFrameworkLabel || "静态框架";
    if (value === normalize(current.candidateFrameworkSource)) return current.candidateFrameworkLabel || "候选框架";
    if (value === normalize(current.computedFrameworkSource)) return current.computedFrameworkLabel || "链上重算";
    return String(source || "-");
  }

  function priceEvidenceLabel(source) {
    const value = normalize(source);
    const current = config();
    const labels = current.priceEvidenceSourceLabels || {};
    return labels[value] || String(source || "-");
  }

  function priceEvidenceModeLabel(mode) {
    const value = normalize(mode);
    const current = config();
    const labels = current.priceEvidenceModeLabels || {};
    return labels[value] || String(mode || "-");
  }

  function marketObservationSourceStageLabel(source) {
    const value = normalize(source);
    const current = config();
    const labels = current.marketObservationSourceStageLabels || {};
    return labels[value] || String(source || "-");
  }

  window.DexProvenance = Object.assign(window.DexProvenance || {}, {
    frameworkLabel,
    priceEvidenceLabel,
    priceEvidenceModeLabel,
    marketObservationSourceStageLabel,
  });
  window.frameworkLabel = frameworkLabel;
  window.priceEvidenceLabel = priceEvidenceLabel;
  window.priceEvidenceModeLabel = priceEvidenceModeLabel;
  window.marketObservationSourceStageLabel = marketObservationSourceStageLabel;
})();
