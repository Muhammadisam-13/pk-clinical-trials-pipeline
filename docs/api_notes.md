# ClinicalTrials.gov API v2: notes from inspection

Inspected on 2026-11-26 by fetching real responses (not from memory).

## Request
```
GET https://clinicaltrials.gov/api/v2/studies?query.locn=Pakistan&pageSize=1000&format=json
```
- Response top level: `totalCount` (only when `countTotal=true`), `studies` (list), `nextPageToken` (absent on the last page).
- Next page: same URL plus `&pageToken=<nextPageToken>`.
- `totalCount` for `query.locn=Pakistan` on 2026-11-26: **6490**.
- The response is UTF-8. On Windows, files must be opened with `encoding="utf-8"` (the cp1252 default crashed).

## Field paths
Every study is `{"protocolSection": {...}, "derivedSection": {...}, "hasResults": bool}`.
All paths below are relative to `protocolSection`.

| Our field | JSON path | Example (NCT02482194) |
|---|---|---|
| nct_id | `identificationModule.nctId` | `NCT02482194` |
| brief_title | `identificationModule.briefTitle` | `Autologous Mesenchymal Stem Cells Transplantation for Spinal Cord Injury- ...` |
| overall_status | `statusModule.overallStatus` | `COMPLETED` |
| study_type | `designModule.studyType` | `INTERVENTIONAL` |
| phases | `designModule.phases` (list) | `["PHASE1"]` |
| start_date | `statusModule.startDateStruct.date` | `2013-06` (partial!) |
| enrollment_count | `designModule.enrollmentInfo.count` | `9` |
| conditions | `conditionsModule.conditions` (list) | `["Spinal Cord Injury"]` |
| lead_sponsor | `sponsorCollaboratorsModule.leadSponsor.name` | `National Institute of Blood and Marrow Transplant (NIBMT), Pakistan` |
| locations | `contactsLocationsModule.locations[]` → `facility`, `city`, `country` | `Armed Forces Bone Marrow Transplant Centre, Rawalpindi, Pakistan` |

Sample studies (saved in `data/raw/`, gitignored):
- NCT02482194: mesenchymal stem cells for spinal cord injury, Phase 1
- NCT05610865: adipose-derived stem cells for diabetic foot ulcers, Phase 1

## What is messy (measured on the first page, 1000 studies)
- **phases missing** in 96 of 1000 studies (the key is absent). These become `NOT_REPORTED`.
- **phases = `["NA"]`** in 753 of 1000 studies. This means "not applicable" (non-drug studies), which is different from missing.
- **Multiple phases** exist, e.g. `["PHASE2","PHASE3"]` (15 of 1000). These get joined to `PHASE2/PHASE3`.
- **Partial start dates**: 88 of 1000 are `YYYY-MM`; the other 912 are `YYYY-MM-DD`. No `YYYY`-only dates on this page, but we handle them anyway.
- **enrollment count missing** in 2 of 1000.
- **Non-Pakistan locations**: 45 of 1000 studies are multinational, with sites in other countries too.
- **Personal data is present in locations**: `locations[].contacts[]` holds names, phones and emails. We only copy `facility`, `city` and `country`, and we never store the contacts.

## Decisions
- `phases = ["NA"]` is stored as `NA` (not applicable). Only a missing phase becomes `NOT_REPORTED`.
- Before a study's `raw_json` goes into the `rejected` table, the person fields in `contactsLocationsModule`
  (`centralContacts`, `overallOfficials`, `locations[].contacts`) have their name/phone/email
  replaced with `"REDACTED"`. The structure stays the same, but no personal data is stored.
- The contact email for the User-Agent comes from `CTGOV_CONTACT` in `.env` (gitignored). See `.env.example`.

## robots.txt and rate limits
`https://clinicaltrials.gov/robots.txt` (checked 2026-11-26) contains `User-agent: *`, `Disallow: /api/` and `Crawl-delay: 1`.

robots.txt disallows /api/ for generic crawlers. This spider uses NLM's documented public API v2 and therefore
overrides robots.txt **for this spider only** (`custom_settings = {"ROBOTSTXT_OBEY": False}` in
`spiders/studies.py`). The global `ROBOTSTXT_OBEY` stays `True`. In exchange, the spider is deliberately gentle:
- `DOWNLOAD_DELAY = 1` (matches their `Crawl-delay: 1`), one request at a time, AutoThrottle on.
- An identified `USER_AGENT` with a contact address.
- `pageSize=1000` (the API maximum), so the full crawl is ~7 requests instead of ~33.
- HTTP 429 (rate limited): exponential backoff (10, 20, 40, 80, 160 s, or the server's `Retry-After`), max 5 retries.
  NLM doesn't publish a formal rate limit; unofficial guides mention roughly 50 requests/minute per IP, and we stay far below that.
- While debugging, use `-s CLOSESPIDER_PAGECOUNT=2` instead of repeating the full crawl.
