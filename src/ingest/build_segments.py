"""Load JPM segment facts into DuckDB (Round 1: Basis / Lens A).

Native-run only (SEC unreachable from cloud/VM). For each target 10-K:
  fetch FilingSummary.xml -> locate the Business Segments (Details) report
  -> archive the raw R-file dated under data/raw/edgar/segments_JPM/ (Layer 1,
  append-only) -> parse with segment_extractor -> load segment_facts (Layer 2).

Targets: FY2025 10-K (3SEG regime, carries restated FY2024/FY2023) and the
FY2023 10-K (original 4SEG regime) so both regimes coexist with their own
filed dates — the PIT rule for segments made concrete.

Run:  python src\\ingest\\build_segments.py
"""
import datetime
import pathlib
import sys

import duckdb
from dotenv import load_dotenv

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
load_dotenv(ROOT / ".env", override=True)  # SEC_USER_AGENT for fetch_text

import segment_extractor as se  # noqa: E402

RAW = ROOT / "data" / "raw" / "edgar" / "segments_JPM"
DB = ROOT / "data" / "db" / "chainsight.duckdb"
TODAY = datetime.date.today().isoformat()
CIK = 19617

TARGETS = [  # (accession, filed_date) — both verified in prior audits
    ("0001628280-26-008131", "2026-02-13"),  # FY2025 10-K, 3SEG + restated comps
    ("0000019617-24-000225", "2024-02-16"),  # FY2023 10-K, original 4SEG
]

all_rows = []
for accn, filed in TARGETS:
    base = "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (CIK, accn.replace("-", ""))
    fs = se.fetch_text(base + "FilingSummary.xml")
    rep = se.locate_segment_report(fs)
    if not rep:
        sys.exit("no segment report located for %s" % accn)
    url = base + rep
    html = se.fetch_text(url)
    out = RAW / ("%s_%s_%s.htm" % (accn, rep.replace(".htm", ""), TODAY))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():               # Layer 1: append-only, never overwrite
        out.write_text(html, encoding="utf-8")
    rows, warns = se.parse_r_file(html, accn, filed, url)
    print("%s -> %s : %d rows, warnings: %s" % (accn, rep, len(rows), warns or "none"))
    all_rows.extend(rows)

con = duckdb.connect(str(DB))
con.execute("DROP TABLE IF EXISTS segment_facts")
con.execute("""CREATE TABLE segment_facts(
    cik INTEGER, company VARCHAR, segment VARCHAR, member VARCHAR,
    dimension VARCHAR, consolidation_role VARCHAR, segment_regime VARCHAR,
    metric VARCHAR, label VARCHAR, value_millions BIGINT, scale VARCHAR,
    unit VARCHAR, value_base BIGINT, period_start DATE, period_end DATE,
    filed_date DATE, accession VARCHAR, source_reference VARCHAR, notes VARCHAR)""")
cols = ["cik", "company", "segment", "member", "dimension", "consolidation_role",
        "segment_regime", "metric", "label", "value_millions", "scale", "unit",
        "value_base", "period_start", "period_end", "filed_date", "accession",
        "source_reference", "notes"]
con.executemany(
    "INSERT INTO segment_facts VALUES (%s)" % ",".join("?" * len(cols)),
    [[r[c] for c in cols] for r in all_rows])

print("\n=== segment_facts loaded:", con.execute("SELECT count(*) FROM segment_facts").fetchone()[0], "rows ===")

print("\n=== RECONCILIATION (from DB): segments+corporate+reconciling vs consolidated ===")
print(con.execute("""
    WITH parts AS (
      SELECT metric, period_end, segment_regime, SUM(value_millions) AS part_sum
      FROM segment_facts WHERE consolidation_role <> 'consolidated'
      GROUP BY 1,2,3),
    cons AS (
      SELECT metric, period_end, segment_regime, value_millions AS cons_val
      FROM segment_facts WHERE consolidation_role = 'consolidated')
    SELECT p.metric, p.period_end, p.segment_regime, p.part_sum, c.cons_val,
           CASE WHEN p.part_sum = c.cons_val THEN 'OK' ELSE 'FAIL' END AS recon
    FROM parts p JOIN cons c USING (metric, period_end, segment_regime)
    WHERE p.metric IN ('total_net_revenue','pretax_income','net_income')
    ORDER BY p.metric, p.period_end""").fetchdf().to_string(index=False))

print("\n=== LENS A — operating mix, FY2025 (denominator = consolidated, flow) ===")
print(con.execute("""
    SELECT segment, metric, value_millions,
           ROUND(100.0*value_millions / (SELECT value_millions FROM segment_facts
             WHERE consolidation_role='consolidated' AND metric=sf.metric
               AND period_end='2025-12-31'), 1) AS pct_of_consolidated,
           filed_date, accession
    FROM segment_facts sf
    WHERE period_end='2025-12-31' AND consolidation_role <> 'consolidated'
      AND metric IN ('total_net_revenue','pretax_income','net_income')
    ORDER BY metric, value_millions DESC""").fetchdf().to_string(index=False))

print("\n=== PIT DEMO: FY2023 revenue mix — what was knowable when ===")
print(con.execute("""
    SELECT segment_regime, filed_date, segment, value_millions
    FROM segment_facts
    WHERE metric='total_net_revenue' AND period_end='2023-12-31'
      AND consolidation_role='operating'
    ORDER BY filed_date, value_millions DESC""").fetchdf().to_string(index=False))
con.close()
print("\nDB written:", DB)
