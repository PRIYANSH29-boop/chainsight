"""EXPERIMENT 2 (pre-registered 14 Oct 2026, run only after audit PASS).
H: JPM quarterly NII growth follows DGS10 changes with a ONE-QUARTER LAG.
Pass criteria: beta > 0 AND |t| >= 2.0 on the FULL sample (headline).
Robustness: same test excluding pre-2010 (crisis era). Lags 0-4 shown
for context (exploratory)."""
import pathlib, sys
import duckdb
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common_nii import NII_QUARTERS_SQL

DB = HERE.parents[1] / "data" / "db" / "chainsight.duckdb"
con = duckdb.connect(str(DB), read_only=True)
df = con.execute(f"""
WITH nii AS ({NII_QUARTERS_SQL}),
rate_q AS (SELECT date_trunc('quarter', obs_date) q, AVG(value) avg_rate
           FROM macro_series WHERE series_id='DGS10' GROUP BY 1)
SELECT n.period_end,
       100.0*(n.nii_bn - LAG(n.nii_bn) OVER (ORDER BY n.period_end))
            / LAG(n.nii_bn) OVER (ORDER BY n.period_end) AS nii_g,
       r.avg_rate - LAG(r.avg_rate) OVER (ORDER BY r.q)  AS dr
FROM nii n JOIN rate_q r ON date_trunc('quarter', n.period_end) = r.q
ORDER BY n.period_end""").fetchdf().dropna()
con.close()

def ols(x, y):
    n = len(x)
    X = np.column_stack([np.ones(n), x])
    c = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ c
    se = np.sqrt((resid @ resid) / (n - 2) / ((x - x.mean()) ** 2).sum())
    return c[1], se, c[1] / se, 1 - (resid @ resid) / ((y - y.mean()) ** 2).sum(), n

def run(sample, label):
    x, y = sample["dr"].values, sample["nii_g"].values
    print(f"\n--- {label}: {len(x)} quarters ({sample.period_end.min().date()} -> {sample.period_end.max().date()}) ---")
    print("lag  beta   stderr  t-stat  R2")
    out = {}
    for k in range(5):
        xk = x[:-k] if k else x
        yk = y[k:]
        b, se, t, r2, n = ols(xk, yk)
        out[k] = (b, t)
        mark = "  <-- PRE-REGISTERED" if k == 1 else ""
        print(f"  {k}  {b:6.2f}  {se:5.2f}  {t:6.2f}  {r2:.3f}{mark}")
    b, t = out[1]
    print(f"lag-1 verdict (beta>0 and |t|>=2): {'CONFIRMED' if b>0 and abs(t)>=2 else 'NOT CONFIRMED'}")
    return out[1]

hb, ht = run(df, "HEADLINE full sample")
rb, rt = run(df[df.period_end >= "2010-01-01"], "ROBUSTNESS ex-crisis (2010+)")
print(f"\nFINAL: headline beta={hb:.2f} (t={ht:.2f}) | robustness beta={rb:.2f} (t={rt:.2f})")
