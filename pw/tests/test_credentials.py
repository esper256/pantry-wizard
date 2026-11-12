"""Finding the service-account key: the env file, a local cache, or a one-time
bootstrap from the sheet. The bootstrap key never reaches an ad-hoc query."""

import json
import os
import stat
import tempfile
import unittest

from pantry_wizard import credentials, query
from pantry_wizard.errors import PwError

KEY = {"type": "service_account", "client_email": "bot@example.iam", "private_key": "-----BEGIN-----"}
ACCOUNTS_CSV = (
    "account,email,key,note\r\n"
    'dots,bot@example.iam,"{""type"": ""service_account"", ""client_email"": ""bot@example.iam"", '
    '""private_key"": ""-----BEGIN-----""}",the main bot\r\n'
    "grok-bot,other@example.iam,,no key yet\r\n"
)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.env = {"PW_STATE_DIR": self.state}

    def test_an_env_key_file_wins_and_nothing_is_fetched(self):
        def fetch(_):
            raise AssertionError("should not fetch when PW_SERVICE_ACCOUNT_KEY is set")

        env = dict(self.env, PW_SERVICE_ACCOUNT_KEY="/keys/sa.json")
        found = credentials.resolve("sheet1", "dots", env=env, fetch=fetch)
        self.assertEqual((found["source"], found["key_file"]), ("env", "/keys/sa.json"))

    def test_bootstrap_reads_the_agents_row_caches_it_then_uses_the_cache(self):
        calls = []

        def fetch(sheet_id):
            calls.append(sheet_id)
            return ACCOUNTS_CSV

        first = credentials.resolve("sheet1", "dots", env=self.env, fetch=fetch)
        self.assertEqual(first["source"], "bootstrap")
        self.assertEqual(first["info"], KEY)
        self.assertTrue(os.path.exists(first["cache_path"]))
        # A second call uses the cache and does not fetch again.
        second = credentials.resolve("sheet1", "dots", env=self.env, fetch=fetch)
        self.assertEqual((second["source"], second["info"]), ("cache", KEY))
        self.assertEqual(calls, ["sheet1"])

    def test_the_cache_is_private(self):
        found = credentials.resolve("sheet1", "dots", env=self.env, fetch=lambda _: ACCOUNTS_CSV)
        self.assertEqual(stat.S_IMODE(os.stat(found["cache_path"]).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(found["cache_path"])).st_mode), 0o700)

    def test_force_re_reads_and_overwrites_but_a_plain_call_does_not(self):
        credentials.resolve("sheet1", "dots", env=self.env, fetch=lambda _: ACCOUNTS_CSV)
        changed = dict(KEY, private_key="-----ROTATED-----")
        csv2 = ACCOUNTS_CSV.replace("-----BEGIN-----", "-----ROTATED-----")
        # Without force, the cache stands even though the sheet changed.
        plain = credentials.resolve("sheet1", "dots", env=self.env, fetch=lambda _: csv2)
        self.assertEqual(plain["info"]["private_key"], "-----BEGIN-----")
        forced = credentials.resolve("sheet1", "dots", env=self.env, fetch=lambda _: csv2, force=True)
        self.assertEqual((forced["source"], forced["info"]), ("bootstrap", changed))

    def test_a_missing_agent_row_is_a_clear_error(self):
        with self.assertRaises(PwError) as e:
            credentials.resolve("sheet1", "nobody", env=self.env, fetch=lambda _: ACCOUNTS_CSV)
        self.assertIn("nobody", str(e.exception))

    def test_an_empty_key_cell_is_treated_as_no_key(self):
        with self.assertRaises(PwError):
            credentials.resolve("sheet1", "grok-bot", env=self.env, fetch=lambda _: ACCOUNTS_CSV)

    def test_an_unreachable_sheet_points_at_link_sharing(self):
        def fetch(_):
            raise OSError("HTTP Error 404: Not Found")

        with self.assertRaises(PwError) as e:
            credentials.resolve("sheet1", "dots", env=self.env, fetch=fetch)
        self.assertIn("link", str(e.exception).lower())


class SecrecyTests(unittest.TestCase):
    def test_the_accounts_tab_never_reaches_the_sql_mirror(self):
        self.assertIn("Accounts", query.TABS)
        self.assertNotIn("Accounts", query.RAW_TABS)


if __name__ == "__main__":
    unittest.main()
