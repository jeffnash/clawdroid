.PHONY: help setup daemon-venv daemon-run build-apk plugin-link hermes-plugin-link smoke docker-smoke docker-production-build release-check

help:
	@echo "Targets:"
	@echo "  setup       - run the one-shot setup"
	@echo "  daemon-venv - create the Python daemon virtualenv"
	@echo "  daemon-run  - run the daemon in the foreground"
	@echo "  build-apk   - build the Android companion app"
	@echo "  plugin-link - install/link the OpenClaw plugin locally"
	@echo "  hermes-plugin-link - install/link the Hermes plugin locally"
	@echo "  smoke       - basic local smoke checks"
	@echo "  docker-smoke - run the disposable Docker setup smoke test"
	@echo "  docker-production-build - build the privileged production Docker image"
	@echo "  release-check - run publish/readiness checks"

setup:
	./setup_everything.sh --install-system-deps --install-openclaw --init-waydroid --extras libndk,microg

daemon-venv:
	python3 -m venv python-daemon/.venv && python-daemon/.venv/bin/pip install -U pip && python-daemon/.venv/bin/pip install -r python-daemon/requirements.txt

daemon-run:
	python-daemon/.venv/bin/python -m openclaw_android_daemon.main --host 127.0.0.1 --port 48765

build-apk:
	./scripts/build_companion_apk.sh

plugin-link:
	openclaw plugins install -l ./openclaw-plugin || openclaw plugins install ./openclaw-plugin
	openclaw plugins enable android-waydroid || true

hermes-plugin-link:
	./scripts/install_hermes_plugin.sh

smoke:
	curl -fsS http://127.0.0.1:48765/v1/status | jq .

docker-smoke:
	./scripts/docker_smoke_setup.sh

docker-production-build:
	docker build -f docker/production/Dockerfile -t clawdroid:local .

release-check:
	./scripts/release_check.sh
