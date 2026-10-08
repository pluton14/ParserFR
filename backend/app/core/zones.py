"""Зоны статьи, по которым можно вести анализ: заголовок, подписи к фото, основной текст.

В базе и в API набор зон хранится одной строкой. Прежние значения («body»,
«title», «title_body») продолжают читаться — старые анализы в истории остаются
валидными.
"""

from __future__ import annotations

# Порядок фиксирован: он же порядок сканирования и канонической записи.
ZONES = ("title", "captions", "body")

_LEGACY = {
    "body": ("body",),
    "title": ("title",),
    "title_body": ("title", "body"),
}


def parse_text_scope(value: str) -> tuple[str, ...]:
    """Строка набора зон → кортеж зон в каноническом порядке.

    Принимает прежние значения и список через запятую («title,captions»).
    Бросает ValueError на неизвестную зону или пустой набор.
    """
    value = (value or "").strip()
    if value in _LEGACY:
        return _LEGACY[value]
    chosen = {part.strip() for part in value.split(",") if part.strip()}
    unknown = chosen - set(ZONES)
    if unknown or not chosen:
        raise ValueError("text_scope: допустимы зоны title, captions, body (через запятую)")
    return tuple(zone for zone in ZONES if zone in chosen)


def format_text_scope(zones: tuple[str, ...]) -> str:
    """Каноническая запись набора зон для хранения."""
    for legacy, legacy_zones in _LEGACY.items():
        if tuple(zones) == legacy_zones:
            return legacy
    return ",".join(zones)
