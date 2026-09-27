FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    curl \
    ffmpeg \
    fonts-liberation \
    novnc \
    openbox \
    pulseaudio \
    pulseaudio-utils \
    tigervnc-scraping-server \
    tini \
    websockify \
    x11-utils \
    xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 browser \
    && install -d -m 1777 /tmp/.X11-unix \
    && mkdir -p /home/browser/profile \
    && chown -R browser:browser /home/browser

COPY --chmod=755 start.sh /usr/local/bin/start-browser-source

USER browser
ENV HOME=/home/browser \
    DISPLAY=:99 \
    XDG_RUNTIME_DIR=/tmp/browser-runtime

WORKDIR /home/browser
VOLUME ["/home/browser/profile"]
EXPOSE 6080/tcp
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s \
    CMD curl --fail --silent http://127.0.0.1:6080/vnc.html > /dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start-browser-source"]
