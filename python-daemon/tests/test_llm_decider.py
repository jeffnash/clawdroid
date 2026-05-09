from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw_android_daemon.config import Settings
from openclaw_android_daemon.llm_decider import AndroidLlmDecider


class AndroidLlmDeciderConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.settings = Settings(
            llm_config_path=root / "llm.json",
            llm_models_path=root / "models.json",
            llm_settings_path=root / "settings.json",
            screenshot_dir=root / "screenshots",
            download_dir=root / "downloads",
        )
        self.decider = AndroidLlmDecider(self.settings)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_load_provider_configs_prefers_xdg_paths(self) -> None:
        self.settings.llm_models_path.write_text(json.dumps({"providers": {"openrouter": {"models": [{"id": "model-a"}]}}}))
        self.settings.llm_settings_path.write_text(json.dumps({"defaultProvider": "openrouter"}))

        _, settings_obj, models_obj = self.decider._load_provider_configs()

        self.assertEqual(settings_obj["defaultProvider"], "openrouter")
        self.assertIn("openrouter", models_obj["providers"])

    def test_load_provider_configs_falls_back_to_legacy_paths(self) -> None:
        with tempfile.TemporaryDirectory() as legacy_dir:
            legacy_root = Path(legacy_dir)
            legacy_models = legacy_root / "models.json"
            legacy_settings = legacy_root / "settings.json"
            legacy_models.write_text(json.dumps({"providers": {"cliproxy": {"models": [{"id": "legacy-model"}]}}}))
            legacy_settings.write_text(json.dumps({"defaultProvider": "cliproxy"}))

            with (
                patch.object(self.decider, "_legacy_llm_models_paths", return_value=(legacy_models,)),
                patch.object(self.decider, "_legacy_llm_settings_paths", return_value=(legacy_settings,)),
            ):
                _, settings_obj, models_obj = self.decider._load_provider_configs()

        self.assertEqual(settings_obj["defaultProvider"], "cliproxy")
        self.assertIn("cliproxy", models_obj["providers"])

    def test_resolve_provider_prefers_configured_settings_defaults_over_package_defaults(self) -> None:
        self.settings.llm_models_path.write_text(json.dumps({
            "providers": {
                "openrouter": {
                    "base_url": "https://openrouter.invalid/api/v1",
                    "api_key": "openrouter-key",
                    "models": [{"id": "bytedance/ui-tars-1.5-7b"}],
                },
                "custom": {
                    "base_url": "https://custom.invalid/v1",
                    "api_key": "custom-key",
                    "models": [{"id": "custom-vision-model"}],
                },
            }
        }))
        self.settings.llm_settings_path.write_text(json.dumps({
            "defaultProvider": "custom",
            "defaultModel": "custom-vision-model",
        }))

        provider = self.decider._resolve_provider()

        self.assertEqual(provider["provider"], "custom")
        self.assertEqual(provider["model"], "custom-vision-model")
        self.assertEqual(provider["base_url"], "https://custom.invalid/v1")

    def test_resolve_provider_accepts_api_key_env_list(self) -> None:
        self.settings.llm_config_path.write_text(json.dumps({
            "default_provider": "openrouter",
            "default_model": "bytedance/ui-tars-1.5-7b",
            "providers": {
                "openrouter": {
                    "base_url": "https://openrouter.invalid/api/v1",
                    "api_key_env": ["OPENCLAW_ANDROID_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"],
                    "models": [{"id": "bytedance/ui-tars-1.5-7b", "input": ["text", "image"]}],
                }
            },
        }))

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "openrouter-key"}, clear=True):
            provider = self.decider._resolve_provider()

        self.assertEqual(provider["provider"], "openrouter")
        self.assertEqual(provider["model"], "bytedance/ui-tars-1.5-7b")
        self.assertEqual(provider["api_key"], "openrouter-key")

    def test_config_status_reports_unconfigured_without_secret_values(self) -> None:
        self.settings.llm_config_path.write_text(json.dumps({
            "default_provider": "openrouter",
            "default_model": "bytedance/ui-tars-1.5-7b",
            "providers": {
                "openrouter": {
                    "base_url": "https://openrouter.invalid/api/v1",
                    "api_key_env": "MISSING_OPENROUTER_KEY",
                    "models": [{"id": "bytedance/ui-tars-1.5-7b", "input": ["text", "image"]}],
                }
            },
        }))

        status = self.decider.config_status()

        self.assertFalse(status["configured"])
        self.assertIn("missing", status["error"].lower())
        self.assertNotIn("api_key", status)

    def test_config_status_does_not_fetch_remote_model_catalog(self) -> None:
        self.settings.llm_config_path.write_text(json.dumps({
            "default_provider": "openrouter",
            "default_model": "bytedance/ui-tars-1.5-7b",
            "providers": {
                "openrouter": {
                    "base_url": "https://openrouter.invalid/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                }
            },
        }))

        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "openrouter-key"}, clear=False),
            patch.object(self.decider, "_fetch_remote_models", side_effect=AssertionError("status must not fetch remote models")),
        ):
            status = self.decider.config_status()

        self.assertTrue(status["configured"])
        self.assertEqual(status["provider"], "openrouter")
        self.assertEqual(status["model"], "bytedance/ui-tars-1.5-7b")
        self.assertTrue(status["supports_images"])
        self.assertNotIn("api_key", status)


if __name__ == "__main__":
    unittest.main()
