"""Подсчёт статистики ключевых слов по токенизированным статьям.

Логика подсчёта (какие соседи считаются, как режется окно примера,
как словосочетание отличается от одиночного слова) перенесена из
WordStatistics в parser.py один в один. Отличие только в источнике
данных: здесь на вход приходят уже сохранённые токены статьи, а не
свежескачанный HTML.
"""

from collections import Counter
from dataclasses import dataclass, field

from .tokenizer import split_keyword

# Сколько слов контекста сохраняется слева и справа от находки.
CONTEXT_SIZE = 10


@dataclass
class Example:
    url: str
    context_before: str
    keyword: str
    context_after: str
    published_date: str | None = None
    title: str | None = None


class WordStatistics:
    """Счётчики соседних слов и примеры употребления для одного ключевого слова."""

    def __init__(self) -> None:
        self.left_context_words: Counter[str] = Counter()
        self.right_context_words: Counter[str] = Counter()
        self.total_occurrences = 0
        self.articles_with_word = 0
        self.examples: list[Example] = []
        # Вхождения по датам — для графика динамики на дашборде.
        self.occurrences_by_date: Counter[str] = Counter()
        self.articles_by_date: Counter[str] = Counter()

    def add_context(
        self,
        words: list[str],
        keyword_index: int,
        article_url: str | None = None,
        is_phrase: bool = False,
        phrase_length: int = 1,
        published_date: str | None = None,
        title: str | None = None,
        collect_example: bool = True,
    ) -> None:
        """Добавляет контекст для слова или словосочетания.

        Args:
            words: список слов в статье
            keyword_index: индекс начала слова/словосочетания
            article_url: URL статьи
            is_phrase: является ли ключевое слово словосочетанием
            phrase_length: длина словосочетания (количество слов)
            published_date: дата публикации статьи (ISO), для динамики по дням
            title: заголовок статьи, показывается рядом с примером
            collect_example: копить ли сам пример (счётчики считаются всегда)
        """
        if is_phrase:
            # Для словосочетания берем слово перед началом и после конца
            if keyword_index > 0:  # Если есть слово слева
                self.left_context_words[words[keyword_index - 1]] += 1
            if keyword_index + phrase_length < len(words):  # Если есть слово справа
                self.right_context_words[words[keyword_index + phrase_length]] += 1
        else:
            # Для одиночного слова берем слова слева и справа
            if keyword_index > 0:  # Если есть слово слева
                self.left_context_words[words[keyword_index - 1]] += 1
            if keyword_index < len(words) - 1:  # Если есть слово справа
                self.right_context_words[words[keyword_index + 1]] += 1

        if collect_example:
            # Сохраняем пример с контекстом (берем 10 слов слева и справа)
            start_idx = max(0, keyword_index - CONTEXT_SIZE)
            end_idx = min(len(words), keyword_index + phrase_length + CONTEXT_SIZE)

            self.examples.append(
                Example(
                    url=article_url or "",
                    context_before=' '.join(words[start_idx:keyword_index]),
                    keyword=' '.join(words[keyword_index:keyword_index + phrase_length]),
                    context_after=' '.join(words[keyword_index + phrase_length:end_idx]),
                    published_date=published_date,
                    title=title,
                )
            )

        if published_date:
            self.occurrences_by_date[published_date] += 1

        self.total_occurrences += 1


@dataclass
class KeywordPlan:
    """Разобранное ключевое слово, подготовленное к сканированию."""

    original: str
    words: list[str]

    @property
    def is_phrase(self) -> bool:
        return len(self.words) > 1

    @property
    def length(self) -> int:
        return len(self.words)


def build_keyword_plans(keywords: list[str]) -> list[KeywordPlan]:
    plans: list[KeywordPlan] = []
    for keyword in keywords:
        words, original = split_keyword(keyword)
        if words:
            plans.append(KeywordPlan(original=original, words=words))
    return plans


@dataclass
class ScanResult:
    """Итог сканирования корпуса."""

    stats: dict[str, WordStatistics] = field(default_factory=dict)
    total_articles: int = 0


def _index_by_first_word(plans: list[KeywordPlan]) -> dict[str, list[KeywordPlan]]:
    """Группирует планы по первому слову фразы.

    Даёт единственный проход по тексту статьи вместо перебора всех
    ключевых слов на каждой позиции (см. build_first_word_index ниже за
    объяснением, почему это было необходимо).
    """
    index: dict[str, list[KeywordPlan]] = {}
    for plan in plans:
        index.setdefault(plan.words[0], []).append(plan)
    return index


def scan_article(
    words: list[str],
    plans: list[KeywordPlan],
    stats: dict[str, WordStatistics],
    url: str,
    published_date: str | None = None,
    title: str | None = None,
    example_limit: int | None = None,
) -> dict[str, int]:
    """Сканирует одну статью и обновляет счётчики.

    Возвращает число вхождений каждого ключевого слова в этой статье.

    Алгоритм: один проход по токенам статьи, на каждой позиции — поиск
    в хеш-таблице по первому слову фразы вместо перебора всех ключевых
    слов словаря. Порядок обработки совпадений на одной позиции (по
    порядку плана в словаре) сохранён из parser.py, чтобы пересечения
    словосочетаний считались так же, как раньше.

    Почему это важно: наивный перебор "для каждой позиции — все ключевые
    слова" даёт время O(токены × слова_словаря). Замер 2026-09-22 на
    медианной статье (370 токенов, 16 ключевых слов): 3.3 мс/статья, что
    на полном архиве Le Figaro (~1.55 млн статей) давало бы ~86 минут на
    один прогон анализа — неприемлемо для "выбрал период, нажал кнопку".
    Хеш-индекс по первому слову убирает множитель на размер словаря.
    """
    counts = {plan.original: 0 for plan in plans}
    n = len(words)
    index = _index_by_first_word(plans)

    for i in range(n):
        candidates = index.get(words[i])
        if not candidates:
            continue
        for plan in candidates:
            if i + plan.length <= n:
                if all(words[i + j] == word for j, word in enumerate(plan.words)):
                    counts[plan.original] += 1
                    entry = stats[plan.original]
                    collect = example_limit is None or len(entry.examples) < example_limit
                    entry.add_context(
                        words,
                        i,
                        article_url=url,
                        is_phrase=plan.is_phrase,
                        phrase_length=plan.length,
                        published_date=published_date,
                        title=title,
                        collect_example=collect,
                    )

    if published_date:
        for keyword, count in counts.items():
            if count > 0:
                stats[keyword].articles_by_date[published_date] += 1

    return counts
