"""熔断状态持久化：原子写 + 配置指纹失效（零依赖）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PersistedState:
    fingerprint: str = ""
    states: dict[str, dict[str, Any]] = field(default_factory=dict)


class CircuitStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> PersistedState:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return PersistedState()
            states = data.get("states", {})
            if not isinstance(states, dict):
                states = {}
            clean = {
                str(k): dict(v)
                for k, v in states.items()
                if isinstance(v, dict)
            }
            return PersistedState(
                fingerprint=str(data.get("fingerprint", "")),
                states=clean,
            )
        except Exception:
            return PersistedState()

    def save(self, fingerprint: str, states: dict[str, dict[str, Any]]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fingerprint": fingerprint, "states": states}
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)
