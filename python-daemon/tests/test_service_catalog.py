from __future__ import annotations

import unittest

from openclaw_android_daemon.service_catalog import resolve_services


class ResolveServicesTests(unittest.TestCase):
    def test_amazon_shopping_matches_first(self) -> None:
        matches = resolve_services("add this to my cart on amazon")

        self.assertTrue(matches)
        self.assertEqual(matches[0].candidate.service, "amazon")
        self.assertEqual(matches[0].candidate.packages, ("com.amazon.mShop.android.shopping",))

    def test_prime_video_matches_video_service(self) -> None:
        matches = resolve_services("watch prime video")

        self.assertTrue(matches)
        self.assertEqual(matches[0].candidate.service, "prime_video")

    def test_generic_requests_do_not_false_positive(self) -> None:
        self.assertEqual(resolve_services("chat with someone"), [])
        self.assertEqual(resolve_services("listen to music"), [])

    def test_youtube_is_not_routed_to_android(self) -> None:
        self.assertEqual(resolve_services("open youtube"), [])


if __name__ == "__main__":
    unittest.main()
