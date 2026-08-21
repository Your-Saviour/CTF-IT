# Module Assignment Semantic Colours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Make module assignment provenance scannable through consistent planner-native semantic colour.

**Architecture:** Add provenance classes to summary/usage rows and map all provenance classes to shared CSS variables; persistence and state derivation remain unchanged.

### Task 1: Provenance markers

- [ ] Add failing state/controller contract tests for provenance row classes and explicit labels.
- [ ] Render provenance classes in Assignment Summary and plan-wide usage.
- [ ] Run Node tests and syntax checks.

### Task 2: Semantic card system

- [ ] Add failing CSS contracts for cyan, green, amber, neutral, and red mappings.
- [ ] Add card rails, tinted surfaces, badges, markers, and distinct selected outline.
- [ ] Run focused/full tests, rebuild 8091, verify HTTP 200, and commit.
