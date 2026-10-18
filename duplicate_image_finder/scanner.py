import hashlib
import logging
import os
import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from time import perf_counter

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import ImageInfo, ScanResult, SimilarGroup

LOGGER = logging.getLogger(__name__)
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
CHUNK_SIZE = 1024 * 1024


class ScanError(Exception):
    """Raised when a scan cannot be started."""


class Cache:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = Lock()
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS images "
            "(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, width INTEGER, "
            "height INTEGER, format TEXT, digest TEXT, phash TEXT)"
        )
        self.connection.commit()

    def get(self, path: Path, size: int, mtime_ns: int) -> tuple | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT width,height,format,digest,phash FROM images "
                "WHERE path=? AND size=? AND mtime_ns=?",
                (str(path), size, mtime_ns),
            ).fetchone()
        return row

    def put(self, info: ImageInfo, mtime_ns: int) -> None:
        with self.lock:
            previous = self.connection.execute(
                "SELECT digest,phash FROM images WHERE path=? AND size=? AND mtime_ns=?",
                (str(info.path), info.size_bytes, mtime_ns),
            ).fetchone()
            digest = info.exact_hash or (previous[0] if previous else None)
            phash = info.perceptual_hash or (previous[1] if previous else None)
            self.connection.execute(
                "INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(info.path),
                    info.size_bytes,
                    mtime_ns,
                    info.width,
                    info.height,
                    info.format,
                    digest,
                    phash,
                ),
            )

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


def discover_images(
    root: Path, recursive: bool = True, excludes: list[str] | None = None
) -> list[Path]:
    excluded = {item.casefold() for item in (excludes or [])}
    found: list[Path] = []
    if recursive:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [
                d
                for d in dirs
                if d.casefold() not in excluded and not (Path(current) / d).is_symlink()
            ]
            found.extend(
                path
                for name in files
                if Path(name).suffix.casefold() in SUPPORTED_SUFFIXES
                and not (path := Path(current) / name).is_symlink()
            )
    else:
        found.extend(
            p
            for p in root.iterdir()
            if p.is_file()
            and not p.is_symlink()
            and p.suffix.casefold() in SUPPORTED_SUFFIXES
        )
    return sorted(found)


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_image(path: Path, *, compute_phash: bool) -> tuple[int, int, str, str | None]:
    with Image.open(path) as image:
        image.seek(0)
        image_format = image.format or path.suffix[1:].upper()
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        perceptual_hash = str(imagehash.phash(normalized)) if compute_phash else None
        return normalized.width, normalized.height, image_format, perceptual_hash


def _analyze(path: Path, cache: Cache | None, *, compute_phash: bool) -> ImageInfo:
    stat = path.stat()
    if cache:
        row = cache.get(path, stat.st_size, stat.st_mtime_ns)
        if row and (not compute_phash or row[4] is not None):
            return ImageInfo(path, stat.st_size, row[0], row[1], row[2], row[3], row[4])
    width, height, image_format, phash = _read_image(path, compute_phash=compute_phash)
    info = ImageInfo(path, stat.st_size, width, height, image_format, None, phash)
    if cache:
        cache.put(info, stat.st_mtime_ns)
    return info


def _exact_groups(
    images: list[ImageInfo], workers: int, cache: Cache | None
) -> list[list[ImageInfo]]:
    by_size: dict[int, list[ImageInfo]] = defaultdict(list)
    for image in images:
        by_size[image.size_bytes].append(image)
    candidates = [
        image for group in by_size.values() if len(group) > 1 for image in group
    ]
    by_hash: dict[str, list[ImageInfo]] = defaultdict(list)
    pending: list[ImageInfo] = []
    for image in candidates:
        stat = image.path.stat()
        row = (
            cache.get(image.path, image.size_bytes, stat.st_mtime_ns) if cache else None
        )
        if row and row[3]:
            image.exact_hash = row[3]
            by_hash[image.exact_hash].append(image)
        else:
            pending.append(image)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        hashes = pool.map(lambda image: (image, _digest(image.path)), pending)
        for image, digest in hashes:
            image.exact_hash = digest
            by_hash[digest].append(image)
            if cache:
                cache.put(image, image.path.stat().st_mtime_ns)
    return [group for group in by_hash.values() if len(group) > 1]


def _similar_groups(images: list[ImageInfo], threshold: float) -> list[SimilarGroup]:
    # Partition each 64-bit hash into 7 bands. Any pair within 6 bits must
    # share at least one band; candidates are then checked by exact Hamming distance.
    buckets: dict[tuple[int, int], set[int]] = defaultdict(set)
    hashes = [
        int(image.perceptual_hash, 16) for image in images if image.perceptual_hash
    ]
    valid = [image for image in images if image.perceptual_hash]
    pairs: set[tuple[int, int]] = set()
    for index, value in enumerate(hashes):
        for band in range(7):
            start = band * 9
            width = 10 if band == 6 else 9
            chunk = (value >> start) & ((1 << width) - 1)
            key = (band, chunk)
            pairs.update((other, index) for other in buckets[key])
            buckets[key].add(index)
    parents = list(range(len(valid)))

    def find(item: int) -> int:
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    matches: list[tuple[int, int, float]] = []
    for left, right in pairs:
        distance = (hashes[left] ^ hashes[right]).bit_count()
        score = 100 * (1 - distance / 64)
        if score >= threshold:
            matches.append((left, right, score))
    for left, right, _ in matches:
        a, b = find(left), find(right)
        if a != b:
            parents[b] = a
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(valid)):
        groups[find(index)].append(index)
    output: list[SimilarGroup] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_set = set(members)
        scores = [
            score
            for left, right, score in matches
            if left in member_set and right in member_set
        ]
        output.append(SimilarGroup([valid[index] for index in members], min(scores)))
    return output


def scan(
    root: Path,
    *,
    recursive: bool = True,
    excludes: list[str] | None = None,
    exact_only: bool = False,
    similar_only: bool = False,
    threshold: float = 90,
    workers: int | None = None,
    cache_path: Path | None = None,
) -> ScanResult:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ScanError(f"Not a readable directory: {root}")
    started = perf_counter()
    paths = discover_images(root, recursive, excludes)
    cache = Cache(cache_path) if cache_path else None
    images: list[ImageInfo] = []
    skipped: list[tuple[Path, str]] = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                path: pool.submit(_analyze, path, cache, compute_phash=not exact_only)
                for path in paths
            }
            for path, future in futures.items():
                try:
                    images.append(future.result())
                except (
                    OSError,
                    UnidentifiedImageError,
                    ValueError,
                    EOFError,
                    Image.DecompressionBombError,
                ) as error:
                    LOGGER.warning("Could not read %s: %s", path, error)
                    skipped.append((path, str(error)))
        exact = (
            []
            if similar_only
            else _exact_groups(
                images, workers or min(32, (os.cpu_count() or 1) + 4), cache
            )
        )
        similar = [] if exact_only else _similar_groups(images, threshold)
    finally:
        if cache:
            cache.close()
    return ScanResult(
        root, len(paths), images, exact, similar, skipped, perf_counter() - started
    )
