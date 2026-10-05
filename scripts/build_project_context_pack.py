#!/usr/bin/env python3
"""Build a project-context snapshot from canonical OpenBar repository documents.

The generated files are convenience snapshots for external AI/project-context systems.
The repository documents referenced below remain canonical.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]

SOURCES: dict[str, tuple[str, ...]] = {
    "00_PROJECT_CONTEXT.md": ("README.md", "VISION.md"),
    "01_VISION_AND_PRODUCT.md": ("VISION.md", "docs/product/PRODUCT_STRATEGY.md"),
    "02_ARCHITECTURE.md": ("docs/architecture/ARCHITECTURE.md",),
    "03_M0_ROADMAP.md": ("docs/roadmap/M0.md", "docs/plans/M0_BAR_PATH_MEASUREMENT_EXPERIMENT_PLAN.md"),
    "04_VALIDATION_PROTOCOL.md": ("docs/validation/M0_VALIDATION.md", "docs/validation/BENCHMARK.md"),
    "05_DOMAIN_MODEL.md": ("docs/data/ANALYSIS_SCHEMA.md",),
    "06_LICENSING_AND_IP.md": ("docs/legal/LICENSING_STRATEGY.md",),
    "07_CLEAN_ROOM_BOUNDARIES.md": ("docs/clean-room/COMPETITOR_BOUNDARIES.md",),
    "08_ENGINEERING_PRINCIPLES.md": ("VISION.md", "AGENTS.md"),
    "09_AGENT_WORKFLOW.md": ("AGENTS.md", "SUBAGENT_ROUTING.md"),
}

GENERATED_NAMES = frozenset({*SOURCES, "10_DECISIONS_LOG.md", "README.md"})


def git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    return result.stdout


def git_commit(root: Path, ref: str = "HEAD") -> str:
    return git_bytes(root, "rev-parse", "--verify", f"{ref}^{{commit}}").decode("ascii").strip()


def repository_text(root: Path, commit: str, source: str) -> str:
    try:
        content = git_bytes(root, "show", f"{commit}:{source}")
    except subprocess.CalledProcessError as error:
        raise FileNotFoundError(f"missing canonical source at {commit}: {source}") from error
    return content.decode("utf-8")


def adr_paths(root: Path, commit: str) -> list[str]:
    output = git_bytes(root, "ls-tree", "-r", "--name-only", commit, "--", "docs/adr")
    return sorted(line for line in output.decode("utf-8").splitlines() if line.endswith(".md"))


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
        parts.append(f"## Canonical source: `{source}`\n\n")
        text = repository_text(root, commit, source)
        parts.append(text.rstrip() + "\n\n")
    return "".join(parts).rstrip() + "\n"


def metadata_value(lines: list[str], name: str) -> str | None:
    prefix = f"- {name}:"
    return next((line[len(prefix) :].strip() for line in lines if line.startswith(prefix)), None)


def markdown_cell(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("|", r"\|").replace("\n", " ")


def adr_snapshot(commit: str, root: Path) -> str:
    records: list[tuple[str, str, str | None, str | None, str | None, str]] = []
    for source in adr_paths(root, commit):
        text = repository_text(root, commit, source)
        lines = text.splitlines()
        title = next((line.removeprefix("# ") for line in lines if line.startswith("# ")), source)
        records.append(
            (
                source,
                title,
                metadata_value(lines, "Status"),
                metadata_value(lines, "Date"),
                metadata_value(lines, "Supersedes"),
                text,
            )
        )

    lines = [
        "# OpenBar decisions snapshot",
        "",
        "> Generated from every Markdown ADR committed under `docs/adr/`.",
        "> Accepted ADRs are canonical; status/supersession metadata in the ADRs determines which decisions are current.",
        f"> Repository commit: `{commit}`.",
        "",
        "| ADR | Decision | Status | Date | Supersedes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source, title, status, date, supersedes, _ in records:
        lines.append(
            f"| `{source}` | {markdown_cell(title)} | {markdown_cell(status)} | "
            f"{markdown_cell(date)} | {markdown_cell(supersedes)} |"
        )

    for source, _, _, _, _, text in records:
        lines.extend(
            [
                "",
                "---",
                "",
                f"## Canonical ADR: `{source}`",
                "",
                text.rstrip(),
            ]
        )
    lines.append("")
    return "\n".join(lines)


def pack_readme(commit: str) -> str:
    names = [*SOURCES, "10_DECISIONS_LOG.md"]
    listing = "\n".join(f"{index + 1}. `{name}`" for index, name in enumerate(names))
    return (
        "# OpenBar project-context pack\n\n"
        "This directory is a generated convenience snapshot for systems that need uploaded/persistent project context.\n\n"
        f"Generated from repository commit `{commit}`. The Git repository remains canonical.\n\n"
        "All content is read from that committed Git tree, not from uncommitted working-tree edits. "
        "Do not manually maintain independent product, architecture, validation or licensing decisions in the exported pack. "
        "Regenerate it after durable repository decisions change.\n\n"
        "## Included files\n\n"
        f"{listing}\n\n"
        "Current licensing is sourced from `docs/legal/LICENSING_STRATEGY.md`. "
        "Every committed ADR is collected dynamically into `10_DECISIONS_LOG.md`, including historical/superseded decisions.\n"
    )


def prepare_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    unmanaged = sorted(path.name for path in output_dir.iterdir() if path.name not in GENERATED_NAMES)
    if unmanaged:
        names = ", ".join(unmanaged)
        raise ValueError(
            f"output directory contains unmanaged entries ({names}); use an empty/generated-only directory"
        )


def write_pack(output_dir: Path, root: Path = ROOT, ref: str = "HEAD") -> list[Path]:
    commit = git_commit(root, ref)
    prepare_output_dir(output_dir)
    written = []
    for output_name, sources in SOURCES.items():
        destination = output_dir / output_name
        destination.write_text(
            render_sources(output_name, sources, commit, root), encoding="utf-8", newline="\n"
        )
        written.append(destination)
    decisions = output_dir / "10_DECISIONS_LOG.md"
    decisions.write_text(adr_snapshot(commit, root), encoding="utf-8", newline="\n")
    written.append(decisions)
    readme = output_dir / "README.md"
    readme.write_text(pack_readme(commit), encoding="utf-8", newline="\n")
    written.append(readme)
    return sorted(written)


def write_zip(zip_path: Path, files: list[Path], base_dir: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "w", compression=ZIP_STORED) as archive:
        for path in sorted(files, key=lambda item: item.relative_to(base_dir).as_posix()):
            info = ZipInfo(path.relative_to(base_dir).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--zip", dest="zip_path", type=Path)
    result.add_argument(
        "--ref",
        default="HEAD",
        help="Git branch, tag or commit to snapshot (resolved to an immutable commit; default: HEAD)",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    files = write_pack(args.output_dir, ref=args.ref)
    if args.zip_path:
        write_zip(args.zip_path, files, args.output_dir)
    print(f"project context pack: wrote {len(files)} files to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
