'''Read-only probe: the live per-group worker state of the broadcast chain.

The wake decision is written into the group payload under `broadcastWake`,
and the queue under `executionQueued`.  This probe reads the live groups
endpoint and reports what the running runtime actually decided, so the
chain can be judged from the runtime's own words.
'''

from __future__ import annotations

import json
import urllib.request
from collections import Counter


def fetch(url: str, timeout: int = 180) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def main() -> int:
    snapshot = fetch('http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot')
    if isinstance(snapshot, dict):
        print('--- snapshot top keys ---')
        print(sorted(snapshot.keys()))
        control = snapshot.get('closedLoop') or snapshot.get('closed_loop') or {}
        if isinstance(control, dict) and control:
            print('--- closedLoop keys ---')
            print(sorted(control.keys()))
            queue = control.get('executionQueue') or {}
            print('--- executionQueue ---')
            print(json.dumps(queue, ensure_ascii=False, indent=2, default=str)[:2000])
            print('--- summary ---')
            print(json.dumps(control.get('summary') or {}, ensure_ascii=False,
                             indent=2, default=str)[:2000])
    groups = fetch('http://127.0.0.1:5000/api/dex/closed-loop-groups')
    items = groups.get('groups') if isinstance(groups, dict) else None
    if items:
        first = items[0] if isinstance(items[0], dict) else {}
        worker0 = first.get('worker') or {}
        print('--- group keys ---')
        print(sorted(first.keys()))
        print('--- worker keys ---')
        print(sorted(worker0.keys()) if isinstance(worker0, dict) else worker0)
    reasons: Counter[str] = Counter()
    queued = 0
    sample: dict = {}
    total = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        worker = item.get('worker') or {}
        if not isinstance(worker, dict):
            continue
        total += 1
        wake = worker.get('broadcastWake')
        if worker.get('executionQueued') is True:
            queued += 1
        if isinstance(wake, dict):
            if wake.get('enqueued') is True:
                reasons['enqueued=true'] += 1
            else:
                reasons[str(wake.get('reason'))[:160]] += 1
                if not sample:
                    sample = {
                        'groupKey': item.get('groupKey') or item.get('group_key'),
                        'state': worker.get('state'),
                        'phase': worker.get('phase'),
                        'broadcastWake': wake,
                        'reviewPublish': worker.get('reviewPublish'),
                    }
        else:
            reasons['<no broadcastWake field>'] += 1
    print(f'groups={total} executionQueued={queued}')
    print('--- broadcastWake reasons ---')
    for reason, count in reasons.most_common(20):
        print(f'  {count:>4}  {reason}')
    if sample:
        print('--- one sample ---')
        print(json.dumps(sample, ensure_ascii=False, indent=2, default=str)[:3000])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
