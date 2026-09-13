# Ephemeral per-job sandbox image for the Claude Bridge (master plan §27-28).
#
# The real `claude` CLI binary and the operator's OAuth credential are NOT
# baked into this image — they're bind-mounted read-only at container run
# time by bridge/noc_bridge/service.py (via run_job_sandbox's extra_volumes),
# for jobs that actually invoke the CLI (ADR 0003). Jobs that don't (none,
# currently — the Milestone 0.5 stand-in path has been fully replaced) get
# no such mount. Keeping the binary out of the image means one rebuild
# doesn't need to re-bundle a ~200MB executable, and a host `claude` upgrade
# takes effect on the next job without rebuilding this image at all.
FROM python:3.12-slim

RUN pip install --no-cache-dir "PyYAML>=6.0"

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /app
COPY entrypoint.py /app/entrypoint.py
RUN chown -R sandbox:sandbox /app

USER sandbox

ENTRYPOINT ["python3", "/app/entrypoint.py"]
