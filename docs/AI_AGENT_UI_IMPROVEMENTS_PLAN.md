# AI Agent UI Improvements - Real-Time Updates and Error Feedback

## Overview

This document outlines the plan for improving the AI red team agent UI with better real-time updates and comprehensive error feedback mechanisms.

## Current State Analysis

### What Exists
- Basic polling: sessions (10s), session data (5s), logs (8s)
- Manual refresh buttons
- Basic error display in status panel
- Toast notification system in base.html
- No WebSocket support
- No real-time action updates

### What's Missing
- Instant updates via WebSocket
- Operation health indicators
- Retry suggestions for failed operations
- Detailed error messages with context
- Error history tracking
- Status change notifications
- Action completion feedback

---

## Implementation Plan

### Phase 1: Backend WebSocket Support (API Routes)

**File**: `api/routes/ai_agent.py`

**Add WebSocket endpoint:**
```python
@router.websocket("/sessions/{session_id}/ws")
async def session_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time session updates."""
    await websocket.accept()

    try:
        # Send initial state
        await websocket.send_json({
            "type": "initial",
            "session": await get_session_data(session_id)
        })

        # Subscribe to session events
        # Listen for changes and broadcast to connected clients

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await websocket.close()
```

**Add new endpoints for real-time data:**
- `GET /admin/ai-agent/sessions/{session_id}/actions` - Stream recent actions with filter
- `GET /admin/ai-agent/sessions/{session_id}/health` - Get operation health status
- `GET /admin/ai-agent/sessions/{session_id}/errors` - Get recent errors with context

### Phase 2: Enhanced Error Feedback System

**File**: `frontend/templates/ai_agent_session.html`

**Add error feedback components:**

1. **Operation Health Panel** (new card):
   - Real-time health score (0-1)
   - Agent connectivity status
   - Operation staleness indicator
   - Health issues list (if any)

2. **Action Results Panel** (enhanced):
   - Success/failure indicators
   - Operation health score for each action
   - Retry button for failed operations
   - Error details with context

3. **Error History Panel** (new card):
   - Chronological error list
   - Error severity indicators
   - Error context (target, operation, step)
   - Error suggestions

4. **Enhanced Status Panel**:
   - Error message with expandable details
   - Error suggestion buttons
   - Error timestamp and duration
   - Related action links

### Phase 3: Real-Time Update Implementation

**Enhanced JavaScript in session detail page:**

1. **WebSocket Integration**:
   - Connect to `/admin/ai-agent/sessions/{session_id}/ws`
   - Handle real-time updates (status changes, new actions, errors)
   - Auto-refresh UI on live events

2. **Smart Polling**:
   - Reduce polling frequency during idle periods
   - Increase polling during active operations
   - Poll only when WebSocket disconnects

3. **Auto-refresh Actions**:
   - Automatically reload when action completes
   - Show success/failure toast notifications
   - Auto-expand action results on completion

### Phase 4: User Experience Improvements

**Toast Notifications** (using existing system):
- Session status change (running → stopped)
- Action completion (success/failure)
- New pending actions
- Operation health warnings
- Error notifications

**Progress Indicators**:
- Visual progress bar for session steps
- Health score gauge for operations
- Connection status indicator
- Polling indicator (when WebSocket unavailable)

**Error Handling**:
- Error categorization (network, operation, validation)
- Error recovery suggestions
- Error escalation indicators
- Error context breadcrumbs

### Phase 5: Session List Improvements

**File**: `frontend/templates/ai_agent.html`

1. **Live Status Updates**:
   - Real-time status badge updates
   - Auto-update on status changes
   - Status change notifications

2. **Error Indicators**:
   - Error count badge for sessions with errors
   - Last error timestamp
   - Error severity indicator

3. **Quick Actions**:
   - Quick view session details
   - Quick start/stop buttons
   - Quick error navigation

---

## Technical Architecture

### WebSocket Flow:
```
Client → WebSocket → API → Agent Service → Database → WebSocket → Client
```

**Events to broadcast:**
- `session_status_change` - Status updated
- `new_action` - New action created/approved
- `action_completed` - Action executed
- `action_failed` - Action failed
- `operation_health_changed` - Health status updated
- `new_error` - New error occurred
- `session_error` - Session-level error

### Error Feedback Hierarchy:
```
Critical Errors (blocking)
    ↓
Operation Errors (health degraded)
    ↓
Session Errors (non-blocking)
    ↓
Warnings (informational)
```

---

## Configuration Updates

**New environment variables** (`ai_agent/config.py`):
```python
# WebSocket configuration
WEBSOCKET_ENABLED: bool = os.environ.get("WEBSOCKET_ENABLED", "true").lower() == "true"
WEBSOCKET_HEARTBEAT_INTERVAL: int = int(os.environ.get("WEBSOCKET_HEARTBEAT_INTERVAL", "30"))

# Error feedback
MAX_ERROR_HISTORY: int = int(os.environ.get("MAX_ERROR_HISTORY", "50"))
ERROR_DISPLAY_DURATION: int = int(os.environ.get("ERROR_DISPLAY_DURATION", "300"))
AUTO_RETRY_FAILED_OPS: bool = os.environ.get("AUTO_RETRY_FAILED_OPS", "false").lower() == "true"
```

---

## Database Changes

**New table** (`ai_agent/db/models.py`):
```python
class SessionError(Base):
    """Track errors for better error feedback."""
    __tablename__ = "session_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), nullable=False)
    error_type: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    error_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
```

---

## Implementation Order

**Phase 1**: Backend WebSocket endpoint + error tracking (1-2 days)
**Phase 2**: Enhanced error feedback UI components (1-2 days)
**Phase 3**: Real-time update integration (1-2 days)
**Phase 4**: User experience improvements (1 day)
**Phase 5**: Session list enhancements (0.5-1 day)

**Total Estimated Time**: 4.5-8.5 days

---

## Questions for User

1. **WebSocket vs Polling**: Do you prefer WebSocket for real-time updates or stick with enhanced polling? WebSocket is more efficient but requires backend changes.

2. **Error Display Duration**: How long should errors remain visible? 5 minutes, 15 minutes, or until resolved?

3. **Auto-Retry**: Should failed operations automatically retry, or require manual intervention? (We already have retry logic in Caldera tool, but should we expose UI controls?)

4. **Error Severity Levels**: Should we have different severity levels (critical, high, medium, low) for error categorization?

5. **Performance**: Are you concerned about WebSocket connection overhead, or is it acceptable for the number of concurrent sessions expected?

6. **Fallback Behavior**: Should the UI gracefully degrade to polling if WebSocket fails to connect?

---

## Success Metrics

1. **Real-time Updates**: WebSocket connection success rate >95%
2. **Error Detection**: Errors detected within 5 seconds of occurrence
3. **User Satisfaction**: Reduced manual refresh frequency by 70%
4. **System Performance**: No significant performance impact on session operations
5. **Error Recovery**: Auto-retry success rate >60%

---

## Notes

- All improvements maintain backward compatibility
- Existing toast notification system can be leveraged
- Progressive enhancement approach (WebSocket as primary, polling as fallback)
- Error feedback follows existing UI patterns from the platform
- Consider A/B testing for error display strategies
