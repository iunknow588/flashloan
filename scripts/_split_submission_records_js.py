from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "flashloan/app_runtime/web/static/dex_submission_records.js"
MODEL = ROOT / "flashloan/app_runtime/web/static/dex_submission_record_model.js"
RENDER = ROOT / "flashloan/app_runtime/web/static/dex_submission_record_render.js"


def text_between(lines: list[str], start: str, end: str) -> str:
    start_index = next(i for i, line in enumerate(lines) if line.strip().startswith(start))
    end_index = next(i for i in range(start_index + 1, len(lines)) if lines[i].strip().startswith(end))
    return "\n".join(line.rstrip() for line in lines[start_index:end_index]).rstrip()


def main() -> None:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    model_body = text_between(lines, "const numberValue", "let state")
    render_parts = [
        text_between(lines, "const $", "const numberValue"),
        text_between(lines, "const renderSummary", "const renderCell"),
        text_between(lines, "const renderCell", "const renderHeaders"),
        text_between(lines, "const renderHeaders", "const renderRows"),
        text_between(lines, "const renderRows", "const copyRecordText"),
        text_between(lines, "const copyRecordText", "const copyText"),
        text_between(lines, "const copyText", "const copyRecord"),
        text_between(lines, "const copyRecord", "document.addEventListener"),
    ]
    model_exports = [
        "numberValue",
        "humanTokenValue",
        "money",
        "fmtAmount",
        "fmtSignedAmount",
        "fmtTime",
        "recordTimeValue",
        "evidence",
        "looksReviewDeferred",
        "reviewConsistentOf",
        "routeIdentity",
        "shortAddress",
        "routeAddresses",
        "routePathText",
        "txHashOf",
        "statusKey",
        "statusMeta",
        "analysisOf",
        "costModelOf",
        "broadcastResultOf",
        "confirmationStatusOf",
        "profitEstimate",
        "fmtProfit",
        "bestAmountText",
        "reasonParts",
        "knownReasonLabel",
        "failureReasonText",
        "reviewConclusionText",
        "selectedProfitReviewText",
        "selectedProfitReviewSourceLabel",
        "uniqueTxCount",
    ]
    model_export_block = ",\n    ".join(model_exports)
    MODEL.write_text(
        '/* Pure record-field interpretation for DEX submission pages. */\n'
        '(function () {\n  "use strict";\n\n'
        + "\n".join("  " + line if line else "" for line in model_body.splitlines())
        + "\n\n  window.DexSubmissionRecordModel = {\n    "
        + model_export_block
        + "\n  };\n})();\n",
        encoding="utf-8",
    )

    render_body = "\n\n".join(render_parts)
    render_exports = [
        "renderSummary",
        "renderHeaders",
        "renderRows",
        "copyRecord",
        "renderPagination",
    ]
    render_deps = [
        "numberValue",
        "fmtTime",
        "evidence",
        "looksReviewDeferred",
        "routePathText",
        "txHashOf",
        "shortAddress",
        "statusMeta",
        "profitEstimate",
        "fmtProfit",
        "reviewConclusionText",
        "routeAddresses",
        "failureReasonText",
        "uniqueTxCount",
        "confirmationStatusOf",
    ]
    RENDER.write_text(
        '/* DOM rendering for DEX submission pages. */\n'
        '(function () {\n  "use strict";\n\n'
        "  const model = window.DexSubmissionRecordModel;\n"
        "  if (!model) throw new Error(\"DEX submission record model is not loaded\");\n"
        "  const {\n    "
        + ",\n    ".join(render_deps)
        + "\n  } = model;\n\n"
        "  window.DexSubmissionRecordRender = {\n"
        "    create(state, PAGE_BY_KIND) {\n"
        + "\n".join("      " + line if line else "" for line in render_body.splitlines())
        + "\n\n      return {\n        "
        + ",\n        ".join(render_exports)
        + "\n      };\n    },\n  };\n})();\n",
        encoding="utf-8",
    )

    state_and_scope = text_between(lines, "let state", "const renderSummary")
    click_handler = text_between(lines, "document.addEventListener", "const renderPagination")
    load_and_controller = "\n".join(
        line.rstrip() for line in lines[next(i for i, line in enumerate(lines) if line.strip().startswith("const load")) :]
    )
    main_source = (
        '(function () {\n  "use strict";\n\n'
        "  const PAGE_SIZE = 20;\n"
        "  const PAGE_BY_KIND = window.DEX_SUBMISSION_RECORD_PAGE_CONFIG || {};\n"
        "  const model = window.DexSubmissionRecordModel;\n"
        "  const renderers = window.DexSubmissionRecordRender;\n"
        '  if (!model || !renderers) throw new Error("DEX submission record modules are not loaded");\n\n'
        "  const $ = id => document.getElementById(id);\n\n"
        + "\n".join("  " + line if line else "" for line in state_and_scope.splitlines())
        + "\n\n"
        "  const {\n"
        "    renderSummary,\n"
        "    renderHeaders,\n"
        "    renderRows,\n"
        "    copyRecord,\n"
        "    renderPagination,\n"
        "  } = renderers.create(state, PAGE_BY_KIND);\n\n"
        + "\n".join("  " + line if line else "" for line in click_handler.splitlines())
        + "\n\n"
        + "\n".join("  " + line if line else "" for line in load_and_controller.splitlines())
        + "\n"
    )
    SOURCE.write_text(main_source, encoding="utf-8")


if __name__ == "__main__":
    main()
