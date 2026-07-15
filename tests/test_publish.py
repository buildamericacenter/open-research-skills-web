import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

import app as app_module


SKILL_MD = b"""---
name: reusable-coding-skill
description: Codes research documents.
research_type: Qualitative Analysis
author: Researcher
version: 1.0
license: MIT
---

# Reusable Coding Skill

## Purpose
Extract evidence.

## Inputs
Documents.

## Outputs
Coding table.

## Validation
Compare with benchmark coding.
"""


class PublishSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_data_dir = app_module.DATA_DIR
        self.original_index = app_module.SUBMITTED_SKILLS_INDEX
        self.original_upload_dir = app_module.SUBMITTED_SKILLS_UPLOAD_DIR
        tmp_path = Path(self.tmp.name)
        app_module.DATA_DIR = tmp_path / "data"
        app_module.SUBMITTED_SKILLS_INDEX = app_module.DATA_DIR / "submitted_skills.json"
        app_module.SUBMITTED_SKILLS_UPLOAD_DIR = tmp_path / "uploads" / "submitted_skills"
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.DATA_DIR = self.original_data_dir
        app_module.SUBMITTED_SKILLS_INDEX = self.original_index
        app_module.SUBMITTED_SKILLS_UPLOAD_DIR = self.original_upload_dir
        self.tmp.cleanup()

    def test_publish_requires_package_upload(self):
        response = self.client.post("/publish", data={"action": "preview"}, content_type="multipart/form-data")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 400)
        self.assertIn("No file is uploaded.", body)

    def test_publish_rejects_invalid_file_type(self):
        response = self.client.post(
            "/publish",
            data={"action": "preview", "skill_package": (BytesIO(b"bad"), "notes.txt")},
            content_type="multipart/form-data",
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Uploaded file must be .zip or .md.", body)

    def test_skill_md_upload_shows_extracted_preview(self):
        response = self.client.post(
            "/publish",
            data={"action": "preview", "skill_package": (BytesIO(SKILL_MD), "SKILL.md")},
            content_type="multipart/form-data",
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Extracted Skill Information", body)
        self.assertIn("reusable-coding-skill", body)
        self.assertIn("Qualitative Analysis", body)
        self.assertIn("MIT", body)

    def test_zip_upload_submit_saves_metadata_files_and_confirmation(self):
        zip_bytes = BytesIO()
        with zipfile.ZipFile(zip_bytes, "w") as archive:
            archive.writestr("skill-name/SKILL.md", SKILL_MD)
            archive.writestr("skill-name/input/sample.txt", "input")
            archive.writestr("skill-name/references/source.md", "reference")
            archive.writestr("skill-name/expected/output.csv", "x")
            archive.writestr("skill-name/output/result.csv", "y")
            archive.writestr("skill-name/validation_report.md", "validated")
        zip_bytes.seek(0)

        preview = self.client.post(
            "/publish",
            data={"action": "preview", "skill_package": (zip_bytes, "skill-package.zip")},
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        body = preview.get_data(as_text=True)
        self.assertIn("input/", body)
        self.assertIn("references/", body)
        self.assertIn("expected/", body)
        self.assertIn("output/", body)
        self.assertIn("validation_report.md", body)

        staging_id = next(path.name for path in app_module.SUBMITTED_SKILLS_UPLOAD_DIR.iterdir() if path.name.startswith("_staging-"))
        response = self.client.post(
            "/publish",
            data={
                "action": "submit",
                "staging_id": staging_id,
                "uploaded_file_name": "skill-package.zip",
                "name": "reusable-coding-skill",
                "description": "Codes research documents.",
                "research_type": "Qualitative Analysis",
                "input_type": "Documents.",
                "output_type": "Coding table.",
                "validation_standard": "Compare with benchmark coding.",
                "author": "Researcher",
                "version": "1.0",
                "license": "MIT",
                "skill_status": "Draft",
                "visibility": "Private Draft",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/publish/confirmation/reusable-coding-skill", response.headers["Location"])

        saved = app_module.load_submitted_skills()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], "reusable-coding-skill")
        self.assertEqual(saved[0]["uploaded_file_name"], "skill-package.zip")
        self.assertEqual(saved[0]["license"], "MIT")
        self.assertTrue((app_module.SUBMITTED_SKILLS_UPLOAD_DIR / "reusable-coding-skill" / "package" / "skill-name" / "SKILL.md").exists())

        confirmation = self.client.get(response.headers["Location"])
        confirmation_body = confirmation.get_data(as_text=True)
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("Your skill has been submitted successfully.", confirmation_body)
        self.assertIn("skill-package.zip", confirmation_body)
        self.assertIn("Go to Skill Library", confirmation_body)

        package = self.client.get("/skills/package/reusable-coding-skill")
        self.assertEqual(package.status_code, 200)
        self.assertEqual(package.data[:2], b"PK")
        self.assertIn("attachment", package.headers["Content-Disposition"])
        package.close()


if __name__ == "__main__":
    unittest.main()
