from __future__ import annotations

import unittest
from pathlib import Path


class AndroidBridgeAuthStaticTests(unittest.TestCase):
    def test_tree_and_action_routes_require_token_auth(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = root / "android-companion" / "app" / "src" / "main" / "java" / "ai" / "openclaw" / "androidbridge" / "BridgeHttpServer.kt"
        text = source.read_text(encoding="utf-8")

        for marker in ('"/tree"', '"/configure"', '"/node_action"', '"/global_action"'):
            self.assertIn(marker, text)
        self.assertIn("requiresAuth(path) && !isAuthorized(headers)", text)
        self.assertIn('"x-openclaw-bridge-token"', text)
        self.assertIn("MessageDigest.isEqual", text)
        self.assertIn("Unauthorized bridge request", text)


if __name__ == "__main__":
    unittest.main()
