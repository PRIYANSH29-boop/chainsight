"""SPIKE 4b: Tiingo as candidate PRIMARY price source (Stooq = NO-GO, bot wall).
Setup: free account at https://www.tiingo.com -> API token -> add to ../.env as
  TIINGO_API_KEY=<your_token>
Run:  python spike_04_tiingo.py
Checks: auth works, JPM daily history depth, columns incl. adjusted fields,
archives raw response dated.
"""
import json, os, pathlib, datetime, urllib.request
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")
KEY = os.environ.get("TIINGO_API_KEY", "")
assert KEY, "Put TIINGO_API_KEY in .env first (free at tiingo.com)"

RAW = pathlib.Path(__file__).resolve().parents[2] / "data" / "raw" / "tiingo"
TODAY = datetime.date.today().isoformat()

url = ("https://api.tiingo.com/tiingo/daily/jpm/prices"
       "?startDate=1998-01-01&format=json")
req = urllib.request.Request(url, headers={
    "Content-Type": "application/json",
    "Authorization": f"Token {KEY}",
})
data = urllib.request.urlopen(req, timeout=120).read()
out = RAW / f"jpm_{TODAY}.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(data)

rows = json.loads(data)
print(f"saved {out} ({len(data)/1e6:.2f} MB)")
print(f"rows: {len(rows)}, first: {rows[0]['date'][:10]}, last: {rows[-1]['date'][:10]}")
print("columns:", list(rows[0].keys()))
print("last row:", {k: rows[-1][k] for k in ('date','close','adjClose','divCash','splitFactor')})
print("\nKey check: adjClose vs close differ on older rows -> dividend adjustment present.")
sample = rows[len(rows)//2]
print("mid-history sample:", {k: sample[k] for k in ('date','close','adjClose')})
print("\nMANUAL CHECK: compare last 'close' to a live JPM quote and report both.")
