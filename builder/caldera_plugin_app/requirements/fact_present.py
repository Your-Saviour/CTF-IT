class Requirement:
    """Require that a fact with the configured source trait exists.

    Enforcement mapping keys:
      source  (required)  fact trait that must be present in the operation

    Note: this module deliberately avoids importing from
    ``plugins.ctf-exploit.*`` because hyphens in plugin names are not valid in
    Python ``import`` statements. The base class contract (an ``enforce``
    method receiving ``(link, operation)`` and reading ``self.enforcements``)
    is reproduced here so Caldera's ``load_module`` can import it by string.
    """

    def __init__(self, requirement_info):
        self.enforcements = requirement_info["enforcements"]

    async def enforce(self, link, operation):
        source = self.enforcements.get("source")
        if not source:
            return False
        facts = await operation.all_facts()
        return any(fact.trait == source for fact in facts)
