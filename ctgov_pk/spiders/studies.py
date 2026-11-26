"""Spider that pages through the ClinicalTrials.gov API v2 for studies with a site in Pakistan.

This reads a JSON API (no HTML parsing). Each yielded item is one raw study dict;
all cleaning happens later in the item pipelines.

robots.txt note: clinicaltrials.gov's robots.txt disallows /api/ for generic crawlers.
This spider uses NLM's documented public API v2 and therefore overrides robots.txt
for this spider only, with a 1s delay, single concurrency and an identified user agent.
"""

import asyncio
from urllib.parse import urlencode

import scrapy

API_URL = "https://clinicaltrials.gov/api/v2/studies"

MAX_429_RETRIES = 5
BASE_BACKOFF_SECONDS = 10  # waits 10, 20, 40, 80, 160 s


class StudiesSpider(scrapy.Spider):
    name = "studies"
    allowed_domains = ["clinicaltrials.gov"]

    custom_settings = {
        # Only this spider ignores robots.txt (see module docstring). Global setting stays True.
        "ROBOTSTXT_OBEY": False,
        # 429 is handled in parse() with backoff, so remove it from Scrapy's instant retries.
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 522, 524, 408],
    }
    # Let 429 responses reach parse() instead of being treated as errors.
    handle_httpstatus_list = [429]

    page_size = 1000  # API maximum, so the whole crawl is only ~7 requests

    def build_url(self, page_token=None):
        params = {
            "query.locn": "Pakistan",
            "pageSize": self.page_size,
            "format": "json",
            "countTotal": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        return f"{API_URL}?{urlencode(params)}"

    async def start(self):
        # Scrapy 2.13+ entry point (replaces the older start_requests()).
        yield scrapy.Request(self.build_url(), callback=self.parse)

    async def parse(self, response):
        if response.status == 429:
            retry_request = await self.backoff_on_429(response)
            if retry_request:
                yield retry_request
            return

        data = response.json()

        # The API reports how many studies match; we keep it to compare with our DB count later.
        if "totalCount" in data:
            self.crawler.stats.set_value("api/total_count", data["totalCount"])

        studies = data.get("studies", [])
        self.crawler.stats.inc_value("api/pages", 1)
        self.crawler.stats.inc_value("api/studies_seen", len(studies))

        for study in studies:
            yield {"study": study}

        # Pagination: keep following nextPageToken until the API stops sending one.
        next_token = data.get("nextPageToken")
        if next_token:
            yield scrapy.Request(self.build_url(next_token), callback=self.parse)
        else:
            self.crawler.stats.set_value("api/reached_last_page", True)

    async def backoff_on_429(self, response):
        """Wait (exponential backoff, or the server's Retry-After) and return a retry request."""
        attempt = response.meta.get("retry_429", 0) + 1
        self.crawler.stats.inc_value("api/http_429")
        if attempt > MAX_429_RETRIES:
            self.logger.error("Giving up after %d rate-limit retries: %s", MAX_429_RETRIES, response.url)
            return None

        retry_after = response.headers.get("Retry-After", b"").decode()
        wait = int(retry_after) if retry_after.isdigit() else BASE_BACKOFF_SECONDS * 2 ** (attempt - 1)
        self.logger.warning("HTTP 429, waiting %ss (attempt %d/%d)", wait, attempt, MAX_429_RETRIES)
        await asyncio.sleep(wait)

        return response.request.replace(dont_filter=True, meta={**response.meta, "retry_429": attempt})
