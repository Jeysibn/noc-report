"""Phase 12 PostgreSQL concurrency invariants."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import uuid

from fastapi import HTTPException
from sqlalchemy import select, func

from app.api.v1.routers.reports import _allocate_report_version, _lock_shift_or_404
from app.api.v1.routers.shifts import open_shift
from app.models.models import Job, Report, ReportSnapshot, Shift, ShiftDefinition, SkillSnapshot, User
from app.schemas.schemas import ShiftOpen
from app.core.security import hash_password
from tests.conftest import TestSessionLocal


def _make_shift(db_session) -> Shift:
    definition = ShiftDefinition(
        name="Day", start_time="06:00:00", end_time="14:00:00", timezone="Asia/Manila"
    )
    db_session.add(definition)
    db_session.flush()
    shift = Shift(
        shift_definition_id=definition.id,
        starts_at=datetime.now(timezone.utc),
        state="ended",
    )
    db_session.add(shift)
    db_session.commit()
    return shift


def test_two_concurrent_open_shift_calls_leave_one_active_shift(db_session):
    definition = ShiftDefinition(
        name="Day", start_time="06:00:00", end_time="14:00:00", timezone="Asia/Manila"
    )
    db_session.add(definition)
    users = [
        User(username=f"shift-race-{i}", display_name="Race", password_hash=hash_password("pw123456"))
        for i in range(2)
    ]
    db_session.add_all(users)
    db_session.commit()
    user_ids = [user.id for user in users]
    barrier = Barrier(2)

    def attempt(user_id):
        session = TestSessionLocal()
        try:
            barrier.wait(timeout=5)
            user = session.get(User, user_id)
            try:
                result = open_shift(ShiftOpen(), session, user)
                return ("ok", result.id)
            except HTTPException as exc:
                return ("error", exc.status_code)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, user_ids))

    active_count = db_session.scalar(select(func.count()).select_from(Shift).where(Shift.state == "active"))
    assert active_count == 1
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert next(result[1] for result in results if result[0] == "error") == 409


def test_two_concurrent_report_allocations_are_four_and_five(db_session, seeded):
    shift = _make_shift(db_session)
    skill = SkillSnapshot(
        skill_name="daily-alert-report",
        version_label=1,
        content_hash="c" * 64,
        skill_md="daily report skill",
        output_schema_json="{}",
        manifest_yaml="id: daily-alert-report\n",
        dependency_snapshot_ids={},
        is_active=True,
    )
    db_session.add(skill)
    db_session.flush()

    for version in (1, 2, 3):
        job = Job(
            job_type="daily_report",
            skill_snapshot_id=skill.id,
            skill_name=skill.skill_name,
            skill_version=str(skill.version_label),
            correlation_id=str(uuid.uuid4()),
        )
        db_session.add(job)
        db_session.flush()
        snapshot = ReportSnapshot(
            shift_id=shift.id,
            snapshot_json={},
            sha256="a" * 64,
            skill_snapshot_id=skill.id,
        )
        db_session.add(snapshot)
        db_session.flush()
        db_session.add(
            Report(
                shift_id=shift.id,
                snapshot_id=snapshot.id,
                job_id=job.id,
                version=version,
            )
        )
    db_session.commit()
    shift_id = shift.id
    skill_id = skill.id
    skill_name = skill.skill_name
    skill_version = str(skill.version_label)
    barrier = Barrier(2)

    def allocate_once(_index):
        session = TestSessionLocal()
        try:
            barrier.wait(timeout=5)
            locked_shift = _lock_shift_or_404(session, shift_id)
            version = _allocate_report_version(session, locked_shift)
            job = Job(
                job_type="daily_report",
                skill_snapshot_id=skill_id,
                skill_name=skill_name,
                skill_version=skill_version,
                correlation_id=str(uuid.uuid4()),
            )
            session.add(job)
            session.flush()
            snapshot = ReportSnapshot(
                shift_id=shift_id,
                snapshot_json={},
                sha256=(str(_index) + "b" * 63)[:64],
                skill_snapshot_id=skill_id,
            )
            session.add(snapshot)
            session.flush()
            session.add(
                Report(
                    shift_id=shift_id,
                    snapshot_id=snapshot.id,
                    job_id=job.id,
                    version=version,
                )
            )
            session.commit()
            return version
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = sorted(pool.map(allocate_once, [1, 2]))

    assert versions == [4, 5]
    assert db_session.scalar(select(func.count()).select_from(Report).where(Report.shift_id == shift_id)) == 5
