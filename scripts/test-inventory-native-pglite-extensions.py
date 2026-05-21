#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("inventory-native-pglite-extensions.py")
VECTOR_COMMIT = "35ab919bf5da677709b2ebb8be07480bb25e97cf"
POSTGIS_COMMIT = "08d9b9f749fa3531591055db2a736bfb6df47006"


def run(command: list[str], cwd: pathlib.Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, text=True)


class ExtensionInventoryTests(unittest.TestCase):
    def test_other_extension_inventory_records_gitlinks_and_urls(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            (root / "contrib" / "citext").mkdir(parents=True)
            (root / "contrib" / "Makefile").write_text("SUBDIRS = citext\n")
            (root / "contrib" / "citext" / "Makefile").write_text(
                "EXTENSION = citext\n"
            )
            (root / "pglite" / "other_extensions").mkdir(parents=True)
            (root / "pglite" / "other_extensions" / "Makefile").write_text(
                "SUBDIRS = vector postgis\n"
            )
            (root / ".gitmodules").write_text(
                "\n".join(
                    [
                        '[submodule "pglite/other_extensions/vector"]',
                        "\tpath = pglite/other_extensions/vector",
                        "\turl = https://github.com/pgvector/pgvector.git",
                        '[submodule "pglite/other_extensions/postgis"]',
                        "\tpath = pglite/other_extensions/postgis",
                        "\turl = https://github.com/postgis/postgis.git",
                        "",
                    ]
                )
            )

            run(["git", "init", "-q"], root)
            run(["git", "config", "user.email", "libpglite@example.invalid"], root)
            run(["git", "config", "user.name", "libpglite tests"], root)
            run(["git", "add", "."], root)
            run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"160000,{VECTOR_COMMIT},pglite/other_extensions/vector",
                ],
                root,
            )
            run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"160000,{POSTGIS_COMMIT},pglite/other_extensions/postgis",
                ],
                root,
            )
            run(["git", "commit", "-q", "-m", "fixture"], root)

            out = root / "inventory.txt"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source-dir",
                    str(root),
                    "--out",
                    str(out),
                ],
                check=True,
                text=True,
            )

            inventory = out.read_text()
            self.assertIn(
                "contrib_extension=citext;source=contrib/citext", inventory
            )
            self.assertIn(
                "other_extension=vector;"
                "source=pglite/other_extensions/vector;"
                "submodule_state=-;"
                f"submodule_commit={VECTOR_COMMIT};"
                "status=missing;"
                "submodule_url=https://github.com/pgvector/pgvector.git",
                inventory,
            )
            self.assertIn(
                "other_extension=postgis;"
                "source=pglite/other_extensions/postgis;"
                "submodule_state=-;"
                f"submodule_commit={POSTGIS_COMMIT};"
                "status=missing;"
                "submodule_url=https://github.com/postgis/postgis.git",
                inventory,
            )


if __name__ == "__main__":
    unittest.main()
