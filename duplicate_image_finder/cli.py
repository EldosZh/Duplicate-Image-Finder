import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .actions import move_duplicates
from .reports import write_html, write_json
from .scanner import ScanError, scan


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="duplicate-images",
        description="Find exact and visually similar images safely.",
    )
    result.add_argument("directory", type=Path, nargs="?", help="directory to scan")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument(
        "--similarity",
        type=float,
        default=90,
        help="minimum perceptual similarity (0-100; default: 90)",
    )
    result.add_argument(
        "--exact-only", action="store_true", help="skip perceptual hashing comparisons"
    )
    result.add_argument(
        "--similar-only", action="store_true", help="show only visually similar matches"
    )
    result.add_argument(
        "--no-recursive", action="store_true", help="scan only the top-level directory"
    )
    result.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="exclude directory name (repeatable)",
    )
    result.add_argument(
        "--json", action="store_true", help="write JSON report to stdout"
    )
    result.add_argument(
        "--html", type=Path, metavar="FILE", help="write an HTML report"
    )
    result.add_argument(
        "--no-cache", action="store_true", help="disable SQLite metadata cache"
    )
    result.add_argument(
        "--clear-cache",
        action="store_true",
        help="clear the scan cache before scanning",
    )
    result.add_argument("--cache-file", type=Path, help=argparse.SUPPRESS)
    result.add_argument(
        "--workers", type=int, default=min(32, (os.cpu_count() or 1) + 4)
    )
    result.add_argument(
        "--move-duplicates",
        type=Path,
        metavar="DESTINATION",
        help="move exact duplicates, keeping the first in each group",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="show planned moves without changing files",
    )
    result.add_argument("--verbose", action="store_true", help="show scan warnings")
    return result


def _human(result) -> str:
    lines = [
        "Duplicate Image Finder",
        "=" * 24,
        f"Scanning: {result.root}",
        "",
        f"Images discovered: {result.discovered}",
        f"Images processed:  {len(result.images)}",
        f"Skipped:           {len(result.skipped)}",
        f"Total size:         {sum(i.size_bytes for i in result.images):,} bytes",
        "",
        "Exact Duplicates",
        "----------------",
    ]
    saved = 0
    for index, group in enumerate(result.exact_groups, 1):
        lines.append(f"Group #{index}")
        lines.extend(f"  {image.path.relative_to(result.root)}" for image in group)
        lines.append(f"  Files: {len(group)}")
        saved += sum(image.size_bytes for image in group[1:])
        lines.append("")
    if not result.exact_groups:
        lines.append("None found\n")
    lines.extend(["Visually Similar Images", "-----------------------"])
    for index, group in enumerate(result.similar_groups, 1):
        lines.append(f"Group #{index} ({group.similarity:.1f}%)")
        lines.extend(
            f"  {image.path.relative_to(result.root)}" for image in group.images
        )
        lines.append("")
    if not result.similar_groups:
        lines.append("None found\n")
    lines.extend(
        [
            "Summary",
            "-------",
            f"Exact duplicate groups: {len(result.exact_groups)}",
            f"Similar image groups: {len(result.similar_groups)}",
            f"Potential exact savings: {saved:,} bytes",
            f"Duration: {result.duration_seconds:.2f}s",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.directory is None:
        parser().error("directory is required")
    if args.exact_only and args.similar_only:
        parser().error("--exact-only and --similar-only cannot be used together")
    if not 0 <= args.similarity <= 100:
        parser().error("--similarity must be between 0 and 100")
    if args.workers < 1:
        parser().error("--workers must be at least 1")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s: %(message)s",
    )
    cache_file = (
        args.cache_file
        or Path.home() / ".cache" / "duplicate-image-finder" / "cache.sqlite3"
    )
    if args.clear_cache and cache_file.exists():
        cache_file.unlink()
    if args.no_cache:
        cache_path = None
    else:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_path = cache_file
    try:
        result = scan(
            args.directory,
            recursive=not args.no_recursive,
            excludes=args.exclude,
            exact_only=args.exact_only,
            similar_only=args.similar_only,
            threshold=args.similarity,
            workers=args.workers,
            cache_path=cache_path,
        )
    except ScanError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(write_json(result))
    else:
        print(_human(result))
    if args.html:
        write_html(result, args.html)
    if args.move_duplicates:
        try:
            moves = move_duplicates(
                result.exact_groups,
                args.move_duplicates,
                dry_run=args.dry_run,
                source_root=result.root,
            )
        except (OSError, ValueError) as error:
            print(f"Move error: {error}", file=sys.stderr)
            return 2
        verb = "Would move" if args.dry_run else "Moved"
        for source, target in moves:
            print(
                f"{verb}: {source} -> {target}",
                file=sys.stderr if args.json else sys.stdout,
            )
        if not moves:
            print(
                "No exact duplicates to move.",
                file=sys.stderr if args.json else sys.stdout,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
