#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("fetch-native-dependency-sources.py")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FetchNativeDependencySourcesTests(unittest.TestCase):
    def test_fetches_and_verifies_archive_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source = root / "source.bin"
            payload = b"native dependency source\n"
            source.write_bytes(payload)
            inventory = root / "inventory.json"
            inventory.write_text(
                json.dumps(
                    {
                        "format": "libpglite-native-dependency-inventory-v1",
                        "dependencies": [
                            {
                                "name": "sample",
                                "version": "1",
                                "source": source.as_uri(),
                                "archive": "sample.bin",
                                "sha256": sha256(payload),
                                "buildSystem": "fixture",
                                "headers": [],
                                "libraries": [],
                                "pkgConfig": [],
                                "role": [],
                            }
                        ],
                    }
                )
                + "\n"
            )
            out = root / "out"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--inventory",
                    str(inventory),
                    "--out",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((out / "sample.bin").read_bytes(), payload)
            manifest = json.loads((out / "sources.json").read_text())
            self.assertEqual(manifest["format"], "libpglite-native-dependency-sources-v1")
            self.assertEqual(manifest["sources"][0]["sha256"], sha256(payload))

    def test_verify_cache_only_rejects_bad_archive_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            inventory = root / "inventory.json"
            inventory.write_text(
                json.dumps(
                    {
                        "format": "libpglite-native-dependency-inventory-v1",
                        "dependencies": [
                            {
                                "name": "sample",
                                "version": "1",
                                "source": "file:///missing",
                                "archive": "sample.bin",
                                "sha256": sha256(b"expected"),
                                "buildSystem": "fixture",
                                "headers": [],
                                "libraries": [],
                                "pkgConfig": [],
                                "role": [],
                            }
                        ],
                    }
                )
                + "\n"
            )
            out = root / "out"
            out.mkdir()
            (out / "sample.bin").write_bytes(b"wrong")
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--inventory",
                    str(inventory),
                    "--out",
                    str(out),
                    "--verify-cache-only",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("wrong checksum", result.stderr)

    def test_fetches_git_source_at_exact_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source_repo = root / "source-repo"
            subprocess.run(["git", "init", str(source_repo)], check=True, capture_output=True)
            (source_repo / "README").write_text("fixture\n")
            env = os.environ.copy()
            env.update(
                {
                    "GIT_AUTHOR_NAME": "libpglite",
                    "GIT_AUTHOR_EMAIL": "libpglite@example.invalid",
                    "GIT_COMMITTER_NAME": "libpglite",
                    "GIT_COMMITTER_EMAIL": "libpglite@example.invalid",
                }
            )
            subprocess.run(["git", "add", "README"], cwd=source_repo, env=env, check=True)
            subprocess.run(
                ["git", "commit", "-m", "fixture"],
                cwd=source_repo,
                env=env,
                check=True,
                capture_output=True,
            )
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=source_repo,
                text=True,
            ).strip()
            inventory = root / "inventory.json"
            inventory.write_text(
                json.dumps(
                    {
                        "format": "libpglite-native-dependency-inventory-v1",
                        "dependencies": [
                            {
                                "name": "sample-git",
                                "version": "1",
                                "source": str(source_repo),
                                "gitRef": "fixture",
                                "gitCommit": commit,
                                "buildSystem": "fixture",
                                "headers": [],
                                "libraries": [],
                                "pkgConfig": [],
                                "role": [],
                            }
                        ],
                    }
                )
                + "\n"
            )
            out = root / "out"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--inventory",
                    str(inventory),
                    "--out",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            checkout_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=out / "git" / "sample-git",
                text=True,
            ).strip()
            self.assertEqual(checkout_commit, commit)


if __name__ == "__main__":
    unittest.main()
