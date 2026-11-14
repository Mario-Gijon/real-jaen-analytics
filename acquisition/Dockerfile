# Build the same audited browser and provider SDK used by the current runtime. The vendored
# source carries its upstream license and file manifest; Compose does not need a preloaded
# local browser image or a separate setup step.
FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789 AS browser-runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

COPY vendor/sofascore_scraper.tar.gz /tmp/sofascore-scraper.tar.gz
RUN set -eux; \
    echo "89231510295dc0de838ee072911d19f463033d9a77fc897780305d5bc9e8589b  /tmp/sofascore-scraper.tar.gz" | sha256sum --check -; \
    mkdir -p /opt/scraper; \
    tar -xzf /tmp/sofascore-scraper.tar.gz --strip-components=1 -C /opt/scraper; \
    rm /tmp/sofascore-scraper.tar.gz; \
    cd /opt/scraper; \
    test "$(cat SOURCE_REVISION)" = '611e436a1528b0f487aa56a045f023e8ae8ae098'; \
    test "$(cat SOURCE_ARCHIVE_SHA256)" = '5a77b78aeeb25f2636939ad70b07cbeeedd48ce2d86ec245141fe0e667056494'; \
    sha256sum --check SOURCE_FILES.sha256; \
    python -m pip install --no-cache-dir \
      --constraint /opt/scraper/constraints.txt \
      --requirement /opt/scraper/requirements.txt; \
    python -m pip install --no-cache-dir --no-deps /opt/scraper; \
    patchright install-deps chromium; \
    patchright install chromium; \
    python -m pip check

FROM browser-runtime AS runtime
USER root
RUN rm -rf /opt/probe \
    && groupadd --gid 10001 analyst \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/analyst --shell /usr/sbin/nologin analyst \
    && mkdir -p /profiles /acquisition-jobs /opt/shared-backend/acquisition \
    && chown -R 10001:10001 /home/analyst /profiles /acquisition-jobs
COPY --from=implementation / /opt/app/acquisition/
COPY --from=backend api.py queries.py importar.py /opt/app/
COPY --from=backend acquisition/group_plan.py /opt/app/acquisition/group_plan.py
COPY --from=backend persistencia/bundle.py persistencia/comentarios.py persistencia/bloqueo.py /opt/app/persistencia/
COPY requirements-acquisition.txt /opt/requirements-acquisition.txt
WORKDIR /opt/app
ENV PYTHONPATH=/opt/app:/opt/scraper:/opt/shared-backend HOME=/home/analyst
RUN python -m pip check \
    && python -c "from importlib.metadata import version; required = dict(line.strip().split('==', 1) for line in open('/opt/requirements-acquisition.txt') if line.strip() and not line.lstrip().startswith('#')); assert all(version(package) == expected for package, expected in required.items())"
USER 10001:10001
EXPOSE 8002
ENTRYPOINT ["python", "-B", "-m", "uvicorn"]
CMD ["acquisition.app:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1", "--no-access-log"]

# Test-only source files are added only when an explicit test target is requested.
FROM runtime AS test
COPY --from=checks / /opt/checks/
COPY --from=backend persistencia/adquisicion.py /opt/checks/persistencia/
COPY --from=backend productores-adquisicion.json /opt/checks/
ENV PYTHONPATH=/opt/app:/opt/scraper:/opt/checks
