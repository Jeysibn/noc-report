"""Offline HTTP/profile contract check for the pinned Hermes runtime.

This deliberately sends invalid, incomplete chat requests so the route and
authentication contract are exercised without invoking a configured provider.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


BASE_URL = os.environ.get("CONTRACT_HERMES_BASE_URL", "http://hermes:8642").rstrip("/")
API_KEY = os.environ.get("CONTRACT_HERMES_API_KEY") or os.environ["API_SERVER_KEY"]
PROFILES = ("noc-log-analysis", "noc-daily-report")


def request(path: str, *, token: str | None, body: dict | None = None) -> tuple[int, dict | None]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers=headers,
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            decoded = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            decoded = None
        return exc.code, decoded


def main() -> None:
    status, health = request("/health", token=None)
    assert status == 200 and isinstance(health, dict), f"Hermes health contract failed: HTTP {status}"

    for profile in PROFILES:
        prefix = f"/p/{profile}/v1"
        status, _ = request(f"{prefix}/toolsets", token=None)
        assert status in {401, 403}, f"{profile}: unauthenticated request was not rejected (HTTP {status})"
        status, _ = request(f"{prefix}/toolsets", token="invalid-ci-token")
        assert status in {401, 403}, f"{profile}: invalid authentication was not rejected (HTTP {status})"

        status, toolsets = request(f"{prefix}/toolsets", token=API_KEY)
        assert status == 200 and isinstance(toolsets, dict), f"{profile}: toolsets route failed (HTTP {status})"
        entries = toolsets.get("data")
        assert isinstance(entries, list), f"{profile}: toolsets response has no data list"
        enabled = [entry.get("name", "unknown") for entry in entries if isinstance(entry, dict) and entry.get("enabled") is True]
        assert not enabled, f"{profile}: restricted profile exposes enabled toolsets: {enabled}"

        # A deliberately malformed request must be rejected by HTTP contract
        # validation before Hermes can start a model/provider call.
        status, _ = request(
            f"{prefix}/chat/completions",
            token=API_KEY,
            body={"invalid_contract_probe": True},
        )
        assert status in {400, 422}, f"{profile}: chat validation contract changed (HTTP {status})"

    print("Hermes HTTP, auth, profile, toolset and chat-validation contracts passed")


if __name__ == "__main__":
    main()
