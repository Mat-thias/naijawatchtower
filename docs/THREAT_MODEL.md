# Threat model

NaijaWatchTower is an **unofficial** proof of concept. It digitises photographed polling-unit result sheets, checks their arithmetic and lets people at a unit confirm or dispute what was recorded. It never declares results. This document lists what it protects, who might attack or misuse it, what is in place, and what risk remains.

## Assets

| Asset | Why it matters |
|---|---|
| Extracted figures and their link to the source image | The whole value of the tool is that every figure can be checked against its image. |
| Image SHA-256 recorded at ingest | Detects a later swap of the image behind a figure. |
| Audit log | Shows who did what and when; must be tamper-evident. |
| Phone-number hashes and unit registrations | Personal data; could expose who checks which unit. |
| Real result-sheet originals | Contain officials' and agents' names, signatures and phone numbers, and real party results. |
| Reviewer decisions and reason codes | Determine what a sheet finally says. |

## Actors

- **Honest citizens and accredited observers** registered to their own unit.
- **Brigaders / Sybil attackers**: many phone numbers trying to dispute correct sheets or confirm wrong ones.
- **A malicious uploader** with ingest rights, or someone who steals those rights.
- **A forger** who alters a sheet before it is photographed.
- **Network attackers** spoofing USSD callbacks or hijacking sessions.
- **Partisan actors** trying to use the tool for persuasion or intimidation.
- **Curious insiders** with database access.

## Abuse cases, mitigations and residual risk

| Abuse case | Mitigation in the PoC | Residual risk |
|---|---|---|
| **Sybil / brigading**: many accounts dispute a correct sheet or confirm a wrong one | One account per phone hash; one verification per account per sheet; only the self-declared unit can be verified; rate limits (10 verifications per user per hour, 60 requests per IP per minute); observers weigh 3, citizens 1; any observer dispute marks a sheet disputed; a burst of ≥ 5 verifications from accounts under 1 hour old within 10 minutes is flagged for review (not blocked). Disputes only send a sheet to a reviewer; they never change figures. Simulation (`results/system_metrics.json`): 2 attackers mark a correct sheet disputed; 3 can verify a wrong sheet with no observer present; with one honest observer present a wrong sheet is never verified. | Phone numbers are cheap; registration is self-declared and cannot prove presence at the unit. Citizen checks are a **signal, not proof**. Real deployment needs observer accreditation and SIM-age or carrier signals. |
| **Fake or manipulated uploads** | Only the `ingest` role can upload; type and size checks (JPEG/PNG, ≤ 8 MB); metadata stripped; SHA-256 recorded and audited at ingest; every figure stays linked to its image for public comparison. | A compromised ingest account can upload a doctored photo. The image hash proves which image was read, not that it is genuine. Cross-checking with other copies of the sheet (party agents, observers) is out of scope. |
| **Image tampering after ingest** | Hash recorded in the audit chain at upload; the chain detects any edit to past entries (`/audit/verify`, tested). | An attacker with full database and file access could rewrite everything from a chosen point onward; anchoring the chain head externally (e.g. publishing it periodically) is future work. |
| **A forged sheet that adds up** | Arithmetic rules R1–R7 and constraint-aware decoding flag sheets whose figures do not add up. | A forger who edits all totals **consistently** is not caught by arithmetic. That is what citizen and observer comparison with the posted sheet is for. |
| **Reading errors presented as facts** | Per-field confidence; uncertain, unclear or rule-failing sheets always go to a person; repair suggestions are never applied automatically; every page says UNOFFICIAL; even automatically accepted sheets show as "digitised, unverified". | A confident misreading of a sheet that still adds up could pass as AUTO_OK until citizens or observers check it. |
| **Replay of verification requests** | Unique constraint per user and sheet; repeats return "Already recorded" with the original reference; USSD handling is idempotent. | None significant within the PoC. |
| **Spoofed USSD callbacks / SIM swap** | Shared-secret header `X-USSD-Secret` (401 if missing or wrong); the simulator adds it server-side so it never reaches a browser. | In production the secret must become the aggregator's request signature plus an IP allowlist. SIM swap lets an attacker act as a victim's number; the impact is one verification per sheet. |
| **Session hijack** | Signed, expiring session tokens; `HttpOnly`, `SameSite=Lax` cookies; demo quick sign-in only while `NWT_DEMO_MODE=1`. | No CSRF tokens and no HTTPS in the local PoC (D38). Real deployment needs TLS, CSRF tokens and turning demo sign-in off. |
| **Privacy leakage** | Phone numbers stored only as HMAC-SHA256 with a server secret (tests grep the database file and logs for raw numbers); verifier identities never shown, only counts; audit payloads stored as hashes; no voter cards, registers or biometrics. | Unit-level registration still links a phone hash to a unit; a database leak plus the server secret would allow re-identification by brute force over the phone-number space. |
| **Incomplete redaction of real sheets** | Solid black boxes only (never blur); invented text never written onto a sheet; metadata stripped; Matthias reviews every copy at full size; the reader compares original and redacted copies to catch covered figures; redacted copies stay private until the organisers confirm in writing (D3). | A missed name or phone number in a published copy. Mitigated only by careful human review. |
| **Intimidation from unit-level notifications** | SMS text carries no figures and no names; opt-in is explicit and can be stopped (USSD option 5); nothing is sent in the PoC. | In a tense area, receiving any election-related SMS could expose a person. Real deployment needs a local risk assessment. |
| **Shared phones** | One account per number; a shared phone means one verification per sheet for the household. | Under-represents people who share phones. |
| **Prompt injection through sheet content** | Output is forced by a token-level grammar: the model can emit only digits, `null` and field names into a fixed JSON skeleton, so text on a sheet cannot change the output's structure; the prompt treats all sheet text as data. Tested with text added beside a real sheet: schema valid, no shift to zeros, one field perturbed (`docs/ASSUMPTIONS.md`). | Visible text can still nudge individual digit readings; such sheets usually fail arithmetic checks and go to review. |
| **Misuse of trust features for persuasion** | Same content for every user; no turnout nudges, no targeting by unit, ward or party; the map never colours or ranks by party; party totals only for simulated records; wording tests scan every template and page. | A third party could screenshot figures out of context. Every page carries the UNOFFICIAL banner to limit this. |
| **Partisan reading of results** | Parties sorted alphabetically; no "winner/leading/projected" wording (tested); real sheets never enter party totals. | None within the PoC. |
| **Model supply chain** | Open-weight model pinned by commit hash; runs locally; no image ever sent to a hosted API; no network calls at runtime. | Trust in the published model weights. |

## Out of scope for the PoC

Load testing, denial-of-service resistance, key management (secrets come from environment variables with insecure development defaults that print a warning), backups, and any integration with INEC systems (none exists; the discrepancy export is "an exportable report, subject to INEC's written authorisation").
