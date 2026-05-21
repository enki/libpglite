#!/usr/bin/env python3
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCH_DIR = REPO_ROOT / "patches" / "postgres-pglite"
ADR = REPO_ROOT / "docs" / "ADR-0005-PGLITEC-NATIVE-PORTABILITY.md"


class NativePatchDecisionTests(unittest.TestCase):
    def test_every_carried_patch_has_an_explicit_decision(self):
        text = ADR.read_text()
        patches = sorted(path.name for path in PATCH_DIR.glob("*.patch"))
        self.assertTrue(patches)
        for patch in patches:
            self.assertIn(f"| `{patch}` | carry downstream |", text)


if __name__ == "__main__":
    unittest.main()
