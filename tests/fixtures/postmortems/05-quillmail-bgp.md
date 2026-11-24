---
url: https://example.invalid/postmortems/quillmail-2021-06-bgp-withdrawal
title: Quillmail global email outage: BGP route withdrawal
company: Quillmail
published: 2021-06-30
---
# Summary

On 28 June 2021 Quillmail was unreachable from the internet for 1 hour 26 minutes. Our network automation withdrew the BGP announcements for all of our public IP prefixes, so no traffic could reach our data centres, including the authoritative DNS servers for quillmail.example.

# How it happened

A capacity audit job runs daily and is meant to drain individual edge routers by withdrawing their announcements before maintenance. A bug in a new version of the job's filter treated an empty maintenance list as "all routers", and that day the list was empty. The safety check that should have refused to drain more than 20% of edge capacity was evaluated against the list of routers in the request, which was empty, so the check passed.

# Why recovery was slow

With the prefixes withdrawn, engineers lost remote access to the edge routers because the out-of-band network also authenticated through the production identity service, which was itself unreachable. Staff had to travel to two data centres and use console access to restore the announcements. The badge system for one site also depended on the same network, which added twenty minutes.

# Remediation

- The drain job now refuses to act on an empty router list and checks capacity against the live routing table.
- Out-of-band access no longer depends on production identity or DNS.
- We run a quarterly drill in which on-call engineers restore routing using only out-of-band access.
