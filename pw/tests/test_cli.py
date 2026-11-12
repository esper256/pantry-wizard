import json
import os
import subprocess
import sys
import tempfile
import unittest


class CliTests(unittest.TestCase):
    def pw(self, *args, stdin=None):
        env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"),
                   HOME=self.tmp)
        return subprocess.run([sys.executable, "-m", "pantry_wizard.cli", "--file", self.path, *args],
                              input=stdin, capture_output=True, text=True, env=env)

    def test_a_message_end_to_end(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "book.json")
        self.assertEqual(self.pw("init", "--timezone", "America/Los_Angeles").returncode, 0)
        ops = [{"op": "new_item", "name": "Milk", "new_source": True},
               {"op": "add", "item": "milk"}, {"op": "add", "text": "Beer"}]
        done = self.pw("apply", "--by", "alex", "--raw", "add milk and beer", "-", stdin=json.dumps(ops))
        self.assertEqual(done.returncode, 0, done.stderr)
        listed = json.loads(self.pw("list").stdout)
        self.assertEqual([b["text"] for b in listed["buy"]], ["Milk", "Beer"])
        bad = self.pw("apply", "--by", "alxe", json.dumps([{"op": "add", "item": "milk"}]))
        self.assertEqual(bad.returncode, 1)
        self.assertIn("Unknown person", bad.stderr)
        self.assertIn("On the list", self.pw("brief", "anywhere").stdout)
        self.assertEqual(json.loads(self.pw("doctor").stdout), {"problems": []})
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ".pantry-wizard", "ops.log")))
        wipe = self.pw("init")
        self.assertEqual(wipe.returncode, 1)
        self.assertIn("already holds household data", wipe.stderr)
        self.assertEqual(len(json.loads(self.pw("list").stdout)["buy"]), 2)

    def test_the_importer_commands_exchange_files(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "book.json")
        self.pw("init", "--timezone", "America/Los_Angeles")
        leased = self.pw("--agent", "bot-a", "sync", "lease", "costco", "--location", "000 Example City")
        self.assertEqual(json.loads(leased.stdout)["owner"], "bot-a")
        self.assertEqual(self.pw("--agent", "bot-b", "sync", "lease", "costco").returncode, 1)
        snap = json.loads(self.pw("sync", "snapshot", "costco", "--trip").stdout)
        self.assertEqual(snap["priority_item_numbers"], [])
        self.assertEqual(snap["integrations"][0]["location"], "000 Example City")
        mutations = os.path.join(self.tmp, "mutations.json")
        with open(mutations, "w") as f:
            json.dump({"generated_at": "2026-11-11T08:20:00-08:00", "receipts": [
                {"barcode": "B1", "date": "2026-11-10", "lines": [
                    {"item_number": "500", "description": "SPARKLING WATER", "kind": "purchase",
                     "quantity": "1", "unit_price": "9.99", "source_ref": "costco:wh:B1:500"}]}],
                "summary": {"text": "Imported 1 Costco receipts."}}, f)
        applied = json.loads(self.pw("sync", "apply", "costco", mutations).stdout)
        self.assertEqual((applied["receipts"], applied["summary"]), (1, "Imported 1 Costco receipts."))
        old = os.path.join(self.tmp, "old.json")
        with open(old, "w") as f:
            json.dump({"events": [{"source_ref": "costco:wh:B2:500"}]}, f)
        refused = self.pw("sync", "apply", "costco", old)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("older costco-sync", refused.stderr)


if __name__ == "__main__":
    unittest.main()
