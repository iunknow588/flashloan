#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the overview's version-consistency verdicts (read-only)."""

import json
import sys
import urllib.request

URL = "http://127.0.0.1:5000/api/dex/overview"


def main() -> int:
    with urllib.request.urlopen(URL, timeout=180) as response:
        document = json.load(response)
    versions = document.get("versions") or {}
    pipeline = document.get("pipelineStatus") or {}
    print(
        json.dumps(
            {
                "consistencyIssues": versions.get("consistencyIssues"),
                "versionsConsistent": versions.get("versionsConsistent"),
                "upstreamAssessment": pipeline.get("upstreamAssessment"),
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
