"""Outbound integration adapters and contracts."""

from api.integrations.expo_it import ExpoITAdapter
from api.integrations.registry import adapter_keys, register_adapter


if "expo_it" not in adapter_keys():
    register_adapter(ExpoITAdapter())
