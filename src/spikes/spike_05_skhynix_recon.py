"""SPIKE 5: SK Hynix data reconnaissance.
SK Hynix (KRX 000660) is Korean -> NOT in SEC EDGAR. Checks what we CAN get:
 1. Tiingo coverage of the US OTC ADR ticker HXSCL (prices).
 2. Tiingo metadata for it (depth, exchange).
Prints findings; archives nothing yet (recon only). Run natively."""
import json, os, pathlib, urllib.request
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)
KEY = os.environ["TIINGO_API_KEY"]
H = {"Content-Type": "application/json", "Authorization": "Token " + KEY}

def get(url):
    try:
        req = urllib.request.Request(url, headers=H)
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except Exception as e:
        return {"error": str(e)}

for tk in ["hxscl", "000660"]:
    meta = get("https://api.tiingo.com/tiingo/daily/" + tk)
    print(tk, "meta:", {k: meta.get(k) for k in ("ticker","name","exchangeCode","startDate","endDate")} if "error" not in meta else meta)

px = get("https://api.tiingo.com/tiingo/daily/hxscl/prices?startDate=2015-01-01&format=json")
if isinstance(px, list) and px:
    print("hxscl prices: rows=", len(px), "| first:", px[0]["date"][:10], "| last:", px[-1]["date"][:10],
          "| last close:", px[-1]["close"], "| last volume:", px[-1]["volume"])
else:
    print("hxscl prices:", px if not isinstance(px, list) else "EMPTY")
print("\nNOTE: fundamentals for SK Hynix are NOT in EDGAR. Primary source = DART")
print("(dart.fss.or.kr, Korea's filing system, free API key, IFRS statements).")
