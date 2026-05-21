#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = REPO_ROOT / "deps" / "native-pglite-dependencies.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Describe a libpglite native dependency prefix."
    )
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="fail if any locked dependency artifact is absent from the prefix",
    )
    parser.add_argument(
        "--require-static",
        action="store_true",
        help="fail if the dependency prefix contains dynamic libraries or modules",
    )
    args = parser.parse_args()

    prefix = pathlib.Path(args.prefix).resolve()
    inventory_path = pathlib.Path(args.inventory)
    out = pathlib.Path(args.out)
    if not prefix.is_dir():
        print(f"dependency prefix not found: {prefix}", file=sys.stderr)
        return 2

    inventory = read_inventory(inventory_path)
    missing: list[str] = []
    dependencies = []
    for dependency in inventory["dependencies"]:
        artifact_results = []
        for rel in dependency.get("headers", []) + dependency.get("libraries", []):
            path = prefix / rel
            present = path.exists()
            if not present:
                missing.append(f"{dependency['name']}:{rel}")
            artifact_results.append({"path": rel, "present": present})
        pkg_config_results = [
            pkg_config_probe(prefix, package)
            for package in dependency.get("pkgConfig", [])
        ]
        dependencies.append(
            {
                "name": dependency["name"],
                "version": dependency["version"],
                "source": dependency["source"],
                "buildSystem": dependency["buildSystem"],
                "role": dependency["role"],
                "artifacts": artifact_results,
                "pkgConfig": pkg_config_results,
            }
        )

    dynamic_objects = dynamic_prefix_objects(prefix)
    manifest = {
        "format": "libpglite-native-dependency-prefix-v1",
        "prefix": str(prefix),
        "inventory": inventory_path.relative_to(REPO_ROOT).as_posix()
        if inventory_path.is_relative_to(REPO_ROOT)
        else str(inventory_path),
        "inventorySha256": sha256(inventory_path),
        "complete": not missing,
        "staticOnly": not dynamic_objects,
        "missing": missing,
        "dynamicObjects": dynamic_objects,
        "dependencies": dependencies,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if missing and args.require_complete:
        for item in missing:
            print(f"missing dependency prefix artifact: {item}", file=sys.stderr)
        return 1
    if dynamic_objects and args.require_static:
        for item in dynamic_objects:
            print(f"dynamic dependency prefix object: {item}", file=sys.stderr)
        return 1
    return 0


def read_inventory(path: pathlib.Path) -> dict:
    try:
        inventory = json.loads(path.read_text())
    except Exception as err:
        raise SystemExit(f"could not read dependency inventory {path}: {err}") from err
    if inventory.get("format") != "libpglite-native-dependency-inventory-v1":
        raise SystemExit(f"dependency inventory has wrong format: {path}")
    dependencies = inventory.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise SystemExit("dependency inventory must contain dependencies")
    names = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise SystemExit("dependency inventory entry must be an object")
        name = dependency.get("name")
        if not isinstance(name, str) or not name:
            raise SystemExit("dependency inventory entry is missing name")
        if name in names:
            raise SystemExit(f"dependency inventory repeats name: {name}")
        names.add(name)
        for key in ["version", "source", "buildSystem", "role"]:
            if key not in dependency:
                raise SystemExit(f"dependency inventory {name} is missing {key}")
    return inventory


def pkg_config_probe(prefix: pathlib.Path, package: str) -> dict:
    path = ":".join(
        [
            str(prefix / "lib" / "pkgconfig"),
            str(prefix / "share" / "pkgconfig"),
        ]
    )
    env = os.environ.copy()
    env["PKG_CONFIG_LIBDIR"] = path
    env.pop("PKG_CONFIG_PATH", None)
    result = subprocess.run(
        ["pkg-config", "--modversion", package],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "package": package,
        "present": result.returncode == 0,
        "version": result.stdout.strip() if result.returncode == 0 else "",
    }


def dynamic_prefix_objects(prefix: pathlib.Path) -> list[str]:
    results = []
    for path in prefix.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if (
            name.endswith(".dylib")
            or name.endswith(".bundle")
            or name.endswith(".so")
            or ".so." in name
        ):
            results.append(path.relative_to(prefix).as_posix())
    return sorted(results)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
