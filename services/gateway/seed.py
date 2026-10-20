"""Explicit catalog seeding. Fixture loading is opt-in and development-only."""

import argparse
import json
from pathlib import Path

from contracts.scenario.models import ScenarioConfig
from services.gateway.persistence.database import database
from services.gateway.persistence.models import Scenario
from services.gateway.settings import ROOT, Settings


def seed(db, development=False, fixture_path=None):
    items = json.loads((ROOT / "scenarios/catalog.json").read_text())
    items += [
        json.loads(p.read_text()) for p in sorted((ROOT / "scenarios/versions").glob("*.json"))
    ]
    for item in items:
        if not db.get(Scenario, item["id"]):
            config = ScenarioConfig(**item).model_dump()
            db.add(
                Scenario(
                    id=item["id"],
                    title=item["title"],
                    status=config["status"],
                    is_fixture=False,
                    config=config,
                )
            )
    if fixture_path:
        if not development:
            raise RuntimeError("개발 모드에서만 fixture를 불러올 수 있습니다.")
        config = ScenarioConfig.model_validate_json(Path(fixture_path).read_text()).model_dump()
        if not config["is_fixture"]:
            raise RuntimeError("fixture 표시가 필요합니다.")
        if not db.get(Scenario, config["id"]):
            db.add(
                Scenario(
                    id=config["id"],
                    title=config["title"],
                    status=config["status"],
                    is_fixture=True,
                    config=config,
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", action="store_true")
    args = parser.parse_args()
    settings = Settings().validate()
    engine, sessions = database(settings.database_url)
    with sessions.begin() as db:
        seed(
            db,
            settings.development,
            ROOT / "tests/fixtures/workspace.json" if args.fixtures else None,
        )
