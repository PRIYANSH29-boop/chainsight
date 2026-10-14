"""Shared SQL: JPM discrete-quarter NII including DERIVED Q4.
Q4 is never filed as a discrete quarter (it lives inside the 10-K annual
figure), so Q4 := FY - (Q1+Q2+Q3) of the same year, only when all three
quarters exist. Derived rows are tagged source='derived_q4'."""

NII_QUARTERS_SQL = """
WITH pit AS (
  SELECT period_start, period_end, value,
         ROW_NUMBER() OVER (PARTITION BY period_start, period_end
                            ORDER BY filed_date DESC, accession DESC) rn
  FROM fundamentals WHERE tag='InterestIncomeExpenseNet' AND unit='USD'),
q AS (  -- filed discrete quarters
  SELECT period_end, value/1e9 AS nii_bn, 'filed' AS source FROM pit
  WHERE rn=1 AND datediff('day', period_start, period_end) BETWEEN 80 AND 100),
fy AS (  -- full-year rows
  SELECT period_end, value/1e9 AS fy_bn FROM pit
  WHERE rn=1 AND datediff('day', period_start, period_end) BETWEEN 360 AND 370),
q4 AS (  -- derive Q4 when Q1..Q3 of that year all present
  SELECT f.period_end, f.fy_bn - SUM(q.nii_bn) AS nii_bn, 'derived_q4' AS source
  FROM fy f JOIN q ON date_trunc('year', q.period_end)=date_trunc('year', f.period_end)
  GROUP BY f.period_end, f.fy_bn HAVING count(*)=3)
SELECT * FROM q UNION ALL SELECT * FROM q4 ORDER BY period_end
"""
