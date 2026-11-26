"""Item pipelines, run in this order (see ITEM_PIPELINES in settings.py):

    NormalizePipeline -> ValidatePipeline -> DedupePipeline -> SqlitePipeline

Each item from the spider is {"study": <raw study dict from the API>}.
"""

import copy
import json
import re
from datetime import datetime, timezone

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
