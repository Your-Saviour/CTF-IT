# Live Event Dashboard

**Date:** 2026-04-11  
**Status:** Proposed  
**Priority:** High — major admin quality-of-life during events

---

## Context

Admins currently have no aggregated real-time view of event progress. During a live exercise they cannot see: which teams are progressing, which modules are stumping everyone, VM health, or what's happening right now. The existing scoreboard shows per-user rankings but no event-wide analytics.

---

## Design

### 1. New Page Route

`GET /admin/event/{event_id}/dashboard` renders `event_dashboard.html`. Link added per-event row in `admin.html`.

### 2. Backend — Single Aggregate Endpoint

New file `api/routes/event_dashboard.py`.

`GET /admin/events/{event_id}/dashboard-data` (admin-only) returns:

```json
{
  "event": {"id": 1, "name": "...", "status": "open"},
  "summary": {
    "total_users": 12,
    "total_teams": 4,
    "total_vms": 8,
    "total_modules_assigned": 96,
    "total_completed": 34,
    "completion_pct": 35.4
  },
  "module_stats": [
    {
      "module_id": "suid_find",
      "name": "SUID bit on find",
      "type": "vulnerability",
      "difficulty": "easy",
      "assigned": 12,
      "completed": 8,
      "completion_pct": 66.7
    }
  ],
  "team_progress": [
    {
      "team_id": 1,
      "name": "Red Team",
      "total_points": 450,
      "completed": 6,
      "total": 12,
      "pct": 50.0,
      "vm_count": 2,
      "vms_active": 2
    }
  ],
  "user_progress": [
    {
      "user_id": 1,
      "username": "alice",
      "total_points": 300,
      "completed": 3,
      "total": 9,
      "pct": 33.3,
      "build_status": "ready"
    }
  ],
  "recent_activity": [
    {
      "timestamp": "...",
      "type": "module_completed",
      "actor": "alice",
      "module": "suid_find",
      "points": 100
    }
  ],
  "vm_health": [
    {
      "vm_id": 1,
      "hostname": "team1-vm1",
      "status": "active",
      "team": "Red Team",
      "agent_status": "connected",
      "completed": 3,
      "total": 6
    }
  ]
}
```

Query strategy:
- `summary` — COUNTs on `User`, `Team`, `VM`, `UserModule`, `VMModule` filtered by event_id
- `module_stats` — GROUP BY module_id across UserModule + VMModule, count completed vs total
- `team_progress` — JOIN Team → VM → VMModule, aggregate per team
- `user_progress` — JOIN User → UserModule, aggregate per user
- `recent_activity` — query `UserModule` and `VMModule` WHERE `completed_at IS NOT NULL`, ORDER BY `completed_at DESC`, LIMIT 20, merge and re-sort
- `vm_health` — query VMs for this event, include status and agent_status fields

### 3. Update Mechanism — Polling

10-second polling interval. SSE adds proxy/connection complexity for minimal gain in an admin-only context. Payload is small (single JSON object). Matches the existing polling pattern used throughout the codebase.

### 4. UI Layout

New template `event_dashboard.html` extending `base.html`. Uses existing CSS patterns (card layout, color variables, `progress-bar`/`progress-fill` classes). No external JS charting library — pure CSS progress bars.

**Row 1 — Summary cards (5 across):**
Total Users | Total Teams | Total VMs | Completion % | Points Earned

**Row 2 — Two columns:**
- Left: **Module Completion Rates** — horizontal bar chart (CSS), one row per module, sorted by completion rate ascending (hardest at top), color-coded by difficulty badge
- Right: **Team Leaderboard** — compact table: team name, points, completion progress bar, VM health dot indicators

**Row 3 — Two columns:**
- Left: **Recent Activity** — scrolling feed of latest completions, auto-updating. Each entry: timestamp, actor, module name, points
- Right: **VM Health Grid** — colored status tiles per VM. Green = active + agent connected, yellow = active + no agent, red = failed, grey = registered

### 5. Navigation

- "Dashboard" link added to each event row in the events table in `admin.html`
- Back link to admin page in the dashboard template

---

## Files to Create / Modify

| Action | Path |
|--------|------|
| Create | `api/routes/event_dashboard.py` |
| Create | `frontend/templates/event_dashboard.html` |
| Modify | `api/main.py` — register router, add page route |
| Modify | `frontend/templates/admin.html` — "Dashboard" link per event row |

---

## Verification / Testing

- **API:** create event with users/teams/VMs/modules, complete some, call `/admin/events/{id}/dashboard-data`, assert all sections populated
- **Edge cases:** event with no users; event with no teams; event where nothing is completed yet
- **UI:** manual — verify auto-refresh works, layout renders correctly, activity feed updates live

---

## Dependencies

- **Feature 1 (VM Verification)** — without it, `VMModule.completed` is always 0, so team progress and VM module stats are empty. Dashboard still shows Docker user progress without Feature 1.
- No other feature dependencies
