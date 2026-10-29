"""Справочники Travelpayouts: кэш в DATA_DIR, а сбой записи не ломает ход агента.

В Docker код и seed принадлежат root, а приложение работает от пользователя pulse: писать можно
только в том с данными.
"""

from pathlib import Path

import pytest

from backend.agent import reference

CITIES = [{"code": "MOW", "name": "Москва", "country_code": "RU", "name_translations": {"en": "Moscow"}}]


class _Response:
    def raise_for_status(self):
        pass

    def json(self):
        return CITIES


@pytest.fixture
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(reference, "CACHE_DIR", tmp_path / "data" / "reference")
    monkeypatch.setattr(reference, "SEED_DIR", tmp_path / "seed")
    monkeypatch.setattr(reference, "_CACHE", {})
    monkeypatch.setattr(reference.requests, "get", lambda url, timeout: _Response())
    return tmp_path


def test_downloaded_reference_is_cached_in_data_dir(clean):
    assert reference.find_city("Москва")[0]["code"] == "MOW"
    assert (clean / "data" / "reference" / "cities_ru.json").exists()
    assert not (clean / "seed").exists()


def test_read_only_cache_does_not_break_lookup(clean, monkeypatch):
    def deny(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "write_text", deny)
    assert reference.find_city("Москва")[0]["code"] == "MOW"
    assert reference.city_name("MOW") == "Москва"
