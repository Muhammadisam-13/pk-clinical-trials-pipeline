# Development log: problems, fixes and interview notes

Written while building, so I can explain every decision. Newest step at the bottom.

---

## Step 1: Setup and API inspection

### What we did
- Created a venv from Python 3.12 (`py -3.12 -m venv .venv`) and installed `scrapy` and `pydantic` from `requirements.txt`.
- Fetched real API responses and wrote the actual JSON paths into `docs/api_notes.md` before writing any parsing code.

### Problems and fixes
| Problem | Cause | Fix |
|---|---|---|
| `UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f` when reading a saved page | Windows' default file encoding is cp1252, but the API returns UTF-8 (names with accents etc.) | Always open files with `encoding="utf-8"` |
| Phase looked "missing" for most studies | It isn't missing. 753/1000 studies have `phases: ["NA"]` (not applicable, non-drug studies); only 96/1000 have no phase key at all | Keep `NA` as its own value; only a missing key becomes `NOT_REPORTED`. Merging them would inflate "% not reported" from ~10% to ~85% |
| Location records contain doctors' names, phones and emails | That is how the registry publishes site contacts | Never copy contacts into our tables. In `rejected.raw_json`, person fields are replaced with `"REDACTED"` |
| Contact email for the User-Agent shouldn't go into a public repo | Personal data in code | It goes in `.env` (gitignored), read by a few lines of stdlib code in `settings.py`; `.env.example` shows the format |

### Interview talking points
- **Why inspect the API first?** Field names from memory or old docs are often wrong (API v1 vs v2 differ completely). Measuring how often each field is missing tells you what the cleaning code has to handle.
- **Why a venv?** It isolates the project's packages from the system Python, so `requirements.txt` fully describes the environment and anyone can reproduce it.
- **Why no python-dotenv?** Reading `KEY=value` lines takes 8 lines of stdlib code. One less dependency to explain or keep updated.

---

## Step 2: The spider

### What we did
- `spiders/studies.py` requests `/api/v2/studies?query.locn=Pakistan&pageSize=1000`, yields one item per study (the raw dict), and follows `nextPageToken` until the API stops sending one.
- It records stats: `api/total_count` (what the API says exists), `api/pages`, `api/studies_seen`, `api/reached_last_page`.
- Tested with `-s CLOSESPIDER_PAGECOUNT=2`: 2 pages, 2000 items, API total 6490.

### Problems and fixes
| Problem | Cause | Fix |
|---|---|---|
| First test run made **0 requests** and finished instantly with no error | Scrapy 2.13+ replaced `start_requests()` with `async def start()`; Scrapy 2.20 never called our old method | Renamed it to `async def start(self)`. Lesson: a run that produces "nothing" with no error means check that the entry point is being called at all |
| Crawl stopped with `finish_reason: robotstxt_denied` | clinicaltrials.gov's robots.txt has `Disallow: /api/` for all user agents | Override **for this spider only** (`custom_settings = {"ROBOTSTXT_OBEY": False}`), keep the global setting `True`, and be extra polite (see below) |
| Risk of being rate-limited (HTTP 429) | NLM publishes no formal limit; unofficial guides say ~50 requests/min per IP | Handle 429 ourselves: exponential backoff 10→20→40→80→160 s (or the server's `Retry-After`), give up after 5 tries. Tested offline with a fake 429 response |

### Interview talking points
- **Why Scrapy for a JSON API?** It gives scheduling, delays, retries, stats and the item-pipeline structure without me writing them myself. The spider only decides *what* to request and *what* to yield.
- **How does pagination work?** `parse()` yields the studies, then, if `nextPageToken` exists, yields a new `Request` for the next page with the same callback. No token means last page.
- **Why override robots.txt? Isn't that bad?** robots.txt is aimed at generic crawlers (it stops search engines indexing endless API URLs). The API v2 is NLM's officially documented interface *for programs*. I overrode it narrowly (one spider, not globally), documented it openly, and followed the rest of the file's intent: `DOWNLOAD_DELAY = 1` matches their `Crawl-delay: 1`, one request at a time, an identified User-Agent with contact info.
- **Why pageSize 1000?** Fewer requests means less load on the server: about 7 requests instead of about 33.
- **Why `handle_httpstatus_list = [429]` and remove 429 from `RETRY_HTTP_CODES`?** By default Scrapy treats 429 as an error, or retries it immediately with no wait. I want the 429 to reach my code so I can wait first. Removing it from the built-in retry list stops two retry systems from both firing.
- **Why exponential backoff?** If the server is overloaded, retrying quickly makes it worse. Doubling the wait gives it time to recover. `Retry-After` wins when the server tells us exactly how long to wait.
- **Why `dont_filter=True` on the retry?** Scrapy drops requests for URLs it has already seen (its duplicate filter). A retry has the same URL, so it must skip that filter.
- **How do you test without hammering the server?** `-s CLOSESPIDER_PAGECOUNT=2` for real runs; the 429 path was tested offline by passing a fake `TextResponse(status=429)` to `parse()`.

---

## Step 3: Schema

### What we did
- `sql/schema.sql`: 4 tables (`studies`, `conditions`, `locations`, `rejected`) and 4 indexes, all `IF NOT EXISTS`.
- `ctgov_pk/db.py`: `connect()` opens SQLite with foreign keys on; `init_db()` runs the schema file.
- Tested: running `init_db()` twice raises no error; an omitted phase defaults to `NOT_REPORTED`; inserting a condition for a study that doesn't exist fails with `FOREIGN KEY constraint failed`.

### Problems and fixes
| Problem | Cause | Fix |
|---|---|---|
| SQLite doesn't enforce `REFERENCES` by default | Backwards-compatibility default in SQLite | `PRAGMA foreign_keys = ON` on every connection (in `connect()`) |
| `locations` has no primary key, so re-running the crawl could duplicate sites | A site has no natural unique ID (facility names can repeat or be missing) | Handled in the store pipeline: delete a study's child rows, then re-insert, in one transaction (step 4) |

### Interview talking points
- **Why separate tables for conditions and locations?** One study has many of each (one-to-many). Child tables with `nct_id` let you index and query them ("studies in Lahore") instead of searching inside a comma-separated text column. This is normalization (first normal form: one value per cell).
- **Why `PRIMARY KEY (nct_id, condition)`?** The same condition can't be listed twice for one study, so the database itself rejects duplicates.
- **Why these 4 indexes?** Each one matches an analysis query: filtering/grouping by status, by start year, by condition, and by city. In step 5 I prove each one is used with `EXPLAIN QUERY PLAN`.
- **Why store dates as TEXT?** SQLite has no date type. ISO `YYYY-MM-DD` text sorts and compares correctly, and `substr(start_date, 1, 4)` gives the year.
- **Why `start_date_partial`?** Padding `2013-06` to `2013-06-01` makes the date usable, but invents a day. The flag keeps that honest, so analyses can exclude padded dates if precision matters.

---

## Step 4.1: NormalizePipeline

### What we did
- `NormalizePipeline.process_item()` turns `{"study": <nested raw dict>}` into a flat dict whose keys match the
  `studies` columns, plus `conditions` and `locations` lists for the child tables, plus a redacted `raw_json`.
- Small helper functions (`clean_text`, `normalize_date`, `normalize_phase`, `unique_ignoring_case`, `redact_people`),
  so each rule can be explained and tested on its own.
- Tested on the 2 real stem-cell studies plus synthetic edge cases: missing phase, `NA`, phases out of order,
  year-only date, garbage date, messy and case-duplicate conditions, non-Pakistan site.

### Normalization rules (and why)
| Rule | Example | Why |
|---|---|---|
| Pad partial dates, set `start_date_partial = 1` | `2013-06` → `2013-06-01` | Makes dates sortable/comparable; the flag records that the day was invented |
| Unrecognized date passes through unchanged | `soon` → `soon` | Normalize cleans, Validate judges. Validate will reject it with a clear reason instead of us silently guessing |
| Missing phase → `NOT_REPORTED`; `["NA"]` → `NA` | | Missing and not-applicable mean different things (see step 1) |
| Several phases joined in a fixed order | `["PHASE3","PHASE2"]` → `PHASE2/PHASE3` | Same set of phases always gives the same string, so GROUP BY works |
| Unknown future phase values kept (appended, sorted) | | Never silently lose data if the API adds a new value |
| Whitespace stripped and collapsed | `"Spinal   Cord Injury "` → `"Spinal Cord Injury"` | Prevents "same" values counting as different |
| Conditions deduped within a study, ignoring case; first spelling kept | `["COVID-19","Covid-19"]` → `["COVID-19"]` | Avoids near-duplicate child rows |
| Only Pakistan locations kept; only facility/city/country | | The project is about Pakistan sites; contacts are personal data |
| Person name/phone/email in `raw_json` → `"REDACTED"` | | `raw_json` is stored for rejected rows; no personal data may be stored |

### Problems and fixes
| Problem | Cause | Fix |
|---|---|---|
| CLAUDE.md asked for "consistent casing" of conditions/sponsors, but a blanket rule (e.g. Title Case) would corrupt acronyms: `HIV` → `Hiv`, `COVID-19` → `Covid-19` | Medical names mix acronyms and words | **Measured first:** in 1000 studies there were only 2 casing-variant groups in conditions (`COVID-19`/`Covid-19`, `Prediabetes`/`PreDiabetes`) and 0 in sponsors. So: keep the original text, dedupe case-insensitively within a study, and group case-insensitively in analysis queries. No re-casing |
| Contacts appear in 3 places, not just locations | `centralContacts`, `overallOfficials` and `locations[].contacts` all hold people | `redact_people()` covers all three; checked that no `@` or phone number survives in the output |
| Keeping redacted raw JSON for *every* item costs memory | It's only needed for rejected rows | Accepted for simplicity: ~6.5k studies is small. The store pipeline doesn't save it for accepted rows |

### Interview talking points
- **What is an item pipeline?** A class with `process_item(item, spider)`. Scrapy passes each yielded item through every enabled pipeline in order of the numbers in `ITEM_PIPELINES` (lower runs first). Returning the item passes it on; raising `DropItem` stops it.
- **Why separate Normalize from Validate?** Single responsibility. Normalize *transforms* and never drops; Validate *judges*. That means every rejection happens in one place, with one reason format, and gets logged to the `rejected` table.
- **Why deep-copy before redacting?** `redact_people` must not change the original dict other code may still use. `copy.deepcopy` makes it side-effect free.
- **Why `.get(..., {})` chains everywhere?** Any module or key can be missing in real data. Chained `.get` with defaults means a missing piece becomes `None` instead of a `KeyError` crash, and Validate then decides whether `None` is acceptable.

---

## Step 4.2: ValidatePipeline

### What we did
- `StudyRecord` (pydantic model) encodes the rules from CLAUDE.md:
  - `nct_id` matches `^NCT\d{8}$`
  - `title` is a non-empty string
  - `enrollment` is >= 0 or None
  - `start_date` is a real `YYYY-MM-DD` date between 1990-01-01 and today + ~10 years, or None
- `ValidatePipeline` builds the model from each normalized item. On failure it inserts
  `(nct_id, reason, raw_json, rejected_at)` into `rejected`, increments `validate/rejected`, and raises `DropItem`.
  On success it increments `validate/accepted` and passes the item on.
- Unit-tested on 11 cases (4 valid: 2 real studies, missing enrollment, missing start date; 7 invalid: bad ID,
  empty title, negative enrollment, garbage date, 1985, 2050, two errors at once). The stats and `rejected` rows matched exactly.
- Real 2-page crawl: **2000 seen, 2000 accepted, 0 rejected.**

### Problems and fixes
| Problem | Cause | Fix |
|---|---|---|
| Validate must write to the DB, but the store pipeline doesn't exist yet | Rejections must be persisted, not just logged | Validate opens its own SQLite connection (`open_spider`/`close_spider`). It's independent of the store pipeline, so it can be tested alone |
| Re-running the crawl would pile up old rejections (the `rejected` table has no key) | Every run re-inserts the same failures | `DELETE FROM rejected` at the start of each run: the table describes the **latest run**, so its row count equals `validate/rejected` in the stats |
| pydantic's raw error is a long multi-line block | Its default `str(error)` is meant for developers | `short_reason()` flattens it into one line per field, `field: message; field: message`, which is easy to GROUP BY for "top rejection reasons" |
| An empty title reports "Input should be a valid string" rather than "too short" | Normalize turns `"   "` into `None` before Validate sees it | Accepted. The reason still names the field (`title`), and the behaviour is correct |
| 0 rejections on 2000 real studies: is validation even working? | The registry already enforces most of these rules on submission | The synthetic tests prove each rule fires. Zero real rejections is a finding, not a bug, and it goes in the README as such |

### Interview talking points
- **Why pydantic instead of if-statements?** The rules are declared in one readable class. pydantic collects *all* failures (not just the first) with the field name and message, and it's the standard tool for this in Python. It has nothing to do with FastAPI; FastAPI just uses it internally.
- **Why not drop invalid records silently?** You can't fix or even notice data problems you've thrown away. The `rejected` table keeps the reason and the (redacted) raw record, so every drop is auditable.
- **What is `from_crawler`?** Scrapy's hook for building a component. It hands us `crawler.settings` (for `SQLITE_PATH`) and `crawler.stats` (for counters), instead of the pipeline using globals.
- **What do the stats prove?** At the end: `api/studies_seen` = `validate/accepted` + `validate/rejected`, and after the dedupe step, accepted = stored + duplicates. Every record is accounted for.
- **Why does Validate check `enrollment >= 0` and not more?** Only rules we can justify go in. A study with 0 participants exists (withdrawn studies), so `>= 0`, not `> 0`.

---

## Step 4.3: DedupePipeline

### What we did
- `DedupePipeline` keeps a `set` of `nct_id`s seen in this run. A repeat increments `dedupe/duplicates` and raises
  `DropItem`; a new ID increments `dedupe/unique` and passes on.
- Unit test with 5 IDs (2 repeats): 3 passed, 2 dropped, and the stats matched.
- Real 2-page crawl: 2000 accepted, 2000 unique, 0 duplicates.

### Problems and fixes
| Problem | Cause | Fix |
|---|---|---|
| Dedupe only remembers IDs for one run; a second run starts with an empty set | The set lives in memory | By design. Two layers: the set handles repeats *within* a run, and `INSERT OR REPLACE` / upsert in SqlitePipeline handles *re-runs* (step 4.4) |
| 0 duplicates on 2 real pages: is the step needed? | The API's `pageToken` pagination is stable for a quick crawl | Keep it: it's cheap, and pagination can return repeats if records change mid-crawl or a page is retried. The full run's stats will show the real count |

### Interview talking points
- **Why dedupe after validate, not before?** Invalid records are already logged and gone, so the set only tracks IDs that will actually be stored. Either order works; this one keeps the `seen_ids` set to good records.
- **Why a set?** Membership checks are O(1) on average (hash table). About 6.5k short strings is trivial memory.
- **Why both in-memory dedupe *and* a DB upsert?** Defense in depth: the set avoids wasted DB writes and gives a clean duplicate count in the stats; the primary key and upsert guarantee correctness even across runs or if the set logic had a bug.
- **Accounting:** `api/studies_seen` = `validate/rejected` + `dedupe/duplicates` + `dedupe/unique`, and `dedupe/unique` should equal the rows written to `studies`.

---

## Step 4.4: SqlitePipeline

### What we did
- `SqlitePipeline` opens one connection in `open_spider`. For each study it runs **one transaction** (`with self.conn:`):
  1. upsert the `studies` row (`INSERT ... ON CONFLICT(nct_id) DO UPDATE`)
  2. delete that study's `conditions` and `locations`
  3. insert the current ones with `executemany`

  It commits and closes in `close_spider`, and counts `sqlite/stored`.
- It returns a tiny `{"nct_id": ...}` item, so Scrapy's logs and feeds don't carry the whole record.
- Tests:
  - **2-page crawl twice on a fresh DB:** identical counts both times
    (studies 2000, conditions 3099, locations 2317, rejected 0), so it is idempotent.
  - **Rollback:** an item whose 2nd condition is `None` fails on `NOT NULL`. Afterwards 0 study rows and
    0 condition rows remain, so the half-written study was fully rolled back.

### Problems and fixes
| Problem | Cause | Fix |
|---|---|---|
| CLAUDE.md suggested `INSERT OR REPLACE`, but it would break on re-runs | In SQLite, `REPLACE` *deletes* the conflicting row and inserts a new one. With `PRAGMA foreign_keys = ON`, deleting a study that still has conditions/locations is a foreign-key violation | Use a true upsert: `ON CONFLICT(nct_id) DO UPDATE SET col = excluded.col`. It updates in place and never deletes the parent |
| `locations` has no primary key, so re-inserting would duplicate sites | Sites have no reliable unique ID | Delete-then-insert a study's child rows inside the same transaction. A re-run replaces them instead of appending |
| A crash between the parent insert and the child inserts could leave a half-written study | Several statements per study | `with self.conn:` makes them one transaction: commit on success, rollback on any exception (verified by the rollback test) |

### Interview talking points
- **What does "idempotent" mean here?** Running the crawl once or ten times leaves the database in the same state. It's proven by running twice and comparing row counts in every table.
- **What is `excluded`?** In an upsert, `excluded.col` means "the value from the row I tried to insert". So `title = excluded.title` overwrites the old title with the freshly pulled one.
- **Why update instead of skipping existing studies?** Registry records change (status goes RECRUITING → COMPLETED). Updating keeps the DB current; `pulled_at` shows when each row was last refreshed.
- **Why one connection for the whole crawl?** Opening a connection per item is slow. `open_spider`/`close_spider` are Scrapy's lifecycle hooks for exactly this kind of setup and teardown.
- **Why `?` placeholders?** Parameterized queries: values never get pasted into SQL text, which prevents SQL injection and quoting bugs (titles contain apostrophes).
- **Why does the pipeline return only `{"nct_id": ...}`?** The final pipeline's return value is what Scrapy logs/exports. The full record (including raw JSON) would bloat logs for no benefit.
