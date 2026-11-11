# The browser runtime is built and audited separately; this image adds only the controlled HTTP service.
ARG PROFILE_RUNTIME=local/rj-browser-runtime:required
FROM ${PROFILE_RUNTIME} AS runtime
USER root
RUN rm -rf /opt/probe && mkdir -p /profiles /acquisition-jobs /opt/shared-backend/acquisition && chown 10001:10001 /profiles /acquisition-jobs
COPY --from=implementation / /opt/app/acquisition/
COPY --from=backend api.py queries.py importar.py /opt/app/
COPY --from=backend acquisition/group_plan.py /opt/app/acquisition/group_plan.py
COPY --from=backend persistencia/bundle.py persistencia/comentarios.py persistencia/bloqueo.py /opt/app/persistencia/
COPY requirements-acquisition.txt /opt/requirements-acquisition.txt
WORKDIR /opt/app
ENV PYTHONPATH=/opt/app:/opt/scraper:/opt/shared-backend PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
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
