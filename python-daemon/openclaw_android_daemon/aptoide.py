from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(slots=True)
class AptoideArtifact:
    package: str
    name: str | None
    store_name: str | None
    version_code: int | None
    version_name: str | None
    download_url: str
    md5sum: str | None
    filesize: int | None
    malware_rank: str | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "name": self.name,
            "store_name": self.store_name,
            "version_code": self.version_code,
            "version_name": self.version_name,
            "download_url": self.download_url,
            "md5sum": self.md5sum,
            "filesize": self.filesize,
            "malware_rank": self.malware_rank,
            "source": self.source,
        }


class AptoideClient:
    def __init__(
        self,
        *,
        meta_base_url: str = "https://ws2.aptoide.com/api/7/app/getMeta/package_name=",
        search_url: str = "https://ws2.aptoide.com/api/7/apps/search",
        timeout_s: float = 20.0,
    ) -> None:
        self.meta_base_url = meta_base_url.rstrip("=")
        self.search_url = search_url
        self.timeout_s = timeout_s

    def _get_json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.get(
            url,
            params=params,
            timeout=self.timeout_s,
            headers={"accept": "application/json", "user-agent": "openclaw-android-waydroid/0.1"},
        )
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(f"Unexpected Aptoide response shape from {url}")

    @staticmethod
    def _status_ok(payload: dict[str, Any]) -> bool:
        status = str((payload.get("info") or {}).get("status") or "").upper()
        return status in {"OK", "QUEUED"}

    @staticmethod
    def _artifact_from_meta(payload: dict[str, Any], *, package: str | None = None, source: str) -> AptoideArtifact | None:
        data = payload.get("data") or {}
        file_info = data.get("file") or {}
        download_url = str(file_info.get("path") or file_info.get("path_alt") or "").strip()
        resolved_package = str(data.get("package_name") or package or "").strip()
        if not download_url or not resolved_package:
            return None
        malware = file_info.get("malware") or {}
        return AptoideArtifact(
            package=resolved_package,
            name=(data.get("name") or None),
            store_name=((data.get("store") or {}).get("name") if isinstance(data.get("store"), dict) else None),
            version_code=(int(data.get("vercode")) if str(data.get("vercode") or "").isdigit() else None),
            version_name=(str(data.get("vername") or "").strip() or None),
            download_url=download_url,
            md5sum=(str(file_info.get("md5sum") or "").strip() or None),
            filesize=(int(file_info.get("filesize")) if str(file_info.get("filesize") or "").isdigit() else None),
            malware_rank=(str(malware.get("rank") or "").strip() or None),
            source=source,
        )

    def get_meta(self, package: str) -> AptoideArtifact | None:
        payload = self._get_json(f"{self.meta_base_url}={package}")
        if not payload or not self._status_ok(payload):
            return None
        return self._artifact_from_meta(payload, package=package, source="aptoide_meta")

    def search(self, query: str, *, limit: int = 10) -> list[AptoideArtifact]:
        payload = self._get_json(self.search_url, params={"query": query, "limit": max(1, min(limit, 25))})
        if not payload or not self._status_ok(payload):
            return []
        items = (((payload.get("datalist") or {}).get("list")) or [])
        results: list[AptoideArtifact] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            package = str(item.get("package") or "").strip()
            file_info = item.get("file") or {}
            download_url = str(file_info.get("path") or file_info.get("path_alt") or "").strip()
            if not package or not download_url:
                continue
            malware = file_info.get("malware") or {}
            version_code_raw = item.get("vercode") or item.get("file_vercode")
            filesize_raw = file_info.get("filesize")
            results.append(
                AptoideArtifact(
                    package=package,
                    name=(item.get("name") or None),
                    store_name=((item.get("store") or {}).get("name") if isinstance(item.get("store"), dict) else None),
                    version_code=(int(version_code_raw) if str(version_code_raw or "").isdigit() else None),
                    version_name=(str(item.get("vername") or "").strip() or None),
                    download_url=download_url,
                    md5sum=(str(file_info.get("md5sum") or "").strip() or None),
                    filesize=(int(filesize_raw) if str(filesize_raw or "").isdigit() else None),
                    malware_rank=(str(malware.get("rank") or "").strip() or None),
                    source="aptoide_search",
                )
            )
        return results
