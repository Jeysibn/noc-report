"""Failure classification — Reliability mission Batch A / Phase 3.

Not every job failure should be retried: retrying a permanently-invalid
schema, an unsupported skill, or a credential problem just burns another
attempt (and, for a Claude-invoking job, another dollar) reproducing the
exact same failure. `classify_failure` gives `service.py` one place to
decide "is this worth trying again" instead of a bare `except` that
retries everything indiscriminately (the previous behavior).
"""
from __future__ import annotations

RETRYABLE = "retryable"
TERMINAL = "terminal"

# Substrings looked for (case-insensitively) in an exception's message when
# its exact type doesn't already settle the question (see
# classify_failure's RuntimeError branch below). Matches the reliability
# mission's own "Terminal" examples.
_TERMINAL_MESSAGE_MARKERS = (
    "invalid credentials",
    "unauthorized",
    "unsupported skill",
    "invalid schema",
    "invalid evidence",
    "prompt too large",
    "security violation",
    "missing required immutable snapshot",
)

# A structured_output_retry_exhausted failure is explicitly called out as
# retryable-with-one-bounded-escalation by the reliability mission (Phase
# 13) — not a reason to give up immediately, but also not something to
# retry indefinitely at the same effort tier. Produced by
# sandbox/entrypoint.py's `_invoke_claude`, which already retries a
# malformed/unparseable structured result once at the CLI level before
# raising this — so a job-level retry (this classification) gets its own
# fresh pair of CLI attempts rather than looping on the same bad output.
_RETRYABLE_MESSAGE_MARKERS = (
    "structured_output_retry_exhausted",
)


def classify_failure(exc: BaseException) -> str:
    """Returns RETRYABLE or TERMINAL for a job failure raised from
    `BridgeService._process_job`."""
    from noc_bridge.skill_registry import SkillHashMismatch, SkillSnapshotMissing
    from noc_bridge.storage import ChecksumMismatch
    from noc_bridge.validation import OutputValidationError

    # Import locally (not at module scope) to avoid a circular import
    # with service.py, which imports this module.
    from noc_bridge.service import UnsupportedJobType  # noqa: PLC0415

    if isinstance(exc, UnsupportedJobType):
        return TERMINAL
    if isinstance(exc, OutputValidationError):
        return TERMINAL
    if isinstance(exc, SkillHashMismatch):
        # The skill's content has genuinely drifted since this job was
        # enqueued — retrying re-runs against the same (now-different)
        # skill content and reproduces the exact same mismatch.
        return TERMINAL
    if isinstance(exc, SkillSnapshotMissing):
        # The immutable SkillSnapshot this job depends on doesn't exist —
        # retrying re-attempts the same missing-row lookup and fails
        # identically every time.
        return TERMINAL
    if isinstance(exc, ChecksumMismatch):
        # A checksum mismatch could mean tampered/corrupted evidence
        # (terminal — retrying reads the same bad object again) or a
        # transient partial download (retryable). Treated as retryable:
        # MinIO/network hiccups are the far more common real-world cause,
        # and a bounded MAX_ATTEMPTS still stops it from looping forever
        # against genuinely corrupted evidence.
        return RETRYABLE

    message = str(exc).lower()
    if any(marker in message for marker in _TERMINAL_MESSAGE_MARKERS):
        return TERMINAL
    if any(marker in message for marker in _RETRYABLE_MESSAGE_MARKERS):
        return RETRYABLE

    # Default: an otherwise-unclassified RuntimeError (sandbox exit
    # failure, transient Claude CLI invocation failure, transient
    # RabbitMQ/MinIO error surfaced as a plain RuntimeError, etc.) is
    # retryable up to MAX_ATTEMPTS — the safer default for failures whose
    # cause isn't explicitly known to be permanent.
    return RETRYABLE
