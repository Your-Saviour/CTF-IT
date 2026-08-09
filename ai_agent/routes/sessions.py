from __future__ import annotations

import os
import time
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel

from ai_agent.config import get_config
from ai_agent.services.session_manager import session_manager
from ai_agent.db import get_db
from ai_agent.db.models import RateLimitRecord

logger = logging.getLogger(__name__)

router = APIRouter()


class RateLimiter:
    """Rate limiter for API endpoints with database persistence."""

    def __init__(self):
        self.requests = defaultdict(list)
        self.max_per_minute = 30
        self.cleanup_interval = 60  # seconds

    def is_allowed(self, key: str, max_per_minute: int = None) -> bool:
        """Check if request is allowed based on rate limit."""
        if max_per_minute is None:
            max_per_minute = self.max_per_minute

        now = time.time()
        self.requests[key] = [t for t in self.requests[key] if t > now - 60]

        if len(self.requests[key]) >= max_per_minute:
            return False

        self.requests[key].append(now)

        # Persist to database
        self._persist_to_db(key)

        return True

    def _persist_to_db(self, key: str) -> None:
        """Persist rate limit data to database."""
        try:
            with get_db() as db:
                record = db.query(RateLimitRecord).filter(
                    RateLimitRecord.key == key
                ).first()

                if record:
                    record.request_count = len(self.requests[key])
                    record.last_request_at = time.time()
                    record.updated_at = time.time()
                else:
                    record = RateLimitRecord(
                        id=key[:36],
                        key=key,
                        request_count=len(self.requests[key]),
                        last_request_at=time.time(),
                    )
                    db.add(record)

                db.commit()
        except Exception as e:
            # Don't fail the request if database write fails
            logger.error(f"Failed to persist rate limit data: {e}")

    def cleanup_expired(self, seconds: int = 60) -> int:
        """Clean up expired rate limit records from database."""
        try:
            with get_db() as db:
                cutoff = time.time() - seconds
                expired = db.query(RateLimitRecord).filter(
                    RateLimitRecord.last_request_at < cutoff
                ).delete()
                db.commit()
                return expired
        except Exception as e:
            logger.error(f"Failed to cleanup expired rate limits: {e}")
            return 0


rate_limiter = RateLimiter()


def require_agent_auth(x_api_key: str | None = Header(None)):
    """Require API key auth for agent service endpoints."""
    config = get_config()
    if config.AGENT_API_KEY and x_api_key != config.AGENT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check rate limit
    if not rate_limiter.is_allowed("agent_api", max_per_minute=30):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


class CreateSessionRequest(BaseModel):
    event_id: int
    vm_id: int | None = None
    target_ip: str | None = None
    approval_required: bool | None = None


@router.post("/sessions")
async def create_session(req: CreateSessionRequest, _auth=Depends(require_agent_auth)):
    try:
        session = await session_manager.create_session(
            event_id=req.event_id,
            vm_id=req.vm_id,
            target_ip=req.target_ip,
            approval_required=req.approval_required,
        )
        return session
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sessions")
async def list_sessions(_auth=Depends(require_agent_auth)):
    return await session_manager.list_sessions()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, _auth=Depends(require_agent_auth)):
    try:
        return await session_manager.get_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/start")
async def start_session(session_id: str, _auth=Depends(require_agent_auth)):
    try:
        return await session_manager.start_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, _auth=Depends(require_agent_auth)):
    try:
        return await session_manager.stop_session(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/step")
async def step_session(session_id: str, _auth=Depends(require_agent_auth)):
    try:
        session = await session_manager.get_session(session_id)
        if session["status"] != "running":
            raise HTTPException(status_code=400, detail="Session is not running")
        result = await session_manager.step(session_id)
        return result or {"status": "no_action", "message": "No action planned"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/approve/{action_id}")
async def approve_action(session_id: str, action_id: str, _auth=Depends(require_agent_auth)):
    try:
        return await session_manager.approve_action(session_id, action_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/reject/{action_id}")
async def reject_action(session_id: str, action_id: str, _auth=Depends(require_agent_auth)):
    try:
        return await session_manager.reject_action(session_id, action_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/sessions/{session_id}/logs")
async def get_logs(session_id: str, limit: int = Query(50, ge=1, le=500), _auth=Depends(require_agent_auth)):
    try:
        return await session_manager.get_logs(session_id, limit)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
