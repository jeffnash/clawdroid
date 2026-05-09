from __future__ import annotations

import os
import unittest

from openclaw_android_daemon.config import Settings
from openclaw_android_daemon.controller import AndroidRuntime


RUN_LIVE = os.environ.get("OPENCLAW_ANDROID_RUN_LIVE_TESTS") == "1"
RUN_LIVE_SNAPSHOT = os.environ.get("OPENCLAW_ANDROID_RUN_LIVE_SNAPSHOT_TESTS") == "1"


@unittest.skipUnless(RUN_LIVE, "set OPENCLAW_ANDROID_RUN_LIVE_TESTS=1 to run live Waydroid checks")
class LiveWaydroidIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = AndroidRuntime(Settings())

    def test_status_and_doctor_return_expected_shapes(self) -> None:
        status = self.runtime.status()
        doctor = self.runtime.doctor()

        self.assertIn("ok", status)
        self.assertIn("waydroid", status)
        self.assertIn("bridge", status)
        self.assertIn("current_app", status)
        self.assertIn("runtime_backend", status)
        self.assertIn("ok", doctor)
        self.assertIn("waydroid", doctor)
        self.assertIn("bridge", doctor)
        self.assertIn("runtime_backend", doctor)

    @unittest.skipUnless(
        RUN_LIVE_SNAPSHOT,
        "set OPENCLAW_ANDROID_RUN_LIVE_SNAPSHOT_TESTS=1 with live tests to fetch a bridge snapshot",
    )
    def test_snapshot_fetches_bridge_tree(self) -> None:
        snapshot = self.runtime.snapshot(mode="interactive", include_screenshot=False)

        self.assertTrue(snapshot.get("ok"), snapshot.get("error"))
        self.assertIn("snapshot_id", snapshot)
        self.assertIn("refs", snapshot)
        self.assertIn("top_refs", snapshot)
        self.assertIn("screen_context", snapshot)
        self.assertIn("stats", snapshot)


if __name__ == "__main__":
    unittest.main()
