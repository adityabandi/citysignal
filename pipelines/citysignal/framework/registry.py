"""Adapter discovery. Import a module under adapters/ and it registers itself."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Iterator, Type

from .adapter import BaseAdapter


def _iter_modules(package_name: str) -> Iterator[str]:
    package = importlib.import_module(package_name)
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        yield module.name


def discover_adapters() -> dict[str, Type[BaseAdapter]]:
    found: dict[str, Type[BaseAdapter]] = {}
    for module_name in _iter_modules("citysignal.adapters"):
        module = importlib.import_module(module_name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseAdapter)
                and obj is not BaseAdapter
                and not inspect.isabstract(obj)
                and getattr(obj, "manifest", None) is not None
                and obj.__module__ == module_name
            ):
                found[obj.manifest.source_id] = obj
    return dict(sorted(found.items()))


def get_adapter(source_id: str) -> Type[BaseAdapter]:
    adapters = discover_adapters()
    if source_id not in adapters:
        raise KeyError(f"unknown source_id {source_id!r}; known: {', '.join(adapters)}")
    return adapters[source_id]
