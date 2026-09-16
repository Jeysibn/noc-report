"""Local/controlled Bridge Health interface (master plan §27): status,
version, Docker/RabbitMQ/MinIO connectivity, active jobs, max concurrency.
Bound to 127.0.0.1 only — never exposed beyond the host, per the
"local/controlled" wording in the master plan.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import docker
import pika

from noc_bridge.storage import get_client


def _check_docker() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


def _check_rabbitmq(rabbitmq_url: str) -> bool:
    try:
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        connection.close()
        return True
    except Exception:
        return False


def _check_minio(service) -> bool:
    try:
        client = get_client(service.settings)
        client.list_buckets()
        return True
    except Exception:
        return False


def _make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib method name
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return

            docker_connected = _check_docker()
            rabbitmq_connected = _check_rabbitmq(service.settings.rabbitmq_url)
            minio_connected = _check_minio(service)
            connected = (docker_connected, rabbitmq_connected, minio_connected)
            overall = "healthy" if all(connected) else ("degraded" if any(connected) else "unavailable")
            body = json.dumps(
                {
                    "status": overall,
                    "version": service.settings.version,
                    "docker_connected": docker_connected,
                    "rabbitmq_connected": rabbitmq_connected,
                    "minio_connected": minio_connected,
                    "active_jobs": service.active_jobs,
                    "max_concurrency": service.settings.max_concurrency,
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass  # quiet; the bridge's own logger covers job activity

    return Handler


def start_health_server(service) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(
        (service.settings.health_bind_host, service.settings.health_bind_port),
        _make_handler(service),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
