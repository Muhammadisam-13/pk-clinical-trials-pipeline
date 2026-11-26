-- Schema for ClinicalTrials.gov studies with a site in Pakistan.
-- Every statement uses IF NOT EXISTS, so running this file again is safe.

-- One row per study (parent table).
CREATE TABLE IF NOT EXISTS studies (
  nct_id TEXT PRIMARY KEY,                       -- e.g. NCT02482194; the primary key makes duplicates impossible
  title TEXT NOT NULL,
  status TEXT,                                   -- e.g. COMPLETED, RECRUITING
  study_type TEXT,                               -- INTERVENTIONAL / OBSERVATIONAL / EXPANDED_ACCESS
  phase TEXT NOT NULL DEFAULT 'NOT_REPORTED',    -- PHASE1, PHASE2/PHASE3, NA (not applicable), NOT_REPORTED
  start_date TEXT,                               -- ISO YYYY-MM-DD (partial dates padded)
  start_date_partial INTEGER DEFAULT 0,          -- 1 if the API only gave YYYY or YYYY-MM
  enrollment INTEGER,
  sponsor TEXT,
  pulled_at TEXT NOT NULL                        -- when we fetched it (UTC ISO timestamp)
);

-- Many conditions per study (child table).
CREATE TABLE IF NOT EXISTS conditions (
  nct_id TEXT NOT NULL REFERENCES studies(nct_id),
  condition TEXT NOT NULL,
  PRIMARY KEY (nct_id, condition)
);

-- Many sites per study (child table). No contact details are ever stored here.
CREATE TABLE IF NOT EXISTS locations (
  nct_id TEXT NOT NULL REFERENCES studies(nct_id),
  facility TEXT,
  city TEXT,
  country TEXT
);

-- Records that failed validation: kept, not silently dropped.
-- raw_json has person names/phones/emails replaced with "REDACTED".
CREATE TABLE IF NOT EXISTS rejected (
  nct_id TEXT,
  reason TEXT NOT NULL,
  raw_json TEXT,
  rejected_at TEXT NOT NULL
);

-- Indexes for the columns we filter/group on in sql/analysis.sql.
CREATE INDEX IF NOT EXISTS idx_studies_status ON studies(status);
CREATE INDEX IF NOT EXISTS idx_studies_start ON studies(start_date);
CREATE INDEX IF NOT EXISTS idx_conditions_condition ON conditions(condition);
CREATE INDEX IF NOT EXISTS idx_locations_city ON locations(city);
