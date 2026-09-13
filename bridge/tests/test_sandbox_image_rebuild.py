"""Regression test: ensure_image_built must rebuild the sandbox image when
its build context (Dockerfile, entrypoint.py) changes, not only when the
image is entirely missing. Before this fix, an existing image was assumed
current forever — every job silently ran an ever-more-stale entrypoint.py
until someone happened to `docker rmi` or otherwise force a rebuild by
hand (as actually observed live: an image built 2026-09-10 was still
serving jobs on 2026-09-13, three commits after the telemetry feature and
the Phase 17 permission hardening it ran without)."""
from __future__ import annotations

from docker.errors import ImageNotFound

from noc_bridge.sandbox_runner import _BUILD_HASH_LABEL, _build_context_hash, ensure_image_built


class _FakeImage:
    def __init__(self, labels: dict):
        self.labels = labels


class _FakeImagesAPI:
    def __init__(self, existing_label: str | None):
        self._existing_label = existing_label
        self.build_calls = []

    def get(self, name):
        if self._existing_label is None:
            raise ImageNotFound("no such image")
        return _FakeImage({_BUILD_HASH_LABEL: self._existing_label})

    def build(self, *, path, tag, rm, labels):
        self.build_calls.append(labels[_BUILD_HASH_LABEL])


class _FakeClient:
    def __init__(self, existing_label: str | None):
        self.images = _FakeImagesAPI(existing_label)


def test_ensure_image_built_rebuilds_when_missing():
    client = _FakeClient(existing_label=None)
    ensure_image_built(client)
    assert client.images.build_calls == [_build_context_hash()]


def test_ensure_image_built_rebuilds_when_content_hash_differs():
    client = _FakeClient(existing_label="stale-hash-from-an-older-entrypoint-py")
    ensure_image_built(client)
    assert client.images.build_calls == [_build_context_hash()]


def test_ensure_image_built_skips_rebuild_when_hash_matches():
    client = _FakeClient(existing_label=_build_context_hash())
    ensure_image_built(client)
    assert client.images.build_calls == []
