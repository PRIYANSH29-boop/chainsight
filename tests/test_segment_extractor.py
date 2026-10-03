"""Tests for the approved JPM segment-extraction spike.
Deterministic fixtures only — no live SEC fetches (per approved scope).
Runs on stdlib unittest (also discoverable by pytest)."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "ingest"))
import segment_extractor as se  # noqa: E402

FX = ROOT / "tests" / "fixtures"
ACC25, FILED25 = "0001628280-26-008131", "2026-02-13"
ACC23, FILED23 = "0000019617-24-000225", "2024-02-16"


def load(name):
    return (FX / name).read_text()


class TestLocator(unittest.TestCase):
    def test_picks_summary_details_not_decoys(self):
        fn = se.locate_segment_report(load("filing_summary_min.xml"))
        self.assertEqual(fn, "R223.htm")  # Summary (Details) wins over Narrative/Goodwill/Loans/Tables


class TestFY2025(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.warnings = se.parse_r_file(
            load("fy2025_segments_min.htm"), ACC25, FILED25, "fixture:R223")

    def val(self, role_or_seg, metric, end):
        for r in self.rows:
            if r["metric"] == metric and r["period_end"] == end and (
                    r["consolidation_role"] == role_or_seg or r["segment"] == role_or_seg):
                return r["value_millions"]
        raise AssertionError("missing %s %s %s" % (role_or_seg, metric, end))

    def test_revenue_five_way_reconciliation_fy2025(self):
        parts = sum(self.val(s, "total_net_revenue", "2025-12-31") for s in
                    ["Consumer & Community Banking", "Commercial & Investment Bank",
                     "Asset & Wealth Management", "corporate", "reconciling"])
        self.assertEqual(parts, self.val("consolidated", "total_net_revenue", "2025-12-31"))
        self.assertEqual(parts, 182447)

    def test_pretax_and_net_income_reconciliation_fy2025(self):
        for metric, total in [("pretax_income", 72595), ("net_income", 57048)]:
            parts = sum(self.val(s, metric, "2025-12-31") for s in
                        ["Consumer & Community Banking", "Commercial & Investment Bank",
                         "Asset & Wealth Management", "corporate", "reconciling"])
            self.assertEqual(parts, total)
            self.assertEqual(parts, self.val("consolidated", metric, "2025-12-31"))

    def test_regime_members_and_provenance(self):
        self.assertTrue(all(r["segment_regime"] == "3SEG" for r in self.rows))
        cib = [r for r in self.rows if r["segment"] == "Commercial & Investment Bank"][0]
        self.assertEqual(cib["member"], "jpm_CommercialAndInvestmentBankMember")
        self.assertEqual(cib["dimension"], se.SEGMENT_AXIS)
        self.assertEqual(cib["accession"], ACC25)
        self.assertEqual(cib["filed_date"], FILED25)
        corp = [r for r in self.rows if r["consolidation_role"] == "corporate"][0]
        self.assertEqual(corp["member"], "us-gaap_CorporateNonSegmentMember")

    def test_visa_and_apple_excluded_and_no_pct_rows(self):
        blob = " ".join(r["segment"] for r in self.rows)
        self.assertNotIn("Apple", blob)
        self.assertNotIn("VISA", blob)
        self.assertFalse([r for r in self.rows if r["metric"] not in
                          ("total_net_revenue", "pretax_income", "net_income",
                           "total_assets", "net_interest_income", "noninterest_revenue")])

    def test_pct_guard_fires_on_mapped_label(self):
        # a mapped metric whose values are percentages must be skipped with a warning
        html = ("<table><tr><td>12 Months Ended</td></tr>"
                "<tr><td>Dec. 31, 2025</td></tr>"
                "<tr><td>Segment Reporting Information [Line Items]</td></tr>"
                "<tr><td>Net income</td><td>17.00%</td></tr></table>")
        rows, warnings = se.parse_r_file(html, "test-accn", "2026-01-01", "inline")
        self.assertEqual(rows, [])
        self.assertTrue(any("pct row skipped" in w for w in warnings))

    def test_scale_normalization_and_basis(self):
        cons = self.val("consolidated", "total_net_revenue", "2025-12-31")
        row = [r for r in self.rows if r["metric"] == "total_net_revenue"
               and r["consolidation_role"] == "consolidated"
               and r["period_end"] == "2025-12-31"][0]
        self.assertEqual(cons, 182447)
        self.assertEqual(row["value_base"], 182_447_000_000)
        self.assertEqual(row["scale"], "millions")
        self.assertEqual(row["period_start"], "2025-01-01")
        assets = [r for r in self.rows if r["metric"] == "total_assets"
                  and r["consolidation_role"] == "consolidated"
                  and r["period_end"] == "2025-12-31"][0]
        self.assertIsNone(assets["period_start"])  # instant basis
        self.assertEqual(assets["value_millions"], 4424900)


class TestCrossRegime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r25, _ = se.parse_r_file(load("fy2025_segments_min.htm"), ACC25, FILED25, "fixture:R223")
        cls.r23, _ = se.parse_r_file(load("fy2023_segments_min.htm"), ACC23, FILED23, "fixture:R217")

    def get(self, rows, seg, end):
        return [r for r in rows if r["segment"] == seg and r["period_end"] == end
                and r["metric"] == "total_net_revenue"][0]

    def test_old_cib_plus_cb_equals_restated_cib(self):
        old = (self.get(self.r23, "Corporate & Investment Bank", "2023-12-31")["value_millions"]
               + self.get(self.r23, "Commercial Banking", "2023-12-31")["value_millions"])
        new = self.get(self.r25, "Commercial & Investment Bank", "2023-12-31")["value_millions"]
        self.assertEqual(old, new)
        self.assertEqual(old, 64353)

    def test_asof_provenance_original_vs_restated_fy2023(self):
        orig = self.get(self.r23, "Consumer & Community Banking", "2023-12-31")
        rest = self.get(self.r25, "Consumer & Community Banking", "2023-12-31")
        self.assertEqual(orig["segment_regime"], "4SEG")
        self.assertEqual(rest["segment_regime"], "3SEG")
        self.assertEqual(orig["filed_date"], "2024-02-16")
        self.assertEqual(rest["filed_date"], "2026-02-13")
        self.assertLess(orig["filed_date"], rest["filed_date"])  # as-of ordering
        self.assertEqual(orig["accession"], ACC23)
        self.assertEqual(rest["accession"], ACC25)

    def test_old_regime_members_and_reconciliation(self):
        self.assertEqual(self.get(self.r23, "Corporate & Investment Bank", "2023-12-31")["member"],
                         "jpm_CorporateAndInvestmentBankMember")
        self.assertEqual(self.get(self.r23, "Commercial Banking", "2023-12-31")["member"],
                         "jpm_CommercialBankingMember")
        parts = sum(self.get(self.r23, s, "2023-12-31")["value_millions"] for s in
                    ["Consumer & Community Banking", "Corporate & Investment Bank",
                     "Commercial Banking", "Asset & Wealth Management",
                     "Corporate", "Reconciling Items"])
        cons = [r for r in self.r23 if r["consolidation_role"] == "consolidated"
                and r["period_end"] == "2023-12-31"][0]["value_millions"]
        self.assertEqual(parts, cons)
        self.assertEqual(parts, 158104)


class TestLiveHeaderForm(unittest.TestCase):
    """Regression tests for the live SEC R-file header structure discovered
    in the 3 Oct 2026 live validation (pipe-joined single-cell headers with
    defref_ anchors)."""

    def test_pipe_joined_operating_header_parses(self):
        html = ("<table><tr><td>12 Months Ended</td></tr>"
                "<tr><td>Dec. 31, 2025</td></tr>"
                "<tr><td>Operating Segments | Asset & Wealth Management</td></tr>"
                "<tr><td>Segment Reporting Information [Line Items]</td></tr>"
                "<tr><td>Net income</td><td>6,522</td></tr></table>")
        rows, _ = se.parse_r_file(html, "a", "2026-02-13", "inline")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["segment"], "Asset & Wealth Management")
        self.assertEqual(rows[0]["member"], "jpm_AssetandWealthManagementSegmentMember")
        self.assertEqual(rows[0]["consolidation_role"], "operating")

    def test_pipe_joined_one_off_member_excluded(self):
        html = ("<table><tr><td>12 Months Ended</td></tr>"
                "<tr><td>Dec. 31, 2025</td></tr>"
                "<tr><td>Operating Segments | Consumer & Community Banking | "
                "Apple Credit Card Portfolio</td></tr>"
                "<tr><td>Segment Reporting Information [Line Items]</td></tr>"
                "<tr><td>Net income</td><td>2,200</td></tr></table>")
        rows, _ = se.parse_r_file(html, "a", "2026-02-13", "inline")
        self.assertEqual(rows, [])

    def test_defref_business_segment_axis_overrides_member(self):
        # display name NOT in the canon map, member supplied by the defref
        html = ("<table><tr><td>12 Months Ended</td></tr>"
                "<tr><td>Dec. 31, 2025</td></tr>"
                "<tr><td><a onclick=\"Show.showAR( this, 'defref_us-gaap_"
                "StatementBusinessSegmentsAxis=jpm_FutureSegmentMember', window );\">"
                "Operating Segments | Future Segment</a></td></tr>"
                "<tr><td>Segment Reporting Information [Line Items]</td></tr>"
                "<tr><td>Net income</td><td>1,000</td></tr></table>")
        rows, _ = se.parse_r_file(html, "a", "2026-02-13", "inline")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["member"], "jpm_FutureSegmentMember")
        self.assertEqual(rows[0]["consolidation_role"], "operating")

    def test_fy2025_fixture_defref_anchors_do_not_break_canonical_members(self):
        rows, _ = se.parse_r_file(load("fy2025_segments_min.htm"), ACC25, FILED25, "f")
        cib = [r for r in rows if r["segment"] == "Commercial & Investment Bank"]
        self.assertTrue(cib)
        self.assertEqual(cib[0]["member"], "jpm_CommercialAndInvestmentBankMember")


if __name__ == "__main__":
    unittest.main(verbosity=2)
