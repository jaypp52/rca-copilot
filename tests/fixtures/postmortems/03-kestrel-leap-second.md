---
url: https://example.invalid/postmortems/kestrel-cloud-2017-01-leap-second
title: Kestrel Cloud lock service failover storm during the leap second
company: Kestrel Cloud
published: 2017-01-05
---
# Overview

At 23:59:60 UTC on 31 December 2016 a leap second was inserted into UTC. Within two minutes the Kestrel Cloud lock service, which coordinates leader election for our block storage control plane, lost its quorum and entered a cycle of repeated elections that lasted 38 minutes. During that time customers could not create, attach or detach volumes. Existing volumes kept serving reads and writes.

# Root cause

Lock leases in the service are measured with the wall clock rather than a monotonic clock. Some hosts applied the leap second as a one-second step backwards while others had been configured to smear it over twenty-four hours. Followers whose clocks had stepped back computed that the leader's lease had not been renewed in time and started an election, while the leader believed its lease was still valid. Each new leader was deposed by the same mismatch, and the elections kept restarting until clocks converged.

# Resolution

Engineers stopped the election loop by pinning leadership to a single node with an emergency flag at 00:36 UTC. The underlying fix replaced wall-clock lease arithmetic with a monotonic clock and moved the whole fleet to the same leap smearing configuration on our NTP servers.

# Lessons

Time is a dependency. Mixed time-handling policies across one fleet are more dangerous than either policy alone, and leap seconds should be rehearsed in a staging environment by faking the time source.
