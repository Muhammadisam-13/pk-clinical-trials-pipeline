# PIPELINE_GUIDE.md — Understand what you're building

Read this before you start, then keep it open while Claude Code builds. Goal: by the end you can explain every box below without looking at the code.

## 1. The big picture

```
ClinicalTrials.gov API (JSON, paginated)
        |
        v
  [Spider]  requests page -> yields raw study dicts -> follows nextPageToken
        |
        v   (each study is an "item" flowing through pipelines, in order)
  [Normalize]  flatten nested JSON, fix dates, fill missing phase, tidy text
        |
        v
  [Validate]   pydantic rules; bad records -> `rejected` table + DropItem
        |
        v
  [Dedupe]     drop repeated nct_id within the run
        |
        v
  [Store]      SQLite: studies / conditions / locations (+ indexes)
        |
        v
  SQL analysis -> numbers for the README
```

One sentence version for interviews: *"A Scrapy spider pages through the ClinicalTrials.gov v2 API for Pakistan studies; item pipelines normalize, validate and dedupe each record, rejected records are kept with reasons, and clean data lands in an indexed relational schema."*

## 2. Scrapy in 5 minutes

| Piece | What it is | Where it is here |
|---|---|---|
| **Spider** | Class that decides which URLs to request and how to turn responses into items | `spiders/studies.py` |
| **Request / Response** | Scrapy fetches asynchronously; each response goes to a callback (`parse`) | pagination happens by yielding a new Request |
| **Item** | A record you `yield`; here just a dict holding the raw study | `items.py` / yielded dict |
| **Item pipeline** | Ordered chain of classes; every item passes through each. A pipeline can modify it, pass it on, or `raise DropItem` | `pipelines.py` |
| **Settings** | Politeness and wiring: user agent, delay, autothrottle, pipeline order | `settings.py` |
| **Stats** | Scrapy counts things (items scraped, dropped, requests). You'll use these for your README numbers | printed at end of run |

Key mental model: **the spider should be dumb, the pipelines should be smart.** Extraction = "get the data out". Cleaning = "make it correct". Keeping them separate makes each testable.

Pipeline order is set by numbers in `ITEM_PIPELINES` (lower runs first), e.g. Normalize 100, Validate 200, Dedupe 300, Store 400.

## 3. Concepts you should be able to explain

**Pagination with a token (not page numbers).** The API returns `nextPageToken`; you send it back to get the next page. Why tokens? The server decides the position, so results stay consistent even while data changes. Loop ends when no token comes back.

**Normalize vs. validate vs. dedupe.** Three different jobs:
- *Normalize* = change the data into a standard shape (pad `2021-03` to `2021-03-01`, trim spaces).
- *Validate* = check the data obeys rules (ID format, non-negative enrollment) and reject what doesn't.
- *Dedupe* = make sure the same entity appears once.

**Why keep rejected records.** Silently dropping hides data-quality problems. A `rejected` table with a reason lets you measure quality ("3% rejected, mostly bad dates") and fix rules later. This is the main thing that distinguishes a pipeline from a script.

**Missing is not the same as bad.** A missing phase is common in this registry, so we store `NOT_REPORTED` instead of rejecting. Deciding *what to reject vs. what to keep with a flag* is a real data-engineering judgment. Be ready to defend your choices.

**Partial dates.** Registries often give only year+month. Padding to the 1st makes it sortable, but it invents precision, so we keep `start_date_partial = 1`. Never destroy the information that something was an assumption.

**Idempotency.** Running the pipeline twice must give the same database, not double rows. Primary key on `nct_id` + upsert (`INSERT OR REPLACE`) achieves this.

**Normalized schema (one-to-many).** A study has many conditions and many locations. Putting them in child tables avoids stuffing lists into one column and makes queries like "top conditions" simple. Trade-off: more joins.

**Indexes.** An index is a sorted lookup structure on a column, making filters/group-bys on it faster at the cost of slower writes and extra space. We index the columns we actually filter on (`status`, `start_date`, `condition`, `city`). You can prove it with `EXPLAIN QUERY PLAN`.

**Politeness.** Descriptive user agent, delay, autothrottle, obey robots.txt. Scraping responsibly is part of the job and an easy interview point.

**Attribution/terms.** The source asks to be credited. Always record where data came from and when you pulled it (`pulled_at`).

## 4. Self-check questions (answer out loud before you finish)

1. Why does the spider yield raw dicts instead of cleaned ones?
2. What happens to an item when a pipeline raises `DropItem`? What did we do so we don't lose that information?
3. Why is `nct_id` a good primary key? What breaks if two records share one?
4. If you run the spider twice, why don't you get duplicate rows?
5. Which queries benefit from the `city` index, and how would you prove it?
6. What does `start_date_partial` protect against?
7. What would change if the source had 50 million records instead of ~2,500? (Think: batching inserts, memory for the dedupe set, Postgres, incremental loads.)
8. What would you add to run this daily? (Think: incremental pulls using last-update date, scheduling, monitoring rejected rate.)
9. Where is the line between "data cleaning" and "making up data"?
10. What's one thing about this dataset that surprised you?

If you can answer 1–6 without looking, you understand the pipeline. 7–9 are the "senior-ish" follow-ups interviewers like.

## 5. How to work with Claude Code (so you actually learn)

- After each step, **run it yourself** and read the output before moving on.
- Ask it: "why did you do X instead of Y?" for any line you can't explain.
- **Break something on purpose**: feed one pipeline a bad record (negative enrollment, malformed NCT ID) and watch it land in `rejected`.
- Before the final run, **predict** the row count and rejection rate, then compare. The gap is where you learn about the data.
- Don't let it add features beyond `CLAUDE.md`. Scope creep is how this stops being a 1-hour project.

## 6. Describing it honestly (resume/interview)

Accurate: "Built a Scrapy pipeline ingesting the ClinicalTrials.gov v2 API for studies sited in Pakistan; implemented normalization, pydantic validation with a rejected-records table, dedupe, and idempotent loads into an indexed relational schema."

Avoid: "web scraped" (it's an API), inflated numbers, tools you didn't use. Put the real measured stats from your own README (records ingested, rejection rate, % phase not reported) in a bullet. Concrete numbers beat adjectives.
