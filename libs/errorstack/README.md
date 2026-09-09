# errorstack

Minimal, project-agnostic error stack for modules that return a standard error
body.

## Standard body

Every module that participates in an error stack returns only:

```text
module  module-chosen unique module/entry identifier
code    module-chosen integer error code; 0 means OK unless the module defines otherwise
message module-parsed short error reason
```

The stack is per module, not global.  It does not read, parse, or aggregate
those fields.  It only:

1. marks an OK element when the module succeeds,
2. pushes a module error body when the module fails,
3. exposes the list for serialization.

## mark_ok

At a successful module boundary, call `mark_ok(element)`:

```json
{"traceId": "...", "errorStack": [{"module": "CORE", "code": 0, "message": "OK"}]}
```

`element` is the module's own OK body.  `mark_ok` replaces the whole stack with
that single element.

When an upper module succeeds after a lower module succeeded, the upper module
calls `mark_ok(own_ok)` again.  This replaces the lower OK body and keeps the
public stack small.

When any lower stack has errors, the upper module reuses those errors instead
of hiding them behind an OK element.

## adopt_children

For callers that hold several child module results, `adopt_children(children,
ok_element=...)` is a small helper:

- When a child exposes any non-zero code, those errors are kept and the upper
  module must stop; an `ok_element` is ignored.
- When every child is OK and the upper module is also OK, the caller passes its
  own `ok_element` and the stack is replaced with that single OK body.

`adopt_children` is a convenience for module boundaries.  The underlying rule
remains: each module owns its parsing and decides its own `module`, `code` and
`message`.

## Unique entry requirement

Each module is responsible for making its own error entry findable:

- Choose a stable `module` string.  If the same module is used from multiple
  call sites, include the call site in the module string, for example
  `DEX.onchain_hop[1]`, `BOT.liquidation_payload_build[account]`.
- If a module invokes a child module repeatedly, put the exact call location
  (hop number, probe attempt, route step, script step, account, or route key)
  into the message so support can locate the failing entry without the stack
  knowing the caller chain.
- In a loop, push only the failed iteration.  The iteration number or loop
  variable may be included in the module name or message when it helps locate
  the failure; it is a convention at module level, not stack metadata.
- Do not rely on the error stack to infer flow, node names, or whether an error
  is terminal.  That is the caller's job.

## Compatibility alias

`ResultStamp` is kept as an alias of `ModuleError` so existing import paths
keep working.  New code should use `ModuleError`.
