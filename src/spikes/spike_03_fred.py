"""SPIKE 5: FRED/ALFRED. Needs free API key in ../.env (FRED_API_KEY).
Register: https://fredaccount.stlouisfed.org/apikeys
Run:  python spike_03_fred.py
Checks: key works, DGS10 series pulls, and ALFRED vintage params respond.
"""
import json, os, pathlib, datetime, urllib.request
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env", override=True)
KEY = os.environ.get("FRED_API_KEY", "")
assert KEY and len(KEY) == 32, "FRED_API_KEY missing or malformed in .env"

RAW = pathlib.Path(__file__).resolve().parents[2] / "data" / "raw" / "fred"
TODAY = datetime.date.today().isoformat()

def get(params, name):
    url = "https://api.stlouisfed.org/fred/series/observations?" + params + f"&api_key={KEY}&file_type=json"
    data = urllib.request.urlopen(url, timeout=60).read()
    out = RAW / name / f"{TODAY}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return json.loads(data)

d = get("series_id=DGS10&observation_start=2025-01-01", "DGS10_current")
print("DGS10 current obs:", len(d["observations"]), "latest:", d["observations"][-1])

v = get("series_id=CPIAUCSL&observation_start=2025-01-01&realtime_start=2025-06-01&realtime_end=2025-06-01",
        "CPIAUCSL_vintage_2025-06-01")
print("CPI as known on 2025-06-01:", v["observations"][-1])
print("\nFRED + ALFRED vintage access PASSED")
