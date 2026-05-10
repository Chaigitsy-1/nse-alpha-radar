from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RuntimeConfig:
    root: Path
    settings: dict[str, Any]
    signals: dict[str, Any]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_config() -> RuntimeConfig:
    return RuntimeConfig(
        root=ROOT,
        settings=load_json(ROOT / "config" / "settings.json"),
        signals=load_json(ROOT / "config" / "signals.json"),
    )


def load_env_file(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def project_path(config: RuntimeConfig, relative_path: str) -> Path:
    return config.root / relative_path
