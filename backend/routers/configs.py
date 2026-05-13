from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.services import config_store, prefect_runner


router = APIRouter(prefix="/api/configs", tags=["configs"])


@router.get("")
def list_configs():
    return config_store.list_configs()


@router.get("/{config_id}")
def get_config(config_id: str):
    try:
        return config_store.get_config(config_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("")
def create_config(config: dict):
    return config_store.create_config(config)


@router.put("/{config_id}")
def save_config(config_id: str, config: dict):
    return config_store.save_config(config_id, config)


@router.delete("/{config_id}")
def delete_config(config_id: str):
    try:
        deleted = config_store.delete_config(config_id)
        return {"ok": True, "deleted": deleted}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{config_id}/draft")
def save_draft(config_id: str, config: dict):
    return config_store.save_draft(config_id, config)


@router.get("/{config_id}/versions")
def list_versions(config_id: str):
    return config_store.list_versions(config_id)


@router.get("/{config_id}/versions/{version_id}")
def get_version(config_id: str, version_id: str):
    try:
        return config_store.get_version(config_id, version_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{config_id}/versions/{version_id}/restore")
def restore_version(config_id: str, version_id: str):
    try:
        return config_store.restore_version(config_id, version_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{config_id}/validate")
def validate_config(config_id: str, config: dict):
    return {"issues": prefect_runner.validate_config(config)}


@router.post("/{config_id}/test-run")
def test_run_config(config_id: str, config: dict):
    issues = prefect_runner.validate_config(config)
    if issues:
        raise HTTPException(status_code=400, detail={"issues": issues})
    try:
        return prefect_runner.test_run_config(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{config_id}/real-test-run")
def real_test_run_config(config_id: str, config: dict):
    issues = prefect_runner.validate_config(config)
    if issues:
        raise HTTPException(status_code=400, detail={"issues": issues})
    try:
        return prefect_runner.real_test_run_config(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{config_id}/publish")
def publish_config(config_id: str, config: dict):
    issues = prefect_runner.validate_config(config)
    if issues:
        raise HTTPException(status_code=400, detail={"issues": issues})
    saved = config_store.save_config(config_id, config)
    try:
        return prefect_runner.publish_config(saved)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
