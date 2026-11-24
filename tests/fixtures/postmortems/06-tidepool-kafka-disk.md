---
url: https://example.invalid/postmortems/tidepool-2023-02-kafka-disk-full
title: Tidepool Analytics ingestion stall: Kafka brokers out of disk
company: Tidepool Analytics
published: 2023-02-17
---
# Summary

Between 02:40 and 07:15 UTC on 15 February 2023, Tidepool stopped ingesting customer events. Dashboards showed stale data for four and a half hours, and around 3% of events sent during the window were lost after client-side buffers overflowed.

# Root cause

Three of the six brokers in our primary Kafka cluster filled their data disks and went offline. A topic created the previous week for a new product feature had been configured with `retention.ms=-1`, meaning infinite retention, copied from a template intended for compacted configuration topics. The topic received roughly 4 TB per day. Our disk usage alert fired at 85% but was routed to a team mailbox rather than to the pager, so nobody acted on it overnight.

# Recovery

Once the brokers were offline the remaining replicas could not satisfy the minimum in-sync replica setting, so producers were rejected cluster-wide. We expanded the volumes on the full brokers, set a seven-day retention on the topic, and waited for log cleanup before restarting the brokers one at a time. Reassigning partition leadership took most of the recovery time.

# Action items

- Topic creation now goes through a review that rejects infinite retention on non-compacted topics.
- Disk usage above 80% on any broker pages the streaming on-call.
- Producers get a larger local spill buffer so short broker outages do not drop events.
