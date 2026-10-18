import os
import shutil
from pathlib import Path

from .models import ImageInfo


def move_duplicates(
    groups: list[list[ImageInfo]],
    destination: Path,
    *,
    dry_run: bool,
    source_root: Path,
) -> list[tuple[Path, Path]]:
    destination = destination.expanduser().resolve()
    source_root = source_root.resolve()
    try:
        destination.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise ValueError("Move destination cannot be inside the scan source tree")
    moved: list[tuple[Path, Path]] = []
    for group in groups:
        for image in group[1:]:
            source = image.path.resolve()
            relative = source.relative_to(source_root)
            target = destination / relative
            try:
                target.resolve(strict=False).relative_to(destination)
            except ValueError as error:
                raise ValueError(
                    "Move target resolves outside the destination"
                ) from error
            target.parent.mkdir(parents=True, exist_ok=True) if not dry_run else None
            if target.exists():
                stem, suffix, number = target.stem, target.suffix, 1
                while target.exists():
                    target = target.with_name(f"{stem}_{number}{suffix}")
                    number += 1
            moved.append((source, target))
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(os.fspath(source), os.fspath(target))
    return moved
