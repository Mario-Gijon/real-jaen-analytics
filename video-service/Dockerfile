FROM --platform=$BUILDPLATFORM denoland/deno:bin-2.9.8@sha256:b507583d3c68ba69f3ed2ce5969ae5b8b70f43421c781279c7aea06435263543 AS deno

FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src VIDEO_SERVICE_HOST=0.0.0.0 VIDEO_SERVICE_PORT=8010 VIDEO_SERVICE_DATA_DIR=/app/data \
    VIDEO_SERVICE_STATE_DIR=/state VIDEO_SERVICE_TEMP_DIR=/tmp/video-service TMPDIR=/tmp/video-service \
    DENO_DIR=/tmp/video-service/deno

COPY requirements.txt ./
COPY --from=deno /deno /usr/local/bin/deno
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 video-service \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin video-service \
    && mkdir -p /state/calibrations /tmp/video-service \
    && chown -R 10001:10001 /state /tmp/video-service

COPY --chown=10001:10001 src ./src
COPY --chown=10001:10001 data ./data
RUN if [ ! -f ./data/videos.json ]; then cp ./data/videos.example.json ./data/videos.json; fi

USER 10001:10001
EXPOSE 8010
CMD ["python", "-B", "-m", "video_service"]
