"""Item pipelines, run in this order (see ITEM_PIPELINES in settings.py):

    NormalizePipeline -> ValidatePipeline -> DedupePipeline -> SqlitePipeline

Each item from the spider is {"study": <raw study dict from the API>}.
"""

import copy
import json
import re
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field, ValidationError, field_validator
from scrapy.exceptions import DropItem

from ctgov_pk.db import connect, init_db

# Fixed order used when a study lists several phases, so ["PHASE3", "PHASE2"] -> "PHASE2/PHASE3".
PHASE_ORDER = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]

# Person fields replaced before a raw study is stored anywhere (rejected.raw_json).
PERSON_FIELDS = {"name", "phone", "phoneExt", "email"}


def clean_text(value):
    """Strip ends and collapse internal whitespace. Returns None for empty or missing values."""
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def normalize_date(raw):
    """Pad partial dates: '2021' -> '2021-01-01', '2021-03' -> '2021-03-01'.

    Returns (date, was_padded). Anything else is returned unchanged so ValidatePipeline
    can reject it with a clear reason instead of us guessing here.
    """
    raw = clean_text(raw)
    if raw is None:
        return None, 0
    if re.fullmatch(r"\d{4}", raw):
        return f"{raw}-01-01", 1
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        return f"{raw}-01", 1
    return raw, 0


def normalize_phase(phases):
    """Missing -> NOT_REPORTED. ['NA'] stays 'NA' (not applicable). Several -> joined with '/'."""
    if not phases:
        return "NOT_REPORTED"
    known = [p for p in PHASE_ORDER if p in phases]
    unknown = sorted(p for p in phases if p not in PHASE_ORDER)  # keep anything new rather than lose it
    return "/".join(known + unknown)


def unique_ignoring_case(values):
    """Clean each value and drop repeats that differ only in case, keeping the first spelling."""
    seen, result = set(), []
    for v in values:
        v = clean_text(v)
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            result.append(v)
    return result


def redact_people(study):
    """Copy of the study with names/phones/emails of contacts and officials replaced by 'REDACTED'."""
    study = copy.deepcopy(study)
    module = study.get("protocolSection", {}).get("contactsLocationsModule", {})
    people = module.get("centralContacts", []) + module.get("overallOfficials", [])
    for loc in module.get("locations", []):
        people += loc.get("contacts", [])
    for person in people:
        for field in PERSON_FIELDS & person.keys():
            person[field] = "REDACTED"
    return study


class NormalizePipeline:
    """Flatten the nested API JSON into one flat study dict plus child lists."""

    def process_item(self, item, spider=None):
        study = item["study"]
        p = study.get("protocolSection", {})
        ident = p.get("identificationModule", {})
        status = p.get("statusModule", {})
        design = p.get("designModule", {})

        start_date, partial = normalize_date(status.get("startDateStruct", {}).get("date"))

        # Keep only Pakistan sites, and only facility/city/country (never contacts).
        locations = []
        for loc in p.get("contactsLocationsModule", {}).get("locations", []):
            if clean_text(loc.get("country")) == "Pakistan":
                locations.append({
                    "facility": clean_text(loc.get("facility")),
                    "city": clean_text(loc.get("city")),
                    "country": "Pakistan",
                })

        return {
            "nct_id": clean_text(ident.get("nctId")),
            "title": clean_text(ident.get("briefTitle")),
            "status": clean_text(status.get("overallStatus")),
            "study_type": clean_text(design.get("studyType")),
            "phase": normalize_phase(design.get("phases")),
            "start_date": start_date,
            "start_date_partial": partial,
            "enrollment": design.get("enrollmentInfo", {}).get("count"),
            "sponsor": clean_text(p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name")),
            "conditions": unique_ignoring_case(p.get("conditionsModule", {}).get("conditions", [])),
            "locations": locations,
            "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw_json": json.dumps(redact_people(study), ensure_ascii=False),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

EARLIEST_START = date(1990, 1, 1)


class StudyRecord(BaseModel):
    """What a valid, normalized study looks like. Only fields with rules are listed."""

    nct_id: str = Field(pattern=r"^NCT\d{8}$")
    title: str = Field(min_length=1)
    enrollment: int | None = Field(default=None, ge=0)
    start_date: str | None = None

    @field_validator("start_date")
    @classmethod
    def start_date_in_range(cls, value):
        if value is None:
            return value
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"not a valid YYYY-MM-DD date: {value!r}")
        latest = date.today() + timedelta(days=3653)  # ~10 years ahead
        if not EARLIEST_START <= parsed <= latest:
            raise ValueError(f"{value} is outside {EARLIEST_START} .. {latest}")
        return value


def short_reason(error: ValidationError):
    """Turn pydantic's error into one readable line, e.g. 'nct_id: String should match pattern ...'."""
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())


class ValidatePipeline:
    """Check each normalized study. Bad ones go to the `rejected` table, then are dropped."""

    def __init__(self, db_path, stats):
        self.db_path = db_path
        self.stats = stats

    @classmethod
    def from_crawler(cls, crawler):
        # Scrapy calls this to build the pipeline; it gives us settings and the stats collector.
        return cls(crawler.settings.get("SQLITE_PATH"), crawler.stats)

    def open_spider(self, spider=None):
        self.conn = connect(self.db_path)
        init_db(self.conn)
        # `rejected` describes the latest run only, so its row count matches this run's stats.
        self.conn.execute("DELETE FROM rejected")
        self.conn.commit()

    def close_spider(self, spider=None):
        self.conn.commit()
        self.conn.close()

    def process_item(self, item, spider=None):
        try:
            StudyRecord(**item)
        except ValidationError as error:
            reason = short_reason(error)
            self.conn.execute(
                "INSERT INTO rejected (nct_id, reason, raw_json, rejected_at) VALUES (?, ?, ?, ?)",
                (item.get("nct_id"), reason, item.get("raw_json"),
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            self.conn.commit()
            self.stats.inc_value("validate/rejected")
            raise DropItem(f"{item.get('nct_id')} rejected: {reason}")

        self.stats.inc_value("validate/accepted")
        return item


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------

class DedupePipeline:
    """Drop a study if the same nct_id already passed through in this run.

    This only covers one run. Re-runs are made safe by the upsert in SqlitePipeline.
    """

    def __init__(self, stats):
        self.stats = stats
        self.seen_ids = set()  # set lookup is O(1), fine for ~6.5k ids

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.stats)

    def process_item(self, item, spider=None):
        nct_id = item["nct_id"]
        if nct_id in self.seen_ids:
            self.stats.inc_value("dedupe/duplicates")
            raise DropItem(f"duplicate {nct_id} in this run")
        self.seen_ids.add(nct_id)
        self.stats.inc_value("dedupe/unique")
        return item


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

STUDY_COLUMNS = ["nct_id", "title", "status", "study_type", "phase", "start_date",
                 "start_date_partial", "enrollment", "sponsor", "pulled_at"]

# Upsert: insert a new study, or update the existing row in place if nct_id is already there.
# (INSERT OR REPLACE would delete the old row first, which the foreign keys from child tables forbid.)
UPSERT_STUDY = f"""
INSERT INTO studies ({", ".join(STUDY_COLUMNS)})
VALUES ({", ".join("?" for _ in STUDY_COLUMNS)})
ON CONFLICT(nct_id) DO UPDATE SET
  {", ".join(f"{c} = excluded.{c}" for c in STUDY_COLUMNS if c != "nct_id")}
"""


class SqlitePipeline:
    """Write each accepted study (parent row + child rows) to SQLite in one transaction."""

    def __init__(self, db_path, stats):
        self.db_path = db_path
        self.stats = stats

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.get("SQLITE_PATH"), crawler.stats)

    def open_spider(self, spider=None):
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def close_spider(self, spider=None):
        self.conn.commit()
        self.conn.close()

    def process_item(self, item, spider=None):
        nct_id = item["nct_id"]
        # `with conn:` commits if the block succeeds and rolls back if anything raises,
        # so a study is never stored with only half of its child rows.
        with self.conn:
            self.conn.execute(UPSERT_STUDY, [item[c] for c in STUDY_COLUMNS])

            # Children: replace them wholesale so re-runs never duplicate them.
            self.conn.execute("DELETE FROM conditions WHERE nct_id = ?", (nct_id,))
            self.conn.execute("DELETE FROM locations WHERE nct_id = ?", (nct_id,))
            self.conn.executemany(
                "INSERT INTO conditions (nct_id, condition) VALUES (?, ?)",
                [(nct_id, c) for c in item["conditions"]],
            )
            self.conn.executemany(
                "INSERT INTO locations (nct_id, facility, city, country) VALUES (?, ?, ?, ?)",
                [(nct_id, loc["facility"], loc["city"], loc["country"]) for loc in item["locations"]],
            )
        self.stats.inc_value("sqlite/stored")
        return {"nct_id": nct_id}  # keep Scrapy's log/feeds small; the data is in the DB now
