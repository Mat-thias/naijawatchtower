# Demo script (3 minutes)

**Setup (before recording):** `make setup && make demo`, then open http://127.0.0.1:8000. Over SSH, forward port 8000 first. The demo database is deterministic, so the IDs below always match. It holds simulated records only (redacted real sheets appear only when approved, see D3).

| Time | Do this | Say |
|---|---|---|
| **0:00** | Dashboard `/`. Point at the two banners. | "Everything here is unofficial and labelled as demo data. The tool never declares results; only INEC does." |
| 0:10 | Point at the map, then change **Map layer → Verified or reviewed** and press **Show**. Point at the hatched states. | "The map shows how far sheets have been read and checked, never votes for any party. Hatched means no data yet." |
| 0:20 | Click **Edo** on the map (the panel loads beside it), then scroll to **Totals by party (simulated records only)**. | "Party totals are alphabetical, come three ways (verified only, reviewed, all digitised), and exist only for simulated records. Real sheets never enter party totals." |
| **0:35** | Open `/sheet/30`. | "This sheet came in with a figure that breaks the arithmetic." Point at the red rule. |
| 0:45 | Point at **Possible alternative reading** (Party C: 12 → 22). | "The system searched for the most probable reading that adds up. It is only a suggestion for a reviewer, never applied automatically, because a tampered digit can look like a misreading." |
| **1:00** | **Sign in → Demo accounts → citizen**. Open `/sheet/1`. | "A citizen registered to this unit can compare the figures with the sheet posted at the unit." |
| 1:10 | Choose **No, something differs**, pick **Party B**, type a number, press the button. Show the reference. Then **Track a report** with that reference. | "Every report gets a reference, and its status is public, without names or figures." |
| **1:30** | Open `/ussd-sim`. Press **Dial / Send**; type `3`, Send; type `9020102002`, Send; `1`, Send. | "Most voters don't have smartphones. Over USSD on any phone, people register to their unit…" |
| 1:45 | **Dial / Send** again; `2`, Send; then `1` (Next) until "Does this match…" appears; `1` (Yes). Point at the counter (≤ 160) and the reference. | "…read the figures and confirm or dispute them in five key presses. Every screen fits 160 characters, in English or Pidgin." |
| **2:05** | Open `/discrepancies`, set **Type → ARITHMETIC_INCONSISTENT**, **Show**, then **Export CSV**. | "Discrepancies are flags for human review, not accusations, and they export as a report, subject to INEC's written authorisation." |
| **2:25** | Open `/audit`. | "Every upload, reading, review and verification is hash-chained. Edit any past entry and this check breaks." |
| **2:40** | Back to `/about`. | "The reader runs on one local GPU. No image leaves the machine, phone numbers are stored only as salted hashes, and uncertain sheets always go to a person." |
| 3:00 | End. | |

## Optional: live reading on the GPU (`make demo-live`)

Needs an approved redacted copy in `data_redacted/`. **Sign in → Demo accounts → ingest → Upload**, choose a unit "awaiting its sheet", upload the copy. The sheet page shows "being read" and refreshes in about 10–15 seconds with the figures, confidence and checks.
