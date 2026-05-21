#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("describe-native-dependency-prefix.py")
SPEC = importlib.util.spec_from_file_location("describe_native_dependency_prefix", SCRIPT)
assert SPEC is not None
descriptor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(descriptor)


class NativeDependencyPrefixDescriptorTests(unittest.TestCase):
    def test_inventory_is_valid_and_names_are_unique(self):
        inventory = descriptor.read_inventory(descriptor.DEFAULT_INVENTORY)
        names = [dependency["name"] for dependency in inventory["dependencies"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("openssl", names)
        self.assertIn("postgis", {role for dep in inventory["dependencies"] for role in dep["role"]})

    def test_descriptor_reports_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            prefix = root / "prefix"
            prefix.mkdir()
            out = root / "manifest.json"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--prefix",
                    str(prefix),
                    "--out",
                    str(out),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(out.read_text())
            self.assertEqual(manifest["format"], "libpglite-native-dependency-prefix-v1")
            self.assertFalse(manifest["complete"])
            self.assertTrue(manifest["staticOnly"])
            self.assertTrue(manifest["missing"])
            self.assertEqual(manifest["dynamicObjects"], [])

    def test_require_complete_fails_for_incomplete_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            prefix = root / "prefix"
            prefix.mkdir()
            out = root / "manifest.json"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--prefix",
                    str(prefix),
                    "--out",
                    str(out),
                    "--require-complete",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing dependency prefix artifact", result.stderr)

    def test_require_static_fails_for_dynamic_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            prefix = root / "prefix"
            (prefix / "lib").mkdir(parents=True)
            (prefix / "lib" / "legacy.dylib").write_bytes(b"placeholder")
            out = root / "manifest.json"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--prefix",
                    str(prefix),
                    "--out",
                    str(out),
                    "--require-static",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dynamic dependency prefix object: lib/legacy.dylib", result.stderr)
            manifest = json.loads(out.read_text())
            self.assertFalse(manifest["staticOnly"])
            self.assertEqual(manifest["dynamicObjects"], ["lib/legacy.dylib"])

    def test_pkg_config_probe_is_isolated_to_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            prefix = root / "prefix"
            host_pkgconfig = root / "host" / "lib" / "pkgconfig"
            host_pkgconfig.mkdir(parents=True)
            (prefix / "lib" / "pkgconfig").mkdir(parents=True)
            (prefix / "share" / "pkgconfig").mkdir(parents=True)
            (host_pkgconfig / "fake-libpglite-host-only.pc").write_text(
                "\n".join(
                    [
                        "prefix=/host",
                        "libdir=${prefix}/lib",
                        "includedir=${prefix}/include",
                        "Name: fake-libpglite-host-only",
                        "Description: host-only package",
                        "Version: 1.2.3",
                        "Libs: -L${libdir} -lfake",
                        "Cflags: -I${includedir}",
                    ]
                )
                + "\n"
            )
            env = os.environ.copy()
            env["PKG_CONFIG_PATH"] = str(host_pkgconfig)

            result = subprocess.run(
                [
                    "python3",
                    "-c",
                    (
                        "import importlib.util, pathlib, json; "
                        f"spec=importlib.util.spec_from_file_location('d', {str(SCRIPT)!r}); "
                        "mod=importlib.util.module_from_spec(spec); "
                        "spec.loader.exec_module(mod); "
                        f"print(json.dumps(mod.pkg_config_probe(pathlib.Path({str(prefix)!r}), 'fake-libpglite-host-only')))"
                    ),
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            probe = json.loads(result.stdout)
            self.assertFalse(probe["present"])


if __name__ == "__main__":
    unittest.main()
