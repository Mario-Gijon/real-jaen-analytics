FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements-sql.txt requirements-fastapi.txt ./
RUN pip install --no-cache-dir -r requirements-fastapi.txt \
    && groupadd --gid 10001 analyst \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin analyst \
    && mkdir /state \
    && chown 10001:10001 /state
COPY --chown=10001:10001 api.py app.py control.py importar.py queries.py ambito.json alembic.ini productores-adquisicion.json ./
COPY --chown=10001:10001 acquisition/group_plan.py ./acquisition/group_plan.py
COPY --chown=10001:10001 persistencia ./persistencia
COPY --chown=10001:10001 enrichment ./enrichment
COPY --chown=10001:10001 scripts ./scripts
USER 10001:10001
EXPOSE 8002
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3).close()"
ENTRYPOINT ["python", "-B"]
CMD ["-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
