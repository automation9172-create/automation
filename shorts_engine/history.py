from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def known_signatures(path: Path) -> set[str]:
    data = read_json(path, {"shorts": []})
    return {str(item.get("signature")) for item in data.get("shorts", []) if item.get("signature")}


def record(path: Path, item: dict[str, Any]) -> None:
    data = read_json(path, {"shorts": []})
    shorts = data.setdefault("shorts", [])
    signature = item.get("signature")
    if signature and not any(row.get("signature") == signature for row in shorts):
        shorts.append(item)
    write_json(path, data)

