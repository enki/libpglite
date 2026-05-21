#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("materialize-native-pglite-other-extensions.py")


def run(command: list[str], cwd: pathlib.Path | None = None) -> str:
    result = subprocess.run(
        command, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE
    )
    return result.stdout.strip()


class MaterializeOtherExtensionsTests(unittest.TestCase):
    def test_materializes_exact_inventory_commit(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            upstream = root / "upstream-vector"
            upstream.mkdir()
            run(["git", "init", "-q"], upstream)
            run(["git", "config", "user.email", "libpglite@example.invalid"], upstream)
            run(["git", "config", "user.name", "libpglite tests"], upstream)
            (upstream / "vector.control").write_text("# fixture\n")
            run(["git", "add", "."], upstream)
            run(["git", "commit", "-q", "-m", "fixture"], upstream)
            commit = run(["git", "rev-parse", "HEAD"], upstream)

            inventory = root / "extension-inventory.txt"
            inventory.write_text(
                "\n".join(
                    [
                        "format=libpglite-native-extension-inventory-v1",
                        "other_extension=vector;"
                        "source=pglite/other_extensions/vector;"
                        "submodule_state=-;"
                        f"submodule_commit={commit};"
                        "status=missing;"
                        f"submodule_url={upstream}",
                        "",
                    ]
                )
            )

            out_root = root / "patched-postgres-pglite"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--inventory",
                    str(inventory),
                    "--out-root",
                    str(out_root),
                ],
                check=True,
                text=True,
            )

            materialized = out_root / "pglite" / "other_extensions" / "vector"
            self.assertEqual(run(["git", "rev-parse", "HEAD"], materialized), commit)
            self.assertTrue((materialized / "vector.control").is_file())
            self.assertIn(
                f"commit={commit}",
                (materialized / ".libpglite-extension-source").read_text(),
            )

            second = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--inventory",
                    str(inventory),
                    "--out-root",
                    str(out_root),
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            self.assertIn("already materialized", second.stdout)


if __name__ == "__main__":
    unittest.main()
