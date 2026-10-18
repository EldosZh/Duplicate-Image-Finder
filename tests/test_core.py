import json

from PIL import Image, ImageDraw

from duplicate_image_finder.cli import main
from duplicate_image_finder.reports import write_json
from duplicate_image_finder.scanner import discover_images, scan


def make_image(path, color=(240, 30, 20)):
    Image.new("RGB", (64, 64), color).save(path)


def test_finds_exact_and_similar(tmp_path):
    original = tmp_path / "one.jpg"
    make_image(original)
    (tmp_path / "copy.jpg").write_bytes(original.read_bytes())
    make_image(tmp_path / "other.png", (20, 40, 220))
    result = scan(tmp_path, cache_path=None)
    assert len(result.exact_groups) == 1
    assert len(result.similar_groups) == 1
    assert result.images[0].width == 64


def test_json_and_case_insensitive_extensions(tmp_path):
    make_image(tmp_path / "CAPS.JPG")
    assert len(discover_images(tmp_path)) == 1
    result = scan(tmp_path, exact_only=True)
    assert json.loads(write_json(result))["processed"] == 1


def test_cli_json(tmp_path, capsys):
    make_image(tmp_path / "a.png")
    assert main([str(tmp_path), "--json", "--no-cache"]) == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 1


def test_move_dry_run_does_not_change_source(tmp_path):
    from duplicate_image_finder.actions import move_duplicates

    make_image(tmp_path / "a.jpg")
    (tmp_path / "b.jpg").write_bytes((tmp_path / "a.jpg").read_bytes())
    result = scan(tmp_path, exact_only=True, cache_path=None)
    before = sorted(p.name for p in tmp_path.iterdir())
    move_duplicates(
        result.exact_groups,
        tmp_path.parent / "review",
        dry_run=True,
        source_root=tmp_path,
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_cache_reuses_and_invalidates_hashes(tmp_path, monkeypatch):
    from duplicate_image_finder import scanner

    image = tmp_path / "cached.jpg"
    original_image = Image.new("RGB", (64, 64), "white")
    ImageDraw.Draw(original_image).rectangle((4, 4, 40, 50), fill="black")
    original_image.save(image)
    cache_path = tmp_path / "cache.sqlite3"
    first = scan(tmp_path, cache_path=cache_path)
    first_hash = first.images[0].perceptual_hash

    def unexpected_read(*args, **kwargs):
        raise AssertionError("unchanged image should use cached metadata")

    monkeypatch.setattr(scanner, "_read_image", unexpected_read)
    second = scan(tmp_path, cache_path=cache_path)
    assert second.images[0].perceptual_hash == first_hash

    monkeypatch.undo()
    make_image(image, (30, 180, 70))
    third = scan(tmp_path, cache_path=cache_path)
    assert third.images[0].perceptual_hash != first_hash


def test_exact_only_does_not_calculate_phash(tmp_path, monkeypatch):
    from duplicate_image_finder import scanner

    make_image(tmp_path / "image.png")
    original_read = scanner._read_image
    called = []

    def record_mode(path, *, compute_phash):
        called.append(compute_phash)
        return original_read(path, compute_phash=compute_phash)

    monkeypatch.setattr(scanner, "_read_image", record_mode)
    scan(tmp_path, exact_only=True, cache_path=None)
    assert called == [False]


def test_html_escapes_untrusted_filename(tmp_path):
    from duplicate_image_finder.reports import write_html

    make_image(tmp_path / "evil&.jpg")
    (tmp_path / "copy.jpg").write_bytes((tmp_path / "evil&.jpg").read_bytes())
    report = tmp_path / "report.html"
    write_html(scan(tmp_path, cache_path=None), report)
    content = report.read_text(encoding="utf-8")
    assert "evil&amp;.jpg" in content
    assert "evil&.jpg" not in content


def test_actual_move_keeps_one_exact_copy(tmp_path):
    from duplicate_image_finder.actions import move_duplicates

    source = tmp_path / "source"
    destination = tmp_path / "review"
    source.mkdir()
    make_image(source / "a.jpg")
    (source / "b.jpg").write_bytes((source / "a.jpg").read_bytes())
    result = scan(source, exact_only=True, cache_path=None)
    moves = move_duplicates(
        result.exact_groups, destination, dry_run=False, source_root=source
    )
    assert len(moves) == 1
    assert len(list(source.glob("*.jpg"))) == 1
    assert len(list(destination.rglob("*.jpg"))) == 1


def test_corrupt_images_are_skipped(tmp_path):
    make_image(tmp_path / "valid.png")
    (tmp_path / "broken.jpg").write_bytes(b"not an image")
    result = scan(tmp_path, cache_path=None)
    assert result.discovered == 2
    assert len(result.images) == 1
    assert len(result.skipped) == 1


def test_move_destination_inside_source_is_rejected(tmp_path):
    import pytest

    from duplicate_image_finder.actions import move_duplicates

    make_image(tmp_path / "a.jpg")
    (tmp_path / "b.jpg").write_bytes((tmp_path / "a.jpg").read_bytes())
    result = scan(tmp_path, exact_only=True, cache_path=None)
    with pytest.raises(ValueError, match="inside the scan source tree"):
        move_duplicates(
            result.exact_groups,
            tmp_path / "review",
            dry_run=True,
            source_root=tmp_path,
        )
