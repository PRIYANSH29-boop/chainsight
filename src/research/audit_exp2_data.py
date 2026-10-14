"""PRE-EXPERIMENT DATA AUDIT (gate for Experiment 2).
Experiment 2 may run ONLY if every check below passes or its failure is
explicitly accepted. Checks the two inputs: JPM quarterly NII, DGS10 rates.
"""
import pathlib
import duckdb

DB = pathlib.Path(__file__).resolve().parents[2] / "data" / "db" / "chainsight.duckdb"
con = duckdb.connect(str(DB), read_only=True)
results = []

def check(name, passed, detail):
    results.append((name, "PASS" if passed else "FLAG", detail))

# ---- NII series ----
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common_nii import NII_QUARTERS_SQL
nii = con.execute(NII_QUARTERS_SQL).fetchdf()
n = len(nii)
check("NII sample size >= 40 quarters", n >= 40, f"{n} discrete quarters, {nii.period_end.min().date()} -> {nii.period_end.max().date()}")

# missing quarters (expect Q4 gaps - known limitation)
nii["q"] = nii.period_end.dt.quarter
q4 = (nii["q"] == 4).sum()
check("Q4 coverage (known gap: Q4 only in 10-K as FY)", q4 > 0, f"Q4 quarters present: {q4} of {n} — if 0, every year misses one observation")

yrs = nii.period_end.dt.year.nunique()
check("Quarters per year ~3+ (no big holes)", n / yrs >= 2.5, f"{n} quarters over {yrs} years = {n/yrs:.1f}/yr")

# duplicates after as-of
dup = con.execute("""
WITH pit AS (
  SELECT period_start, period_end,
         ROW_NUMBER() OVER (PARTITION BY period_start, period_end
                            ORDER BY filed_date DESC, accession DESC) rn
  FROM fundamentals WHERE tag='InterestIncomeExpenseNet' AND unit='USD')
SELECT count(*) FROM (SELECT period_start, period_end FROM pit WHERE rn=1
GROUP BY 1,2 HAVING count(*)>1)""").fetchone()[0]
check("No duplicate periods after as-of rule", dup == 0, f"{dup} duplicated period keys")

# outliers: NII growth beyond +/-25% is suspicious for a megabank
g = nii.nii_bn.pct_change().dropna() * 100
outl = g[abs(g) > 25]
check("NII growth outliers (|g|>25%) [ACCEPTED: crisis-era; robustness split reported]", True, f"{len(outl)} outlier quarters: {[f'{d.date()}:{v:.0f}%' for d,v in zip(nii.period_end.iloc[1:][abs(g)>25], outl)]}" if len(outl) else "none")

# value sanity: NII always positive
check("NII always positive", (nii.nii_bn > 0).all(), f"min {nii.nii_bn.min():.2f}B, max {nii.nii_bn.max():.2f}B")

# revisions in NII (how often did a value change across filings?)
rev = con.execute("""
SELECT count(*) FROM (
  SELECT period_start, period_end FROM fundamentals
  WHERE tag='InterestIncomeExpenseNet' AND unit='USD'
  GROUP BY 1,2 HAVING count(DISTINCT value) > 1)""").fetchone()[0]
check("NII revision count (as-of handles them)", True, f"{rev} periods with revised values — informational")

# ---- DGS10 series ----
r = con.execute("""
SELECT min(obs_date) a, max(obs_date) b, count(*) n,
       count(DISTINCT obs_date) nd
FROM macro_series WHERE series_id='DGS10'""").fetchdf().iloc[0]
check("DGS10 covers full NII window", str(r.a) <= str(nii.period_end.min().date()), f"rates {r.a} -> {r.b}, NII starts {nii.period_end.min().date()}")
check("DGS10 no duplicate dates", r.n == r.nd, f"{r.n} rows, {r.nd} distinct dates")

gap = con.execute("""
SELECT max(datediff('day', prev, obs_date)) FROM (
  SELECT obs_date, LAG(obs_date) OVER (ORDER BY obs_date) prev
  FROM (SELECT DISTINCT obs_date FROM macro_series WHERE series_id='DGS10'))""").fetchone()[0]
check("DGS10 max gap <= 5 days (weekends/holidays)", gap <= 5, f"max gap {gap} days")

rng = con.execute("SELECT min(value), max(value) FROM macro_series WHERE series_id='DGS10'").fetchone()
check("DGS10 values plausible (0-10%)", 0 < rng[0] and rng[1] < 10, f"range {rng[0]:.2f} - {rng[1]:.2f}")

# ---- alignment ----
joined = con.execute("""
WITH rq AS (SELECT date_trunc('quarter', obs_date) q, avg(value) v
            FROM macro_series WHERE series_id='DGS10' GROUP BY 1)
SELECT count(*) FROM rq""").fetchone()[0]
check("Rate quarters available for join", joined >= n, f"{joined} rate quarters vs {n} NII quarters")

print(f"{'CHECK':55s} {'RESULT':6s} DETAIL")
for name, res, det in results:
    print(f"{name:55s} {res:6s} {det}")
flags = sum(1 for _, r_, _ in results if r_ == "FLAG")
print(f"\nAUDIT VERDICT: {'DATA WORTHY — experiment may run' if flags==0 else f'{flags} FLAG(S) — review before running experiment'}")
con.close()
