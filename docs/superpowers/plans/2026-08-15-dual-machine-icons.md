# Dual Machine Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users independently choose a large primary function icon and a small secondary platform badge for every machine.

**Architecture:** Extend the icon module with pair resolution and independent override setters. Store `primary_icon` for the new primary override and preserve `icon` as the secondary override; project both resolved icons into D3 and render them as a large icon plus badge.

**Tech Stack:** JavaScript ES modules, D3.js, SVG, Python validation, Node test runner, pytest, Docker Compose.

## Global Constraints

- Automatic primary defaults: gateway Router, firewall Firewall, workload VM Server.
- Automatic secondary follows base-type icon metadata.
- Both selectors permit every library icon and remove only their own override when Automatic is selected.
- Existing `icon` overrides remain secondary overrides without migration.
- No machine card, and no provisioning/layout behavior changes.

### Task 1: Pair Resolution and Persistence

- [ ] Add failing tests for automatic defaults, independent overrides, override removal, and validation of `primary_icon` plus `icon`.
- [ ] Implement `machineIconPair(type, machine, bases)` and `setMachineIconOverride(machine, field, value)`.
- [ ] Add OPNsense to the library and use it as the OPNsense base type's secondary icon.
- [ ] Run focused tests and commit.

### Task 2: Inspector and Canvas

- [ ] Add failing contracts for both inspector selectors and primary/secondary graph projection.
- [ ] Render the primary icon at 36px and the secondary icon in the lower-right 16px badge; retain name, hit target, and state ring.
- [ ] Run syntax, focused, and full suites; request review; commit; rebuild port 8091 and verify HTTP 200.
