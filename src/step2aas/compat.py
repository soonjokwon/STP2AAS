"""Compatibility for the object-store rename in BaSyx Python SDK 2.1."""

try:
    from basyx.aas.model import DictIdentifiableStore as ObjectStore
except ImportError:  # SDK < 2.1 retains the original public name.
    from basyx.aas.model import DictObjectStore as ObjectStore

__all__ = ["ObjectStore"]
