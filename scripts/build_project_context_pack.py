#!/usr/bin/env python3
"""Build a project-context snapshot from canonical OpenBar repository documents.

The generated files are convenience snapshots for external AI/project-context systems.
The repository documents referenced below remain canonical.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]

SOURCES: dict[str, tuple[str, ...]] = {
    "00_PROJECT_CONTEXT.md": ("README.md", "VISION.md"),
    "01_VISION_AND_PRODUCT.md": ("VISION.md", "docs/product/PRODUCT_STRATEGY.md"),
    "02_ARCHITECTURE.md": (
        "docs/architecture/ARCHITECTURE.md",
        "docs/adr/0001-runtime-and-language-boundaries.md",
        "docs/adr/0005-tracker-frame-boundary.md",
    ),
    "03_M0_ROADMAP.md": ("docs/roadmap/M0.md", "docs/plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md"),
    "04_VALIDATION_PROTOCOL.md": ("docs/validation/M0_VALIDATION.md", "docs/validation/BENCHMARK.md"),
    "05_DOMAIN_MODEL.md": ("docs/data/ANALYSIS_SCHEMA.md", "docs/adr/0003-canonical-analysis-domain-model.md"),
    "06_LICENSING_AND_IP.md": ("docs/legal/LICENSING_STRATEGY.md", "docs/adr/0011-mit-licence.md"),
    "07_CLEAN_ROOM_BOUNDARIES.md": ("docs/clean-room/COMPETITOR_BOUNDARIES.md",),
    "08_ENGINEERING_PRINCIPLES.md": ("VISION.md", "AGENTS.md"),
    "09_AGENT_WORKFLOW.md": ("AGENTS.md", "SUBAGENT_ROUTING.md"),
}


def git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def source_header(output_name: str, sources: tuple[str, ...], commit: str) -> str:
    source_lines = "\n".join(f"- `{source}`" for source in sources)
    return (
        f"# OpenBar project-context snapshot: {output_name}\n\n"
        "> Generated from canonical repository documents. Do not treat this snapshot as a new source of truth.\n"
        f"> Repository commit: `{commit}`.\n\n"
        "Canonical sources included in this snapshot:\n\n"
        f"{source_lines}\n\n"
        "If this snapshot conflicts with the repository, the repository and accepted ADRs win.\n\n"
        "---\n\n"
    )


def render_sources(output_name: str, sources: tuple[str, ...], commit: str, root: Path) -> str:
    parts = [source_header(output_name, sources, commit)]
    for source in sources:
        path = root / source
        if not path.is_file():
            raise FileNotFoundError(f"missing canonical source: {source}")
        parts.append(f"## Canonical source: `{source}`\n\n")
        text = path.read_text(encoding="utf-8")
        parts.append(text.rstrip() + "\n\n")
    return "".join(parts).rstrip() + "\n"


def adr_summary(commit: str, root: Path) -> str:
    adr_dir = root / "docs" / "adr"
    rows = []
    for path in sorted(adr_dir.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        title = next((line.removeprefix("# ") for line in lines if line.startswith("# ")), path.name)
        status = next((line.removeprefix("- Status: ") for line in lines if line.startswith("- Status: ")), "unknown")
        date = next((line.removeprefix("- Date: ") for line in lines if line.startswith("- Date: ")), "unknown")
        supersedes = next((line.removeprefix("- Supersedes: ") for line in lines if line.startswith("- Supersedes: ")), None)
        rows.append((path.relative_to(root).as_posix(), title, status, date, supersedes))

    lines = [
        "# OpenBar decisions log snapshot",
        "",
        "> Generated from `docs/adr/`. Accepted ADRs are canonical; later accepted ADRs may supersede earlier ones.",
        f"> Repository commit: `{commit}`.",
        "",
        "| ADR | Decision | Status | Date | Supersedes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source, title, status, date, supersedes in rows:
        lines.append(f"| `{source}` | {title} | {status} | {date} | {supersedes or '—'} |")
    lines += [
        "",
        "## Current licensing decision",
        "",
        "ADR-0011 is the current accepted licensing decision: OpenBar is MIT licensed. ADR-0002 is retained as historical context and is superseded.",
        "",
    ]
    return "\n".join(lines)


def pack_readme(commit: str) -> str:
    names = [*SOURCES, "10_DECISIONS_LOG.md"]
    listing = "\n".join(f"{index + 1}. `{name}`" for index, name in enumerate(names))
    return (
        "# OpenBar project-context pack\n\n"
        "This directory is a generated convenience snapshot for systems that need uploaded/persistent project context.\n\n"
        f"Generated from repository commit `{commit}`. The Git repository remains canonical.\n\n"
        "Do not manually maintain independent product, architecture, validation or licensing decisions in the exported pack. Regenerate it after durable repository decisions change.\n\n"
        "## Included files\n\n"
        f"{listing}\n\n"
        "In particular, licensing is sourced from `docs/legal/LICENSING_STRATEGY.md` and ADR-0011, so historical ADR-0002/PolyForm text cannot silently become the current licensing posture again.\n"
    )


def write_pack(output_dir: Path, root: Path = ROOT, commit: str | None = None) -> list[Path]:
    commit = commit or git_commit(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for output_name, sources in SOURCES.items():
        destination = output_dir / output_name
        destination.write_text(render_sources(output_name, sources, commit, root), encoding="utf-8", newline="\n")
        written.append(destination)
    decisions = output_dir / "10_DECISIONS_LOG.md"
    decisions.write_text(adr_summary(commit, root), encoding="utf-8", newline="\n")
    written.append(decisions)
    readme = output_dir / "README.md"
    readme.write_text(pack_readme(commit), encoding="utf-8", newline="\n")
    written.append(readme)
    return sorted(written)


def write_zip(zip_path: Path, files: list[Path], base_dir: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            info = ZipInfo(path.relative_to(base_dir).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--zip", dest="zip_path", type=Path)
    result.add_argument("--commit", help="Override recorded source commit (useful for deterministic tests)")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    files = write_pack(args.output_dir, commit=args.commit)
    if args.zip_path:
        write_zip(args.zip_path, files, args.output_dir)
    print(f"project context pack: wrote {len(files)} files to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
