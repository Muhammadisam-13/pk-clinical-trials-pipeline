# ClinicalTrials.gov Pakistan Pipeline

A small data pipeline built with Scrapy that ingests every clinical study with a site in Pakistan from the
**ClinicalTrials.gov API v2** (a JSON API, not HTML), then normalizes, validates and dedupes the studies and stores them in
a SQLite database with indexes.

## Source and attribution
- **Data source:** [ClinicalTrials.gov](https://clinicaltrials.gov), U.S. National Library of Medicine, API v2
  (`https://clinicaltrials.gov/api/v2/studies?query.locn=Pakistan`).
- **Data pulled:** 2026-11-26 (UTC timestamps are stored per study in `studies.pulled_at`).
- Only study registry metadata is stored. No patient data exists in the source, and contact names, phone numbers and
  emails of investigators are never stored (they are redacted even in the `rejected` table's raw JSON).

## Architecture
```
ClinicalTrials.gov API v2  (JSON, 1000 studies/page, follow nextPageToken)
          │
          ▼
  StudiesSpider ─ yields {"study": <raw dict>} per study
          │
          ▼
  NormalizePipeline   flatten JSON, pad partial dates, map phases, clean text,
          │           strip accents from city names, keep Pakistan sites only, redact contacts
          ▼
  ValidatePipeline    pydantic rules; failures -> `rejected` table + DropItem
          │
          ▼
  DedupePipeline      drop repeated nct_id within a run (in-memory set)
          │
          ▼
  SqlitePipeline      upsert study + replace its conditions/locations, one transaction per study
          │
          ▼
  data/ctgov_pk.db  ──►  sql/analysis.sql  ──►  results below
```

## How to run
```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env          # then set CTGOV_CONTACT=<your email> (used in the User-Agent)

.venv\Scripts\scrapy crawl studies -s CLOSESPIDER_PAGECOUNT=2   # quick test: 2 pages
.venv\Scripts\scrapy crawl studies                              # full run: 7 requests, ~4 min
.venv\Scripts\python sql/run_analysis.py                        # print every analysis query
```
Re-running is safe: the row counts stay the same (verified, see below).

## Schema
| Table | Rows | Contents |
|---|---|---|
| `studies` | 6490 | one row per study: `nct_id` (PK), title, status, study_type, phase, start_date, start_date_partial, enrollment, sponsor, pulled_at |
| `conditions` | 9961 | (`nct_id`, `condition`), PK on both |
| `locations` | 7516 | Pakistan sites only: `nct_id`, facility, city, country |
| `rejected` | 0 | `nct_id`, reason, redacted raw_json, rejected_at (latest run only) |

Indexes: `studies(status)`, `studies(start_date)`, `conditions(condition)`, `locations(city)`.
Each one is shown as used (`SEARCH ... USING INDEX`) by `EXPLAIN QUERY PLAN`. Full DDL: [sql/schema.sql](sql/schema.sql).

## Results
All numbers come from [sql/analysis.sql](sql/analysis.sql) run against the database from the 2026-11-26 pull.

**Studies by status**
| Status | Studies |
|---|---|
| COMPLETED | 3727 |
| UNKNOWN | 982 |
| RECRUITING | 867 |
| NOT_YET_RECRUITING | 400 |
| ACTIVE_NOT_RECRUITING | 379 |
| ENROLLING_BY_INVITATION | 100 |
| TERMINATED | 20 |
| WITHDRAWN | 15 |

**Studies per start year**
| Period | Studies |
|---|---|
| 1991–2016 | 398 |
| 2017 | 101 |
| 2018 | 159 |
| 2019 | 260 |
| 2020 | 394 |
| 2021 | 561 |
| 2022 | 625 |
| 2023 | 851 |
| 2024 | 944 |
| 2025 | 1172 |
| 2026 | 1006 |
| 2027–2028 (planned) | 18 |
| no start date | 1 |

**Top 10 cities** (distinct studies with a site there)
| City | Studies |
|---|---|
| Lahore | 2356 |
| Karachi | 1152 |
| Islamabad | 884 |
| Rawalpindi | 656 |
| Peshawar | 321 |
| Faisalabad | 294 |
| Multan | 119 |
| Sialkot | 86 |
| Abbottabad | 65 |
| Bahawalpur | 59 |

**Top 10 lead sponsors**
| Sponsor | Studies |
|---|---|
| Riphah International University | 2220 |
| Superior University | 249 |
| Foundation University Islamabad | 249 |
| Aga Khan University | 249 |
| University of Lahore | 223 |
| Dow University of Health Sciences | 168 |
| University of Faisalabad | 84 |
| King Edward Medical University | 73 |
| Lahore University of Biological and Applied Sciences | 68 |
| (individual investigator) | 60 |

The 10th sponsor is a private individual registered as sponsor-investigator. The name is in the public registry and
in the database, but it is not repeated here.

**Phase, study type and dates**
| Measure | Value |
|---|---|
| Interventional / observational | 5910 / 580 |
| Phase `NA` (not applicable, e.g. behavioural/device studies) | 4955 (76.3%) |
| Phase `NOT_REPORTED` | 580 (8.9%) |
| Phase 1 / 2 / 3 / 4 (single phase) | 89 / 161 / 217 / 311 |
| Early phase 1; phase 1/2; phase 2/3 | 64; 46; 67 |
| Start date was partial (`YYYY-MM`, padded to day 01) | 7.6% |
| Rejected by validation | 0 |

## Verification
| Check | Result |
|---|---|
| Full crawl reached the last page | 7 pages, no `nextPageToken` on the last one, 0 errors |
| DB vs API | 6490 rows in `studies` = API `totalCount` 6490 |
| seen = rejected + duplicates + stored | 6490 = 0 + 0 + 6490 (Scrapy stats) |
| Idempotency | a second full run left all four table counts unchanged |
| Indexes | `EXPLAIN QUERY PLAN` shows each index used |

## Data quality findings
- **"Missing" phase is really "not applicable".** All 580 `NOT_REPORTED` studies are observational, and 0 of 5910
  interventional studies lack a phase. Observational studies have no trial phase. Separately, 4955 studies carry the
  explicit value `NA`. The pipeline keeps `NA` and `NOT_REPORTED` apart instead of merging them.
- **Partial start dates.** 7.6% of studies give only `YYYY-MM`. They are padded to the 1st of the month and flagged
  with `start_date_partial = 1`. One study has no start date at all.
- **City names spelled two ways.** The registry writes 20 Pakistani cities both with and without diacritics
  (e.g. `Faisalabad` 145 / `Faisalābad` 151 studies). Accents are stripped from city names, which merges those
  variants. 181 distinct cities remain, with 0 non-ASCII names. Faisalabad becomes 294 studies, not 296, because 2 studies listed both spellings.
- **A study with no Pakistan site.** NCT06980805 is returned by the API's Pakistan search only because one
  Polish site is entered with `state: "Pakistan"`. Our country filter keeps that site out of `locations`; the study
  row is kept so the count matches the API.
- **Casing.** Only 2 condition names in a 1000-study sample differed by case alone (`COVID-19`/`Covid-19`), and no sponsors did.
  Original spelling is kept, conditions are deduped case-insensitively per study, and sponsors are grouped
  case-insensitively in queries. No blanket re-casing, which would corrupt acronyms like `HIV`.
- **Validation found nothing to reject.** All 6490 studies passed (valid NCT ID, non-empty title, enrollment ≥ 0, start
  date between 1990 and today + 10 years). The rules are exercised by hand-made test cases; the registry
  evidently enforces them at submission.
- **Personal data in the source.** Location contacts, central contacts and overall officials include names, phones and
  emails. None of it is copied, and it is redacted in stored raw JSON.

## Compliance note: robots.txt
robots.txt disallows `/api/` for generic crawlers. This spider uses NLM's documented public API v2 and therefore
overrides robots.txt for this spider only, with a 1s delay, single concurrency and an identified user agent.
It requests the maximum page size (1000) so a full crawl is 7 requests, and backs off exponentially on HTTP 429.
Details: [docs/api_notes.md](docs/api_notes.md).

## Known limitations
- **"Pakistan" is matched by the API's location search,** which also matches non-country fields (see NCT06980805).
- **Only one sponsor field is used:** `leadSponsor`. Collaborators are not stored.
- **Sponsor and condition names are not entity-resolved** (e.g. a university under two different names stays as two).
- **City names are only accent-normalized;** other spelling variants would remain separate.
- **The `rejected` table reflects the latest run only.**
- **No incremental updates:** each run re-fetches everything (7 requests).
- **One SQLite commit per study** makes a full run take about 4 minutes. Batching commits would speed it up.
- **Counts reflect the registry as of the pull date.** Statuses such as `UNKNOWN` (982 studies) are as reported by the registry.

More detail on every problem hit while building, and how it was solved: [docs/dev_log.md](docs/dev_log.md).
