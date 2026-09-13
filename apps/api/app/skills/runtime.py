"""Authoritative skill execution contract.

This module is deliberately small and deep: callers deal in a
``SkillSnapshot`` and never need to know whether its bytes originated in a
checkout, PostgreSQL, or a per-job materialization directory.
"""
from __future__ import annotations

import json
import yaml
import jsonschema
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import SkillSnapshot


class SkillContractError(ValueError):
    pass


SUPPORTED_RENDERER_PROFILES = {None, "daily_report_docx", "report-document-v1"}


def load_snapshot(db: Session, snapshot_id) -> SkillSnapshot:
    snapshot = db.get(SkillSnapshot, snapshot_id)
    if snapshot is None:
        raise SkillContractError(f"SkillSnapshot {snapshot_id} does not exist")
    return snapshot


def load_manifest(snapshot: SkillSnapshot) -> dict:
    try:
        manifest = yaml.safe_load(snapshot.manifest_yaml)
    except yaml.YAMLError as exc:
        raise SkillContractError(f"{snapshot.skill_name} manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SkillContractError("skill manifest must be an object")
    return manifest


def load_schema(snapshot: SkillSnapshot) -> dict:
    try:
        schema = json.loads(snapshot.output_schema_json)
        jsonschema.Draft7Validator.check_schema(schema)
    except (json.JSONDecodeError, jsonschema.SchemaError) as exc:
        raise SkillContractError(f"{snapshot.skill_name} output schema is invalid: {exc}") from exc
    return schema


def validate_snapshot(db: Session, snapshot: SkillSnapshot) -> None:
    if not snapshot.skill_md.strip():
        raise SkillContractError("skill instructions must not be empty")
    manifest = load_manifest(snapshot)
    # Older local fixtures used ``name`` and did not yet declare the full
    # runtime manifest. Accept that legacy shape while enforcing the stronger
    # contract for manifests that opt into the new fields.
    if manifest.get("id", manifest.get("name")) != snapshot.skill_name:
        raise SkillContractError("manifest id does not match snapshot skill name")
    output_schema = manifest.get("output_schema")
    if output_schema is not None and output_schema.get("file") != "output.schema.json":
        raise SkillContractError("manifest must declare output.schema.json")
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise SkillContractError("manifest dependencies must be a list")
    for dependency in dependencies:
        if isinstance(dependency, str):
            dependency_name, version = dependency, None
        elif isinstance(dependency, dict):
            dependency_name, version = dependency.get("id"), dependency.get("version")
        else:
            raise SkillContractError("skill dependency must be a name or {id, version}")
        query = select(SkillSnapshot.id).where(SkillSnapshot.skill_name == dependency_name)
        if version is not None:
            query = query.where(SkillSnapshot.version_label == int(version))
        if db.scalar(query) is None:
            raise SkillContractError(f"unresolvable skill dependency: {dependency}")
    renderer_profile = manifest.get("renderer_profile")
    if renderer_profile not in SUPPORTED_RENDERER_PROFILES:
        raise SkillContractError(f"unsupported renderer profile: {renderer_profile}")
    load_schema(snapshot)


def validate_input(snapshot: SkillSnapshot, payload: object) -> None:
    # Input schemas are intentionally not embedded in generic code yet;
    # manifests identify the input contract and skills may add one later.
    if payload is None:
        raise SkillContractError("skill input must not be null")


def validate_output(snapshot: SkillSnapshot, result: object) -> None:
    try:
        jsonschema.validate(result, load_schema(snapshot))
    except jsonschema.ValidationError as exc:
        raise SkillContractError(f"skill output failed schema validation: {exc.message}") from exc
