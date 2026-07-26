# Flashloan Observer

## Overview

This project runs a Flask control panel for the flashloan price observer. The
application entrypoint is `flashloan/run.py`, and it listens on port 5000.
Runtime data is stored in PostgreSQL through the `DATABASE_URL` environment
variable.

## User preferences

- Keep startup failures explicit rather than silently using mock data.
- Use the existing `flashloan/` project structure unless a change is necessary.