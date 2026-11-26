# ClinicalTrials.gov Pakistan Pipeline

A Scrapy pipeline that ingests every clinical study with a site in Pakistan from the
ClinicalTrials.gov API v2 (a JSON API), then cleans, validates and dedupes the studies and stores them in SQLite.

> Work in progress. Results, schema and run instructions will be added once the pipeline is complete.

## Source and attribution
Data source: [ClinicalTrials.gov](https://clinicaltrials.gov), U.S. National Library of Medicine (API v2).
Pull date: to be filled in after the final run.

## Compliance note: robots.txt
robots.txt disallows `/api/` for generic crawlers. This spider uses NLM's documented public API v2 and therefore
overrides robots.txt for this spider only, with a 1s delay, single concurrency and an identified user agent.
It requests the maximum page size (1000) to keep the crawl to a handful of requests, and backs off exponentially on
HTTP 429. Details are in [docs/api_notes.md](docs/api_notes.md).
