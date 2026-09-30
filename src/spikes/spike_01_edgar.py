"""SPIKE 1-3: SEC EDGAR audit with JPM (CIK 0000019617) as test ticker.
Run:  python spike_01_edgar.py
Checks: no-auth access, ticker map, submissions, companyfacts,
and prints one point-in-time (PIT) example: a fact WITH its filed date.
Saves raw responses to data/raw/edgar/ (immutable archive, dated).
"""
import json, time, pathlib, datetime, urllib.request

UA = {"User-Agent": "Priyansh Patel priyansh2005p@gmail.com"}  # SEC requires a real contact UA
RAW = pathlib.Path(__file__).resolve().parents[2] / "data" / "raw" / "edgar"
TODAY = datetime.date.today().isoformat()

def get(url, name):
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    dt = time.time() - t0
    out = RAW / name / f"{TODAY}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)          # Layer 1: saved exactly as received, never edited
    print(f"  saved {out} ({len(data)/1e6:.2f} MB, {dt:.1f}s)")
    return json.loads(data)

print("SPIKE 1: ticker map (proves keyless access)")
m = get("https://www.sec.gov/files/company_tickers.json", "company_tickers")
jpm = [v for v in m.values() if v["ticker"] == "JPM"]
print("  entries:", len(m), "| JPM row:", jpm, "\n")

print("SPIKE 2: submissions (filing index = the PIT calendar)")
s = get("https://data.sec.gov/submissions/CIK0000019617.json", "submissions_JPM")
r = s["filings"]["recent"]
print("  company:", s["name"])
for form, dt_, acc in list(zip(r["form"], r["filingDate"], r["accessionNumber"]))[:5]:
    print(f"    {form:8s} filed {dt_}  {acc}")
print()

time.sleep(0.2)  # stay well under SEC's 10 req/s guidance

print("SPIKE 3: companyfacts (every reported number + its filed date)")
f = get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000019617.json", "companyfacts_JPM")
gaap = f["facts"]["us-gaap"]
print("  us-gaap tags:", len(gaap))
ni = gaap["NetIncomeLoss"]["units"]["USD"]
ann = [u for u in ni if u.get("form") == "10-K" and u.get("fp") == "FY"]
last = ann[-1]
print(f"  PIT EXAMPLE -> NetIncomeLoss period {last['start']}..{last['end']}")
print(f"    value = {last['val']:,} USD")
print(f"    FILED = {last['filed']}   <-- the day the world could know it (our PIT anchor)")
print(f"    accession = {last['accn']}")
print("\nALL EDGAR SPIKES PASSED" )
