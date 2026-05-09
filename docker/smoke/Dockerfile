FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      dbus-user-session \
      git \
      iproute2 \
      jq \
      sudo \
      systemd \
      unzip \
      xz-utils && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash smoke && \
    printf 'smoke ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/smoke && \
    chmod 0440 /etc/sudoers.d/smoke

WORKDIR /workspace/clawdroid
COPY --chown=smoke:smoke . /workspace/clawdroid

USER smoke
ENV HOME=/home/smoke
ENV XDG_RUNTIME_DIR=/tmp/clawdroid-smoke-runtime

CMD ["bash", "scripts/docker_smoke_setup.sh", "--inside-container"]
