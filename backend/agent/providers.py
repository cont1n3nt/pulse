"""Источники данных агента: контракты и реестр провайдеров.

Ядро (пакеты, проверка поездки, перенос заказа, инструменты) не знает, откуда пришли отели,
билеты и заказы: оно вызывает провайдеров только через функции этого модуля и получает модели
из contracts.py. Чтобы подключить настоящий API (например, Островка), достаточно написать классы
с методами из протоколов ниже и выбрать их переменной PULSE_PROVIDERS — код агента не меняется.

    PULSE_PROVIDERS=demo                         отели OSM + демо-цены, Aviasales, заказы в SQLite
    PULSE_PROVIDERS=mypackage.ostrovok:providers функция без аргументов, которая возвращает Providers

Поезда пока подключены напрямую адаптером adapters/trains.py: это отдельный вид дороги поверх тех же
контрактов (FlightOption с mode="train", TransferOption от вокзала).
"""

import importlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol, runtime_checkable

from .config import PROVIDERS as PROVIDERS_SPEC
from .contracts import CartItem, FlightOption, HotelOption, Order, TransferOption


@runtime_checkable
class HotelProvider(Protocol):
    """Инвентарь и тарифы: поиск, цена на даты, карточка объекта."""

    def search(self, check_in: date, check_out: date, district: str | None = None,
               max_price_per_night: int | None = None, must_have=(), near_sea: bool = False,
               exclude_kinds=(), limit: int = 5, diversify: bool = True,
               target_per_night: int | None = None) -> list[HotelOption]:
        """Доступные на даты объекты, лучшие первыми; у каждого заполнены цена, наличие и условия."""

    def quote(self, hotel_id: str, check_in: date, check_out: date) -> HotelOption | None:
        """Актуальная цена и наличие объекта на даты: перед оплатой и при переносе заказа."""

    def get(self, hotel_id: str) -> HotelOption | None:
        """Карточка объекта без дат: описание, координаты, удобства."""


@runtime_checkable
class TransferProvider(Protocol):
    """Дорога от аэропорта до отеля и такси туда-обратно."""

    def transfer(self, hotel: HotelOption, guests: int) -> TransferOption | None:
        """Такси аэропорт ⇄ отель на всех гостей; None — дорога неизвестна."""

    def transfer_minutes(self, hotel: HotelOption) -> int | None:
        """Сколько ехать от выхода из аэропорта до отеля."""

    def for_guests(self, transfer: TransferOption, guests: int) -> TransferOption:
        """Тот же маршрут на другое число гостей (машина и цена меняются)."""


@runtime_checkable
class FlightProvider(Protocol):
    """Билеты. Временный сбой источника — contracts.SourceUnavailable: агент повторит и скажет честно."""

    def cheapest_on(self, origin: str, destination: str, day: date,
                    arrive_by: date | None = None) -> FlightOption | None:
        """Самый дешёвый билет на день; arrive_by — прилететь не позже этой даты."""

    def prices_around(self, origin: str, destination: str, day: date, days: int) -> dict[date, FlightOption]:
        """Самые дешёвые билеты на дни day±days: для подсказки «даты подешевле»."""

    def fetch_flights(self, origin: str, destination: str, depart: str,
                      return_date: str | None = None, limit: int = 5) -> list[FlightOption]:
        """Варианты на дату или месяц (YYYY-MM-DD или YYYY-MM)."""


@runtime_checkable
class BookingGateway(Protocol):
    """Бронирование и оплата. create вызывается только после кнопки «Оплатить» (interrupt в checkout)."""

    def create(self, session_id: str, version: int, items: list[CartItem], total_rub: int) -> Order:
        """Оформить корзину. Повтор с той же версией корзины возвращает тот же заказ."""

    def get(self, order_id: str) -> Order | None:
        """Заказ по номеру."""

    def cancel(self, order_id: str) -> None:
        """Отменить заказ: его заменил перенесённый."""


@dataclass(frozen=True)
class Providers:
    hotels: HotelProvider
    transfers: TransferProvider
    flights: FlightProvider
    orders: BookingGateway


def demo() -> Providers:
    """Что подключено сейчас: модули адаптеров сами реализуют протоколы."""
    from . import orders
    from .adapters import aviasales, hotels
    return Providers(hotels=hotels, transfers=hotels, flights=aviasales, orders=orders)


REGISTRY: dict[str, Callable[[], Providers]] = {"demo": demo}


def load(spec: str) -> Providers:
    """Провайдеры по имени из REGISTRY или по пути «модуль:функция»."""
    if spec in REGISTRY:
        found = REGISTRY[spec]()
    elif ":" in spec:
        module, name = spec.split(":", 1)
        found = getattr(importlib.import_module(module), name)()
    else:
        raise ValueError(f"PULSE_PROVIDERS={spec}: нет в {sorted(REGISTRY)} и не похоже на «модуль:функция»")
    check(found)
    return found


def check(found: Providers) -> None:
    """Ошибку в адаптере видно при старте, а не на первом запросе пользователя."""
    for field, protocol in (("hotels", HotelProvider), ("transfers", TransferProvider),
                            ("flights", FlightProvider), ("orders", BookingGateway)):
        if not isinstance(getattr(found, field), protocol):
            raise TypeError(f"Провайдер {field} не реализует {protocol.__name__}")


_active: Providers | None = None


def active() -> Providers:
    global _active
    if _active is None:
        _active = load(PROVIDERS_SPEC)
    return _active


def hotels() -> HotelProvider:
    return active().hotels


def transfers() -> TransferProvider:
    return active().transfers


def flights() -> FlightProvider:
    return active().flights


def orders() -> BookingGateway:
    return active().orders


@contextmanager
def use(**overrides) -> Iterator[Providers]:
    """Временно подменить провайдеров: в тестах и при встраивании ядра без агента."""
    global _active
    previous = active()
    swapped = replace(previous, **overrides)
    check(swapped)
    _active = swapped
    try:
        yield _active
    finally:
        _active = previous
