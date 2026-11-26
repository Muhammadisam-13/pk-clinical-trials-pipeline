# CLAUDE.md — ClinicalTrials.gov Pakistan Pipeline

## Goal
Build a small, clean data pipeline with Scrapy that ingests all clinical studies with a site in Pakistan from the official ClinicalTrials.gov API v2, then cleans, validates, dedupes and stores them in a relational database with sensible indexes.

Time box: ~1 hour of work. Scope is deliberately small. A finished, well-explained small project beats an unfinished big one. If time runs short, cut stretch items, never the validation/rejection logic.

## Working agreement (IMPORTANT — the user must understand everything)
The user (Isam) is a final-year CS student who has done scraping before but has NOT used Scrapy. He must be able to explain every part of this pipeline in an interview. So:

1. **Teach as you build.** Before writing each component, give 2-4 sentences: what it is, why it exists, how it fits the pipeline. After writing it, point out the one or two lines that matter most.
2. **Small steps.** Build in the order of the "Build order" section below. Stop after each step, show how to run/verify it, and let the user run it himself when practical.
3. **No magic.** Prefer plain, readable code over clever abstractions. No frameworks beyond Scrapy, pydantic and the stdlib `sqlite3`.
4. **Inspect before assuming.** Do NOT trust field names from memory. In step 1, fetch a real response and write the actual JSON paths into `docs/api_notes.md` before writing parsing code.
5. **Never fabricate numbers.** Every statistic that goes in the README must come from a SQL query actually run against the produced database.
6. **Ask when ambiguous**, don't silently pick. Keep questions short.
7. **Honest framing.** This ingests a JSON API via Scrapy. Do not describe it as HTML scraping anywhere (README, comments, commit messages).

## Source and compliance
- Endpoint: `https://clinicaltrials.gov/api/v2/studies`
- Query: `query.locn=Pakistan`, `pageSize` up to a few hundred, `format=json`, paginate with `pageToken` from `nextPageToken`.
- Terms ask for attribution: README must credit ClinicalTrials.gov as the source and state the date the data was pulled.
- Be polite: set a descriptive `USER_AGENT` (project name + contact email placeholder the user fills in), `ROBOTSTXT_OBEY = True`, `DOWNLOAD_DELAY = 0.5`, `AUTOTHROTTLE_ENABLED = True`.
- No personal/patient data is involved; study registry metadata only. Do not collect contact emails or phone numbers from the records.

## Stack
Python 3.10+, Scrapy, pydantic, SQLite (via `sqlite3`). Postgres is a stretch item only.

## Project layout
```
ctgov_pk/
  CLAUDE.md
  PIPELINE_GUIDE.md        # concepts doc for the user (already provided)
  README.md                # written at the end
  docs/api_notes.md        # real JSON paths found in step 1
  scrapy.cfg
  ctgov_pk/
    settings.py
    items.py
    pipelines.py           # Normalize -> Validate -> Dedupe -> Store
    spiders/studies.py
  sql/
    schema.sql
    analysis.sql           # queries whose results feed the README
  data/                    # gitignored: sqlite db, raw dumps
```

## Build order
1. **Setup + API inspection.** Create the Scrapy project, fetch 2 studies with curl/browser, write `docs/api_notes.md` listing the real JSON path for each field below and which ones are sometimes missing.
2. **Spider.** Yield one item per study containing the raw study dict. Follow `nextPageToken` until absent. Test with `CLOSESPIDER_PAGECOUNT=2` first, then full run.
3. **Schema.** Write `sql/schema.sql` (below) and a small init function.
4. **Pipelines**, one at a time, each tested in isolation on a few records:
   1. `NormalizePipeline`
   2. `ValidatePipeline`
   3. `DedupePipeline`
   4. `SqlitePipeline`
5. **Full run + verification** (checklist below).
6. **Analysis queries + README.**
7. *Stretch (only if time remains, in this order):* keyword flag for genetics/gene-therapy/oncology/rare-disease conditions; Postgres swap; simple tests with pytest for normalize/validate.

## Fields to extract (confirm paths in step 1)
`nct_id`, `brief_title`, `overall_status`, `study_type`, `phases` (list, may be missing), `start_date` (often partial like `2021-03`), `enrollment_count`, `conditions` (list), `lead_sponsor`, `locations` (facility, city, country; keep only entries where country is Pakistan in `locations`, but it is fine to store all if simpler and documented).

## Pipeline behavior

**NormalizePipeline** — flatten nested JSON into a flat dict plus child lists.
- Partial dates: `YYYY` -> `YYYY-01-01`, `YYYY-MM` -> `YYYY-MM-01`; set `start_date_partial = 1` when padded.
- Missing phase -> `NOT_REPORTED` (do not drop the record). Multiple phases -> join with `/` in a consistent order.
- Strip whitespace, collapse internal spaces, consistent casing for conditions and sponsor names (document the rule; don't over-engineer entity resolution).
- Keep `raw_json` of the original study for the rejected table.

**ValidatePipeline** — pydantic model.
- `nct_id` matches `^NCT\d{8}$`; `brief_title` non-empty; `enrollment_count` >= 0 or None; `start_date` parseable and between 1990-01-01 and today + 10 years (or None).
- On failure: do NOT silently drop. Insert into `rejected(nct_id, reason, raw_json)` and `raise DropItem`.
- Count accepted/rejected in spider stats (`crawler.stats`).

**DedupePipeline** — drop repeats by `nct_id` (in-memory set per run). Also make DB inserts idempotent (`INSERT OR REPLACE` / upsert) so re-running the spider does not duplicate rows.

**SqlitePipeline** — open connection in `open_spider`, create schema if absent, insert parent + child rows in one transaction per item (or batch), commit/close in `close_spider`.

## Schema (`sql/schema.sql`)
```sql
CREATE TABLE IF NOT EXISTS studies (
  nct_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT,
  study_type TEXT,
  phase TEXT NOT NULL DEFAULT 'NOT_REPORTED',
  start_date TEXT,
  start_date_partial INTEGER DEFAULT 0,
  enrollment INTEGER,
  sponsor TEXT,
  pulled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conditions (
  nct_id TEXT NOT NULL REFERENCES studies(nct_id),
  condition TEXT NOT NULL,
  PRIMARY KEY (nct_id, condition)
);
CREATE TABLE IF NOT EXISTS locations (
  nct_id TEXT NOT NULL REFERENCES studies(nct_id),
  facility TEXT,
  city TEXT,
  country TEXT
);
CREATE TABLE IF NOT EXISTS rejected (
  nct_id TEXT,
  reason TEXT NOT NULL,
  raw_json TEXT,
  rejected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_studies_status ON studies(status);
CREATE INDEX IF NOT EXISTS idx_studies_start ON studies(start_date);
CREATE INDEX IF NOT EXISTS idx_conditions_condition ON conditions(condition);
CREATE INDEX IF NOT EXISTS idx_locations_city ON locations(city);
```

## Verification checklist (do all before calling it done)
- [ ] 2-page test run works; full run reaches the last page (no `nextPageToken`).
- [ ] `SELECT COUNT(*) FROM studies` is plausible vs. the count the API reports for the same query (compare against `countTotal` / the website search; explain any difference).
- [ ] accepted + rejected + deduped = total records seen (from Scrapy stats). Report the three numbers.
- [ ] Re-running the spider does not increase the row count (idempotency).
- [ ] `EXPLAIN QUERY PLAN` on one query per index shows the index is used.
- [ ] No secrets, emails or the SQLite file committed (`.gitignore`).

## Analysis queries (`sql/analysis.sql`) — results go in README
Studies by status; studies per start year; top 10 cities; top 10 sponsors; percent with `phase = 'NOT_REPORTED'`; percent with partial start date; rows rejected and the top rejection reasons.

## README requirements
Short and real: purpose, source + attribution + pull date, architecture diagram (ASCII is fine), how to run, schema summary, a results table from the analysis queries, a "data quality findings" section (what was messy and how the pipeline handled it), and known limitations. No hype, no claims that aren't backed by the data.

## Out of scope
Scheduling/orchestration, cloud deployment, dashboards, NLP on study text, scraping any site other than the API above, collecting personal contact data.
