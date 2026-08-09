from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ai_agent.services.session_manager import session_manager

logger = logging.getLogger(__name__)

_auto_step_event = asyncio.Event()


def start_auto_stepper():
    asyncio.create_task(_auto_step_loop())
    return _auto_step_event


def stop_auto_stepper():
    _auto_step_event.set()


async def _auto_step_loop():
    """Background task that automatically steps running sessions."""
    from ai_agent.config import get_config

    config = get_config()
    logger.info(f"Auto-stepper started (interval={config.AUTO_STEP_INTERVAL}s)")

    while not _auto_step_event.is_set():
        try:
            await asyncio.sleep(config.AUTO_STEP_INTERVAL)
            await _step_all_sessions()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Auto-step error: {e}")


async def _step_all_sessions():
    """Step all running sessions with sophisticated filtering."""
    sessions = await session_manager.list_sessions()

    now = datetime.now(timezone.utc)

    for session in sessions:
        # Skip non-running sessions
        if session["status"] != "running":
            continue

        # Skip sessions with pending approvals if approval is required
        if session.get("approval_required") and session.get("pending_actions"):
            logger.debug(f"Skipping {session['id'][:8]}: pending approvals required")
            continue

        # Skip sessions that have recently failed
        if session.get("error_message"):
            last_error_time = session.get("error_timestamp")
            if last_error_time:
                error_age = (now - last_error_time).total_seconds()
                if error_age < 300:  # Don't step for 5 minutes after error
                    logger.debug(f"Skipping {session['id'][:8]}: recent error ({error_age:.0f}s ago)")
                    continue

        # Skip sessions that have exceeded budget
        if session.get("current_step", 0) >= session.get("max_steps", 100):
            logger.info(f"Skipping {session['id'][:8]}: budget exhausted ({session['current_step']}/{session['max_steps']})")
            continue

        # Skip sessions that have been running too long without progress
        if session.get("started_at"):
            start_time = datetime.fromisoformat(session["started_at"])
            age = (now - start_time).total_seconds()

            # Calculate progress
            progress = session.get("current_step", 0) / max(session.get("max_steps", 1), 1)

            # Skip if session is stalled (running > 10 minutes with < 30% progress)
            if age > 600 and progress < 0.3:
                logger.info(f"Skipping {session['id'][:8]}: stalled ({progress:.1%} progress in {age/60:.0f}m)")
                continue

        try:
            result = await session_manager.step(session["id"])
            if result:
                logger.info(
                    f"Auto-step {session['id'][:8]}: "
                    f"{result.get('status')} - {result.get('action', {}).get('description', '')[:50]}"
                )
        except Exception as e:
            logger.error(f"Auto-step failed for {session['id'][:8]}: {e}", exc_info=True)
