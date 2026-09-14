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
SUPPORTED_INPUT_CONTRACTS = {"log-evidence-v1", "daily-report-context-v1"}


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


def declared_skill_version(snapshot: SkillSnapshot) -> str:
    """Return the manifest's declared version, falling back only for old
    snapshots created before manifests had a version field."""
    manifest = load_manifest(snapshot)
    value = manifest.get("version")
    return str(value if value is not None else snapshot.version_label)


def execution_policy(snapshot: SkillSnapshot) -> dict:
    """Return the immutable snapshot's runtime policy.

    The policy is copied from the snapshot manifest so callers never need to
    reopen the mutable checkout.  An absent policy is the compatibility
    shape used by pre-runtime snapshots.
    """
    policy = load_manifest(snapshot).get("execution_policy")
    return dict(policy) if isinstance(policy, dict) else {}


def input_contract_version(snapshot: SkillSnapshot) -> str | None:
    contract = load_manifest(snapshot).get("input_contract")
    if isinstance(contract, str):
        return contract
    if isinstance(contract, dict):
        version = contract.get("version")
        return str(version) if version is not None else None
    return None


def _resolve_dependency_snapshot(db: Session, dependency, pinned_ids: dict) -> SkillSnapshot:
    if isinstance(dependency, str):
        dependency_name, version = dependency, None
    elif isinstance(dependency, dict):
        dependency_name, version = dependency.get("id"), dependency.get("version")
    else:
        raise SkillContractError("skill dependency must be a name or {id, version}")
    if not dependency_name:
        raise SkillContractError(f"skill dependency is missing an id: {dependency}")

    pinned_id = pinned_ids.get(dependency_name)
    if pinned_id is not None:
        pinned = db.get(SkillSnapshot, pinned_id)
        if pinned is None or pinned.skill_name != dependency_name:
            raise SkillContractError(f"unresolvable pinned skill dependency: {dependency}")
        if version is not None:
            try:
                requested_version = int(version)
            except (TypeError, ValueError) as exc:
                raise SkillContractError(f"invalid dependency version: {dependency}") from exc
            if pinned.version_label != requested_version:
                raise SkillContractError(f"pinned dependency version does not match: {dependency}")
        return pinned

    query = select(SkillSnapshot).where(SkillSnapshot.skill_name == dependency_name)
    if version is not None:
        try:
            version_label = int(version)
        except (TypeError, ValueError) as exc:
            raise SkillContractError(f"invalid dependency version: {dependency}") from exc
        query = query.where(SkillSnapshot.version_label == version_label)
    dependency_snapshot = db.scalar(query.order_by(SkillSnapshot.created_at.desc()))
    if dependency_snapshot is None:
        raise SkillContractError(f"unresolvable skill dependency: {dependency}")
    return dependency_snapshot


def _validate_dependency_graph(db: Session, snapshot: SkillSnapshot, active_names: set[str]) -> None:
    if snapshot.skill_name in active_names:
        raise SkillContractError(f"cyclic skill dependency: {snapshot.skill_name}")
    manifest = load_manifest(snapshot)
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise SkillContractError("manifest dependencies must be a list")
    pinned_ids = snapshot.dependency_snapshot_ids or {}
    next_active_names = active_names | {snapshot.skill_name}
    for dependency in dependencies:
        dependency_snapshot = _resolve_dependency_snapshot(db, dependency, pinned_ids)
        _validate_dependency_graph(db, dependency_snapshot, next_active_names)


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
    if output_schema is not None and (
        not isinstance(output_schema, dict) or output_schema.get("file") != "output.schema.json"
    ):
        raise SkillContractError("manifest must declare output.schema.json")
    dependencies = manifest.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise SkillContractError("manifest dependencies must be a list")
    _validate_dependency_graph(db, snapshot, set())
    renderer_profile = manifest.get("renderer_profile")
    if renderer_profile not in SUPPORTED_RENDERER_PROFILES:
        raise SkillContractError(f"unsupported renderer profile: {renderer_profile}")
    policy = manifest.get("execution_policy")
    if policy is not None and not isinstance(policy, dict):
        raise SkillContractError("manifest execution_policy must be an object")
    projection = manifest.get("input_projection")
    if projection is not None:
        if not isinstance(projection, dict):
            raise SkillContractError("manifest input_projection must be an object")
        fields = projection.get("fields") or {}
        if not isinstance(fields, dict):
            raise SkillContractError("input_projection fields must be an object")
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in fields.items()
        ):
            raise SkillContractError("input_projection fields must map names to JSON paths")
        collections = projection.get("collections") or {}
        if not isinstance(collections, dict):
            raise SkillContractError("input_projection collections must be an object")
        for name, collection in collections.items():
            if not isinstance(name, str) or not isinstance(collection, dict):
                raise SkillContractError("input_projection collection is invalid")
            if not isinstance(collection.get("source"), str):
                raise SkillContractError(f"input_projection collection {name} source is required")
            fields = collection.get("fields") or {}
            if not isinstance(fields, dict):
                raise SkillContractError(f"input_projection collection {name} fields are invalid")
            if not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in fields.items()
            ):
                raise SkillContractError(f"input_projection collection {name} fields are invalid")
    load_schema(snapshot)
    # Activation is the contract publication boundary, so reject a
    # snapshot whose declared input contract cannot be consumed before it
    # becomes visible to new jobs.
    validate_input(snapshot, {})


def validate_input(snapshot: SkillSnapshot, payload: object) -> None:
    if payload is None:
        raise SkillContractError("skill input must not be null")
    contract = load_manifest(snapshot).get("input_contract")
    if isinstance(contract, str):
        if contract not in SUPPORTED_INPUT_CONTRACTS:
            raise SkillContractError(f"unsupported input contract: {contract}")
        return
    # Preserve registration of pre-Phase-5 snapshots.  New manifests are
    # validated strictly; old snapshots remain executable from their frozen
    # bytes and can be upgraded by publishing a new contract version.
    if contract is None:
        return
    if not isinstance(contract, dict):
        raise SkillContractError("manifest input_contract must be a supported name or object")
    version = contract.get("version")
    if version is not None and version not in SUPPORTED_INPUT_CONTRACTS:
        raise SkillContractError(f"unsupported input contract: {version}")
    if not isinstance(contract.get("filename"), str) or not contract["filename"].strip():
        raise SkillContractError("input contract filename is required")
    if not isinstance(contract.get("intro_text"), str) or not contract["intro_text"].strip():
        raise SkillContractError("input contract intro_text is required")


def validate_output(snapshot: SkillSnapshot, result: object) -> None:
    try:
        jsonschema.validate(result, load_schema(snapshot))
    except jsonschema.ValidationError as exc:
        raise SkillContractError(f"skill output failed schema validation: {exc.message}") from exc
