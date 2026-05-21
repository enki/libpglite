#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).with_name("generate-native-dependency-manifest.py")
SPEC = importlib.util.spec_from_file_location("generate_native_dependency_manifest", SCRIPT)
assert SPEC is not None
generator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(generator)


class DependencyManifestGeneratorTests(unittest.TestCase):
    def test_parse_otool_dependencies(self):
        output = """libpglite.dylib:
\t@loader_path/libcrypto.3.dylib (compatibility version 3.0.0, current version 3.0.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)
"""
        self.assertEqual(
            generator.parse_otool(output),
            ["@loader_path/libcrypto.3.dylib", "/usr/lib/libSystem.B.dylib"],
        )

    def test_parse_ldd_dependencies(self):
        output = """linux-vdso.so.1 (0x00007ffc)
libcrypto.so.3 => /tmp/build/libcrypto.so.3 (0x00007f)
libmissing.so => not found
statically linked
/lib64/ld-linux-x86-64.so.2 (0x00007f)
"""
        self.assertEqual(
            generator.parse_ldd(output),
            [
                "linux-vdso.so.1",
                "/tmp/build/libcrypto.so.3",
                "libmissing.so => not found",
                "/lib64/ld-linux-x86-64.so.2",
            ],
        )

    def test_dependency_classification(self):
        root = pathlib.Path("/tmp/package")
        cases = {
            "@loader_path/libcrypto.3.dylib": "loader-relative",
            "/tmp/package/postgres/lib/libpq.dylib": "package",
            "/usr/lib/libSystem.B.dylib": "platform",
            "linux-vdso.so.1": "platform",
            "/opt/homebrew/lib/libcrypto.3.dylib": "local-provider",
            "/tmp/build/libcrypto.so.3": "build-machine",
            "libmissing.so => not found": "missing",
            "/elsewhere/libcustom.so": "absolute-external",
        }
        for raw, classification in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    generator.dependency_record(raw, root)["classification"],
                    classification,
                )


if __name__ == "__main__":
    unittest.main()
