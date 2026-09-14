"""Docker SDK sandbox lifecycle for Claude Bridge job execution.

Implements the container-creation steps of the Bridge Responsibilities
(master plan §27, steps 7-8 and 17) and the Sandbox Security Requirements
(§28) for a single job. This module owns container lifecycle only — job
queue consumption, MinIO transfer, and checksum validation are separate
Milestone 12 concerns, out of scope for the Milestone 0.5 spike.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import tempfile

import docker
from docker.errors import ContainerError, ImageNotFound
from docker.types import Ulimit

SANDBOX_IMAGE = "noc-sandbox:spike"
SANDBOX_DOCKERFILE_DIR = pathlib.Path(__file__).resolve().parents[2] / "sandbox"
_BUILD_HASH_LABEL = "noc.sandbox.build_context_sha256"

# Conceptual limits from master plan §28.
CPU_LIMIT_NANO_CPUS = 2_000_000_000  # 2 CPUs
MEM_LIMIT = "4g"
PID_LIMIT = 128
TIMEOUT_SECONDS = 60


@dataclasses.dataclass
class SkillJobResult:
    exit_code: int
    output: dict | None
    logs: str
    telemetry: dict | None = None


def _read_optional_json(path: pathlib.Path) -> dict | None:
    """Telemetry is best-effort and must never mask a valid skill result."""
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _grant_sandbox_uid_access(input_dir: pathlib.Path, output_dir: pathlib.Path) -> None:
    """Widen `input_dir`/`output_dir` permissions just enough for the
    sandbox container's fixed non-root uid (10001:10001, §28) to read the
    input mount and write the output mount, without the blanket
    world-writable/world-readable `0o777`/`0o755` these directories used to
    get.

    The host process is neither the owner nor a group member of anything
    the container's uid touches (Docker bind mounts don't remap uids), so
    the only lever available without root/`chown` is the "other" bits —
    this grants exactly the "other" bits each mount actually needs and
    zeroes the "group" bits entirely (no other local user or group has any
    reason to touch these ephemeral per-job directories):
      - input: dir needs traverse+list (other=r-x) so the container can
        read the files; each file needs other=r-- only (never write,
        never execute).
      - output: dir needs traverse+create (other=-wx) so the container can
        write result files; it does not need "other" read (the host
        process, as owner, already reads its own output back).
    """
    input_dir.chmod(0o700)
    for child in input_dir.iterdir():
        child.chmod(0o600)
    output_dir.chmod(0o700)


def _build_context_hash() -> str:
    """sha256 over every file in the sandbox build context (currently just
    `Dockerfile` and `entrypoint.py` — see SANDBOX_DOCKERFILE_DIR), each
    length-prefixed in a fixed (sorted-by-relative-path) order. Used as an
    image label so `ensure_image_built` can tell a real content change
    (e.g. entrypoint.py picking up a new mission's code) apart from "the
    image already exists" — the two were conflated before, which silently
    ran an ever-more-stale entrypoint.py against every job until something
    forced a manual rebuild."""
    digest = hashlib.sha256()
    paths = sorted(
        p for p in SANDBOX_DOCKERFILE_DIR.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and "tests" not in p.parts
    )
    for path in paths:
        contents = path.read_bytes()
        rel = str(path.relative_to(SANDBOX_DOCKERFILE_DIR)).encode()
        digest.update(len(rel).to_bytes(8, "big"))
        digest.update(rel)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


def ensure_image_built(client: docker.DockerClient) -> None:
    """Rebuilds `SANDBOX_IMAGE` whenever its build context (Dockerfile,
    entrypoint.py) has changed since the last build, not only when the
    image is entirely missing — a stale image previously ran an
    out-of-date entrypoint.py against every job indefinitely, since
    nothing ever invalidated it."""
    current_hash = _build_context_hash()
    try:
        image = client.images.get(SANDBOX_IMAGE)
        if image.labels.get(_BUILD_HASH_LABEL) == current_hash:
            return
    except ImageNotFound:
        pass
    client.images.build(
        path=str(SANDBOX_DOCKERFILE_DIR),
        tag=SANDBOX_IMAGE,
        rm=True,
        labels={_BUILD_HASH_LABEL: current_hash},
    )


def run_job_sandbox(
    *,
    input_dir: pathlib.Path,
    output_dir: pathlib.Path,
    skills_dir: pathlib.Path,
    timeout_seconds: int = TIMEOUT_SECONDS,
    cpu_nano_cpus: int = CPU_LIMIT_NANO_CPUS,
    mem_limit: str = MEM_LIMIT,
    pid_limit: int = PID_LIMIT,
    network_disabled: bool = True,
    extra_volumes: dict | None = None,
    environment: dict | None = None,
) -> SkillJobResult:
    """Milestone 12 generalization of `run_skill_job`: the caller (bridge
    service) has already populated `input_dir` (from MinIO downloads) and
    created `output_dir`; this function only owns the container lifecycle
    (§27 steps 7-8, 17 / §28 security requirements) for whatever job type
    is running. `run_skill_job` below is kept as-is for the Milestone 0.5
    spike entry point.

    `network_disabled=False` is required for a job that actually invokes
    the real Claude Code CLI (ADR 0003) — every other §28 requirement
    (non-root, cap-drop=ALL, no-new-privileges, read-only rootfs,
    tmpfs-only writes, CPU/mem/PID limits, timeout, force-remove) still
    applies unchanged; only network egress is a deliberate, documented
    exception for that one job type.
    """
    client = docker.from_env()
    ensure_image_built(client)

    _grant_sandbox_uid_access(input_dir, output_dir)

    volumes = {
        str(input_dir): {"bind": "/input", "mode": "ro"},
        str(skills_dir): {"bind": "/skills", "mode": "ro"},
        str(output_dir): {"bind": "/output", "mode": "rw"},
    }
    volumes.update(extra_volumes or {})
    if os.getuid() == 0:
        raise RuntimeError("refusing to run sandbox as root; start the bridge as a dedicated non-root user")

    container = client.containers.run(
        SANDBOX_IMAGE,
        detach=True,
        # Keep the bridge UID for bind-mounted private files (notably the
        # OAuth credential copy). Docker's per-container `pids_limit` still
        # bounds this process tree; do not add an `nproc` ulimit here because
        # Linux accounts that limit per UID and would include unrelated host
        # desktop threads owned by the same operator.
        user=f"{os.getuid()}:{os.getgid()}",
        read_only=True,
        tmpfs={"/tmp": "size=64m"},
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        network_disabled=network_disabled,
        nano_cpus=cpu_nano_cpus,
        mem_limit=mem_limit,
        pids_limit=pid_limit,
        volumes=volumes,
        environment=environment or {},
    )
    try:
        exit_status = container.wait(timeout=timeout_seconds)
        exit_code = exit_status.get("StatusCode", -1)
        logs = container.logs().decode("utf-8", errors="replace")
    except ContainerError as exc:  # pragma: no cover - defensive
        exit_code = -1
        logs = str(exc)
    finally:
        container.remove(force=True)  # §27 step 17 / §28 force removal

    result_path = output_dir / "result.json"
    output = json.loads(result_path.read_text()) if result_path.exists() else None
    telemetry = _read_optional_json(output_dir / "telemetry.json")

    return SkillJobResult(exit_code=exit_code, output=output, logs=logs, telemetry=telemetry)


def run_skill_job(log_text: str, skills_dir: pathlib.Path) -> SkillJobResult:
    """Spawn one ephemeral sandbox container to run a skill against a log
    excerpt, following the mount/security contract in §27-28, then force-
    remove it. Mirrors bridge steps 7 (create sandbox), 8 (mount), 9-12
    (invoke skill / capture output), 17 (force-remove) — steps 1-6 and 13-16
    (queue, MinIO, DB, status) are Milestone 12 scope, not this spike.
    """
    client = docker.from_env()
    ensure_image_built(client)

    with tempfile.TemporaryDirectory(prefix="noc-sandbox-input-") as input_dir, \
            tempfile.TemporaryDirectory(prefix="noc-sandbox-output-") as output_dir:
        input_log = pathlib.Path(input_dir) / "log.txt"
        input_log.write_text(log_text)
        # See _grant_sandbox_uid_access's docstring: grant only the "other"
        # bits the fixed-uid-10001 sandbox actually needs, not 0o777/0o755.
        _grant_sandbox_uid_access(pathlib.Path(input_dir), pathlib.Path(output_dir))

        container = client.containers.run(
            SANDBOX_IMAGE,
            detach=True,
            user="10001:10001",
            read_only=True,
            tmpfs={"/tmp": "size=64m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            network_disabled=True,
            nano_cpus=CPU_LIMIT_NANO_CPUS,
            mem_limit=MEM_LIMIT,
            pids_limit=PID_LIMIT,
            ulimits=[Ulimit(name="nproc", soft=PID_LIMIT, hard=PID_LIMIT)],
            volumes={
                str(input_dir): {"bind": "/input", "mode": "ro"},
                str(skills_dir): {"bind": "/skills", "mode": "ro"},
                str(output_dir): {"bind": "/output", "mode": "rw"},
            },
        )
        try:
            exit_status = container.wait(timeout=TIMEOUT_SECONDS)
            exit_code = exit_status.get("StatusCode", -1)
            logs = container.logs().decode("utf-8", errors="replace")
        except ContainerError as exc:  # pragma: no cover - defensive
            exit_code = -1
            logs = str(exc)
        finally:
            container.remove(force=True)  # §27 step 17 / §28 force removal

        result_path = pathlib.Path(output_dir) / "result.json"
        output = json.loads(result_path.read_text()) if result_path.exists() else None
        telemetry = _read_optional_json(pathlib.Path(output_dir) / "telemetry.json")

        return SkillJobResult(exit_code=exit_code, output=output, logs=logs, telemetry=telemetry)
