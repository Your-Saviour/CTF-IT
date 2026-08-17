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
