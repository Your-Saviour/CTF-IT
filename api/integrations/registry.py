from api.integrations.base import IntegrationAdapter


_ADAPTERS: dict[str, IntegrationAdapter] = {}


def register_adapter(adapter: IntegrationAdapter) -> None:
    key = adapter.key.strip()
    if not key:
        raise ValueError("integration adapter key is required")
    if key in _ADAPTERS:
        raise ValueError(f"integration adapter '{key}' is already registered")
    _ADAPTERS[key] = adapter


def get_adapter(key: str) -> IntegrationAdapter:
    try:
        return _ADAPTERS[key]
    except KeyError:
        raise KeyError(f"unknown integration adapter: {key}") from None


def adapter_keys() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))

