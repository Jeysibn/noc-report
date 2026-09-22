FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/noc-report/sandbox:/opt/noc-report/bridge

WORKDIR /opt/noc-report
COPY bridge/requirements.txt bridge/constraints.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt -c /tmp/constraints.txt
COPY bridge /opt/noc-report/bridge
COPY sandbox /opt/noc-report/sandbox
COPY packages/contracts /opt/noc-report/packages/contracts
COPY skills /opt/noc-report/skills

RUN useradd --create-home --uid 10001 worker \
    && chown -R worker:worker /opt/noc-report
USER worker

CMD ["python", "-m", "noc_bridge.worker"]
