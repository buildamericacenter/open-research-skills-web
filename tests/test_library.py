import unittest

from app import SAMPLE_SKILLS, app


class SkillLibraryTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_library_shows_search_filters_and_sample_skills(self):
        response = self.client.get("/library")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Find reusable research skills.", body)
        self.assertIn("Featured Skills", body)
        self.assertIn("Research Method Skills", body)
        self.assertIn("Paper-Specific Skills", body)
        self.assertIn("Grant Application Skills", body)
        self.assertIn("skillSearch", body)
        self.assertIn("Research Type", body)
        self.assertIn("Validation Status", body)
        self.assertIn("Input Type", body)
        self.assertIn("Output Type", body)
        for skill in SAMPLE_SKILLS:
            self.assertIn(skill["name"], body)

    def test_library_cards_use_one_primary_action(self):
        response = self.client.get("/library")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Open Skill", body)
        self.assertNotIn("View Details", body)
        self.assertNotIn(">Use Skill</a>", body)
        self.assertNotIn(">Comment</a>", body)

    def test_sample_skill_detail_pages_render_required_sections(self):
        required_sections = [
            "Purpose",
            "Required Inputs",
            "Expected Outputs",
            "Procedure Summary",
            "Validation Standard",
            "Example Files",
            "Comments",
        ]
        for skill in SAMPLE_SKILLS:
            response = self.client.get(f"/library/skills/{skill['id']}")
            body = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn(skill["name"], body)
            for section in required_sections:
                self.assertIn(section, body)

    def test_npv_use_skill_redirects_to_executable_flow(self):
        response = self.client.get("/skills/use/npv-dcf")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/skills/npv-dcf"))

    def test_skill_package_downloads(self):
        response = self.client.get("/skills/package/npv-dcf")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("# NPV-DCF Analysis", body)
        self.assertIn("## Validation Standard", body)


if __name__ == "__main__":
    unittest.main()
