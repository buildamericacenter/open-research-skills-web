import unittest

from app import app


class UseSkillsPageTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_use_skills_page_lists_ready_and_upcoming_skills(self):
        response = self.client.get("/use-skills")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Ready to Run", body)
        self.assertIn("NPV-DCF Analysis", body)
        self.assertIn("Run Skill", body)
        self.assertIn("More Skills", body)
        self.assertIn("Literature Screening and Theme Coding", body)

    def test_primary_navigation_contains_only_core_modules(self):
        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertIn('href="/publish">Contribute Skills</a>', body)
        self.assertIn('href="/library">Skills Library</a>', body)
        self.assertIn('href="/use-skills">Use Skills</a>', body)
        self.assertIn('href="/resources">Resources</a>', body)
        self.assertIn('href="/login">Sign In</a>', body)
        self.assertNotIn('href="/validation"', body)
        self.assertNotIn('href="/videos"', body)


if __name__ == "__main__":
    unittest.main()
