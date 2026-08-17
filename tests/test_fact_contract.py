# tests/test_fact_contract.py
from types import SimpleNamespace

from builder.fact_contract import (
    AbilityFacts, FactSpec, ability_facts, extract_facts,
    substitute_command, validate_catalogue_facts, validate_module_facts,
)


def mod(module_id, caldera, type_="vulnerability"):
    return SimpleNamespace(id=module_id, type=type_, caldera=caldera)


def test_recon_auto_derives_vuln_fact():
    m = mod("weak_ssh", {"recon": {"command": "echo VULNERABLE user=x"}})
    facts = ability_facts(m, "recon")
    assert facts.outputs == [FactSpec(trait="ctf.vuln.weak_ssh", marker="VULNERABLE")]
    assert facts.inputs == []


def test_exploit_auto_derives_input_from_recon():
    m = mod("weak_ssh", {
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {"command": "su x"},
    })
    facts = ability_facts(m, "exploit")
    assert facts.inputs == ["ctf.vuln.weak_ssh"]
    assert facts.outputs == []


def test_goal_exploit_auto_derives_goal_fact():
    m = mod("install_c2", {"exploit": {"command": "echo GOAL_ACHIEVED"}}, type_="goal")
    facts = ability_facts(m, "exploit")
    assert facts.outputs == [FactSpec(trait="ctf.goal.install_c2", marker="GOAL_ACHIEVED")]


def test_explicit_outputs_and_inputs_take_precedence():
    m = mod("nopasswd_sudo", {
        "recon": {"command": "echo VULNERABLE"},
        "exploit": {
            "command": "sudo id",
            "inputs": ["ctf.weak_ssh.shell"],
            "outputs": [{"trait": "ctf.nopasswd_sudo.root", "marker": "ROOT_SHELL"}],
        },
    })
    facts = ability_facts(m, "exploit")
    assert facts.inputs == ["ctf.weak_ssh.shell"]
    assert facts.outputs == [FactSpec(trait="ctf.nopasswd_sudo.root", marker="ROOT_SHELL")]


def test_parser_mappings_derive_outputs():
    m = mod("weak_ssh", {
        "recon": {
            "command": "echo VULNERABLE user=svc",
            "parser": [{"module": "x", "mappings": [{
                "source": "ctf.vuln.weak_ssh",
                "custom_parser_vals": {"marker": "VULNERABLE", "pattern": "user=(\\S+)"},
            }]}],
        },
        "exploit": {"command": "su x"},
    })
    facts = ability_facts(m, "recon")
    assert facts.outputs == [FactSpec(trait="ctf.vuln.weak_ssh", marker="VULNERABLE", pattern="user=(\\S+)")]


def test_substitute_command_replaces_traits():
    assert substitute_command("su #{ctf.user.creds}", {"ctf.user.creds": "svc"}) == "su svc"


def test_extract_facts_captures_group_and_marker():
    specs = [FactSpec(trait="ctf.vuln.x", marker="VULNERABLE", pattern="user=(\\S+)")]
    assert extract_facts("noise\nVULNERABLE user=svc-monitor", specs) == {"ctf.vuln.x": "svc-monitor"}


def test_validate_rejects_colliding_trait():
    errors = validate_module_facts(mod("a", {
        "exploit": {"command": "x", "outputs": [{"trait": "ctf.b.root"}]},
    }))
    assert any("ctf.a." in e for e in errors)
