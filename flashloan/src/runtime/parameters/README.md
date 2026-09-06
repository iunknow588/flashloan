Runtime parameter files live here.

Each page keeps one JSON file in this directory as the file mirror for the
database-backed parameter store.

.env remains the global environment layer for shared infrastructure and
secrets. Page switches and page-specific values belong to this parameter store,
not to .env.
