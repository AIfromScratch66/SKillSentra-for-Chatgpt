from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
DOC_ROOTS = (ROOT / "README.md", ROOT / "DEVELOPMENT.md", ROOT / "SECURITY.md", ROOT / "product")


def documents() -> list[Path]:
    files: list[Path] = []
    for root in DOC_ROOTS:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(root.rglob("*.md"))
    return sorted(files)


def main() -> int:
    broken: list[str] = []
    checked = 0
    for document in documents():
        text = document.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            raw = match.group(1).strip().strip("<>").split(maxsplit=1)[0]
            if not raw or raw.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target_text = unquote(raw.split("#", 1)[0])
            target = (ROOT / target_text.lstrip("/")) if target_text.startswith("/") else (document.parent / target_text)
            checked += 1
            if not target.exists():
                line = text.count("\n", 0, match.start()) + 1
                broken.append(f"{document.relative_to(ROOT)}:{line} -> {raw}")
    if broken:
        print("Broken local documentation links:")
        print("\n".join(broken))
        return 1
    print(f"Documentation links OK: {checked} local targets across {len(documents())} Markdown files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
