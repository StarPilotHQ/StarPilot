# Automatic Sentry relay enrollment

Implemented across [StarPilot #18](https://github.com/dirwin31/openpilot/pull/18), [Android/Worker #3](https://github.com/dirwin31/StarPilotAndroid/pull/3), and [iOS #4](https://github.com/natehuby76/ios-clone/pull/4). StarPilot is based on FixesTest; Android is based on Play-Dev.

## User flow

The user connects the phone app to comma and enables alerts. The app requests OS notification permission, registers its FCM/APNs token with comma, and asks comma to enroll automatically. Galaxy's Enable alerts action starts the same flow. Users never copy keys, invitations, tokens or URLs. Each phone must grant permission; comma and phone need internet for delivery.

The relay is deployed with its provider credentials, and `DEFAULT_RELAY_URL` in `openpilot/starpilot/sentry_mode/relay_config.py` points at it (`https://galaxy-sentry-relay.dannyirwin31.workers.dev`). With an empty value the UI reports that hosting is not configured and does not claim success. The apps discover readiness from comma and need no duplicate URL configuration.

Enabling is explicit. Existing subscriptions or app upgrades do not silently opt into a new relay. Custom invitation-based relays remain available under advanced controls; they are not the normal user flow.

## Credential ownership and recovery

Comma creates a random installation ID and 256-bit sending secret and saves them privately before enrollment. It sends only the SHA-256 digest at enrollment. Later requests use an installation bearer credential. The Worker stores digests; phone apps never receive the comma credential. Provider keys remain Worker secrets. Snapshots expose only capability, lifecycle state, installation ID and destination count.

Pending enrollment, rotation, revocation and destination reconciliation survive restarts. Requests use HTTPS without redirects; credentials are bound to the configured origin. A lost enrollment response is recovered by authenticating with the saved candidate. Rotation checks the candidate before retrying, including after the old credential's ten-minute overlap expires. Confirmation retires the old credential.

Revocation stops new local sending immediately and retries remote revocation offline. The Worker keeps a revocation tombstone so retries cannot reactivate the old ID. An already authorized provider request may finish. Forgetting locally does not revoke a copied credential. An explicit new enrollment generates a new ID; anonymous enrollment cannot prove that it represents the same hardware.

## Public enrollment and capacity

Automatic enrollment is intentionally public. It does not provide hardware attestation or verified ownership. The Worker requires operator configuration and uses transactional Durable Object state for signup limits, credential checks, destination scope, revocation, send quotas and deduplication. Approximate ingress rate limiting adds another layer.

Default limits are 5 new installations per IP/day, 100 per relay/day, 1,000 total automatic installations including revoked IDs, 8 destinations per installation, 60 sends/hour/installation and 1,000 sends/day/relay. Operators can change these bounded settings. Capacity exhaustion fails closed. Daily IP hashes expire after two days and are pruned by an alarm. Event receipts expire after a day and are pruned on later sends.

An attacker can consume capacity or generate new identities. These controls limit resource use; they do not establish physical-device identity. A stronger trust boundary would require accounts, attestation or a challenge, which is outside the one-action anonymous flow. A registered installation can only send to its own registered destinations; provider-token secrecy still matters because this phase does not prove phone ownership cryptographically.

## Protocol

| Route | Purpose |
| --- | --- |
| `POST /v2/enroll` | Register installation and digest; an empty invitation selects automatic enrollment |
| `GET /v2/installation` | Recover enrollment or rotation with the saved credential |
| `PUT /v2/subscriptions/{id}` | Register or replace a scoped destination |
| `DELETE /v2/subscriptions/{id}` | Remove a destination |
| `POST /v2/sentry` | Send an allowlisted, bounded event to a registered destination |
| `POST /v2/credentials/rotate` | Activate a replacement with bounded overlap |
| `POST /v2/credentials/confirm` | Retire the old credential |
| `DELETE /v2/installation` | Revoke the installation |

Operator-only routes issue optional private invitations and revoke installations. Admin credentials never enter apps or comma. Provider responses are acceptance, not proof of display; interrupted provider responses may lead to duplicates. Camera media stays on comma. Authentication failures pause v2 delivery; they never fall back to anonymous or legacy sending.

## Setup and validation

The relay is running (see [HOSTING.md](https://github.com/dirwin31/StarPilotAndroid/blob/sentry-cloudflare/push-relay/HOSTING.md)). Remaining work is registering the iOS app and Nate's APNs key in Firebase, signed app builds and delivery tests on real phones.

Focused notification/recovery tests, Galaxy browser checks, Worker unit tests, Android Play builds/tests and iOS production-model regressions cover the implementation. The Worker was also run locally in the Cloudflare runtime against the real Firebase project. A relay rejecting one phone skips that phone instead of blocking the comma. Full Galaxy HTTP tests need Linux GUI dependencies; full iOS compilation needs upstream's newer SDK. Live delivery to phones has not been tested.
