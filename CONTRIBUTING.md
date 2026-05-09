# Contributing

## Ground rules

- Keep the stack accessibility-first. Do not reintroduce `uiautomator2` into the default runtime path.
- Preserve the split between deterministic execution in the daemon and reasoning in the agent/plugin layer.
- Prefer generic runtime improvements over app-specific hacks.
- Keep docs, install scripts, and manifest/schema files aligned with runtime behavior.

## Local checks

Run the standard validation pass before opening a change:

```bash
./scripts/release_check.sh
```

If you touch the Android companion build or Gradle files, also run:

```bash
./scripts/release_check.sh --include-android-build
```

## Change expectations

- Add or update tests for routing, daemon behavior, or parsing changes.
- Use XDG-friendly paths in docs and defaults.
- Keep generated files, local SDK paths, caches, and build outputs out of commits.
- Document any new environment variables or tool actions in the relevant README.

## Review focus

The highest-risk areas are:

- Weston and desktop-session ownership
- Accessibility bridge binding stability
- snapshot/ref quality
- direct install and direct-launch flows
- agent instruction drift
