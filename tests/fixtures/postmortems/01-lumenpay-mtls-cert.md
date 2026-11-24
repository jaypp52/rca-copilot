---
url: https://example.invalid/postmortems/lumenpay-2024-03-mtls-certificate
title: Lumenpay payment API outage (March 2024)
company: Lumenpay
published: 2024-03-14
---
# Summary

On 12 March 2024 between 09:02 and 10:47 UTC, 71% of card payment requests to the Lumenpay API failed with HTTP 503. The API gateway could not open connections to the internal settlement service because the client certificate used for mutual TLS between the two services had expired at 09:00 UTC. Merchants saw declined checkouts for one hour and forty-five minutes.

# Root cause

The gateway's client certificate was issued manually in 2022 with a two-year validity period, outside the automated issuance pipeline that renews every other internal certificate thirty days before expiry. Because it was not in the certificate inventory, no renewal job and no expiry alert existed for it. When the certificate expired, the settlement service rejected every TLS handshake from the gateway with a "certificate expired" alert, and the gateway retried until its connection pool was exhausted.

# Detection and response

The on-call engineer was paged at 09:06 by the checkout error-rate alert. The first forty minutes were spent investigating a deploy of the gateway that had gone out at 08:55, which was rolled back without effect. At 09:51 an engineer found handshake failures in the settlement service logs. A new certificate was issued from the internal CA at 10:30 and rolled out to the gateway fleet by 10:47.

# Action items

- Move every internal certificate, including manually issued ones, into the automated issuance pipeline.
- Alert on any certificate in the fleet expiring within 21 days, discovered by scanning live endpoints rather than trusting the inventory.
- Add TLS handshake failures to the gateway dashboard so responders see them first.
