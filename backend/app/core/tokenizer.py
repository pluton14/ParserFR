"""Токенизация французского текста.

Перенесено из parser.py без изменений в логике: правила разбиения слов
определяют весь последующий подсчёт статистики, поэтому любое отклонение
здесь сделало бы новые результаты несравнимыми со старыми.
"""

import re

# Заменяем все разделители (кроме апострофов) на пробелы.
# Em-dash (—), en-dash (–), дефис (-) для тире;
# различные типы кавычек: " " « » ' '
_SEPARATORS = r'[\s\—\–\:\,\;\!\?\"\"\«\»\'\'\(\)\[\]\{\}\.\…]'
_SEPARATORS_RE = re.compile(_SEPARATORS)


def tokenize_french_text(text: str) -> list[str]:
    """
    Токенизирует французский текст с учетом особенностей языка:
    - Апострофы разделяют слова: j'ai -> ['j', 'ai']
    - Пробелы, тире (—), двоеточие, запятая, точка с запятой, кавычки,
      восклицательный и вопросительные знаки разделяют слова
    - Диакритические знаки сохраняются
    """
    text = _SEPARATORS_RE.sub(' ', text)

    # Теперь разделяем по апострофам, но сохраняем каждую часть
    words: list[str] = []
    for part in text.split():
        if part:  # Игнорируем пустые строки
            # Разделяем по апострофу
            for subpart in part.split("'"):
                if subpart:  # Добавляем только непустые части
                    words.append(subpart.lower())

    return words


def split_keyword(keyword: str) -> tuple[list[str], str]:
    """Split a keyword into individual words, preserving the original for statistics."""
    return keyword.lower().split(), keyword
