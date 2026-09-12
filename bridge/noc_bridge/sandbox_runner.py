"""Docker SDK sandbox lifecycle for Claude Bridge job execution.

Implements the container-creation steps of the Bridge Responsibilities
(master plan §27, steps 7-8 and 17) and the Sandbox Security Requirements
(§28) for a single job. This module owns container lifecycle only — job
queue consumption, MinIO transfer, and checksum validation are separate
Milestone 12 concerns, out of scope for the Milestone 0.5 spike.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import tempfile

import docker
from docker.errors import ContainerError, ImageNotFound
from docker.types import Ulimit

SANDBOX_IMAGE = "noc-sandbox:spike"
SANDBOX_DOCKERFILE_DIR = pathlib.Path(__file__).resolve().parents[2] / "sandbox"

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


def ensure_image_built(client: docker.DockerClient) -> None:
    try:
        client.images.get(SANDBOX_IMAGE)
    except ImageNotFound:
        client.images.build(
            path=str(SANDBOX_DOCKERFILE_DIR),
            tag=SANDBOX_IMAGE,
            rm=True,
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

    input_dir.chmod(0o755)
    for child in input_dir.iterdir():
        child.chmod(0o644)
    output_dir.chmod(0o777)

    volumes = {
        str(input_dir): {"bind": "/input", "mode": "ro"},
        str(skills_dir): {"bind": "/skills", "mode": "ro"},
        str(output_dir): {"bind": "/output", "mode": "rw"},
    }
    volumes.update(extra_volumes or {})

    container = client.containers.run(
        SANDBOX_IMAGE,
        detach=True,
        user="10001:10001",
        read_only=True,
        tmpfs={"/tmp": "size=64m"},
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        network_disabled=network_disabled,
        nano_cpus=cpu_nano_cpus,
        mem_limit=mem_limit,
        pids_limit=pid_limit,
        ulimits=[Ulimit(name="nproc", soft=pid_limit, hard=pid_limit)],
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

    return SkillJobResult(exit_code=exit_code, output=output, logs=logs)


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
        # Container runs as uid 10001; temp dirs default to 0700 for the host
        # user, so widen perms enough for the sandbox's non-root user to read
        # the input/skill mounts and write the output mount.
        pathlib.Path(input_dir).chmod(0o755)
        input_log.chmod(0o644)
        pathlib.Path(output_dir).chmod(0o777)

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

        return SkillJobResult(exit_code=exit_code, output=output, logs=logs)
