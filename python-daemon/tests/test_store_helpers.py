from __future__ import annotations

import unittest

from openclaw_android_daemon.aptoide import AptoideArtifact
from openclaw_android_daemon.store import enrich_store_results, select_store_candidate, store_query_score


def artifact(package: str, name: str, rank: str = "TRUSTED") -> AptoideArtifact:
    return AptoideArtifact(
        package=package,
        name=name,
        store_name="Aptoide",
        version_code=1,
        version_name="1.0",
        download_url=f"https://example.invalid/{package}.apk",
        md5sum=None,
        filesize=1,
        malware_rank=rank,
        source="test",
    )


class StoreHelperTests(unittest.TestCase):
    def test_store_query_score_prioritizes_exact_package(self) -> None:
        self.assertGreater(
            store_query_score("com.example.app", {"package": "com.example.app", "name": "Example"}),
            store_query_score("com.example.app", {"package": "com.other", "name": "Example"}),
        )

    def test_enrich_store_results_marks_exact_matches_and_sorts_by_score(self) -> None:
        results = enrich_store_results(
            "Spotify",
            [
                artifact("com.other.music", "Other Music"),
                artifact("com.spotify.music", "Spotify"),
            ],
            limit=10,
        )

        self.assertEqual(results[0]["package"], "com.spotify.music")
        self.assertTrue(results[0]["exact_name"])
        self.assertIn("score", results[0])

    def test_select_store_candidate_requires_clear_match_for_ambiguous_queries(self) -> None:
        chosen, reason = select_store_candidate(
            "music",
            [
                {"package": "a", "name": "Music One", "score": 250},
                {"package": "b", "name": "Music Two", "score": 245},
            ],
        )

        self.assertIsNone(chosen)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
