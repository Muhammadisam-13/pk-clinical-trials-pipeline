-- Analysis queries. Every number in README.md comes from one of these.
-- Run all of them with:  .venv\Scripts\python sql/run_analysis.py

-- 1. Studies by status
SELECT status, COUNT(*) AS studies
FROM studies
GROUP BY status
ORDER BY studies DESC;

-- 2. Studies per start year (padded partial dates count toward their year)
SELECT substr(start_date, 1, 4) AS start_year, COUNT(*) AS studies
FROM studies
GROUP BY start_year
ORDER BY start_year;

-- 2b. Same, with everything before 2017 grouped (as shown in the README)
SELECT CASE WHEN start_date IS NULL THEN 'no date'
            WHEN start_date < '2017' THEN '1991-2016'
            ELSE substr(start_date, 1, 4) END AS period,
       COUNT(*) AS studies
FROM studies
GROUP BY period
ORDER BY period;

-- 4b. Share of all studies led by the top sponsor
SELECT ROUND(100.0 * MAX(n) / (SELECT COUNT(*) FROM studies), 1) AS pct_top_sponsor
FROM (SELECT COUNT(*) AS n FROM studies GROUP BY lower(sponsor));

-- 3. Top 10 cities (number of distinct studies with a site there)
SELECT city, COUNT(DISTINCT nct_id) AS studies
FROM locations
GROUP BY city
ORDER BY studies DESC
LIMIT 10;

-- 4. Top 10 lead sponsors (grouped case-insensitively, see docs/dev_log.md step 4.1)
SELECT MIN(sponsor) AS sponsor, COUNT(*) AS studies
FROM studies
GROUP BY lower(sponsor)
ORDER BY studies DESC
LIMIT 10;

-- 5. Studies by phase, with percentages (shows NOT_REPORTED vs NA separately)
SELECT phase, COUNT(*) AS studies,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM studies), 1) AS pct
FROM studies
GROUP BY phase
ORDER BY studies DESC;

-- 6. Percent with phase = NOT_REPORTED
SELECT ROUND(100.0 * SUM(phase = 'NOT_REPORTED') / COUNT(*), 1) AS pct_phase_not_reported
FROM studies;

-- 7. Percent with a partial (padded) start date, and with no start date at all
SELECT ROUND(100.0 * SUM(start_date_partial = 1) / COUNT(*), 1) AS pct_partial_start_date,
       SUM(start_date IS NULL) AS no_start_date
FROM studies;

-- 8. Rows rejected and top rejection reasons
SELECT COUNT(*) AS rejected_rows FROM rejected;

SELECT reason, COUNT(*) AS rows
FROM rejected
GROUP BY reason
ORDER BY rows DESC
LIMIT 10;

-- 9. Study type split
SELECT study_type, COUNT(*) AS studies
FROM studies
GROUP BY study_type
ORDER BY studies DESC;

-- 10. Totals
SELECT (SELECT COUNT(*) FROM studies) AS studies,
       (SELECT COUNT(*) FROM conditions) AS condition_rows,
       (SELECT COUNT(*) FROM locations) AS pakistan_site_rows,
       (SELECT MIN(pulled_at) FROM studies) AS first_pulled_at,
       (SELECT MAX(pulled_at) FROM studies) AS last_pulled_at;
