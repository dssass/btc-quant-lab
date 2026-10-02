"""策略登記簿：strategies/registry.json。凍結後的策略程式碼不能再改。"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "strategies", "registry.json")


def load() -> list[dict]:
    with open(REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


def save(entries: list[dict]) -> None:
    with open(REGISTRY, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def get_class(path: str):
    mod, cls = path.split(":")
    return getattr(importlib.import_module(mod), cls)


def instantiate(entry: dict):
    obj = get_class(entry["class"])(timeframe=entry["timeframe"], **entry.get("params", {}))
    obj.id = entry["id"]
    return obj


def code_sha(class_path: str, params: dict, timeframe: str) -> str:
    """策略本身 + 它繼承的所有策略類別（例如 hso_v2 繼承 HSO）的原始碼 + 參數。"""
    cls = get_class(class_path)
    mods = []
    for k in cls.__mro__:
        m = inspect.getmodule(k)
        if m is not None and m.__name__.startswith("strategies") and m not in mods:
            mods.append(m)
    src = "".join(inspect.getsource(m) for m in mods)
    blob = src + json.dumps(params, sort_keys=True) + timeframe
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def verify(entry: dict) -> bool:
    return code_sha(entry["class"], entry.get("params", {}), entry["timeframe"]) == entry["code_sha"]
