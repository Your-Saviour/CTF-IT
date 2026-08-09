# AI Red Team Agent — Implementation Plan

## Architecture

```
┌─────────────┐     HTTP      ┌──────────────┐     Caldera API / SSH    ┌──────────┐
│ CTF Admin   │ ◄──────────►  │ AI Agent     │ ◄──────────────────────► │ Target   │
│ UI          │               │ Container    │                          │ VMs      │
└─────────────┘               └──────┬───────┘                            └──────────┘
                                     │
                              OpenAI API
                                     │
                              ┌──────▼───────┐
                              │ LLM Backend  │
                              │ (user infra) │
                              └──────────────┘
```

## Design Decisions

- **Role**: AI Red Team — autonomously attacks VMs, adapts strategy
- **Execution**: Both Caldera (structured) + SSH (flexible exploration)
- **Autonomy**: Human-in-the-loop — agent proposes, admin approves, agent executes
- **Deployment**: Separate container communicating with API via HTTP
- **AI**: OpenAI-compatible API only

## Core Components (PentestGPT-inspired)

1. **EGATS Planner** — Attack tree search with UCB node selection, TDI difficulty scoring, promise backpropagation
2. **State Store** — SQLite tracking hosts, services, credentials, vulnerabilities, sessions
3. **Tool Layer** — Typed interfaces for:
   - Caldera operations (list abilities, create operations, execute links)
   - SSH execution (connect to attacker VMs, run nmap, sqlmap, etc.)
4. **Context Manager** — Selective context injection, progressive compression at 40%/70% thresholds
5. **Approval Gateway** — Human-in-the-loop: agent proposes → admin approves → agent executes

## API Endpoints

- `POST /admin/ai-agent/sessions` — Create new AI session for an event/VM
- `GET /admin/ai-agent/sessions/{id}` — Session state, attack tree, pending approvals
- `POST /admin/ai-agent/sessions/{id}/approve` — Approve pending action
- `POST /admin/ai-agent/sessions/{id}/reject` — Reject action
- `POST /admin/ai-agent/sessions/{id}/start` — Start autonomous loop
- `POST /admin/ai-agent/sessions/{id}/stop` — Stop session
- `GET /admin/ai-agent/sessions/{id}/logs` — Real-time agent reasoning logs

## File Structure

```
ai_agent/
├── __init__.py
├── main.py                 # FastAPI app (separate container)
├── config.py               # Settings (AI API, Caldera URL, SSH config)
├── models.py               # SQLAlchemy models (sessions, actions, state)
├── planner/
│   ├── __init__.py
│   ├── egats.py            # EGATS planner (UCB, TDI, backpropagation)
│   ├── attack_tree.py      # Attack tree data structures
│   └── tda.py              # Task Difficulty Assessment
├── memory/
│   ├── __init__.py
│   ├── state_store.py      # Persistent state (hosts, services, creds)
│   └── context.py          # Context assembly + compression
├── tools/
│   ├── __init__.py
│   ├── base.py             # Tool interface
│   ├── caldera.py          # Caldera execution tools
│   └── ssh.py              # SSH execution tools
├── llm/
│   ├── __init__.py
│   └── client.py           # OpenAI-compatible API client
└── routes/
    ├── __init__.py
    └── sessions.py         # API endpoints
```

## Phase 1: Core Infrastructure (this PR)

- Agent container + FastAPI app
- Session management with database
- EGATS planner consuming existing attack trees from CTF API
- Caldera tool integration (create operations, execute abilities)
- Human-in-the-loop approval flow
- Basic UI integration in admin panel (session list, approve/reject actions)
- Docker Compose integration

## Phase 2: Advanced Features (future)

- SSH tool layer for direct VM access
- Multi-agent specialization (recon agent, exploit agent)
- Context compression + memory optimization
- Integration with VMGoal scoring
- Adaptive strategy based on blue team defenses

## Key Integrations with Existing Platform

- Consumes `GET /admin/caldera/attack-tree/{vm_id}` for initial attack graph
- Uses `api/services/caldera.py` patterns for Caldera API calls
- Reports findings back via Caldera operations (existing tracking)
- Respects event scoping and VM ownership
