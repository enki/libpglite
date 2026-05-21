#!/usr/bin/env python3
import argparse
import pathlib
import re
import shutil
import subprocess


PROVENANCE_FILE = ".libpglite-extension-source"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Populate pinned PGlite other_extension submodules."
    )
    parser.add_argument("--inventory", required=True, type=pathlib.Path)
    parser.add_argument("--out-root", required=True, type=pathlib.Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inventory and print planned materialization without cloning",
    )
    args = parser.parse_args()

    inventory = args.inventory.resolve()
    out_root = args.out_root.resolve()
    extensions = parse_other_extensions(inventory)
    if not extensions:
        raise SystemExit(f"inventory has no PGlite other_extension entries: {inventory}")

    for extension in extensions:
        materialize_extension(out_root, extension, dry_run=args.dry_run)
    return 0


def parse_other_extensions(inventory: pathlib.Path) -> list[dict[str, str]]:
    extensions: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in inventory.read_text().splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("other_extension="):
            continue
        name, fields = parse_semicolon_fields(line.split("=", 1)[1])
        if name in seen:
            raise SystemExit(f"inventory repeats other_extension: {name}")
        seen.add(name)
        source = required_field(name, fields, "source")
        expected_source = f"pglite/other_extensions/{name}"
        if source != expected_source:
            raise SystemExit(
                f"inventory source for {name} must be {expected_source}, got {source}"
            )
        commit = required_field(name, fields, "submodule_commit")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise SystemExit(f"inventory has invalid submodule_commit for {name}: {commit}")
        url = required_field(name, fields, "submodule_url")
        if not url:
            raise SystemExit(f"inventory has empty submodule_url for {name}")
        extensions.append(
            {
                "name": name,
                "source": source,
                "commit": commit,
                "url": url,
            }
        )
    return extensions


def parse_semicolon_fields(raw_value: str) -> tuple[str, dict[str, str]]:
    parts = raw_value.split(";")
    fields: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key] = value
    return parts[0], fields


def required_field(name: str, fields: dict[str, str], key: str) -> str:
    value = fields.get(key)
    if value is None:
        raise SystemExit(f"inventory is missing {key} for {name}")
    return value


def materialize_extension(
    out_root: pathlib.Path, extension: dict[str, str], dry_run: bool
) -> None:
    name = extension["name"]
    destination = (out_root / extension["source"]).resolve()
    allowed_root = (out_root / "pglite" / "other_extensions").resolve()
    if not destination.is_relative_to(allowed_root):
        raise SystemExit(f"refusing to write outside other_extensions: {destination}")

    provenance = provenance_text(extension)
    provenance_path = destination / PROVENANCE_FILE
    if provenance_path.is_file() and provenance_path.read_text() == provenance:
        print(f"{name}: already materialized at {extension['commit']}")
        return

    if dry_run:
        print(f"{name}: would materialize {extension['url']}#{extension['commit']}")
        return

    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    run(["git", "init", "-q", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", extension["url"]])
    fetch_commit(destination, extension["commit"])
    run(["git", "-C", str(destination), "checkout", "-q", "--detach", extension["commit"]])
    provenance_path.write_text(provenance)
    print(f"{name}: materialized {extension['commit']}")


def provenance_text(extension: dict[str, str]) -> str:
    return "\n".join(
        [
            "format=libpglite-native-other-extension-source-v1",
            f"name={extension['name']}",
            f"source={extension['source']}",
            f"url={extension['url']}",
            f"commit={extension['commit']}",
            "",
        ]
    )


def fetch_commit(destination: pathlib.Path, commit: str) -> None:
    attempts = [
        ["git", "-C", str(destination), "fetch", "-q", "--depth=1", "origin", commit],
        ["git", "-C", str(destination), "fetch", "-q", "origin", commit],
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "-q",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
            "+refs/tags/*:refs/tags/*",
        ],
    ]
    errors: list[str] = []
    for command in attempts:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode == 0:
            return
        errors.append(result.stderr.strip())
    raise SystemExit(
        f"could not fetch pinned extension commit {commit}: "
        + " | ".join(error for error in errors if error)
    )


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, text=True)


if __name__ == "__main__":
    raise SystemExit(main())
