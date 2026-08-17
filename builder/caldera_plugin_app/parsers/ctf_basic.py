from app.objects.secondclass.c_fact import Fact
from app.objects.secondclass.c_relationship import Relationship
from app.utility.base_parser import BaseParser


class Parser(BaseParser):
    """Emit a fact from command output when a configured marker is present.

    Each ParserConfig mapping may set ``source`` (required), ``edge`` and
    ``target`` (optional relationship), and a ``custom_parser_vals.marker``
    substring that the output must contain for the fact to be emitted.

    When ``marker`` is omitted the fact is emitted for every non-empty output
    line. Fact values default to the matched line, the value of a used fact
    whose trait matches the mapping's ``source``/``target``, or — when the
    operation source already carries a fact with that trait — the seeded value
    (so recon re-runs merge into known facts instead of overwriting them).
    """

    def parse(self, blob):
        relationships = []
        for match in self.line(blob):
            for mp in self.mappers:
                marker = ""
                if getattr(mp, "custom_parser_vals", None):
                    marker = mp.custom_parser_vals.get("marker") or ""
                if marker and marker not in match:
                    continue
                source_value = self._merge_value(mp.source, match)
                if source_value is None:
                    continue
                source = Fact(mp.source, source_value)
                if mp.target:
                    target_value = self._merge_value(mp.target, match)
                    relationships.append(
                        Relationship(
                            source=source,
                            edge=mp.edge,
                            target=Fact(mp.target, target_value),
                        )
                    )
                else:
                    relationships.append(Relationship(source=source))
        return relationships

    def _merge_value(self, trait, match):
        """Resolve a fact value, preferring an existing seeded source fact.

        Mirrors BaseParser.set_value (used facts win) but first checks the
        operation's source facts so recon re-runs are idempotent: once a fact
        with this trait exists in the source, later runs reuse its value.
        """
        for uf in self.used_facts:
            if trait == uf.trait:
                return uf.value
        for sf in self.source_facts:
            if trait == sf.trait:
                return sf.value
        return self.set_value(trait, match, self.used_facts)
