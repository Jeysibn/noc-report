"""Failure classification — Reliability mission Batch A / Phase 3.

Not every job failure should be retried: retrying a permanently-invalid
schema or an unsupported skill just reproduces the
same failure. `classify_failure` gives runtime consumers one place to decide
whether a failure is worth trying again.
"""
from __future__ import annotations

RETRYABLE = "retryable"
TERMINAL = "terminal"


class EvidenceRetrievalError(RuntimeError):
    """Frozen evidence exists but a temporary storage/read failure occurred."""


class EvidenceIntegrityError(RuntimeError):
    """Frozen evidence is permanently unavailable or cannot be decoded."""

# Substrings looked for (case-insensitively) in an exception's message when
# its exact type doesn't already settle the question (see
# classify_failure's RuntimeError branch below). Matches the reliability
# mission's own "Terminal" examples.
_TERMINAL_MESSAGE_MARKERS = (
    "unauthorized",
    "unsupported skill",
    "invalid schema",
    "invalid evidence",
    "prompt too large",
    "security violation",
    "missing required immutable snapshot",
)

# A structured output retry exhaustion remains retryable only for the bounded
# worker path that can make a second inference attempt.
_RETRYABLE_MESSAGE_MARKERS = (
    "structured_output_retry_exhausted",
)


def classify_failure(exc: BaseException) -> str:
    """Returns RETRYABLE or TERMINAL for a job failure raised from
    a runtime worker's job-processing callback."""
    from noc_bridge.skill_registry import SkillHashMismatch, SkillSnapshotMissing
    from noc_bridge.storage import ChecksumMismatch
    from noc_bridge.validation import OutputValidationError

    # Hermes adapter errors carry an explicit retry policy. This keeps
    # provider/runtime classification at the worker boundary and lets the
    # existing RabbitMQ retry/DLQ path remain the only retry system.
    if hasattr(exc, "retryable"):
        return RETRYABLE if bool(getattr(exc, "retryable")) else TERMINAL

    if isinstance(exc, ValueError) and "unsupported" in str(exc).lower():
        return TERMINAL
    if isinstance(exc, EvidenceIntegrityError):
        return TERMINAL
    if isinstance(exc, EvidenceRetrievalError):
        return RETRYABLE
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

    # Default: an otherwise-unclassified runtime/storage error is
    # retryable up to MAX_ATTEMPTS — the safer default for failures whose
    # cause isn't explicitly known to be permanent.
    return RETRYABLE
