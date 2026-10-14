"""Layer 2 load: raw EDGAR companyfacts (data/raw/<TICKER>/) -> PostgreSQL.

Reads the newest archived file per (ticker, CIK), validates every fact at the
border (malformed rows are REJECTED and counted, never patched or zero-filled),
and rebuilds `companies` and `fundamentals` from scratch in one transaction.

Design rules encoded here:
- Layer 2 is always rebuildable from Layer 1 (so we drop & recreate tables).
- filed_date is mandatory on every row: the day the number became public (PIT anchor).
- Duplicate facts (same value re-reported in later filings) are ALL kept; no dedupe.
- value is NUMERIC, parsed from JSON as Decimal, so reported figures stay exact.
- No API calls here; run pull_companyfacts.py first.

Connection: standard libpq PG* variables (PGHOST, PGPORT, PGDATABASE, PGUSER,
PGPASSWORD) read from .env. Nothing secret lives in this file.

Run:  python src/ingest/load_fundamentals_pg.py
"""
import collections, datetime, decimal, json, os, pathlib, re, sys

import psycopg
from dotenv import load_dotenv

from pull_companyfacts import RAW, ROOT, TICKERS

# Static sector labels (GICS-style). companyfacts carries no sector field.
SECTOR = {
    "NFLX": "Communication Services", "RDDT": "Communication Services",
    "GOOGL": "Communication Services",
    "MU": "Information Technology", "AAPL": "Information Technology",
    "MSFT": "Information Technology", "NVDA": "Information Technology",
    "AMZN": "Consumer Discretionary",
    "JPM": "Financials", "GS": "Financials", "BAC": "Financials", "V": "Financials",
    "LMT": "Industrials", "RTX": "Industrials", "NOC": "Industrials", "GD": "Industrials",
    "XOM": "Energy", "CVX": "Energy",
    "FCX": "Materials", "NEM": "Materials",
}

# Expected short histories, keyed by CIK: printed as notes, not treated as errors.
SHORT_HISTORY_NOTES = {
    1713445: "RDDT IPO'd March 2024; short history is expected, not an error.",
    2115436: "ExxonMobil Holdings Corp (2026 reorg successor); pre-2026 XOM history"
             " is under CIK 34088.",
}

RAW_NAME = re.compile(r"^CIK(\d{10})_(\d{4}-\d{2}-\d{2}T\d{6}Z)\.json$")

# Tags tried in order when choosing the example fact to print per company.
EXAMPLE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "NetIncomeLoss", "Assets"]

DDL = """
DROP TABLE IF EXISTS fundamentals;
DROP TABLE IF EXISTS companies;
CREATE TABLE companies(
    cik     INTEGER PRIMARY KEY,
    ticker  TEXT NOT NULL,
    name    TEXT NOT NULL,
    sector  TEXT NOT NULL
);
CREATE TABLE fundamentals(
    cik          INTEGER NOT NULL REFERENCES companies(cik),
    taxonomy     TEXT    NOT NULL,
    tag          TEXT    NOT NULL,
    unit         TEXT    NOT NULL,
    period_start DATE,               -- NULL for instant (balance-sheet) facts
    period_end   DATE    NOT NULL,
    value        NUMERIC NOT NULL,
    filed_date   DATE    NOT NULL,   -- PIT anchor
    form         TEXT,
    accession    TEXT    NOT NULL,
    source_file  TEXT    NOT NULL
);
"""

INDEXES = """
CREATE INDEX fundamentals_cik_tag_end ON fundamentals(cik, tag, period_end);
CREATE INDEX fundamentals_filed ON fundamentals(filed_date);
"""


def newest_per_cik(ticker_dir: pathlib.Path) -> dict[int, pathlib.Path]:
    """Newest archived file per CIK in a ticker dir. Older snapshots stay untouched."""
    newest = {}
    for f in sorted(ticker_dir.glob("*.json")):
        m = RAW_NAME.match(f.name)
        if m:
            newest[int(m.group(1))] = f  # sorted by stamp, so last one wins
    return newest


def parse_fact(o: dict):
    """Validate one companyfacts observation. Returns (row_fields, None) or (None, reason)."""
    try:
        end = datetime.date.fromisoformat(o["end"])
    except (KeyError, TypeError, ValueError):
        return None, "bad/missing end"
    start = None
    if o.get("start") is not None:
        try:
            start = datetime.date.fromisoformat(o["start"])
        except (TypeError, ValueError):
            return None, "bad start"
        if start > end:
            return None, "start after end"
    val = o.get("val")
    if isinstance(val, bool) or not isinstance(val, (int, decimal.Decimal)):
        return None, "bad/missing val"
    if isinstance(val, decimal.Decimal) and not val.is_finite():
        return None, "non-finite val"
    try:
        filed = datetime.date.fromisoformat(o["filed"])
    except (KeyError, TypeError, ValueError):
        return None, "bad/missing filed"
    accn = o.get("accn")
    if not isinstance(accn, str) or not accn:
        return None, "bad/missing accn"
    return (start, end, val, filed, o.get("form"), accn), None


def main() -> None:
    load_dotenv(ROOT / ".env")
    need = ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    unset = [k for k in need if not os.environ.get(k)]
    if unset:
        sys.exit(f"missing in .env: {', '.join(unset)} (see .env.example)")

    companies, sources = [], []
    for t in TICKERS:
        files = newest_per_cik(RAW / t)
        if not files:
            sys.exit(f"no raw companyfacts in {RAW / t}; run pull_companyfacts.py first")
        for cik, f in files.items():
            sources.append((t, cik, f))

    report = {}  # cik -> (ticker, loaded, rejected Counter)
    with psycopg.connect() as con:  # one transaction: all-or-nothing rebuild
        con.execute(DDL)
        for t, cik, f in sources:
            facts = json.loads(f.read_bytes(), parse_float=decimal.Decimal)
            if int(facts["cik"]) != cik:
                sys.exit(f"{f}: file says CIK {facts['cik']}, name says {cik}")
            con.execute("INSERT INTO companies VALUES (%s,%s,%s,%s)",
                        (cik, t, facts["entityName"], SECTOR[t]))
            source_file = f.relative_to(ROOT).as_posix()
            loaded, rejected = 0, collections.Counter()
            with con.cursor().copy(
                "COPY fundamentals (cik, taxonomy, tag, unit, period_start, period_end,"
                " value, filed_date, form, accession, source_file) FROM STDIN"
            ) as cp:
                for taxonomy, tags in facts.get("facts", {}).items():
                    for tag, body in tags.items():
                        for unit, obs in body.get("units", {}).items():
                            for o in obs:
                                row, reason = parse_fact(o)
                                if reason:
                                    rejected[reason] += 1  # reject, count, move on
                                    continue
                                start, end, val, filed, form, accn = row
                                cp.write_row((cik, taxonomy, tag, unit, start, end,
                                              val, filed, form, accn, source_file))
                                loaded += 1
            report[cik] = (t, loaded, rejected)
            print(f"  loaded {t:5s} CIK{cik:010d}: {loaded:,} rows from {source_file}")
        con.execute(INDEXES)

        # ---------- verification: re-read from Postgres, not from memory ----------
        print("\n=== per-company: rows in DB / rejected / one PIT example ===")
        for t, cik, _ in sources:
            _, loaded, rejected = report[cik]
            n, first_end, last_filed = con.execute(
                "SELECT count(*), min(period_end), max(filed_date)"
                " FROM fundamentals WHERE cik = %s", (cik,)).fetchone()
            assert n == loaded, f"{t}: DB has {n}, loader wrote {loaded}"
            ex = con.execute("""
                SELECT tag, unit, period_start, period_end, value, filed_date, form, accession
                FROM fundamentals
                WHERE cik = %s AND taxonomy = 'us-gaap'
                ORDER BY COALESCE(array_position(%s::text[], tag), 999), filed_date DESC,
                         period_end DESC
                LIMIT 1""", (cik, EXAMPLE_TAGS)).fetchone()
            rej = sum(rejected.values())
            rej_detail = f" ({dict(rejected)})" if rej else ""
            print(f"{t:5s} CIK{cik:010d} {n:>8,} rows | rejected {rej}{rej_detail}"
                  f" | periods from {first_end} | last filed {last_filed}")
            if ex:
                tag, unit, ps, pe, val, filed, form, accn = ex
                span = f"{ps}..{pe}" if ps else f"as of {pe}"
                print(f"      e.g. {tag} [{unit}] {span} = {val:,}"
                      f"  FILED {filed} ({form} {accn})")
            if cik in SHORT_HISTORY_NOTES:
                print(f"      note: {SHORT_HISTORY_NOTES[cik]}")

        total = con.execute("SELECT count(*) FROM fundamentals").fetchone()[0]
        no_filed = con.execute(
            "SELECT count(*) FROM fundamentals WHERE filed_date IS NULL").fetchone()[0]
        total_rej = sum(sum(r.values()) for _, _, r in report.values())
        print(f"\nTOTAL {total:,} rows loaded, {total_rej:,} rejected,"
              f" {no_filed} rows without filed_date")


if __name__ == "__main__":
    main()
