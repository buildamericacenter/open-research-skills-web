import tempfile
import unittest
from pathlib import Path

from npv_dcf import parse_discount_rate, read_cash_flows, run_analysis


class NpvDcfTests(unittest.TestCase):
    def test_parse_discount_rate_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assumptions.md"
            path.write_text("Discount rate: 12.5%", encoding="utf-8")
            self.assertAlmostEqual(parse_discount_rate(path), 0.125)

    def test_run_analysis_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cash_flows = base / "cash_flows.csv"
            assumptions = base / "assumptions.md"
            expected = base / "expected_results.csv"
            output = base / "out"

            cash_flows.write_text(
                "period,inflows,outflows\n"
                "0,0,100000\n"
                "1,40000,5000\n"
                "2,45000,5000\n"
                "3,50000,5000\n"
                "4,50000,5000\n",
                encoding="utf-8",
            )
            assumptions.write_text("Discount rate: 10%\n", encoding="utf-8")
            expected.write_text("metric,expected\nnpv,29420.80\n", encoding="utf-8")

            result = run_analysis(cash_flows, assumptions, expected, output)

            self.assertAlmostEqual(result.discount_rate, 0.10)
            self.assertAlmostEqual(result.npv, 29420.80, places=2)
            self.assertIsNotNone(result.irr)
            self.assertTrue((output / "npv_results.csv").exists())
            self.assertTrue((output / "validation_report.md").exists())
            self.assertEqual(result.validation[0]["status"], "Pass")

    def test_read_cash_flows_with_explicit_net(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cash_flows.csv"
            path.write_text("period,net_cash_flow\n0,-100\n1,120\n", encoding="utf-8")
            rows = read_cash_flows(path, 0.10)
            self.assertEqual(rows[0].net_cash_flow, -100)
            self.assertEqual(rows[1].net_cash_flow, 120)


if __name__ == "__main__":
    unittest.main()
