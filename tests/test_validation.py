import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import app as app_module


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_upload_dir = app_module.VALIDATION_UPLOAD_DIR
        self.original_output_dir = app_module.VALIDATION_OUTPUT_DIR
        tmp_path = Path(self.tmp.name)
        app_module.VALIDATION_UPLOAD_DIR = tmp_path / "uploads" / "validation_runs"
        app_module.VALIDATION_OUTPUT_DIR = tmp_path / "output"
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.VALIDATION_UPLOAD_DIR = self.original_upload_dir
        app_module.VALIDATION_OUTPUT_DIR = self.original_output_dir
        self.tmp.cleanup()

    def write_file(self, name: str, content: str) -> Path:
        path = Path(self.tmp.name) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_numerical_validation_passes(self):
        output = self.write_file("output.csv", "metric,value,unit,notes\nNPV,1000,USD,\nIRR,12.5,%,\nDecision,Accept,,\n")
        expected = self.write_file(
            "expected.csv",
            "metric,expected_value,unit,tolerance,notes\nNPV,1001,USD,2,\nIRR,12.5,%,0.01,\nDecision,Accept,,,\n",
        )

        result = app_module.validate_skill_output("Numerical", output, expected, "Expected Results File")

        self.assertEqual(result["status"], "Validated")
        self.assertEqual(result["total_checked"], 3)
        self.assertEqual(result["passed_count"], 3)
        self.assertTrue((app_module.VALIDATION_OUTPUT_DIR / "validation_report.md").exists())

    def test_numerical_validation_invalidates_failed_metric(self):
        output = self.write_file("output.csv", "metric,value,unit,notes\nNPV,900,USD,\nDecision,Reject,,\n")
        expected = self.write_file(
            "expected.csv",
            "metric,expected_value,unit,tolerance,notes\nNPV,1000,USD,10,\nDecision,Accept,,,\n",
        )

        result = app_module.validate_skill_output("Numerical", output, expected, "Expected Results File")

        self.assertEqual(result["status"], "Invalidated")
        self.assertEqual(result["failed_count"], 2)

    def test_numerical_validation_needs_review_for_missing_metric(self):
        output = self.write_file("output.csv", "metric,value,unit,notes\nNPV,1000,USD,\n")
        expected = self.write_file(
            "expected.csv",
            "metric,expected_value,unit,tolerance,notes\nNPV,1000,USD,0,\nIRR,12.5,%,0.1,\n",
        )

        result = app_module.validate_skill_output("Numerical", output, expected, "Expected Results File")

        self.assertEqual(result["status"], "Needs Review")
        self.assertEqual(result["missing_metrics"], ["IRR"])

    def test_other_skill_types_return_placeholder(self):
        output = self.write_file("output.txt", "coded output")
        expected = self.write_file("expected.txt", "baseline")

        result = app_module.validate_skill_output("Case Study", output, expected, "Expert Baseline")

        self.assertEqual(result["status"], "Needs Review")
        self.assertIn("coming soon", result["explanation"])

    def test_validation_route_accepts_uploads(self):
        response = self.client.post(
            "/validation",
            data={
                "skill_type": "Numerical",
                "validation_source_type": "Expected Results File",
                "skill_output_file": (BytesIO(b"metric,value,unit,notes\nNPV,100,USD,\n"), "output.csv"),
                "validation_source_file": (
                    BytesIO(b"metric,expected_value,unit,tolerance,notes\nNPV,100,USD,0,\n"),
                    "expected.csv",
                ),
            },
            content_type="multipart/form-data",
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Validated", body)
        self.assertIn("Download validation_report.md", body)


if __name__ == "__main__":
    unittest.main()
