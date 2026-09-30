"""SPIKE 4: Stooq daily prices for JPM. Checks reachability, format, history depth.
Run:  python spike_02_stooq.py
The 404 on the first attempt taught us Stooq is picky — this version sends a
browser-style User-Agent and tries two symbol formats, printing diagnostics.
"""
import io, csv, pathlib, datetime, urllib.request

RAW = pathlib.Path(__file__).resolve().parents[2] / "data" / "raw" / "stooq"
TODAY = datetime.date.today().isoformat()
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

CANDIDATES = [
    "https://stooq.com/q/d/l/?s=jpm.us&i=d",
    "https://stooq.pl/q/d/l/?s=jpm.us&i=d",
]

data = None
for url in CANDIDATES:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=60)
        body = resp.read()
        print(f"TRY {url} -> HTTP {resp.status}, {len(body)} bytes")
        text = body.decode(errors="replace")
        if text.lower().startswith("date") and len(body) > 1000:
            data, good_url = body, url
            break
        else:
            print("  response is not a price CSV, first 200 chars:")
            print("  ", text[:200].replace("\n", " | "))
    except Exception as e:
        print(f"TRY {url} -> FAILED: {type(e).__name__}: {e}")

if data is None:
    print("\nSPIKE 4 RESULT: NO-GO for now — Stooq unreachable/refusing from this machine.")
    print("Fallback path per audit spec: evaluate alternative price source before trusting anything.")
else:
    out = RAW / f"jpm_us_{TODAY}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    rows = list(csv.DictReader(io.StringIO(data.decode())))
    print(f"\nsaved {out}")
    print(f"rows: {len(rows)}, first: {rows[0]['Date']}, last: {rows[-1]['Date']}")
    print("columns:", list(rows[0].keys()))
    print("last row:", rows[-1])
    print("\nMANUAL CHECK: compare last Close to a live JPM quote (Google 'JPM stock price').")
    print("Report both numbers back.")
