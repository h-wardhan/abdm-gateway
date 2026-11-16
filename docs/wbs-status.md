# WBS status, Days 1-21 (as of 13 Nov 2026)

Honest split: **built and tested** (automated, 1039 tests pass), **verified live on the ABDM sandbox**, and **open**
(needs a person, a decision, or i-medicus code). Nothing here is committed or deployed yet.

| Day | Item | Built + tested | Live on sandbox | Open |
|---|---|---|---|---|
| 1-5 | Foundation: architecture, auth, retries, callbacks, encryption, audit | yes (`docs/architecture.md`) | yes (VPS service, nginx, callbacks) | client sign-off, Week-1 review (not code), CI at deploy time |
| 6-7 | M1 ABHA creation, login, profile, card, QR | yes | login (ABHA address and number), profile, card, QR ran live; creation not (needs a real Aadhaar) | - |
| 8 | Patient linking, Scan & Share | yes | clinic QR scanned by the ABHA app; `patient/share` callback reached the gateway; Scan & Share tested live on the sandbox facility (confirmed by the team, 13 Nov 2026) | - |
| 9-11 | M2 discovery, linking, consent notification, HI-request ack | yes | linking and consent notify ran against the sandbox (12 Nov) | - |
| 12-13 | FHIR Bundles (8 record types), validation | yes | real i-medicus payloads converted | - |
| 14 | Keys, encrypt, push to HIU, status notify, retry, trail | yes (`test_m2_transfer.py`) | local runs recorded in `docs/m2-demo-script.md` | repeat on the sandbox with a second facility id |
| 15 | M2 end to end, failure cases, docs | yes (`test_m2_end_to_end.py`, `test_m2_full_flow.py`) | - | sandbox cases (expired consent, HIU reject), demo to i-medicus |
| 16 | M3 consent request, notify, status | yes (`test_m3_hiu.py`, consent status route) | consent status asked on the sandbox (ABDM-1080 for an expired transaction seen) | - |
| 17 | Consent fetch, HI request, **expired consent blocked** | yes (`test_m3_hiu_state.py`) | - | - |
| 18 | Receive, decrypt, parse; transaction status | decrypt route + `on-request` transaction tracking; bundle check via `POST /fhir/validate` | not run live | partial or multi-page pushes and parsing happen in the i-medicus receiver (not on the gateway) |
| 19 | Secure delivery, access control | decrypt refuses the records of an ended consent when it is given a `transactionId` or `consentId`; a request for an ended consent is stopped before ABDM (Day 17) | - | decision: no change in i-medicus, so the decrypt guard stays dormant (it only matters for a late or replayed push); erasure of received records is out of scope by decision (the records live in i-medicus, which is not changed) |
| 20 | Integration, tracing | all i-medicus ABDM calls are path-only swaps; every call traced in `api_audit_logs` | - | decision: `get-session`, `abhaSission` and `getAPI` stay as they are in i-medicus |
| 21 | Security validation | `docs/m1-security-checklist.md`; masking and audit fixes; production pins private-URL escapes off; start-up warnings for weak settings | log query-string check on the server | decision: hard-coded ABDM secrets stay (already in use); production `.env` values set at deploy: `PUSH_ALLOW_HTTP=false`, `OUTBOX_HTTP_TIMEOUT=60`, `CALLBACK_AUTH_MODE=jwt`, `SHARE_QR_BASE_URL`; delete `src/.env.bak-before-m2-local` |

## Deploy checklist (after "commit")

1. Commit gateway and i-medicus changes (not `.env.bak-before-m2-local`).
2. VPS: `git pull`, `pip install -r requirements.txt`, `flask --app wsgi db upgrade` (migrations 0006, 0007 and 0008), restart app and worker.
3. VPS SQL: `UPDATE api_audit_logs SET request_body = NULL WHERE route LIKE '/api/v1/fhir/%';`
4. Production `.env`: `PUSH_ALLOW_HTTP=false`, `OUTBOX_HTTP_TIMEOUT=60`, `CALLBACK_AUTH_MODE=jwt`, `SHARE_QR_BASE_URL` (host from ABDM).
