from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw_android_daemon.aptoide import AptoideArtifact
from openclaw_android_daemon.config import Settings
from openclaw_android_daemon.controller import AndroidRuntime


class AndroidRuntimeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.settings = Settings(
            screenshot_dir=root / "screenshots",
            download_dir=root / "downloads",
            llm_config_path=root / "llm.json",
            llm_models_path=root / "models.json",
            llm_settings_path=root / "settings.json",
        )
        self.runtime = AndroidRuntime(self.settings)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_task_route_prefers_installed_native_app(self) -> None:
        with patch.object(
            self.runtime,
            "apps_list",
            return_value={
                "ok": True,
                "apps": [{"package": "com.amazon.mShop.android.shopping", "label": "Amazon Shopping"}],
            },
        ):
            result = self.runtime.task_route("add this to my cart on amazon")

        self.assertTrue(result["ok"])
        self.assertEqual(result["service"], "amazon")
        self.assertEqual(result["preferred_backend"], "native_app")
        self.assertEqual(result["selected_match"]["native_package"], "com.amazon.mShop.android.shopping")
        self.assertEqual(result["selected_match"]["recommended_action"]["action"], "app_open")

    def test_task_route_offers_install_option_when_native_is_missing(self) -> None:
        artifact = AptoideArtifact(
            package="com.spotify.music",
            name="Spotify",
            store_name="Aptoide",
            version_code=1,
            version_name="1.0",
            download_url="https://example.invalid/spotify.apk",
            md5sum=None,
            filesize=123,
            malware_rank="TRUSTED",
            source="aptoide_meta",
        )
        with (
            patch.object(self.runtime, "apps_list", return_value={"ok": True, "apps": []}),
            patch.object(self.runtime.aptoide, "get_meta", return_value=artifact),
        ):
            result = self.runtime.task_route("open spotify")

        self.assertTrue(result["ok"])
        self.assertEqual(result["service"], "spotify")
        self.assertEqual(result["preferred_backend"], "android_web")
        self.assertEqual(result["selected_match"]["recommended_action"]["action"], "url_open")
        self.assertEqual(result["selected_match"]["install_option"]["package"], "com.spotify.music")
        self.assertTrue(result["selected_match"]["can_route_to_android"])

    def test_task_route_degrades_to_web_when_store_metadata_is_offline(self) -> None:
        with (
            patch.object(self.runtime, "apps_list", return_value={"ok": True, "apps": []}),
            patch.object(self.runtime.aptoide, "get_meta", side_effect=TimeoutError("store offline")),
        ):
            result = self.runtime.task_route("open amazon")

        self.assertTrue(result["ok"])
        self.assertEqual(result["service"], "amazon")
        self.assertEqual(result["preferred_backend"], "android_web")
        self.assertEqual(result["selected_match"]["recommended_action"]["action"], "url_open")
        self.assertIsNone(result["selected_match"]["install_option"])

    def test_task_route_can_prefer_android_web_over_installed_native(self) -> None:
        settings = Settings(
            screenshot_dir=self.settings.screenshot_dir,
            download_dir=self.settings.download_dir,
            llm_config_path=self.settings.llm_config_path,
            llm_models_path=self.settings.llm_models_path,
            llm_settings_path=self.settings.llm_settings_path,
            prefer_native_apps=False,
        )
        runtime = AndroidRuntime(settings)
        with patch.object(
            runtime,
            "apps_list",
            return_value={
                "ok": True,
                "apps": [{"package": "com.amazon.mShop.android.shopping", "label": "Amazon Shopping"}],
            },
        ):
            result = runtime.task_route("add this to my cart on amazon")

        self.assertEqual(result["preferred_backend"], "android_web")
        self.assertEqual(result["selected_match"]["recommended_action"]["action"], "url_open")

    def test_task_route_returns_desktop_fallback_for_unknown_goal(self) -> None:
        with patch.object(self.runtime, "apps_list", return_value={"ok": True, "apps": []}):
            result = self.runtime.task_route("write a haiku about satellites")

        self.assertTrue(result["ok"])
        self.assertEqual(result["preferred_backend"], "desktop_web")
        self.assertFalse(result["can_route_to_android"])
        self.assertEqual(result["matches"], [])

    def test_scroll_swipe_path_stays_inside_lower_bounds(self) -> None:
        bounds = (0, 1000, 500, 1200)

        forward = self.runtime._swipe_path(bounds, "forward")
        backward = self.runtime._swipe_path(bounds, "backward")

        self.assertEqual(forward, (250, 1160, 250, 1040))
        self.assertEqual(backward, (250, 1040, 250, 1160))
        self.assertTrue(all(1000 <= y <= 1200 for y in (forward[1], forward[3], backward[1], backward[3])))


if __name__ == "__main__":
    unittest.main()
