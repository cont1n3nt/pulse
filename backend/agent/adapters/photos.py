"""Фото отелей по требованию. В OpenStreetMap фотографий нет, поэтому по порядку:

1. превью с сайта самого отеля (og:image) — фото именно этого объекта, со ссылкой на сайт;
2. снимок из Wikimedia Commons в радиусе 150 м — фото места рядом, с автором и лицензией;
3. ничего — фронт показывает карту.

Картинка скачивается один раз и отдаётся с нашего сервера: сторонние сайты часто запрещают
встраивание. Адреса сайтов приходят из OSM, то есть от людей, поэтому каждый запрос проверяется:
только http(s), только публичные IP, лимиты размера и времени.
"""

import html
import ipaddress
import json
import logging
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse

import requests

from ..config import DATA_DIR
from ..contracts import HotelOption
from ..location import HERE

log = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "photos"
INDEX = CACHE_DIR / "index.json"
HEADERS = {"User-Agent": "PulseTravelAgent/0.1 (hackathon demo; hotel preview images)"}
TIMEOUT = 6
MAX_HTML = 600_000
MAX_IMAGE = 4_000_000
MIN_IMAGE = 8_000  # меньше — иконка или логотип
RETRY_AFTER_S = 7 * 24 * 3600  # «фото нет» перепроверяем раз в неделю
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_RADIUS_M = 150
# Диапазон 198.18.0.0/15 зарезервирован под тесты, его отдают DNS прокси-клиентов (fake-IP):
# за ним внешний сайт, а не внутренняя сеть
FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
SKIP_TITLE = re.compile(r"map|карта|схема|logo|логотип|\.svg|\.tif|\.pdf|plan|diagram", re.I)
# слова из названий отелей, которые не отличают отель от соседей: тип, а также город и районы локации
GENERIC_WORDS = {"отель", "hotel", "гостиница", "park", "парк", "resort",
                 "club", "клуб", "house", "дом", "мини", "mini", "гостевой", "apartments", "апартаменты", "center",
                 "centre", "центр", "spa", "спа", "плаза", "plaza", "grand", "гранд"}
GENERIC_WORDS |= {w.lower() for name in (HERE.names.city, *HERE.district_names) for w in name.split()}
GENERIC_WORDS |= set(HERE.photo_stopwords)
# снимки места, которые уместны вместо фото отеля: море, пляж, набережная, вид, горы
PLACE_TITLE = re.compile(r"hotel|отел|гостиниц|resort|sea|море|beach|пляж|embankment|набережн|promenade|"
                         r"view|вид|panorama|панорам|coast|берег|mountain|гор[аы]|sunset|закат|park|парк", re.I)
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

_lock = threading.Lock()


class Unsafe(Exception):
    pass


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Unsafe("схема")
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global and ip not in FAKE_IP:
            raise Unsafe("внутренний адрес")


def _fetch(url: str, limit: int) -> tuple[bytes, str]:
    """GET с проверкой каждого редиректа и лимитом размера."""
    for _ in range(4):
        _check_host(url)
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=False)
        if resp.is_redirect and resp.headers.get("location"):
            url = urljoin(url, resp.headers["location"])
            continue
        resp.raise_for_status()
        body = b""
        for part in resp.iter_content(64_000):
            body += part
            if len(body) > limit:
                raise Unsafe("слишком большой ответ")
        return body, resp.headers.get("content-type", "").split(";")[0].strip().lower()
    raise Unsafe("слишком много редиректов")


def _og_image(website: str) -> str | None:
    body, kind = _fetch(website, MAX_HTML)
    if "html" not in kind:
        return None
    page = body.decode("utf-8", "ignore")
    for pattern in (r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)'):
        if found := re.search(pattern, page, re.I):
            return urljoin(website, html.unescape(found.group(1).strip()))
    return None


def _commons(lat: float, lon: float, name: str = "") -> dict | None:
    resp = requests.get(COMMONS_API, headers=HEADERS, timeout=TIMEOUT, params={
        "action": "query", "format": "json", "generator": "geosearch", "ggsnamespace": 6,
        "ggscoord": f"{lat}|{lon}", "ggsradius": COMMONS_RADIUS_M, "ggslimit": 10,
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": 800})
    resp.raise_for_status()
    pages = sorted((resp.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    # сначала снимки с названием отеля в имени файла, затем виды места; машины и вывески — мимо
    words = [w for w in re.findall(r"\w{4,}", name.lower()) if w not in GENERIC_WORDS]
    named = [p for p in pages if any(w in p.get("title", "").lower() for w in words)]
    scenic = [p for p in pages if PLACE_TITLE.search(p.get("title", ""))]
    for page in named + scenic:
        info = (page.get("imageinfo") or [{}])[0]
        if SKIP_TITLE.search(page.get("title", "")) or info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
        license_name = meta.get("LicenseShortName", {}).get("value", "")
        return {"image": info.get("thumburl") or info["url"], "page": info.get("descriptionurl"),
                "credit": ", ".join(x for x in (artist, license_name, "Wikimedia Commons") if x)}
    return None


def _load_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(hotel_id: str, entry: dict) -> None:
    with _lock:
        index = _load_index()
        index[hotel_id] = entry
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


def _download(hotel_id: str, url: str) -> str | None:
    body, kind = _fetch(url, MAX_IMAGE)
    if kind not in IMAGE_TYPES or len(body) < MIN_IMAGE:
        return None
    name = re.sub(r"[^a-z0-9-]", "", hotel_id.lower()) + IMAGE_TYPES[kind]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / name).write_bytes(body)
    return name


def photo(hotel: HotelOption) -> dict | None:
    """{"file", "source": "site" | "commons", "credit", "page"} или None, если фото не нашлось."""
    cached = _load_index().get(hotel.id)
    if cached and (cached.get("file") or time.time() - cached.get("checked", 0) < RETRY_AFTER_S):
        return cached if cached.get("file") and (CACHE_DIR / cached["file"]).exists() else None

    entry: dict = {"checked": time.time(), "file": None}
    try:
        if hotel.website and (image := _og_image(hotel.website)):
            if name := _download(hotel.id, image):
                entry |= {"file": name, "source": "site", "credit": urlparse(hotel.website).hostname,
                          "page": hotel.website}
    except (requests.RequestException, Unsafe, OSError, ValueError) as exc:
        log.info("Фото с сайта %s не получено: %s", hotel.id, type(exc).__name__)
    if not entry["file"]:
        try:
            if found := _commons(hotel.lat, hotel.lon, hotel.name):
                if name := _download(hotel.id, found["image"]):
                    entry |= {"file": name, "source": "commons", "credit": found["credit"], "page": found["page"]}
        except (requests.RequestException, Unsafe, OSError, ValueError) as exc:
            log.info("Фото из Commons для %s не получено: %s", hotel.id, type(exc).__name__)
    _save(hotel.id, entry)
    return entry if entry["file"] else None


def path(entry: dict):
    return CACHE_DIR / entry["file"]
