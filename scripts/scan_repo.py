"""发布前敏感信息扫描门禁：命中真实路径/用户名/疑似真实 key 即失败。"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "dist", "build", ".github"}
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".ico"}

PATTERNS = [
    re.compile(r"Administrator"),
    re.compile(r"immortalfish-vault"),
    re.compile(r"sk-(?!SYNTH|EXAMPLE|<your_key>)[A-Za-z0-9]{8,}"),
    re.compile(r"RELAY_A_API_KEY\s*=\s*['\"]?[A-Za-z0-9]"),
]


def main() -> int:
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if path.is_dir() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue  # 扫描器自身包含规则字面量，不扫描自己
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pat in PATTERNS:
                if pat.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()[:120]}")
    if hits:
        print("\n".join(hits))
        print("SCAN_FAILED: 发现疑似敏感信息")
        return 1
    print("SCAN_CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
