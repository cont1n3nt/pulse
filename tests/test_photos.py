"""Превью с сайта отеля: перехваченные под казино домены не дают фото."""

from backend.agent.adapters import photos

DOORWAY = """<html><head><title>Pinko Casino — официальный сайт, рабочее зеркало</title>
<meta property="og:image" content="/banner.jpg"></head><body>Слоты, фриспины и бонус на депозит</body></html>"""

HIDDEN_DOORWAY = """<html><head><title>Приют Панды</title><meta property="og:image" content="/a.jpg"></head>
<body>Играйте в слоты, получайте фриспины, ставки на спорт, джекпот каждый день</body></html>"""

HOTEL = """<html><head><title>Приют Панды — отель в Красной Поляне</title>
<meta property="og:image" content="/img/front.jpg"></head>
<body>Номера с видом на горы. До Сочи Казино 2 км. Забронировать проживание.</body></html>"""

NOT_LODGING = """<html><head><title>Продажа домена</title><meta property="og:image" content="/x.jpg"></head>
<body>Этот домен продаётся</body></html>"""


def _serve(monkeypatch, page):
    monkeypatch.setattr(photos, "_fetch", lambda url, limit: (page.encode(), "text/html"))


def test_casino_doorway_gives_no_preview(monkeypatch):
    for page in (DOORWAY, HIDDEN_DOORWAY, NOT_LODGING):
        _serve(monkeypatch, page)
        assert photos._og_image("https://pandahotel.ru/") is None


def test_real_hotel_site_keeps_preview(monkeypatch):
    _serve(monkeypatch, HOTEL)
    assert photos._og_image("https://pandahotel.ru/") == "https://pandahotel.ru/img/front.jpg"


def test_old_cache_entries_are_rechecked(monkeypatch, tmp_path):
    monkeypatch.setattr(photos, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(photos, "INDEX", tmp_path / "index.json")
    (tmp_path / "osm-n1.jpg").write_bytes(b"casino")
    photos._save("osm-n1", {"checked": 0, "file": "osm-n1.jpg", "source": "site"})
    monkeypatch.setattr(photos, "_commons", lambda *a: None)
    _serve(monkeypatch, DOORWAY)

    class Hotel:
        id, name, website, lat, lon = "osm-n1", "Приют Панды", "https://pandahotel.ru/", 43.68, 40.26

    assert photos.photo(Hotel()) is None
    assert photos._load_index()["osm-n1"]["v"] == photos.CACHE_VERSION
