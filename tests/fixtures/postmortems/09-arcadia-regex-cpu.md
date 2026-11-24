---
url: https://example.invalid/postmortems/arcadia-games-2023-05-regex-backtracking
title: Arcadia Games login outage: a regular expression exhausted CPU
company: Arcadia Games
published: 2023-05-11
---
# Summary

Players could not log in to Arcadia Games for 34 minutes on 10 May 2023. All edge proxies hit 100% CPU shortly after a new web application firewall rule was deployed globally.

# Root cause

The new rule was meant to block a credential-stuffing pattern in login request bodies. Its regular expression contained nested quantifiers, which caused catastrophic backtracking: on certain long inputs the regex engine explored an exponential number of paths. Normal login bodies triggered this behaviour, so every proxy spent all of its CPU evaluating the rule. The rule was deployed to all regions at once because firewall rules were treated as configuration rather than code and skipped the staged rollout used for proxy binaries.

# Resolution

A global kill switch for the firewall rule set was used at 21:48, and CPU returned to normal within a minute. The rule was rewritten without nested quantifiers and redeployed the next day.

# Follow-ups

- Firewall rules now go through the same staged rollout as code, starting with one canary region.
- The proxy uses a regex engine with linear-time matching guarantees for firewall rules.
- Rule changes are benchmarked against a corpus of real request bodies before deployment.
