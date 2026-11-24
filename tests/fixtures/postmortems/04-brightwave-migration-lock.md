---
url: https://example.invalid/postmortems/brightwave-2022-11-schema-migration
title: Brightwave Media playback outage caused by a schema migration
company: Brightwave Media
published: 2022-11-09
---
# Impact

From 18:12 to 19:05 UTC on 8 November 2022, during the evening peak, viewers could not start new streams on Brightwave. Streams already playing continued. About 2.3 million playback attempts failed.

# Root cause

A routine migration added a column with a default value to the `entitlements` table, which holds one row per subscriber per title and has roughly 900 million rows. On our PostgreSQL version this ALTER TABLE rewrote the table and held an ACCESS EXCLUSIVE lock for the whole rewrite. Every playback start reads `entitlements` to check that the viewer is allowed to watch, so those reads queued behind the lock, exhausted the connection pool, and timed out. The migration had run in under a second in staging, where the table contains only fifty thousand rows.

# Timeline

18:12 migration started by the deploy pipeline. 18:14 playback error alert fired. 18:31 the team identified lock waits in the database. 18:40 the migration was cancelled, but the rollback of the partial rewrite took another 25 minutes, during which the lock was still held. 19:05 playback recovered.

# What we are changing

Migrations against large tables must now use a lock timeout of five seconds and add columns without defaults, backfilling in batches afterwards. The migration linter blocks ALTER TABLE statements that take an ACCESS EXCLUSIVE lock on tables larger than ten million rows, and schema changes are no longer deployed during peak viewing hours.
