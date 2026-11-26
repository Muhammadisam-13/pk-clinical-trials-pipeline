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
