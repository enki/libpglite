#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate raw and structured native dependency diagnostics."
    )
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--postgres-lib-dir", required=True)
    parser.add_argument("--text-out", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()

    package_root = pathlib.Path(args.package_root).resolve()
    plugin = pathlib.Path(args.plugin).resolve()
    postgres_lib_dir = pathlib.Path(args.postgres_lib_dir).resolve()
    text_out = pathlib.Path(args.text_out)
    json_out = pathlib.Path(args.json_out)

    platform = os.uname().sysname
    if platform == "Darwin":
        tool = ["otool", "-L"]
        module_suffix = "*.dylib"
        parser_fn = parse_otool
    elif platform == "Linux":
        tool = ["ldd"]
        module_suffix = "*.so"
        parser_fn = parse_ldd
    else:
        print(f"unsupported platform for dependency manifest: {platform}", file=sys.stderr)
        return 2

    objects = [plugin]
    objects.extend(sorted(postgres_lib_dir.glob(module_suffix)))

    text_lines = [
        "format=libpglite-native-dependencies-v1",
        f"tool={' '.join(tool)}",
    ]
    manifest_objects = []
    for index, obj in enumerate(objects):
        relative = relpath(obj, package_root)
        if index:
            text_lines.append("")
            text_lines.append(f"module={relative}")
        else:
            text_lines.append(f"binary={relative}")

        result = subprocess.run(
            [*tool, str(obj)], text=True, capture_output=True, check=False
        )
        raw_output = result.stdout
        if result.stderr:
            raw_output = raw_output + result.stderr
        display_output = raw_output.replace(str(package_root), ".")
        text_lines.extend(display_output.rstrip("\n").splitlines())
        dependencies = [
            dependency_record(dep, package_root)
            for dep in parser_fn(raw_output)
        ]
        manifest_objects.append(
            {
                "path": relative,
                "kind": "plugin" if index == 0 else "postgres-lib",
                "toolExitCode": result.returncode,
                "dependencies": dependencies,
            }
        )

    text_out.write_text("\n".join(text_lines).rstrip() + "\n")
    manifest = {
        "format": "libpglite-native-dependencies-v1",
        "platform": platform,
        "tool": " ".join(tool),
        "packageRoot": ".",
        "objects": manifest_objects,
    }
    json_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return 0


def parse_otool(output: str) -> list[str]:
    dependencies: list[str] = []
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        dependencies.append(stripped.split(" ", 1)[0])
    return dependencies


def parse_ldd(output: str) -> list[str]:
    dependencies: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "statically linked":
            continue
        if "=> not found" in stripped:
            dependencies.append(stripped.split("=>", 1)[0].strip() + " => not found")
            continue
        if "=>" in stripped:
            right = stripped.split("=>", 1)[1].strip()
            dependencies.append(right.split(" ", 1)[0])
            continue
        dependencies.append(stripped.split(" ", 1)[0])
    return dependencies


def dependency_record(raw: str, package_root: pathlib.Path) -> dict[str, str]:
    resolved = raw
    if raw.endswith("=> not found"):
        classification = "missing"
    elif raw.startswith(("@loader_path/", "@rpath/", "@executable_path/")):
        classification = "loader-relative"
    elif raw.startswith("$ORIGIN/"):
        classification = "loader-relative"
    elif raw.startswith(str(package_root)):
        classification = "package"
        resolved = relpath(pathlib.Path(raw), package_root)
    elif raw.startswith(("./", "postgres/", "diagnostics/")):
        classification = "package"
    elif raw.startswith("linux-vdso.so."):
        classification = "platform"
    elif raw.startswith(("/usr/lib/", "/System/Library/")):
        classification = "platform"
    elif raw.startswith(("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/")):
        classification = "platform"
    elif raw.startswith(("/opt/homebrew/", "/usr/local/")):
        classification = "local-provider"
    elif re.search(r"^(/Users/|/home/|/private/var/|/tmp/|/var/folders/)", raw):
        classification = "build-machine"
    elif raw.startswith("/"):
        classification = "absolute-external"
    else:
        classification = "unknown"
    return {
        "raw": raw,
        "path": resolved,
        "classification": classification,
    }


def relpath(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
