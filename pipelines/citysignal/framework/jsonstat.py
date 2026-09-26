"""Decode JSON-stat's row-major cells without confusing countries or dimensions."""
import math

from .adapter import AdapterFailure


def observations(data):
    ids, sizes = data.get("id", []), data.get("size", [])
    if not ids or len(ids) != len(sizes) or any(s < 1 for s in sizes):
        raise AdapterFailure("Empty or invalid JSON-stat dimensions")
    if "time" not in ids or "geo" not in ids:
        raise AdapterFailure("JSON-stat must contain geography and time")
    if any(size != 1 for name, size in zip(ids, sizes) if name not in {"geo", "time"}):
        raise AdapterFailure("Ambiguous query: filter every non-geographic dimension")
    labels = []
    for name, size in zip(ids, sizes):
        index = data["dimension"][name]["category"]["index"]
        reverse = dict(enumerate(index)) if isinstance(index, list) else {v: k for k, v in index.items()}
        if set(reverse) != set(range(size)):
            raise AdapterFailure(f"Invalid index for {name}")
        labels.append(reverse)
    values, statuses = data.get("value", {}), data.get("status", {})
    cells = enumerate(values) if isinstance(values, list) else values.items()
    for cell, value in cells:
        if value is None:
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise AdapterFailure("Non-numeric or non-finite JSON-stat value")
        position = int(cell)
        if not 0 <= position < math.prod(sizes):
            raise AdapterFailure("Cell outside JSON-stat dimensions")
        coords = {}
        for name, size, index in reversed(list(zip(ids, sizes, labels))):
            coords[name] = index[position % size]
            position //= size
        status = statuses[int(cell)] if isinstance(statuses, list) else statuses.get(str(cell), "")
        yield {**coords, "value": float(value), "status": status or ""}
