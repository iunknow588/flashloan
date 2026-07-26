---
name: Subdirectory Flask startup
description: Startup conventions for this project when the Flask entrypoint is nested under flashloan/.
---

The Flask control panel lives in a nested application directory, so Replit's
run and deployment commands must change into that directory before invoking
the Python entrypoint. The project should use one managed web workflow for
port 5000; an extra wrapper or stale manual process can start a second server
and cause an otherwise healthy app to fail with "Address already in use".

**Why:** The app's database initialization and HTTP server both started
correctly, but the initial workflow attempt collided with an orphaned process
and generated duplicate workflow configuration.

**How to apply:** When changing startup, preserve the `cd flashloan && python
run.py` command, use the existing `Start application` workflow, and stop an
old project process before restarting if port 5000 is occupied. If the
deployment was previously static, republish after switching it to autoscale;
the old published build does not inherit the new runtime configuration. Keep
database schema initialization off the critical path before Flask binds its
port, or autoscale health checks can fail during cold starts.