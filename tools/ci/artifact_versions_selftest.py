import json
import shutil
import tempfile
import unittest
from pathlib import Path

import check_artifact_versions as cav


class ArtifactVersionCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for path in [*cav.ARTIFACTS, cav.LOCK]:
            dest = self.tmp / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(cav.ROOT / path, dest)

    def edit(self, path, fn):
        file = self.tmp / path
        file.write_text(fn(file.read_text()))

    def test_committed_state_is_clean(self):
        self.assertEqual(cav.evaluate(self.tmp), [])

    def test_content_change_without_bump_fails_for_every_artifact(self):
        for path, (kind, _) in cav.ARTIFACTS.items():
            with self.subTest(path=path):
                shutil.copy(cav.ROOT / path, self.tmp / path)
                if kind == "json":
                    self.edit(path, lambda t: t.rstrip().rstrip("}").rstrip() + ',\n  "seeded": true\n}\n')
                else:
                    self.edit(path, lambda t: t + "\n# seeded change\n")
                errors = cav.evaluate(self.tmp)
                self.assertTrue(any("without a version bump" in e for e in errors), errors)
                self.assertNotEqual(cav.evaluate(self.tmp, update=True), [])

    def test_bump_with_content_change_is_accepted_after_update(self):
        path = "tests/composability/sdk_conformance_fixtures.json"
        self.edit(path, lambda t: t.replace('"schema_version": 1', '"schema_version": 2, "seeded": 1', 1))
        self.assertTrue(any("run with --update" in e for e in cav.evaluate(self.tmp)))
        self.assertEqual(cav.evaluate(self.tmp, update=True), [])
        self.assertEqual(cav.evaluate(self.tmp), [])

    def test_version_regression_and_empty_bump_fail(self):
        path = "tests/composability/sdk_conformance_fixtures.json"
        self.edit(path, lambda t: t.replace('"schema_version": 1', '"schema_version": 3', 1))
        self.assertTrue(any("without a content change" in e for e in cav.evaluate(self.tmp)))
        lock = self.tmp / cav.LOCK
        data = json.loads(lock.read_text())
        data[path]["version"] = "9"
        lock.write_text(json.dumps(data))
        self.assertTrue(any("backwards" in e for e in cav.evaluate(self.tmp)))


if __name__ == "__main__":
    unittest.main()
