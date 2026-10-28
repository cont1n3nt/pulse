"""Локации: каждая папка seed описана полностью, а агент не знает названий мест из кода."""

import json

import pytest

from backend.agent import location, prompts
from backend.agent.config import SEED_DIR

LOCATIONS = sorted(p.parent.name for p in SEED_DIR.glob("*/location.json"))


def test_there_are_several_locations():
    assert {"sochi", "kaliningrad"} <= set(LOCATIONS)


@pytest.mark.parametrize("location_id", LOCATIONS)
def test_location_is_complete(location_id):
    loc = location.load(location_id)
    assert loc.default_district in loc.district_names
    assert set(loc.seasons) >= {"coast", "mountain"} and all(len(m) == 12 for m in loc.seasons.values())
    if loc.sea:
        assert len(loc.sea.norm_by_month) == 12
    if loc.rail:
        assert loc.rail.hub in {s.name for s in loc.rail.stations}
        assert set(loc.rail.via_hub) <= set(loc.district_names)


@pytest.mark.parametrize("location_id", LOCATIONS)
def test_hotels_belong_to_location_districts(location_id):
    path = SEED_DIR / location_id / "hotels.json"
    if not path.exists():
        pytest.skip("отели не выгружены: make seed LOCATION=" + location_id)
    hotels = json.loads(path.read_text(encoding="utf-8"))
    assert hotels
    assert {h["district"] for h in hotels} <= set(location.load(location_id).district_names)


def test_prompt_is_built_from_location():
    text = prompts.system_prompt()
    here = location.HERE
    assert here.names.to in text and here.airport.code in text
    assert all(d.about in text for d in here.districts)
    for other in LOCATIONS:
        if other != here.id:
            assert not any(d.name in text for d in location.load(other).districts
                           if d.name not in here.district_names)
