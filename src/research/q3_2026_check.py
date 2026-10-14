import pathlib, duckdb
DB = pathlib.Path(__file__).resolve().parents[2] / "data" / "db" / "chainsight.duckdb"
con = duckdb.connect(str(DB), read_only=True)
print("=== JPM facts for the new quarter (period ending 2026-09-30) ===")
print(con.execute("""
  SELECT tag, ROUND(value/1e9,2) AS value_bn, period_start, period_end, filed_date, form
  FROM fundamentals
  WHERE period_end='2026-09-30' AND unit='USD'
    AND tag IN ('Revenues','RevenuesNetOfInterestExpense','InterestIncomeExpenseNet',
                'NoninterestIncome','NetIncomeLoss','Deposits')
  ORDER BY tag, period_start NULLS FIRST""").fetchdf().to_string(index=False))
print()
print("=== all facts filed since 13 Oct (the earnings release) ===")
print(con.execute("""
  SELECT form, count(*) AS n_facts, min(filed_date) AS filed
  FROM fundamentals WHERE filed_date >= '2026-10-13' GROUP BY form""").fetchdf().to_string(index=False))
