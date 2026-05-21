#!/usr/bin/env python3
import argparse
import pathlib
import re
import subprocess


PGLITE_CONTRIB_CONDITIONALS = ("pgcrypto", "uuid-ossp", "xml2")


def parse_initial_subdirs(makefile: pathlib.Path) -> list[str]:
    lines = makefile.read_text().splitlines()
    subdirs: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("SUBDIRS ="):
            in_block = True
            stripped = stripped.removeprefix("SUBDIRS =").strip()
        elif in_block and (not stripped or stripped.startswith("ifeq")):
            break
        elif not in_block:
            continue

        stripped = stripped.rstrip("\\").strip()
        if stripped:
            subdirs.extend(stripped.split())
    return subdirs


def parse_makefile_extensions(makefile: pathlib.Path) -> list[str]:
    extensions: list[str] = []
    if not makefile.is_file():
        return extensions
    for line in makefile.read_text().splitlines():
        match = re.match(r"^\s*EXTENSION\s*=\s*(.+?)\s*$", line)
        if match:
            extensions.extend(match.group(1).split())
    return extensions


def parse_other_extensions(makefile: pathlib.Path) -> list[str]:
    extensions = parse_initial_subdirs(makefile)
    for line in makefile.read_text().splitlines():
        match = re.match(r"^\s*EXTENSIONS\s*\+=\s*(.+?)\s*$", line)
        if match:
            extensions.extend(match.group(1).split())
    return sorted(dict.fromkeys(extensions))


def git_stdout(source: pathlib.Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def submodule_status(source: pathlib.Path) -> dict[str, tuple[str, str]]:
    output = git_stdout(source, ["submodule", "status", "--recursive"])
    statuses: dict[str, tuple[str, str]] = {}
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        state = raw_line[0]
        parts = raw_line[1:].split()
        if len(parts) >= 2:
            commit, path = parts[0], parts[1]
            statuses[path] = (state, commit)
    return statuses


def gitlink_commits(source: pathlib.Path) -> dict[str, str]:
    output = git_stdout(source, ["ls-tree", "-r", "HEAD", "pglite/other_extensions"])
    commits: dict[str, str] = {}
    for raw_line in output.splitlines():
        match = re.match(r"^160000\s+commit\s+([0-9a-f]{40})\t(.+)$", raw_line)
        if match:
            commit, path = match.groups()
            commits[path] = commit
    return commits


def submodule_metadata(source: pathlib.Path) -> dict[str, dict[str, str]]:
    gitmodules = source / ".gitmodules"
    if not gitmodules.is_file():
        return {}

    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in gitmodules.read_text().splitlines():
        line = raw_line.strip()
        section = re.match(r'^\[submodule "(.+)"\]$', line)
        if section:
            current = {"name": section.group(1)}
            sections.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip()

    metadata: dict[str, dict[str, str]] = {}
    for section in sections:
        path = section.get("path")
        if path:
            metadata[path] = section
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    args = parser.parse_args()

    source = args.source_dir.resolve()
    contrib_makefile = source / "contrib" / "Makefile"
    contrib_dirs = parse_initial_subdirs(contrib_makefile)
    contrib_dirs.extend(PGLITE_CONTRIB_CONDITIONALS)
    contrib_dirs = sorted(dict.fromkeys(contrib_dirs))

    lines = ["format=libpglite-native-extension-inventory-v1"]
    for subdir in contrib_dirs:
        extensions = parse_makefile_extensions(source / "contrib" / subdir / "Makefile")
        if extensions:
            for extension in extensions:
                lines.append(f"contrib_extension={extension};source=contrib/{subdir}")
        else:
            lines.append(f"contrib_module={subdir};source=contrib/{subdir}")

    other_makefile = source / "pglite" / "other_extensions" / "Makefile"
    statuses = submodule_status(source)
    gitlinks = gitlink_commits(source)
    metadata = submodule_metadata(source)
    for extension in parse_other_extensions(other_makefile):
        rel = f"pglite/other_extensions/{extension}"
        state, status_commit = statuses.get(rel, ("?", ""))
        commit = gitlinks.get(rel, status_commit)
        submodule = metadata.get(rel, {})
        path = source / rel
        present = any(path.iterdir()) if path.is_dir() else False
        status = "present" if present and state != "-" else "missing"
        line = (
            "other_extension="
            f"{extension};source={rel};submodule_state={state};submodule_commit={commit};status={status}"
        )
        url = submodule.get("url")
        if url:
            line += f";submodule_url={url}"
        branch = submodule.get("branch")
        if branch:
            line += f";submodule_branch={branch}"
        lines.append(line)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
