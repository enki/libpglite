#!/usr/bin/env python3
import pathlib
import re
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
README = DOCS / "README.md"
PREFLIGHT = REPO_ROOT / "scripts" / "preflight-native-plugin-release.sh"


def status(path: pathlib.Path) -> str | None:
    match = re.search(r"^Status:\s*(\S+)\s*$", path.read_text(), re.MULTILINE)
    return match.group(1) if match else None


def listed(readme: str, rel_path: str) -> bool:
    return f"`{rel_path}`" in readme


def has_section(text: str, heading: str) -> bool:
    return re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE) is not None


def main() -> int:
    errors: list[str] = []
    readme = README.read_text()
    preflight = PREFLIGHT.read_text()

    open_adrs = sorted(DOCS.glob("ADR-*.md"))
    done_adrs = sorted((DOCS / "done").glob("ADR-*.md"))

    for path in open_adrs:
        rel = path.relative_to(DOCS).as_posix()
        text = path.read_text()
        if status(path) != "Open":
            errors.append(f"{rel} must have Status: Open while it is in docs/")
        if not listed(readme, rel):
            errors.append(f"{rel} is not listed in docs/README.md open records")
        if not has_section(text, "Acceptance Criteria"):
            errors.append(f"{rel} is missing Acceptance Criteria")
        if not has_section(text, "Remaining Closure Criteria"):
            errors.append(f"{rel} is missing Remaining Closure Criteria")
        parts = path.stem.split("-", 2)
        if len(parts) >= 2:
            label = "-".join(parts[:2])
            if label not in readme:
                errors.append(f"{rel} has no current closure frontier entry")

    for path in done_adrs:
        rel = path.relative_to(DOCS).as_posix()
        if status(path) != "Done":
            errors.append(f"{rel} must have Status: Done while it is in docs/done/")
        if not listed(readme, rel):
            errors.append(f"{rel} is not listed in docs/README.md done records")

    for path in sorted((REPO_ROOT / "scripts").glob("test-*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if f"python3 {rel}" not in preflight:
            errors.append(f"{rel} is not wired into scripts/preflight-native-plugin-release.sh")

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"ADR closure audit ok: {len(open_adrs)} open, {len(done_adrs)} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
