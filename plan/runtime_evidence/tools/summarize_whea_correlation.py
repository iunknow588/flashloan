# -*- coding: utf-8 -*-
"""汇总 Event41 <-> CPU 级 WHEA 配对结果，输出 60s 窗口统计。

用法:
    python summarize_whea_correlation.py <correlation.json> [summary_out.json]
"""
import json
import sys
import statistics as st
from collections import Counter

WINDOW_SECONDS = 60


def main():
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None

    data = json.load(open(src, encoding="utf-8"))
    rows = data["rows"]

    deltas = [r["delta_seconds_before_crash"] for r in rows
              if r.get("delta_seconds_before_crash") is not None]
    within = [d for d in deltas if d <= WINDOW_SECONDS]

    monthly_total = Counter()
    monthly_hit = Counter()
    for r in rows:
        month = r["event41_utc"][:7]
        monthly_total[month] += 1
        d = r.get("delta_seconds_before_crash")
        if d is not None and d <= WINDOW_SECONDS:
            monthly_hit[month] += 1

    severities = sorted({r.get("whea_severity") for r in rows
                          if r.get("whea_severity")})
    bugchecks = sorted({r.get("bugcheck_code") for r in rows})

    summary = {
        "window_seconds": WINDOW_SECONDS,
        "event41_count": len(rows),
        "with_cpu_whea_in_window": len(within),
        "match_rate_pct": round(100.0 * len(within) / len(rows), 1) if rows else 0.0,
        "delta_seconds": {
            "min": round(min(deltas), 3) if deltas else None,
            "max": round(max(deltas), 3) if deltas else None,
            "mean": round(st.mean(deltas), 3) if deltas else None,
            "median": round(st.median(deltas), 3) if deltas else None,
            "stdev": round(st.pstdev(deltas), 3) if deltas else None,
        },
        "all_bugcheck_code_zero": all(b == "0" for b in bugchecks),
        "whea_severities": severities,
        "monthly": {m: {"event41": monthly_total[m], "matched": monthly_hit.get(m, 0)}
                    for m in sorted(monthly_total)},
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if dst:
        json.dump(summary, open(dst, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print("-> " + dst)


if __name__ == "__main__":
    main()
