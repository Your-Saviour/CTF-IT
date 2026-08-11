from __future__ import annotations

import json
import os
import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.database import get_db
from api.routes.admin import require_admin
from api.routes.auth import get_current_user

router = APIRouter(prefix="/admin/api/ai-agent", tags=["ai-agent"])

AGENT_API_URL = os.environ.get("AGENT_API_URL", "http://ai-agent:8000/api/agent")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")

# Shared HTTP client for agent communication
_agent_client = httpx.AsyncClient(
    base_url=AGENT_API_URL,
    headers={"X-API-Key": AGENT_API_KEY} if AGENT_API_KEY else {},
    timeout=60.0,
    transport=httpx.AsyncHTTPTransport(retries=3),
)


async def _agent_request(method: str, path: str, **kwargs):
    try:
        resp = await _agent_client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unavailable: {e}")


async def close_agent_client() -> None:
    await _agent_client.aclose()


@router.get("/sessions")
async def list_sessions(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("GET", "/sessions")


@router.post("/sessions")
async def create_session(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    return await _agent_request("POST", "/sessions", json=body)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("GET", f"/sessions/{session_id}")


@router.post("/sessions/{session_id}/start")
async def start_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("POST", f"/sessions/{session_id}/start")


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("POST", f"/sessions/{session_id}/stop")


@router.post("/sessions/{session_id}/step")
async def step_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("POST", f"/sessions/{session_id}/step")


@router.post("/sessions/{session_id}/approve/{action_id}")
async def approve_action(session_id: str, action_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("POST", f"/sessions/{session_id}/approve/{action_id}")


@router.post("/sessions/{session_id}/reject/{action_id}")
async def reject_action(session_id: str, action_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("POST", f"/sessions/{session_id}/reject/{action_id}")


@router.get("/sessions/{session_id}/logs")
async def get_logs(session_id: str, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("GET", f"/sessions/{session_id}/logs")


# WebSocket endpoint for real-time session updates
@router.websocket("/sessions/{session_id}/ws")
async def session_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time session updates."""
    from api.database import SessionLocal

    auth_db = SessionLocal()
    try:
        user = get_current_user(websocket, auth_db)
        if not user or not user.is_admin:
            await websocket.close(code=4403)
            return
    finally:
        auth_db.close()
    await websocket.accept()
    connected_clients = {}

    try:
        # Send initial state
        await websocket.send_json({
            "type": "initial",
            "session": await get_session_data(session_id)
        })

        connected_clients[session_id] = websocket

        # Listen for changes and broadcast to connected clients
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

                # Handle client messages (acknowledgments, etc.)
                data = json.loads(message)
                if data.get("type") == "ack":
                    # Acknowledge receipt
                    pass

            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                await websocket.send_json({"type": "heartbeat", "timestamp": datetime.now(timezone.utc).isoformat()})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if session_id in connected_clients:
            del connected_clients[session_id]
        await websocket.close()


async def get_session_data(session_id: str):
    """Get complete session data including recent actions."""
    resp = await _agent_request("GET", f"/sessions/{session_id}")
    return resp


@router.get("/sessions/{session_id}/actions")
async def get_session_actions(
    session_id: str, request: Request, limit: int = 10,
    db: Session = Depends(get_db),
):
    """Get recent actions with filter."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("GET", f"/sessions/{session_id}/actions", params={"limit": limit})


@router.get("/sessions/{session_id}/health")
async def get_session_health(
    session_id: str, request: Request, db: Session = Depends(get_db),
):
    """Get operation health status."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("GET", f"/sessions/{session_id}/health")


@router.get("/sessions/{session_id}/errors")
async def get_session_errors(
    session_id: str, request: Request, limit: int = 20,
    db: Session = Depends(get_db),
):
    """Get recent errors with context."""
    admin = require_admin(request, db)
    if not admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await _agent_request("GET", f"/sessions/{session_id}/errors", params={"limit": limit})
