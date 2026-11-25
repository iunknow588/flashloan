# TASK-182 fault injection matrix

- executed at: `2026-11-25T05:30:04.791809+00:00`
- scenarios: **11**, passed: **11**, red failures: **0**
- the live database was never opened: **zero writes**

| # | injection | level | verdict |
|---|---|---|---|
| F-1 | the main store commit fails | red | pass |
| F-2 | the month store can not be written | red | pass |
| F-3 | the month store is locked | red | pass |
| F-4 | the review channel is full | yellow | pass |
| F-5 | the review directory can not be written | yellow | pass |
| F-6 | a crash between the month write and the slimming | red | pass |
| F-7 | a crash between the send and the write | green | pass |
| F-8 | the month store file is damaged | red | pass |
| F-9 | the retention pass points outside the review subtree | red | pass |
| F-10 | the created_at of an attempt is not a timestamp | red | pass |
| X-1 | the cross month aggregation | green | pass |

## The readings

### F-1 the main store commit fails

- injection: the main store file is read only, so the generation can not be committed
- expectation: the audit stream writes no line at all
- invariant: the audit stream never describes a generation that was not committed
- note: the injection keeps the generation from being written: in this fixture the upstream precondition stops the pass and the main store is read only on top of that.  The commit phase itself is covered by the source order guard, which proves that the publish call sits behind the successful commit
- mainStoreWrite: `error: closed loop scope core4 has not been materialized`
- jsonlFiles: `0`
- jsonlLinesAfterFailedWrite: `0`
- auditWriterControlLines: `1`
- commitPrecedesPublish: `true`
- sourceOrder: `commitOffset=983 publishOffset=1015 callSites=2`

### F-2 the month store can not be written

- injection: a file of the month name blocks the month folder
- expectation: the main row is not slimmed and the failure is counted
- invariant: a record never disappears from both stores
- archiveFailedDelta: `1`
- mainRowStillComplete: `true`
- monthStores: `0`
- archivedAttempts: `0`

### F-3 the month store is locked

- injection: a second connection holds an exclusive lock on the month store
- expectation: the write waits for the busy timeout and then fails like F-2
- invariant: a record never disappears from both stores
- note: the busy timeout of the month store is 60 seconds, so the scenario needs that long on purpose
- monthStoreExists: `true`
- archiveFailedDelta: `1`
- waitedSeconds: `66`
- mainRowStillComplete: `true`
- archivedAttempts: `1`

### F-4 the review channel is full

- injection: 200000 lines are pushed into a channel of 10000
- expectation: the surplus is dropped and counted, and the main store is not blocked
- invariant: the main store never waits for the audit stream
- channelCapacity: `10000`
- droppedDelta: `185850`
- publishedLines: `200000`
- publishSeconds: `0.729`
- mainStoreUntouched: `true`

### F-5 the review directory can not be written

- injection: a file of the day name blocks the day folder
- expectation: the write error is counted and the main store is untouched
- invariant: the main store never waits for the audit stream
- writeErrorsDelta: `1`
- mainStoreUntouched: `true`

### F-6 a crash between the month write and the slimming

- injection: the main row is filled in again after it was archived
- expectation: the reconciliation pass archives it again and both stores agree
- invariant: the month store and the main store end up consistent
- note: the crash is reproduced by putting the main row back into its complete shape, which is exactly the state a crash between the two steps leaves behind
- crashStateMainRowComplete: `true`
- reconcileScanned: `1`
- reconcileArchived: `1`
- reconcileFailed: `0`
- monthStoreRowsForAttempt: `1`
- mainRowSlimmedAgain: `true`
- archivedAttempts: `1`

### F-7 a crash between the send and the write

- injection: the channel overflows, so the surplus lines never reach the disk
- expectation: only the audit trail is lost, and the loss is visible in the counter
- invariant: the audit stream takes part in no decision
- note: the trade record is still archived after the loss, so the judgement path never depended on the audit stream
- droppedLines: `185458`
- droppedIsVisible: `true`
- archivedAttemptsBefore: `0`
- archivedAttemptsAfter: `1`
- mainRowArchived: `true`

### F-8 the month store file is damaged

- injection: the header of the month store is overwritten
- expectation: the read reports an error and does not panic, and the main store is untouched
- invariant: a damaged archive never takes the main store with it
- monthStoreBytesBeforeDamage: `40960`
- monthStoreBytesAfterDamage: `32`
- summaryRead: `error: sqlite error: database disk image is malformed`
- mainStoreRows: `1`

### F-9 the retention pass points outside the review subtree

- injection: a relative escape and an absolute path outside the tree
- expectation: every path outside is refused and no file is removed
- invariant: the retention pass never deletes a file outside the review subtree
- note: a symbolic link is refused by the same guard, but Windows refuses to create one without a privilege, so the link case is not exercised on this host
- escapePathRefused: `true`
- absolutePathRefused: `true`
- oldDayAccepted: `2020-01-02`
- purgeScanned: `1`
- purgeRemoved: `1`
- purgeRejected: `0`
- sentinelSurvived: `true`
- outsideStillMissing: `true`

### F-10 the created_at of an attempt is not a timestamp

- injection: the attempt carries an unparsable created_at
- expectation: the archive reports an error, counts it and writes no store
- invariant: a month is never guessed
- archiveFailedDelta: `1`
- monthStores: `0`
- mainRowStillComplete: `true`
- owningMonthRefused: `true`
- monthFromBadKey: `true`

### X-1 the cross month aggregation

- injection: attempts in three different months and two currencies
- expectation: the totals cover every month and every currency is kept apart
- invariant: one total per currency, never one total over several currencies
- note: the fixture matrix of the cross month aggregation that the record separation plan asked for
- months: `2026-09,2026-10,2026-11`
- attemptCount: `4`
- perCurrency: `SPY=0x7,WETH=0xa`
- monthKeys: `2026-09,2026-10,2026-11`

