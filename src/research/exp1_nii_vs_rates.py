"""EXPERIMENT 1 (pre-registered): does JPM's NII follow interest rates?
Pre-registered expectation: beta POSITIVE (rates up -> lending income up).
Method: quarterly discrete NII (as-of, PIT) vs change in avg DGS10 that
quarter. Plain OLS. Association only. ~65 quarters, 2009+."""
import pathlib
import duckdb
import numpy as np

DB = pathlib.Path(__file__).resolve().parents[2] / "data" / "db" / "chainsight.duckdb"
con = duckdb.connect(str(DB), read_only=True)
df = con.execute("""
WITH pit AS (
  SELECT period_start, period_end, value,
         ROW_NUMBER() OVER (PARTITION BY period_start, period_end
                            ORDER BY filed_date DESC, accession DESC) rn
  FROM fundamentals WHERE tag='InterestIncomeExpenseNet' AND unit='USD'),
nii AS (
  SELECT period_end, value/1e9 AS nii_bn FROM pit
  WHERE rn=1 AND datediff('day', period_start, period_end) BETWEEN 80 AND 100),
rate_q AS (
  SELECT date_trunc('quarter', obs_date) AS q, AVG(value) AS avg_rate
  FROM macro_series WHERE series_id='DGS10' GROUP BY 1)
SELECT n.period_end,
       n.nii_bn,
       100.0*(n.nii_bn - LAG(n.nii_bn) OVER (ORDER BY n.period_end))
            / LAG(n.nii_bn) OVER (ORDER BY n.period_end)           AS nii_growth_pct,
       r.avg_rate - LAG(r.avg_rate) OVER (ORDER BY r.q)            AS rate_change_pp
FROM nii n JOIN rate_q r ON date_trunc('quarter', n.period_end) = r.q
ORDER BY n.period_end
""").fetchdf().dropna()
con.close()

x = df["rate_change_pp"].values     # driver: quarterly change in 10Y yield (pct points)
y = df["nii_growth_pct"].values     # outcome: NII growth that quarter (%)
n = len(x)
X = np.column_stack([np.ones(n), x])
beta, alpha_resid = np.linalg.lstsq(X, y, rcond=None)[0:2]
a, b = beta
resid = y - X @ beta
se_b = np.sqrt((resid @ resid) / (n - 2) / ((x - x.mean()) ** 2).sum())
t = b / se_b
r2 = 1 - (resid @ resid) / ((y - y.mean()) ** 2).sum()
print(f"quarters used: {n}  (period {df.period_end.min().date()} -> {df.period_end.max().date()})")
print(f"BETA = {b:.2f}  -> a +1.00pp rate rise in a quarter associates with {b:.2f}% NII growth that quarter")
print(f"std error = {se_b:.2f}   t-stat = {t:.2f}   R^2 = {r2:.3f}")
print(f"PRE-REGISTERED CHECK: expected beta > 0 ->", "CONFIRMED" if b > 0 else "REJECTED")
# same-quarter vs next-quarter (does NII react with a lag?)
x_lag, y_lead = x[:-1], y[1:]
Xl = np.column_stack([np.ones(n-1), x_lag])
bl = np.linalg.lstsq(Xl, y_lead, rcond=None)[0][1]
print(f"lagged beta (rates this qtr -> NII growth NEXT qtr) = {bl:.2f}")
