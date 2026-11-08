#!/usr/bin/env python3
"""Capture the five Robinhood DEX pages and assert no viewport overflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "docs" / "关于Robinhood" / "观察" / "screenshots_p2_1_recheck_20261022"
)
PAGES = [
    ("candidates", "/dex/candidates"),
    ("overview", "/dex/overview"),
    ("quotes", "/dex/quotes"),
    ("market_observation", "/dex/market-observation"),
    ("submissions", "/dex/submissions"),
]
VIEWPORTS = [
    ("desktop_1440x900", 1440, 900),
    ("mobile_390x844", 390, 844),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--chain-id", type=int, default=46630)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--wait-ms", type=int, default=6000)
    parser.add_argument("--browser", default="chromium")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"playwright is required: {exc}", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = []
    failures = []
    base_url = args.base_url.rstrip("/")
    query = f"?chain_id={args.chain_id}"

    with sync_playwright() as playwright:
        browser = getattr(playwright, args.browser).launch(headless=True)
        try:
            for viewport_name, width, height in VIEWPORTS:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                try:
                    for page_name, path in PAGES:
                        url = f"{base_url}{path}{query}"
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(args.wait_ms)
                        metrics = page.evaluate(
                            """() => ({
                                scrollWidth: document.documentElement.scrollWidth,
                                bodyScrollWidth: document.body.scrollWidth,
                                innerWidth: window.innerWidth
                            })"""
                        )
                        overflow = (
                            metrics["scrollWidth"] > metrics["innerWidth"] + 1
                            or metrics["bodyScrollWidth"] > metrics["innerWidth"] + 1
                        )
                        row = {
                            "viewport": viewport_name,
                            "page": page_name,
                            "url": url,
                            "scrollWidth": metrics["scrollWidth"],
                            "bodyScrollWidth": metrics["bodyScrollWidth"],
                            "innerWidth": metrics["innerWidth"],
                            "overflow": overflow,
                        }
                        evidence_rows.append(row)
                        if overflow:
                            failures.append(
                                f"{viewport_name}/{page_name}: "
                                f"scrollWidth={metrics['scrollWidth']} "
                                f"innerWidth={metrics['innerWidth']}"
                            )
                        screenshot_name = f"{viewport_name}__{page_name}.png"
                        page.screenshot(
                            path=str(args.output_dir / screenshot_name),
                            full_page=False,
                        )
                        print(
                            f"{viewport_name}/{page_name}: "
                            f"scrollWidth={metrics['scrollWidth']} "
                            f"innerWidth={metrics['innerWidth']} "
                            f"{'FAIL' if overflow else 'OK'}"
                        )
                finally:
                    context.close()
        finally:
            browser.close()

    evidence_path = args.output_dir / "overflow_evidence.json"
    evidence = {
        "schema": "robinhood-dex-surface-overflow-evidence-v1",
        "baseUrl": base_url,
        "chainId": args.chain_id,
        "rows": evidence_rows,
        "passed": not failures,
        "failures": failures,
    }
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {evidence_path}")
    if failures:
        print("overflow failures:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
