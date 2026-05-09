from __future__ import annotations

import hashlib
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from .utils import ensure_dir


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_store_artifact(artifact: Any, download_dir: Path) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(artifact.download_url)
    suffix = Path(parsed.path).suffix or ".apk"
    basename = Path(parsed.path).name or f"{artifact.package}{suffix}"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", basename)
    if artifact.md5sum:
        safe_name = f"{artifact.package}-{artifact.md5sum}{suffix}"
    target = ensure_dir(download_dir) / safe_name
    if target.exists() and target.stat().st_size > 0 and (not artifact.md5sum or file_md5(target) == artifact.md5sum):
        return {"ok": True, "path": str(target), "cached": True, "download_url": artifact.download_url}

    tmp_target = target.with_suffix(target.suffix + ".part")
    proc = subprocess.run(
        ["curl", "-fsSL", artifact.download_url, "-o", str(tmp_target)],
        check=False,
        capture_output=True,
        text=True,
        timeout=600.0,
    )
    if proc.returncode != 0:
        tmp_target.unlink(missing_ok=True)
        return {
            "ok": False,
            "error": (proc.stderr or proc.stdout or "").strip() or "Failed to download store artifact",
            "download_url": artifact.download_url,
        }
    if artifact.md5sum:
        actual_md5 = file_md5(tmp_target)
        if actual_md5 != artifact.md5sum:
            tmp_target.unlink(missing_ok=True)
            return {
                "ok": False,
                "error": f"Downloaded APK checksum mismatch (expected {artifact.md5sum}, got {actual_md5})",
                "download_url": artifact.download_url,
            }
    tmp_target.replace(target)
    return {"ok": True, "path": str(target), "cached": False, "download_url": artifact.download_url}


def store_query_score(query: str, item: dict[str, Any]) -> int:
    query_text = str(query or "").strip().casefold()
    label = str(item.get("name") or "").strip().casefold()
    package = str(item.get("package") or "").strip().casefold()
    score = 0
    if package == query_text:
        score += 500
    if label == query_text:
        score += 420
    if query_text and label.startswith(query_text):
        score += 220
    if query_text and query_text in label:
        score += 160
    if query_text and query_text in package:
        score += 140
    if str(item.get("malware_rank") or "").upper() == "TRUSTED":
        score += 40
    if label and package and label in package:
        score += 20
    return score


def enrich_store_results(query: str, artifacts: list[Any], limit: int) -> list[dict[str, Any]]:
    query_text = str(query).strip().casefold()
    results: list[dict[str, Any]] = []
    for artifact in artifacts:
        item = artifact.to_dict()
        name = str(item.get("name") or "").strip()
        package = str(item.get("package") or "").strip()
        item["exact_name"] = bool(name and name.casefold() == query_text)
        item["exact_package"] = bool(package and package.casefold() == query_text)
        item["score"] = store_query_score(query, item)
        results.append(item)
    results.sort(key=lambda item: (int(item.get("score") or 0), item.get("name") or "", item.get("package") or ""), reverse=True)
    return results[: max(1, min(limit, 25))]


def select_store_candidate(query: str | None, candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    exact_candidates = [item for item in candidates if item.get("exact_name") or item.get("exact_package")]
    chosen = None
    selection_reason = None
    if len(exact_candidates) == 1:
        chosen = exact_candidates[0]
        selection_reason = "single_exact_match"
    elif len(candidates) == 1:
        chosen = candidates[0]
        selection_reason = "single_candidate"
    else:
        top = candidates[0]
        second_score = int(candidates[1].get("score") or 0) if len(candidates) > 1 else -9999
        top_score = int(top.get("score") or 0)
        if top_score >= 260 and top_score - second_score >= 120:
            chosen = top
            selection_reason = "clear_top_match"
    return chosen, selection_reason
