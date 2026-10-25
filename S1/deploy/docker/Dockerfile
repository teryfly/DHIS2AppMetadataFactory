# factory-api / factory-worker image (CP-RT-001).
#
# The build context is the DAMF workspace, so S0 can be installed from the sibling
# checkout during development; in a real deployment S0–S4 come from the private
# index given by INDEX_URL and no sibling directory is in the context.
FROM python:3.12-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir uv
RUN python -m venv /opt/venv
WORKDIR /app
COPY . /ctx
ARG INDEX_URL=""
# Whichever layout the context has, S1 is installed from source and S0 from the
# context when it is present.
RUN set -eux; \
    if [ -d /ctx/S1 ]; then \
      app=/ctx/S1; \
      if [ -d /ctx/S0 ]; then /opt/venv/bin/uv pip install --python /opt/venv/bin/python /ctx/S0; fi; \
    else \
      app=/ctx; \
    fi; \
    /opt/venv/bin/uv pip install --python /opt/venv/bin/python ${INDEX_URL:+--index-url "$INDEX_URL"} "$app"

# FR-DEP-005 / CP-RT-005: assembling S0~S4 into one runtime means running the public-API
# compatibility tests against that exact combination, and a single failure fails the build.
# This is the seam no individual subsystem can check: which names each package exports, and
# whether S1's declaration of which calls perform external I/O still matches what the provider
# actually marks. Both drift silently otherwise, and each one surfaces mid-stage as a
# TransactionMisuseError or a None where a record was expected.
FROM build AS assembly_compat
ARG SIBLING_TEST_DEPS=""
RUN set -eux; \
    /opt/venv/bin/uv pip install --python /opt/venv/bin/python ${SIBLING_TEST_DEPS:-"pytest pytest-asyncio"}; \
    if [ -d /ctx/S1/tests ]; then \
      cd /ctx/S1 && /opt/venv/bin/pytest -q -m assembly tests/assembly; \
    else \
      echo "no test tree in the build context; the image is published unchecked" >&2; exit 1; \
    fi

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN groupadd -r factory && useradd -r -g factory factory
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
USER factory
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"
# The same image serves either process; the command selects it (CP-RT-001).
# The Alembic scripts travel with the installed package, so no source tree is needed.
CMD ["factory-api"]
