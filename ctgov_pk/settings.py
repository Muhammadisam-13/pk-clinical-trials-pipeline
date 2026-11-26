# Scrapy settings for the ctgov_pk project.
# Only the settings we actually use are listed here.

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path):
    """Tiny .env reader (KEY=value lines) so we don't need python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env_file(PROJECT_ROOT / ".env")

BOT_NAME = "ctgov_pk"
SPIDER_MODULES = ["ctgov_pk.spiders"]
NEWSPIDER_MODULE = "ctgov_pk.spiders"

# --- Politeness: identify ourselves and go slowly ---
CONTACT = os.environ.get("CTGOV_CONTACT", "contact-not-set")
USER_AGENT = f"ctgov-pk-pipeline/0.1 (student data project; contact: {CONTACT})"
ROBOTSTXT_OBEY = True
DOWNLOAD_DELAY = 1  # matches Crawl-delay: 1 in clinicaltrials.gov/robots.txt
CONCURRENT_REQUESTS_PER_DOMAIN = 1
AUTOTHROTTLE_ENABLED = True

# --- Where the SQLite database lives (data/ is gitignored) ---
SQLITE_PATH = str(PROJECT_ROOT / "data" / "ctgov_pk.db")

FEED_EXPORT_ENCODING = "utf-8"

# --- Item pipelines: lower number runs first ---
ITEM_PIPELINES = {
    "ctgov_pk.pipelines.NormalizePipeline": 100,
    "ctgov_pk.pipelines.ValidatePipeline": 200,
    "ctgov_pk.pipelines.DedupePipeline": 300,
}
