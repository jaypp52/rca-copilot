---
url: https://example.invalid/postmortems/fernhill-2024-09-cdn-certificate
title: Fernhill Health patient portal certificate expiry
company: Fernhill Health
published: 2024-09-03
---
# Summary

On 1 September 2024 the Fernhill Health patient portal showed browser security warnings to every visitor for 3 hours and 10 minutes. The public TLS certificate served by our CDN for portal.fernhill.example expired at 06:00 local time. Most patients could not log in, and the mobile apps, which pin the certificate chain, failed completely.

# Root cause

The certificate was renewed automatically by our ACME client on the origin servers two weeks earlier, but the CDN does not read certificates from the origin. Uploading the renewed certificate to the CDN was a manual step owned by an engineer who had left the company, and the reminder lived in their personal calendar. Monitoring checked certificate expiry on the origin hosts, which were valid, not on the CDN edge that patients actually connect to.

# Response

The weekend on-call engineer received patient complaints through the support line at 06:40. Access to the CDN account required a hardware key held by two people, and the first was not reachable until 08:50. The renewed certificate was uploaded at 09:05 and propagated to all edge locations by 09:10.

# Changes

- The CDN now provisions and renews its own certificate through its managed certificate feature.
- External probes check the expiry date of the certificate actually served at the edge from three regions.
- Break-glass access to the CDN account is held by the on-call rotation rather than individuals.
