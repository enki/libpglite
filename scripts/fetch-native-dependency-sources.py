#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = REPO_ROOT / "deps" / "native-pglite-dependencies.json"
DEFAULT_OUT = REPO_ROOT / "target" / "native-pglite" / "dependency-sources"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and verify pinned libpglite native dependency sources."
    )
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--verify-cache-only",
        action="store_true",
        help="validate already-fetched sources without network access",
    )
    args = parser.parse_args()

    inventory_path = pathlib.Path(args.inventory)
    out = pathlib.Path(args.out)
    inventory = read_inventory(inventory_path)
    out.mkdir(parents=True, exist_ok=True)

    entries = []
    for dependency in inventory["dependencies"]:
        if "gitCommit" in dependency:
            entries.append(fetch_git_dependency(dependency, out, args.verify_cache_only))
        else:
            entries.append(fetch_archive_dependency(dependency, out, args.verify_cache_only))

    manifest = {
        "format": "libpglite-native-dependency-sources-v1",
        "inventory": str(inventory_path),
        "inventorySha256": sha256(inventory_path),
        "sources": entries,
    }
    (out / "sources.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out / 'sources.json'}")
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
    for dependency in dependencies:
        name = dependency.get("name")
        if not isinstance(name, str) or not name:
            raise SystemExit("dependency inventory entry is missing name")
        if "gitCommit" in dependency:
            require_string(dependency, "source")
            require_string(dependency, "gitRef")
            require_string(dependency, "gitCommit")
        else:
            require_string(dependency, "source")
            require_string(dependency, "archive")
            require_string(dependency, "sha256")
    return inventory


def require_string(dependency: dict, key: str) -> None:
    value = dependency.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"dependency inventory {dependency.get('name')} is missing {key}")


def fetch_archive_dependency(dependency: dict, out: pathlib.Path, verify_only: bool) -> dict:
    archive = out / dependency["archive"]
    expected = dependency["sha256"]
    urls = [dependency["source"], *dependency.get("mirrors", [])]

    if archive.is_file() and sha256(archive) == expected:
        return archive_entry(dependency, archive, expected)

    if verify_only:
        raise SystemExit(f"cached archive is missing or has wrong checksum: {archive}")

    errors = []
    with tempfile.TemporaryDirectory(dir=out) as tempdir:
        tmp = pathlib.Path(tempdir) / dependency["archive"]
        for url in urls:
            try:
                print(f"fetch {dependency['name']}: {url}")
                urllib.request.urlretrieve(url, tmp)
            except (urllib.error.URLError, OSError) as err:
                errors.append(f"{url}: {err}")
                continue
            actual = sha256(tmp)
            if actual != expected:
                errors.append(f"{url}: sha256 {actual}, expected {expected}")
                tmp.unlink(missing_ok=True)
                continue
            shutil.move(str(tmp), archive)
            return archive_entry(dependency, archive, expected)
    raise SystemExit(
        f"could not fetch verified archive for {dependency['name']}:\n"
        + "\n".join(f"  {error}" for error in errors)
    )


def archive_entry(dependency: dict, archive: pathlib.Path, checksum: str) -> dict:
    return {
        "name": dependency["name"],
        "kind": "archive",
        "archive": str(archive),
        "sha256": checksum,
    }


def fetch_git_dependency(dependency: dict, out: pathlib.Path, verify_only: bool) -> dict:
    checkout = out / "git" / dependency["name"]
    expected = dependency["gitCommit"]

    if checkout.is_dir():
        actual = git(["rev-parse", "HEAD"], cwd=checkout)
        if actual == expected:
            return git_entry(dependency, checkout)
        if verify_only:
            raise SystemExit(
                f"cached git checkout has wrong commit for {dependency['name']}: "
                f"{actual}, expected {expected}"
            )

    if verify_only:
        raise SystemExit(f"cached git checkout is missing: {checkout}")

    if checkout.exists():
        shutil.rmtree(checkout)
    checkout.parent.mkdir(parents=True, exist_ok=True)
    git(["init", str(checkout)])
    git(["remote", "add", "origin", dependency["source"]], cwd=checkout)
    git(["fetch", "--depth", "1", "origin", expected], cwd=checkout)
    git(["checkout", "--detach", expected], cwd=checkout)
    actual = git(["rev-parse", "HEAD"], cwd=checkout)
    if actual != expected:
        raise SystemExit(
            f"git checkout has wrong commit for {dependency['name']}: "
            f"{actual}, expected {expected}"
        )
    return git_entry(dependency, checkout)


def git_entry(dependency: dict, checkout: pathlib.Path) -> dict:
    return {
        "name": dependency["name"],
        "kind": "git",
        "checkout": str(checkout),
        "source": dependency["source"],
        "gitRef": dependency["gitRef"],
        "gitCommit": dependency["gitCommit"],
    }


def git(args: list[str], cwd: pathlib.Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
