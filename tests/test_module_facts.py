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


class TestSuidFindFacts:
    def test_recon_extracts_binary_path(self):
        parser = _recon_parser("suid_find")
        assert parser["module"] == CTF_EXTRACT
        assert parser["mappings"][0]["custom_parser_vals"]["pattern"] == "path=(\\S+)"

    def test_exploit_injects_discovered_path(self):
        command = _ability("suid_find", "exploit")["command"]
        assert fact_trait("suid_find") in command


class TestWeakSshFacts:
    def test_recon_extracts_username(self):
        parser = _recon_parser("weak_ssh_credentials")
        assert parser["module"] == CTF_EXTRACT
        assert parser["mappings"][0]["custom_parser_vals"]["pattern"] == "user=(\\S+)"

    def test_exploit_injects_discovered_user(self):
        command = _ability("weak_ssh_credentials", "exploit")["command"]
        assert fact_trait("weak_ssh_credentials") in command


class TestInstallC2Gating:
    def test_exploit_gates_on_nopasswd_sudo_fact(self):
        exploit = _ability("install_c2", "exploit")
        assert exploit["requirements"] == [{
            "module": FACT_PRESENT,
            "mappings": [{"source": fact_trait("nopasswd_sudo")}],
        }]
        assert fact_trait("nopasswd_sudo") in exploit["command"]
        assert "SKIPPED" in exploit["command"]


class TestExfilShadowGating:
    def test_exploit_gates_on_world_writable_shadow_fact(self):
        exploit = _ability("exfil_shadow", "exploit")
        assert exploit["requirements"] == [{
            "module": FACT_PRESENT,
            "mappings": [{"source": fact_trait("world_writable_shadow")}],
        }]
        assert fact_trait("world_writable_shadow") in exploit["command"]
        assert "SKIPPED" in exploit["command"]
