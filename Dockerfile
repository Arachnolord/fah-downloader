# ============================================================================
# FAH Media Downloader – (c) 2026 Fabian Hammer
# ============================================================================
FROM python:3.12-slim

# ffmpeg = Merge/Transcode/Audio-Extraktion; cifs-utils = NAS-Freigaben mounten
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg cifs-utils fuse3 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV CONFIG_DIR=/config \
    DOWNLOADS_DIR=/downloads \
    HOSTFS_DIR=/hostfs \
    SHARES_DIR=/mnt/shares \
    PORT=8080

VOLUME ["/config", "/downloads"]
EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
