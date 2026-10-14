"""Layer 1 pull: SEC EDGAR companyfacts for the 20-company universe.

Output: data/raw/<TICKER>/CIK<10 digits>_<UTC stamp>.json

Every response is saved exactly as received under a UTC-timestamped name and
opened in exclusive-create mode, so an existing file can never be overwritten
(append-only archive). Parsing happens later in load_fundamentals_pg.py.

Run:  python src/ingest/pull_companyfacts.py
"""
import datetime, json, pathlib, sys, time, urllib.request

UA = {"User-Agent": "Priyansh Patel priyansh2005p@gmail.com"}  # SEC requires a real contact UA
ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"

TICKERS = ["NFLX", "RDDT", "MU", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
           "JPM", "GS", "BAC", "V", "LMT", "RTX", "NOC", "GD",
           "XOM", "CVX", "FCX", "NEM"]

# Predecessor registrants whose history the SEC map no longer points at.
# XOM: 2026 holding-company reorg moved the ticker to ExxonMobil Holdings Corp
# (CIK 2115436, filings from 2026-07 only); pre-reorg history lives under
# Exxon Mobil Corp (CIK 34088). Both are pulled and kept as distinct CIKs.
PREDECESSOR_CIKS = {"XOM": [34088]}

MIN_INTERVAL = 0.1  # hard cap: never more than 10 requests/sec
_last_request = 0.0


def get(url: str) -> bytes:
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def archive(data: bytes, dirpath: pathlib.Path, prefix: str = "") -> pathlib.Path:
    """Write raw bytes to <dir>/<prefix><UTC timestamp>.json; 'xb' refuses to overwrite."""
    dirpath.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    out = dirpath / f"{prefix}{stamp}.json"
    with open(out, "xb") as f:
        f.write(data)
    return out


def main() -> None:
    print("ticker map: https://www.sec.gov/files/company_tickers.json")
    raw_map = get("https://www.sec.gov/files/company_tickers.json")
    print(f"  saved {archive(raw_map, RAW / 'edgar' / 'company_tickers')}")
    by_ticker = {v["ticker"]: v for v in json.loads(raw_map).values()}

    missing = [t for t in TICKERS if t not in by_ticker]
    if missing:
        sys.exit(f"tickers not in SEC map: {missing}")

    jobs = [(t, int(by_ticker[t]["cik_str"])) for t in TICKERS]
    jobs += [(t, cik) for t, ciks in PREDECESSOR_CIKS.items() for cik in ciks]

    failures = []
    for t, cik in jobs:
        cik10 = f"{cik:010d}"
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
        try:
            data = get(url)
            json.loads(data)  # must be valid JSON before it enters the archive
        except Exception as e:  # keep going; report at the end
            failures.append((t, repr(e)))
            print(f"  {t:5s} CIK{cik10} FAILED: {e!r}")
        else:
            out = archive(data, RAW / t, prefix=f"CIK{cik10}_")
            print(f"  {t:5s} CIK{cik10} {len(data)/1e6:6.2f} MB -> {out.relative_to(ROOT)}")
        time.sleep(0.2)  # between companies, per SEC fair-access guidance

    if failures:
        sys.exit(f"{len(failures)} pulls failed: {failures}")
    print(f"\nall {len(jobs)} companyfacts archived ({len(TICKERS)} tickers)")


if __name__ == "__main__":
    main()
