import html
import json
from pathlib import Path

from .models import ScanResult


def _data(result: ScanResult) -> dict:
    exact_saved = sum(
        sum(item.size_bytes for item in group[1:]) for group in result.exact_groups
    )
    return {
        "root": str(result.root),
        "discovered": result.discovered,
        "processed": len(result.images),
        "skipped": len(result.skipped),
        "total_size_bytes": sum(item.size_bytes for item in result.images),
        "exact_duplicate_groups": [
            [str(item.path) for item in group] for group in result.exact_groups
        ],
        "similar_groups": [
            {
                "files": [str(item.path) for item in group.images],
                "similarity": round(group.similarity, 1),
            }
            for group in result.similar_groups
        ],
        "potential_exact_savings_bytes": exact_saved,
        "duration_seconds": round(result.duration_seconds, 3),
        "warnings": [
            {"path": str(path), "error": error} for path, error in result.skipped
        ],
    }


def write_json(result: ScanResult, destination: Path | None = None) -> str:
    output = json.dumps(_data(result), indent=2)
    if destination:
        destination.write_text(output + "\n", encoding="utf-8")
    return output


def write_html(result: ScanResult, destination: Path) -> None:
    data = _data(result)
    exact = "".join(
        "<li>" + "<br>".join(html.escape(path) for path in group) + "</li>"
        for group in data["exact_duplicate_groups"]
    )
    similar = "".join(
        "<li>"
        + "<br>".join(html.escape(path) for path in group["files"])
        + f"<br>Similarity: {group['similarity']:.1f}%</li>"
        for group in data["similar_groups"]
    )
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Duplicate Image Finder report</title><style>body{{font:16px system-ui;max-width:900px;margin:3rem auto;padding:0 1rem;color:#18212b}}li{{margin:1rem 0;padding:1rem;background:#f2f5f8;border-radius:.5rem;overflow-wrap:anywhere}}small{{color:#536273}}</style><h1>Duplicate Image Finder</h1><p>Scanned {data["discovered"]} images; processed {data["processed"]}; skipped {data["skipped"]}.</p><p>Potential exact duplicate savings: {data["potential_exact_savings_bytes"]} bytes.</p><h2>Exact duplicates</h2><ul>{exact or "<li>None found</li>"}</ul><h2>Visually similar images</h2><ul>{similar or "<li>None found</li>"}</ul><small>Similarity is an approximation based on perceptual hash distance.</small></html>"""
    destination.write_text(document, encoding="utf-8")
