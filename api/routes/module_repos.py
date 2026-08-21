"""Admin CRUD and sync for external module repositories."""

import asyncio

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import ModuleRepo, utcnow
from api.routes.admin import require_admin
from api.services.secrets import encrypt_secret
from builder.module_repo import delete_repo as _delete_repo_dir, sync_repo

router = APIRouter(prefix="/admin/api", tags=["module_repos"])


def _repo_dict(repo: ModuleRepo) -> dict:
    return {
        "id": repo.id,
        "name": repo.name,
        "repo_url": repo.repo_url,
        "branch": repo.branch,
        "status": repo.status,
        "last_sync_at": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        "last_error": repo.last_error,
        "has_key": bool(repo.ssh_key_encrypted),
    }


def _validate_repo_fields(name, repo_url, branch, ssh_key):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url is required")
    if not isinstance(ssh_key, str) or not ssh_key.strip():
        raise ValueError("ssh_key is required")
    return name.strip(), repo_url.strip(), (branch or "main").strip(), ssh_key


def _run_sync(repo: ModuleRepo, db: Session) -> dict:
    try:
        sync_repo(repo)
        repo.status = "synced"
        repo.last_error = None
    except Exception as exc:
        repo.status = "error"
        repo.last_error = str(exc)
    repo.last_sync_at = utcnow()
    repo.updated_at = utcnow()
    db.commit()
    db.refresh(repo)
    return _repo_dict(repo)


def sync_all_repos(db: Session) -> None:
    for repo in db.query(ModuleRepo).order_by(ModuleRepo.id).all():
        _run_sync(repo, db)


@router.get("/module-repos")
async def list_repos(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return {"repos": [_repo_dict(r) for r in db.query(ModuleRepo).order_by(ModuleRepo.id).all()]}


@router.post("/module-repos", status_code=201)
async def create_repo(request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    try:
        name, repo_url, branch, ssh_key = _validate_repo_fields(
            body.get("name"), body.get("repo_url"), body.get("branch"), body.get("ssh_key"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    repo = ModuleRepo(name=name, repo_url=repo_url, branch=branch,
                      ssh_key_encrypted=encrypt_secret(ssh_key))
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return await asyncio.to_thread(_run_sync, repo, db)


@router.post("/module-repos/{repo_id}/sync")
async def sync_repo_endpoint(repo_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    repo = db.get(ModuleRepo, repo_id)
    if not repo:
        return JSONResponse({"error": "Module repository not found"}, status_code=404)
    return await asyncio.to_thread(_run_sync, repo, db)


@router.delete("/module-repos/{repo_id}", status_code=204)
async def delete_repo_endpoint(repo_id: int, request: Request, db: Session = Depends(get_db)):
    if not require_admin(request, db):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    repo = db.get(ModuleRepo, repo_id)
    if not repo:
        return JSONResponse({"error": "Module repository not found"}, status_code=404)
    _delete_repo_dir(repo)
    db.delete(repo)
    db.commit()
    return Response(status_code=204)
