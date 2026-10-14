"""Loop 1 gauge #1: deposit-beta catch-up (JPM).

Question: is deposit-cost growth outpacing NII growth? When yes, the
rates tailwind is spent (Loop 1 inflection).

All inputs come from the local DuckDB fundamentals table (EDGAR companyfacts,
PIT-anchored). Rules applied:
- as-of selection: latest filed value per (tag, period) — revisions respected;
- flows (InterestExpenseDeposits, NII) use DISCRETE QUARTERS only
  (duration 80-100 days) — 10-Q YTD rows are excluded, no double counting;
- deposit cost rate = 4 x quarterly InterestExpenseDeposits / period-end
  Deposits (annualized, approximation: period-end not average balance — noted).

Descriptive gauge. Association only. No prediction claimed.
Run:  python src\\research\\loop1_deposit_beta.py
"""
import pathlib
import duckdb

DB = pathlib.Path(__file__).resolve().parents[2] / "data" / "db" / "chainsight.duckdb"
con = duckdb.connect(str(DB), read_only=True)

q = """
WITH pit AS (  -- latest filed value per (tag, period): the PIT as-of rule
  SELECT tag, period_start, period_end, value,
         ROW_NUMBER() OVER (PARTITION BY tag, period_start, period_end
                            ORDER BY filed_date DESC, accession DESC) rn
  FROM fundamentals
  WHERE tag IN ('InterestExpenseDeposits','InterestIncomeExpenseNet','Deposits')
    AND unit='USD'
),
flows AS (      -- discrete quarters only (80-100 day durations)
  SELECT tag, period_end, value FROM pit
  WHERE rn=1 AND period_start IS NOT NULL
    AND datediff('day', period_start, period_end) BETWEEN 80 AND 100
),
stock AS (      -- deposits are instant (period-end)
  SELECT period_end, value AS deposits FROM pit
  WHERE rn=1 AND tag='Deposits' AND period_start IS NULL
)
SELECT f1.period_end                                  AS quarter_end,
       ROUND(s.deposits/1e9, 0)                       AS deposits_bn,
       ROUND(f1.value/1e9, 2)                         AS dep_int_exp_bn,
       ROUND(400.0*f1.value/s.deposits, 2)            AS dep_cost_rate_pct,
       ROUND(f2.value/1e9, 2)                         AS nii_bn,
       ROUND(100.0*(f1.value - LAG(f1.value) OVER w) / NULLIF(LAG(f1.value) OVER w,0), 1) AS dep_cost_growth_pct,
       ROUND(100.0*(f2.value - LAG(f2.value) OVER w2) / NULLIF(LAG(f2.value) OVER w2,0), 1) AS nii_growth_pct
FROM flows f1
JOIN flows f2 ON f2.period_end=f1.period_end AND f2.tag='InterestIncomeExpenseNet'
JOIN stock s  ON s.period_end=f1.period_end
WHERE f1.tag='InterestExpenseDeposits'
WINDOW w AS (ORDER BY f1.period_end), w2 AS (ORDER BY f2.period_end)
ORDER BY f1.period_end
"""
df = con.execute(q).fetchdf()
print(df.to_string(index=False))
print("\nGAUGE RULE: catch-up quarter = dep_cost_growth_pct > nii_growth_pct")
df["catchup"] = df["dep_cost_growth_pct"] > df["nii_growth_pct"]
flips = df[df["catchup"] == True]  # noqa: E712
print("catch-up quarters:", list(flips["quarter_end"].astype(str)) or "none in sample")
con.close()
