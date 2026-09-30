"""chainsight ingest: raw archive (Layer 1) -> DuckDB tables (Layer 2).

Reads the newest dated file per source from data/raw/, validates rows at the
border (bad rows are REJECTED and counted, never silently patched), and
rebuilds data/db/chainsight.duckdb from scratch.

Design rules encoded here:
- Layer 2 is always rebuildable from Layer 1 (so we drop & recreate tables).
- fundamentals carries filed_date: the day each number became public (PIT anchor).
- prices keep BOTH close (PIT price) and adj_close (total-return math).
- macro rows carry the realtime window from FRED/ALFRED (vintage discipline).

Run:  python src/ingest/build_db.py
"""
import json, pathlib, sys
import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "db" / "chainsight.duckdb"

def newest(dirpath: pathlib.Path):
    """Newest dated file in a raw dir = the latest snapshot. Older ones stay untouched."""
    files = sorted(dirpath.glob("*.*"))
    if not files:
        sys.exit(f"no raw files in {dirpath} — run the spike/pull first")
    return files[-1]

con = duckdb.connect(str(DB))
report = {}

# ---------- prices_daily (Tiingo) ----------
src = newest(RAW / "tiingo")
rows = json.loads(src.read_bytes())
good, bad = [], 0
for r in rows:
    try:
        good.append((
            "JPM", r["date"][:10], float(r["open"]), float(r["high"]),
            float(r["low"]), float(r["close"]), int(r["volume"]),
            float(r["adjClose"]), float(r["divCash"]), float(r["splitFactor"]),
            src.name,
        ))
    except (KeyError, TypeError, ValueError):
        bad += 1  # validator at the border: reject, count, move on
con.execute("DROP TABLE IF EXISTS prices_daily")
con.execute("""CREATE TABLE prices_daily(
    ticker VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
    close DOUBLE, volume BIGINT, adj_close DOUBLE,
    div_cash DOUBLE, split_factor DOUBLE, source_file VARCHAR)""")
con.executemany("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)", good)
report["prices_daily"] = (len(good), bad)

# ---------- fundamentals (EDGAR companyfacts) ----------
src = newest(RAW / "edgar" / "companyfacts_JPM")
facts = json.loads(src.read_bytes())
good, bad = [], 0
for taxonomy, tags in facts["facts"].items():
    for tag, body in tags.items():
        for unit, obs in body.get("units", {}).items():
            for o in obs:
                try:
                    good.append((
                        int(facts["cik"]), "JPM", taxonomy, tag, unit,
                        o.get("start"), o["end"], float(o["val"]),
                        o["filed"],                 # <-- PIT anchor
                        o.get("form"), o.get("fp"), o.get("fy"),
                        o.get("frame"), o["accn"], src.name,
                    ))
                except (KeyError, TypeError, ValueError):
                    bad += 1
con.execute("DROP TABLE IF EXISTS fundamentals")
con.execute("""CREATE TABLE fundamentals(
    cik INTEGER, ticker VARCHAR, taxonomy VARCHAR, tag VARCHAR, unit VARCHAR,
    period_start DATE, period_end DATE, value DOUBLE,
    filed_date DATE, form VARCHAR, fp VARCHAR, fy INTEGER,
    frame VARCHAR, accession VARCHAR, source_file VARCHAR)""")
con.executemany("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", good)
report["fundamentals"] = (len(good), bad)

# ---------- macro_series (FRED/ALFRED) ----------
good, bad = [], 0
for series_dir in sorted((RAW / "fred").iterdir()):
    if not series_dir.is_dir():
        continue
    src = newest(series_dir)
    payload = json.loads(src.read_bytes())
    for o in payload.get("observations", []):
        if o.get("value") in (".", "", None):
            bad += 1  # FRED encodes missing as "." — rejected, not zero-filled
            continue
        try:
            good.append((
                series_dir.name.split("_")[0], o["date"], float(o["value"]),
                o["realtime_start"], o["realtime_end"], src.name,
            ))
        except (KeyError, TypeError, ValueError):
            bad += 1
con.execute("DROP TABLE IF EXISTS macro_series")
con.execute("""CREATE TABLE macro_series(
    series_id VARCHAR, obs_date DATE, value DOUBLE,
    realtime_start DATE, realtime_end DATE, source_file VARCHAR)""")
con.executemany("INSERT INTO macro_series VALUES (?,?,?,?,?,?)", good)
report["macro_series"] = (len(good), bad)

# ---------- verification queries (read them — this is the payoff) ----------
print("=== ingest report (rows loaded / rejected) ===")
for t, (g, b) in report.items():
    print(f"  {t}: {g} loaded, {b} rejected")

print("\n=== prices sanity: first, last, span ===")
print(con.execute("""SELECT min(date), max(date), count(*) FROM prices_daily""").fetchall())

print("\n=== PIT demo: JPM annual net income AND the day the world learned it ===")
print(con.execute("""
    SELECT period_end, value/1e9 AS billions, filed_date, accession
    FROM fundamentals
    WHERE tag='NetIncomeLoss' AND form='10-K' AND fp='FY' AND unit='USD'
    ORDER BY period_end DESC LIMIT 5""").fetchdf().to_string(index=False))

print("\n=== macro check: latest value per series ===")
print(con.execute("""
    SELECT series_id, max(obs_date) AS latest, count(*) AS n
    FROM macro_series GROUP BY series_id ORDER BY series_id""").fetchall())

con.close()
print(f"\nDB written: {DB}")
