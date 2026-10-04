from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import AppConfig
from app.database import Database
from app.repository import Repository
from app.skill_engine import SkillEngine


def main() -> int:
    defaults = AppConfig.from_env()
    parser = argparse.ArgumentParser(description="Run due SkillSentra update checks once")
    parser.add_argument("--database", type=Path, default=defaults.database_path)
    parser.add_argument("--artifact-dir", type=Path, default=defaults.artifact_dir)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")

    database = Database(args.database.resolve())
    database.migrate()
    repository = Repository(database)
    engine = SkillEngine(
        repository,
        (args.artifact_dir or (defaults.root_dir / "data" / "artifacts")).resolve(),
        defaults.allowed_skill_roots,
    )
    results = engine.run_due_checks(args.limit)
    print(json.dumps({"checked": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 2 if any(item.get("status") == "blocked" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
