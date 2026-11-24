---
url: https://example.invalid/postmortems/orbitcart-2023-08-dns-zone-push
title: Orbitcart storefront unreachable after DNS zone push
company: Orbitcart
published: 2023-08-22
---
# What happened

On 21 August 2023 the Orbitcart storefront and mobile API were unreachable for customers in most regions for 52 minutes. A change to our DNS zone management tool pushed a zone file that was missing the A and AAAA records for shop.orbitcart.example and api.orbitcart.example. Resolvers began returning NXDOMAIN as soon as the previous answers aged out of their caches.

# Why the records disappeared

The zone file is generated from a YAML inventory. A refactor of the generator changed how records with multiple targets were merged, and a record group containing the storefront entries was silently dropped when the inventory was rendered. The generator's unit tests only compared record counts per type, and the count of CNAME records grew in the same change, so the total looked plausible. The push itself succeeded and passed the syntax check run by the DNS provider.

# Why recovery took 52 minutes

Earlier that week the TTL on the storefront records had been raised from 300 seconds to one hour to reduce query costs. Rolling the zone back at 14:31 fixed authoritative answers immediately, but many recursive resolvers kept serving the cached NXDOMAIN response until the negative-caching TTL from the SOA record expired. We lowered the SOA minimum TTL and asked two large public resolver operators to flush their caches.

# Follow-up

- Diff rendered zone files against production before every push and require approval for any removed record.
- Keep a short negative-caching TTL on customer-facing zones.
- Synthetic probes now resolve the storefront names from six public resolvers every minute.
