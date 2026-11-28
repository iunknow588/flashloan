#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract the readings of one healer cycle from the supervisor journal.

The supervisor journal lives in plan/runtime_evidence/diagnostics and is
**ignored by git**: it is a runtime log held by a running process.  The
readings of a cycle are the evidence of what the healer did in production,
so they are copied into a **report** that is tracked by the repository.

Nothing is recomputed here.  The cycle event is read as it was written and
the parts a reader needs are copied out of it; the answers the runtime gave
are read from the runtime's own log.

Usage:
    python extract_healer_cycle_20261128.py --journal <path> [--runtime-log <path>]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

REQUEST_LINE = re.compile(
    r'\[(\d{2}/\w{3}/\d{4} \d{2}:\d{2}:\d{2})\].*?manual-review.*?HTTP/1\.1\D+(\d{3})')


def read_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def layer_readings(report: dict) -> dict:
    """Return the generation and tick readings of one report."""
    reviews = ((report.get('layers') or {}).get('reviews') or {})
    generation = reviews.get('generation') or {}
    tick = reviews.get('tickAmount') or {}
    return {
        'reviewsValid': reviews.get('valid'),
        'generation': {key: generation.get(key) for key in (
            'readable', 'matched', 'currentGeneration', 'staleGeneration',
            'unknownGeneration', 'loopsWithoutCurrentReview')},
        'tickAmount': {key: tick.get(key) for key in (
            'compared', 'nonPositive', 'bestAboveSingleTickStart',
            'bestNotAboveSingleTickStart', 'unknown',
            'executableSizeUnsearched', 'verdicts')},
    }


def local_stamp(moment: dt.datetime) -> dt.datetime:
    """Return the moment in the local time the runtime log is written in."""
    return moment.astimezone().replace(tzinfo=None)


def runtime_answers(path: Path, since: dt.datetime | None = None) -> dict:
    """Count the answers the runtime gave to the review requests.

    Only the requests of the cycle itself are counted: the runtime log
    spans every run of the runtime, and the answers of an earlier run say
    nothing about this cycle.
    """
    counts: dict[str, int] = {}
    last_at = None
    if not path.exists():
        return {'requests': counts, 'lastAt': None}
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        match = REQUEST_LINE.search(line)
        if not match:
            continue
        if since is not None:
            try:
                stamp = dt.datetime.strptime(match.group(1), '%d/%b/%Y %H:%M:%S')
            except ValueError:
                continue
            if stamp < since:
                continue
        status = match.group(2)
        counts[status] = counts.get(status, 0) + 1
        last_at = match.group(1)
    return {'requests': counts, 'lastAt': last_at,
            'sinceLocal': since.strftime('%d/%b/%Y %H:%M:%S') if since else None}


def repair_readings(outcomes: list[dict]) -> dict | None:
    """Return the readings of the generation repair of one cycle."""
    repair = next((item['result'] for item in outcomes
                   if item.get('action') == 'remeasure_stale_generation_reviews'),
                  None)
    if repair is None:
        return None
    return {
        'mode': repair.get('mode'),
        'apply': repair.get('apply'),
        'candidateCount': repair.get('candidateCount'),
        'evidenceCount': repair.get('evidenceCount'),
        'materialization': repair.get('materialization'),
        'results': [{
            'loopKey': item.get('loopKey'),
            'path': item.get('path'),
            'evidenceCaptured': item.get('evidenceCaptured'),
            'previousGenerationState':
                (item.get('previousGeneration') or {}).get('state'),
            'previousGenerationMismatches':
                (item.get('previousGeneration') or {}).get('mismatches'),
            'initialReview': (item.get('liveReviewRequest') or {}).get('initialReview'),
        } for item in repair.get('results') or []],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', required=True, type=Path)
    parser.add_argument('--runtime-log', type=Path, default=None)
    parser.add_argument('--out', type=Path, default=None)
    args = parser.parse_args()

    journal = args.journal
    if not journal.is_absolute():
        journal = REPO / journal
    runtime_log = args.runtime_log
    if runtime_log is not None and not runtime_log.is_absolute():
        runtime_log = REPO / runtime_log

    events = read_events(journal)
    cycles = [event for event in events if event.get('event') == 'healing_cycle']
    if not cycles:
        raise SystemExit(f'no healing_cycle event in {journal}')
    cycle = cycles[0]
    started = None
    try:
        started = dt.datetime.fromisoformat(
            str(cycle.get('timestamp')).replace('Z', '+00:00'))
    except ValueError:
        started = None
    since = local_stamp(started) if started is not None else None
    snapshot = next((event.get('snapshot') for event in events
                     if event.get('event') == 'pnl_snapshot'), None)

    report = {
        'report': 'mainnet_arbitrage_healer_cycle_excerpt',
        'generatedAt': cycle.get('timestamp'),
        'source': str(journal.relative_to(REPO)).replace('\\', '/'),
        'note': 'the supervisor journal is a runtime log and is ignored by git; '
                'this excerpt is the tracked evidence of the cycle',
        'actions': cycle.get('actions') or [],
        'blockers': (cycle.get('after') or {}).get('blockers') or [],
        'stable': cycle.get('stable'),
        'before': layer_readings(cycle.get('before') or {}),
        'after': layer_readings(cycle.get('after') or {}),
        'staleGenerationRepair': repair_readings(cycle.get('outcomes') or []),
        'pnlSnapshot': None if snapshot is None else {
            'tickAmount': snapshot.get('tickAmount'),
            'generation': snapshot.get('generation'),
            'reviews': {key: (snapshot.get('reviews') or {}).get(key) for key in (
                'total', 'withValue', 'missingValue', 'executablePositive',
                'signedPnlSum')},
        },
        'runtimeAnswers': runtime_answers(runtime_log, since) if runtime_log else {},
        'honesty': [
            'no pnl value is invented: the cycle materialized no evidence',
            'the repair sends review requests only and never touches the broadcast path',
            'the answers of the runtime are copied as they were: busy, not registered, timed out',
        ],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    out = args.out
    if out is None:
        print(text)
        return 0
    if not out.is_absolute():
        out = REPO / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + '\n', encoding='utf-8', newline='\n')
    print(str(out.relative_to(REPO)).replace('\\', '/'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
