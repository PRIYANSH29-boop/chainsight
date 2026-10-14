"""Pull FULL DGS10 history (10-year Treasury yield, daily, 1962+) from FRED.
Archives raw dated under data/raw/fred/DGS10_full/. Run natively."""
import json, os, pathlib, datetime, urllib.request
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)
KEY = os.environ["FRED_API_KEY"]
url = ("https://api.stlouisfed.org/fred/series/observations?series_id=DGS10"
       "&observation_start=2005-01-01&api_key=" + KEY + "&file_type=json")
data = urllib.request.urlopen(url, timeout=120).read()
out = ROOT / "data" / "raw" / "fred" / "DGS10_full" / (datetime.date.today().isoformat() + ".json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(data)
obs = json.loads(data)["observations"]
print("saved", out, "|", len(obs), "obs |", obs[0]["date"], "->", obs[-1]["date"])
