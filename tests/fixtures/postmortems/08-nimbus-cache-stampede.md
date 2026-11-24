---
url: https://example.invalid/postmortems/nimbus-notes-2022-04-cache-stampede
title: Nimbus Notes sync outage after Redis failover
company: Nimbus Notes
published: 2022-04-20
---
# What users saw

On 19 April 2022, from 15:20 to 16:05 UTC, note sync failed or took several minutes for most Nimbus Notes users. The web editor loaded but showed "offline" banners.

# What happened

A planned failover of the Redis cluster that caches note metadata promoted the replica as expected, but the new primary started with an empty cache because replication had been paused for an unrelated maintenance task. Every sync request missed the cache at the same moment and fell through to the metadata database. The database, sized for a cache hit rate above 95%, saturated its CPU within thirty seconds. Requests timed out, clients retried with a fixed one-second delay, and the retries kept the database saturated, so the cache could never warm up. This is a classic thundering herd, or cache stampede.

# How we recovered

We shed load by disabling sync for free-tier accounts at the load balancer, which let the database recover and the cache refill. Sync was re-enabled for all accounts gradually over twenty minutes.

# Preventing a repeat

- Cache fills now use request coalescing, so only one request per key goes to the database.
- Clients retry with exponential backoff and jitter instead of a fixed delay.
- The failover runbook verifies that replication is healthy before promoting a replica.
