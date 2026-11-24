---
url: https://example.invalid/postmortems/pinecrest-2019-07-datacenter-power
title: Pinecrest Bank online banking outage: data centre power loss
company: Pinecrest Bank
published: 2019-07-30
---
# Incident summary

On 26 July 2019 Pinecrest Bank's online and mobile banking were unavailable for 5 hours and 40 minutes after our primary data centre lost power. Card payments in stores continued because they are processed by a separate provider.

# Sequence of events

A utility power cut affected the area around the data centre at 13:05. The uninterruptible power supply carried the load as designed, but one of the two diesel generators failed to start because its starter battery had degraded. The automatic transfer switch then tried to place the full load on the remaining generator, which tripped on overload after four minutes. When the UPS batteries were exhausted at 13:21, the whole room lost power.

# Why failover to the secondary site did not happen

The secondary data centre was able to run online banking, but the failover procedure required a database replica promotion that had not been rehearsed for two years. The documented procedure referred to servers that had since been decommissioned. Engineers decided it was faster to restore power at the primary site, which happened at 16:20, after which services were restarted and checked until 18:45.

# Actions

- Generator starter batteries are now tested monthly and replaced every two years.
- Full load transfer to generators is tested twice a year.
- Site failover is rehearsed every quarter, and the runbook is generated from the live inventory.
