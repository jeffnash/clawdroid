# Android companion app

This Android app is intended to run **inside Waydroid**.

It provides:

- an `AccessibilityService` for semantic UI tree inspection,
- node/global actions,
- a localhost bridge server on port `49317`,
- a minimal `MainActivity` that helps the operator enable accessibility.

## Build

The repository-level helper script will download Gradle and the Android SDK if needed:

```bash
./scripts/bootstrap_android_sdk.sh
./scripts/build_companion_apk.sh
```

Output:

```text
android-companion/app/build/outputs/apk/debug/app-debug.apk
```

## Install into Waydroid

```bash
waydroid app install android-companion/app/build/outputs/apk/debug/app-debug.apk
```

After installing or replacing the APK, reboot the Waydroid guest once before expecting the `AccessibilityService` to bind. On this LineageOS/Waydroid stack, the service binds reliably on fresh guest boot, not always immediately after an in-place package replacement.

## Enable the accessibility service

The root setup script tries to do this automatically via ADB secure settings and will reboot the guest after companion APK updates.
If that fails, do it manually inside Waydroid:

1. Open **OpenClaw Android Bridge**.
2. Tap **Open Accessibility Settings**.
3. Enable **OpenClaw Accessibility Bridge**.

## Local bridge endpoints

- `GET /health`
- `GET /tree`
- `POST /configure`
- `POST /node_action`
- `POST /global_action`
