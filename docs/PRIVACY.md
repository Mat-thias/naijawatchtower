# Privacy

The tool is designed to hold as little personal data as possible. It never collects voter cards (PVC/VIN), voter registers, biometrics or any personal voter record.

## Data inventory

| Data | Where | Personal? | Retention in the PoC |
|---|---|---|---|
| Phone number | **Never stored.** Only `HMAC-SHA256(SERVER_SECRET, normalised number)` in `user.phone_hash`. Not written to logs or audit payloads (tested). | Pseudonymous | Until the account is deleted |
| Self-declared polling-unit code | `user.pu_id` | Links a phone hash to a unit | Until changed or deleted |
| Role and language | `user` | No | Until deleted |
| SMS opt-in | `user.opted_in_notifications` (explicit opt-in; USSD option 5 stops it) | No | Until changed |
| Verification (confirm / dispute), optional comment | `verification` | Linked to a phone hash; **only counts are ever shown** | Life of the election record |
| Proposed corrections | `correction` | Linked to a phone hash; never shown publicly | Life of the election record |
| Reviewer's private note | Stored; only its hash enters the audit log; never shown publicly | Possibly | Life of the election record |
| Result-sheet image | `data/uploads/` (redacted copies of real sheets in the demo) | Redacted: names, signatures, phone numbers and codes blacked out | Life of the election record |
| Extracted figures | `extracted_field` | No | Life of the election record |
| Audit log | `audit_log`: role, numeric user id or "system", action, hash of the payload | Pseudonymous | Permanent (append-only) |
| OTP codes (web sign-in stub) | Memory only, 5 minutes; printed to the server log in demo mode, keyed by a hash prefix | No | 5 minutes |
| SMS outbox | `sms_outbox` (nothing is sent); text has no figures and no names | No | Demo only |

## Phone numbers (MSISDN)

- Numbers are normalised to the `234…` form and hashed with HMAC-SHA256 under `SERVER_SECRET`. The raw number is never stored or logged.
- On USSD the telecom network has already authenticated the SIM, so the callback's number replaces the OTP. On the web, a one-time code is required (simulated in the PoC).
- One account per number; one verification per account per sheet.

## Real result sheets

- Downloaded **manually** by Matthias from the public portal in a normal browser. No code ever fetches from IReV or any INEC system.
- Originals stay in `data_real/`, which git ignores (a test fails if anything there is tracked). They are never committed, published, screenshotted, sent to a hosted service, or altered.
- The model runs on a local GPU. No image ever goes to a hosted model API. Only the validated figures are stored, never the model's raw text.
- Only **redacted copies** may be shown: solid black boxes over names, signatures, phone numbers, header codes and the party-name column, with a "REDACTED COPY – NOT AN OFFICIAL DOCUMENT" strip and all metadata stripped. They stay private until the organisers confirm in writing that showing them is acceptable.
- Real party results never leave aggregate accuracy figures: the app never computes per-party totals over real sheets, because the party order is the same on every sheet.

## Deletion

Deleting a user removes the `user` row. Verifications are kept as counts and keep the numeric user id (no phone hash remains linked once the user row is gone). The audit log is append-only by design; it holds only hashes of payloads. A production deployment would add a documented retention period after the election, scheduled deletion, and a data-protection impact assessment under the Nigeria Data Protection Act 2023.

## What a breach would expose

A copy of the database plus `SERVER_SECRET` would let an attacker test candidate phone numbers against the hashes and learn which unit a number registered to and whether it confirmed or disputed a sheet. That is the main residual privacy risk; see `docs/THREAT_MODEL.md`.
