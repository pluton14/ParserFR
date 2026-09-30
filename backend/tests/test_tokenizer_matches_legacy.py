"""Токенизатор обязан побайтово совпадать с parser.py.

Любое расхождение здесь означает, что новая статистика перестаёт быть
сравнимой со старыми выгрузками statistics/*.json — а это прямое требование
спеки («смысловая начинка должна оставаться прежней»).
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.tokenizer import tokenize_french_text as new_tokenize  # noqa: E402

_LEGACY_PATH = Path(__file__).resolve().parents[2] / "parser.py"
_spec = importlib.util.spec_from_file_location("legacy_parser", _LEGACY_PATH)
legacy_parser = importlib.util.module_from_spec(_spec)
import atexit as _atexit  # noqa: E402
import signal as _signal  # noqa: E402

# parser.py регистрирует atexit/signal-хендлеры на уровне модуля (сохранение
# статистики при Ctrl+C). Нам нужна только функция токенизации — глушим
# регистрацию хендлеров на время импорта, иначе они срабатывают при выходе
# из pytest и печатают постороннее сообщение в вывод тестов.
_orig_register, _orig_signal = _atexit.register, _signal.signal
_atexit.register = lambda *a, **k: None
_signal.signal = lambda *a, **k: None
try:
    _spec.loader.exec_module(legacy_parser)
finally:
    _atexit.register, _signal.signal = _orig_register, _orig_signal

SAMPLES = [
    "L'invasion russe de l'Ukraine — un tournant ; dit-il.",
    "Les «troupes russes» ont, selon Paris... avancé!",
    "aujourd'hui l'armée russe s'est retirée (enfin).",
    "Crimée annexée : la République soviétique d'antan",
    "Multi   espaces\tet\ttabs\nnouvelle ligne",
    "",
    "ex-république soviétique",
    "Qu'est-ce que c'est ? « Rien du tout ! »",
    "Élysée — Kremlin: une rencontre décisive…",
]


def test_matches_legacy_on_samples():
    for text in SAMPLES:
        assert new_tokenize(text) == legacy_parser.tokenize_french_text(text), text


def test_empty_string_returns_empty_list():
    assert new_tokenize("") == []


def test_apostrophe_splits_into_parts():
    assert new_tokenize("j'ai") == ["j", "ai"]


def test_case_is_lowered():
    assert new_tokenize("RUSSIE Poutine") == ["russie", "poutine"]
