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
