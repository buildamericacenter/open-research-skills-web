import unittest

from app import app


class ResourcesPageTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_resources_page_contains_three_core_sections(self):
        response = self.client.get("/resources")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tutorials / Education Videos", body)
        self.assertIn("Skill Templates", body)
        self.assertIn("Documentation", body)

    def test_old_videos_url_redirects_to_resources(self):
        response = self.client.get("/videos")

        self.assertEqual(response.status_code, 301)
        self.assertTrue(response.headers["Location"].endswith("/resources"))


if __name__ == "__main__":
    unittest.main()
