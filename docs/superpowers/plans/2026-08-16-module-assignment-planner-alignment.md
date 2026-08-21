# Module Assignment Planner Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make module assignment visually continuous with the network planner.

**Architecture:** Reuse planner shell classes and shared tokens in the template; keep page-specific catalogue/inspector rules in `event-modules.css`.

**Tech Stack:** Jinja2, CSS, pytest, Docker Compose.

### Task 1: Shell contract

- [ ] Add failing template tests for planner page/root/toolbar identity/action/account/status and validation structures.
- [ ] Update the template to use those structures and standard `.btn` classes.
- [ ] Run focused Docker tests.

### Task 2: Token and geometry alignment

- [ ] Add failing CSS tests for 12px shell inset/gap, 240px rail, separated surface panels, shared tokens, planner radii, and responsive breakpoint.
- [ ] Rewrite page CSS without changing behavior.
- [ ] Run focused and full tests, rebuild port 8091, verify HTTP 200, and commit.
