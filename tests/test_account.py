import unittest

from werkzeug.security import check_password_hash

import app as app_module


class FakeAccountRepository:
    def __init__(self):
        self.accounts = {}
        self.next_id = 1

    def find_by_email(self, email):
        normalized = email.lower()
        return next((account for account in self.accounts.values() if account["email"] == normalized), None)

    def find_by_id(self, account_id):
        return self.accounts.get(int(account_id))

    def create(self, full_name, email, password_hash):
        account_id = self.next_id
        self.next_id += 1
        self.accounts[account_id] = {
            "account_id": account_id,
            "full_name": full_name,
            "email": email.lower(),
            "password": password_hash,
            "register_tstamp": "2026-07-15 12:00:00",
        }
        return account_id

    def update_profile(self, account_id, full_name, email, password_hash=None):
        account = self.accounts[int(account_id)]
        account["full_name"] = full_name
        account["email"] = email.lower()
        if password_hash:
            account["password"] = password_hash


class AccountPageTests(unittest.TestCase):
    def setUp(self):
        self.original_repository = app_module.account_repository
        self.repository = FakeAccountRepository()
        app_module.account_repository = self.repository
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.account_repository = self.original_repository

    def test_account_requires_login(self):
        response = self.client.get("/account")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login?next=/account", response.headers["Location"])

    def test_register_creates_account_and_renders_account_page(self):
        response = self.client.post(
            "/register",
            data={
                "full_name": "Ada Lovelace",
                "email": "ada@example.com",
                "password": "research123",
                "confirm_password": "research123",
            },
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        account = self.repository.find_by_email("ada@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertIn("My Account", body)
        self.assertIn("Ada Lovelace", body)
        self.assertIn("ada@example.com", body)
        self.assertIsNotNone(account)
        self.assertNotEqual(account["password"], "research123")
        self.assertTrue(check_password_hash(account["password"], "research123"))

    def test_login_accepts_existing_account(self):
        self.repository.create("Grace Hopper", "grace@example.com", app_module.password_hash("compiler123"))

        response = self.client.post(
            "/login",
            data={"email": "grace@example.com", "password": "compiler123"},
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Grace Hopper", body)
        self.assertIn("Profile Settings", body)

    def test_login_rejects_bad_password(self):
        self.repository.create("Grace Hopper", "grace@example.com", app_module.password_hash("compiler123"))

        response = self.client.post(
            "/login",
            data={"email": "grace@example.com", "password": "wrong-password"},
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid email or password.", body)

    def test_update_profile_changes_name_email_and_password(self):
        account_id = self.repository.create("Old Name", "old@example.com", app_module.password_hash("oldpass123"))
        with self.client.session_transaction() as flask_session:
            flask_session["account_id"] = account_id

        response = self.client.post(
            "/account/profile",
            data={
                "full_name": "New Name",
                "email": "new@example.com",
                "password": "newpass123",
                "confirm_password": "newpass123",
            },
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        account = self.repository.find_by_id(account_id)

        self.assertEqual(response.status_code, 200)
        self.assertIn("New Name", body)
        self.assertIn("new@example.com", body)
        self.assertTrue(check_password_hash(account["password"], "newpass123"))


if __name__ == "__main__":
    unittest.main()
