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
      kmod \
      lzip \
      openjdk-17-jdk \
      python3 \
      python3-pip \
      python3-venv \
      sudo \
      tini \
      unzip \
      weston \
      x11-utils \
      xvfb && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/clawdroid
COPY . /opt/clawdroid

RUN chmod +x \
      /opt/clawdroid/docker/production/start.sh \
      /opt/clawdroid/setup_everything.sh \
      /opt/clawdroid/scripts/*.sh

ENV HOME=/root
ENV XDG_RUNTIME_DIR=/run/clawdroid
ENV DISPLAY=:99
ENV OPENCLAW_ANDROID_DAEMON_HOST=0.0.0.0
ENV OPENCLAW_ANDROID_DAEMON_PORT=48765

EXPOSE 48765

ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/opt/clawdroid/docker/production/start.sh"]
