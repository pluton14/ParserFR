"""Сбор корпуса: скачивание статей Le Figaro в базу.

Это единственная долгая операция в системе. Она идемпотентна: уже
сохранённая статья повторно не скачивается, поэтому повторный запуск за
тот же период стоит почти ничего, а анализ новых ключевых слов не требует
сети вообще.

Сетевые правила (повторы, пауза после серии ошибок, трактовка 500 как
платной статьи) перенесены из parser.process_article.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import date, datetime

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import func, select

from ..config import settings
from ..core.figaro import (
    FetchedArticle,
    PremiumArticle,
    browser_headers,
    fetch_article,
    get_articles_from_sitemap,
    list_daily_sitemaps,
)
from ..core.tokenizer import tokenize_french_text
from ..db import session_scope
from ..models import Article, ArticleStatus, DayStatus, HarvestedDay
from .jobs import JobHandle

# Как часто сбрасывать накопленные статьи в базу.
FLUSH_EVERY = 50


def _build_session() -> requests.Session:
    """Сессия requests с пулом соединений под число рабочих потоков.

    Находка 2026-09-23: urllib3 по умолчанию держит пул на 10 соединений
    на хост, а harvest_workers обычно больше (16). При 16 параллельных
    запросах к www.lefigaro.fr лишние 6 потоков не переиспользуют
    соединение из пула, а каждый раз открывают новое и тут же его роняют
    ("Connection pool is full, discarding connection") — 1994 таких
    предупреждений за один прогон. Это лишние TCP/TLS-рукопожатия на
    каждый "выпавший" запрос и вероятная причина, почему реальный темп
    (~7.4 ст/с) оказался заметно ниже синтетического бенчмарка (~27 ст/с).
    """
    session = requests.Session()
    session.headers.update(browser_headers())
    pool_size = max(settings.harvest_workers, 10) + 4
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class ErrorBudget:
    """Считает ошибки подряд и притормаживает при серии отказов источника."""

    def __init__(self, job: JobHandle) -> None:
        self._consecutive = 0
        self._lock = threading.Lock()
        self._job = job

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0

    def record_error(self) -> None:
        with self._lock:
            self._consecutive += 1
            over_limit = self._consecutive >= settings.error_threshold
            if over_limit:
                self._consecutive = 0
        if over_limit:
            self._job.log(
                f"Слишком много ошибок подряд. Пауза на "
                f"{settings.error_pause_seconds // 60} мин…",
                level="warning",
            )
            # Пауза прерывается, если пользователь остановил задачу.
            self._job.cancel_event.wait(timeout=settings.error_pause_seconds)


def _fetch_with_retries(
    url: str,
    session: requests.Session,
    budget: ErrorBudget,
    job: JobHandle,
) -> tuple[str, FetchedArticle | None, str]:
    """Возвращает (url, статья или None, статус)."""
    for _ in range(settings.max_retries):
        if job.is_cancelled():
            return url, None, ArticleStatus.FAILED.value
        try:
            article = fetch_article(url, session=session)
            budget.record_success()
            if not article.text.strip():
                status = ArticleStatus.EMPTY.value
            elif article.truncated:
                status = ArticleStatus.TRUNCATED.value
            else:
                status = ArticleStatus.OK.value
            return url, article, status
        except PremiumArticle:
            # Платная статья: у источника нет для нас текста, повторы бессмысленны.
            budget.record_success()
            return url, None, ArticleStatus.PREMIUM.value
        except requests.exceptions.RequestException:
            budget.record_error()
            time.sleep(settings.retry_delay)
        except Exception as exc:  # noqa: BLE001
            job.log(f"Неожиданная ошибка статьи {url}: {exc}", level="warning")
            return url, None, ArticleStatus.FAILED.value

    return url, None, ArticleStatus.FAILED.value


def _count_known_in_range(start: date, end: date) -> int:
    """Сколько статей за период уже в базе с текстом — один агрегатный
    запрос по индексу (published_date, status), а не построение множества
    URL. Только для информационного лога, не для решения what to download —
    см. находку 2026-09-28 у run_harvest."""
    with session_scope() as db:
        return db.execute(
            select(func.count())
            .select_from(Article)
            .where(
                Article.published_date.between(start, end),
                Article.status.in_([
                    ArticleStatus.OK.value,
                    ArticleStatus.TRUNCATED.value,
                    ArticleStatus.PREMIUM.value,
                ]),
            )
        ).scalar_one()


def _existing_urls(urls: list[str]) -> set[str]:
    """Какие из этих URL уже лежат в базе с текстом."""
    found: set[str] = set()
    with session_scope() as db:
        # SQLite ограничивает число параметров в запросе, поэтому порциями.
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            rows = db.execute(
                select(Article.url).where(
                    Article.url.in_(chunk),
                    Article.status.in_([
                        ArticleStatus.OK.value,
                        ArticleStatus.TRUNCATED.value,
                        ArticleStatus.PREMIUM.value,
                    ]),
                )
            ).scalars()
            found.update(rows)
    return found


def _days_with_known_urls(days: list[date]) -> set[date]:
    """Дни, для которых ПОЛНЫЙ список статей уже лежит в базе.

    Не просто "есть хотя бы одна строка Article на этот день" — а точное
    совпадение числа строк с total_urls, зафиксированным при последнем
    успешном листинге дня (в HarvestedDay). Разница принципиальна: до
    появления pending-кэша день мог успеть скачать ЧАСТЬ статей (например,
    50 из 300) перед отменой/паузой, и в Article остались только эти 50
    строк — при проверке "есть хоть что-то" такой день ошибочно считался
    бы уже полностью листингованным, и оставшиеся 250 URL терялись бы
    навсегда: их просто негде было бы взять заново, ведь сетевой листинг
    для этого дня больше не запускался бы.
    """
    with session_scope() as db:
        harvested_totals: dict[date, int] = {}
        for i in range(0, len(days), 500):
            chunk = days[i:i + 500]
            rows = db.execute(
                select(HarvestedDay.day, HarvestedDay.total_urls).where(
                    HarvestedDay.day.in_(chunk), HarvestedDay.total_urls > 0
                )
            ).all()
            harvested_totals.update(dict(rows))

        if not harvested_totals:
            return set()

        article_counts: dict[date, int] = {}
        known_days = list(harvested_totals.keys())
        for i in range(0, len(known_days), 500):
            chunk = known_days[i:i + 500]
            rows = db.execute(
                select(Article.published_date, func.count())
                .where(Article.published_date.in_(chunk))
                .group_by(Article.published_date)
            ).all()
            article_counts.update(dict(rows))

    return {
        day for day, total in harvested_totals.items()
        if article_counts.get(day, 0) >= total
    }


def _urls_for_days(days: list[date]) -> dict[date, list[str]]:
    """Полный список URL статей для многих дней сразу — замена повторному листингу карты.

    Находка 2026-09-26: при резюме с тысячами уже закэшированных дней старая
    версия открывала отдельный session_scope() (со своим commit) на КАЖДЫЙ
    день. При ~6666 днях в прогоне это давало фиксированные
    ~150-200мс на день только на накладные расходы сессии/коммита, то есть
    ~18-20 минут ТОЛЬКО на повторный листинг уже известных дней при каждом
    перезапуске сервера — почти весь 15-минутный цикл супервизора уходил на
    это, не оставляя времени на реальное скачивание статей. Один batch-запрос
    (порциями по 500 дней, как везде в этом файле) сводит листинг
    закэшированных дней к секундам вместо минут.
    """
    result: dict[date, list[str]] = {day: [] for day in days}
    with session_scope() as db:
        for i in range(0, len(days), 500):
            chunk = days[i:i + 500]
            rows = db.execute(
                select(Article.published_date, Article.url).where(
                    Article.published_date.in_(chunk)
                )
            ).all()
            for day, url in rows:
                result[day].append(url)
    return result


def _persist_pending_urls(day: date, urls: list[str]) -> None:
    """Сразу после успешного листинга сохраняет URL как 'pending' (известны, не скачаны).

    Это и есть кэш карты: следующий запуск найдёт эти строки через
    _days_with_known_urls и не полезет в сеть за той же картой снова.
    """
    if not urls:
        return
    with session_scope() as db:
        # `seen` защищает не только от дублей, уже лежащих в базе, но и от
        # повтора внутри самого списка urls за этот вызов — на случай, если
        # вызывающий код (пока) не дедуплицировал сам. Дедуп на входе в
        # run_harvest уже есть (см. комментарий там), но эта функция
        # достаточно простая и переиспользуемая, чтобы не полагаться на
        # чужую дисциплину — лучше не упасть второй раз похожим образом.
        seen = set(
            db.execute(select(Article.url).where(Article.url.in_(urls))).scalars()
        )
        for url in urls:
            if url not in seen:
                seen.add(url)
                db.add(Article(url=url, published_date=day, status=ArticleStatus.PENDING.value))


def _persist_batch(batch: list[tuple[str, FetchedArticle | None, str, date]]) -> None:
    if not batch:
        return
    with session_scope() as db:
        urls = [item[0] for item in batch]
        existing = {
            row.url: row
            for row in db.execute(select(Article).where(Article.url.in_(urls))).scalars()
        }
        for url, fetched, status, day in batch:
            article = existing.get(url)
            if article is None:
                article = Article(url=url, published_date=day)
                db.add(article)
            article.published_date = day
            article.status = status
            if fetched is not None:
                article.title = fetched.title
                article.category = fetched.category
                article.set_content(fetched.text, tokenize_french_text(fetched.text))


def _count_by_status(day: date) -> dict[str, int]:
    """Свежий подсчёт статей дня по статусам — читает Article, не кэш HarvestedDay.

    Используется отдельно от _update_day_stats там, где нужно принять
    решение (done vs partial) по действительному состоянию базы на этот
    момент, а не по значению, записанному в HarvestedDay на более ранней
    стадии обработки того же дня (см. находку 2026-09-22: чтение поля
    row.failed_count сразу после RUNNING-отметки давало устаревший снимок).
    """
    with session_scope() as db:
        return dict(
            db.execute(
                select(Article.status, func.count())
                .where(Article.published_date == day)
                .group_by(Article.status)
            ).all()
        )


def _update_day_stats(day: date, sitemap_url: str, total_urls: int, status: str) -> None:
    """Пересчитывает покрытие дня по фактическому содержимому базы."""
    counts = _count_by_status(day)
    with session_scope() as db:
        row = db.get(HarvestedDay, day)
        if row is None:
            row = HarvestedDay(day=day)
            db.add(row)

        row.sitemap_url = sitemap_url
        row.total_urls = total_urls
        row.ok_count = counts.get(ArticleStatus.OK.value, 0)
        row.truncated_count = counts.get(ArticleStatus.TRUNCATED.value, 0)
        row.premium_count = counts.get(ArticleStatus.PREMIUM.value, 0)
        row.empty_count = counts.get(ArticleStatus.EMPTY.value, 0)
        row.failed_count = counts.get(ArticleStatus.FAILED.value, 0)
        row.status = status
        row.harvested_at = datetime.utcnow()


def _known_sitemaps_from_db(start: date, end: date) -> list[tuple[date, str]]:
    """Резерв на случай, если индекс карт сайта недоступен: берём URL суточных
    карт, сохранённые в HarvestedDay при прошлых прогонах, для дней внутри
    периода. Не находит дни, которые никогда не листинговались — только
    подстраховка от повторного падения на уже известных днях."""
    with session_scope() as db:
        rows = db.execute(
            select(HarvestedDay.day, HarvestedDay.sitemap_url).where(
                HarvestedDay.day.between(start, end),
                HarvestedDay.sitemap_url.is_not(None),
            )
        ).all()
    return sorted(((day, url) for day, url in rows), key=lambda item: item[0])


def run_harvest(job: JobHandle, start: date, end: date, refresh: bool = False) -> None:
    """Собирает статьи за период [start, end] в базу.

    refresh=True заставляет перекачать даже те статьи, что уже сохранены.
    """
    job.log(f"Сбор корпуса за период {start} — {end}")
    job.set_progress(stage="sitemaps", message="Ищем суточные карты сайта…")

    try:
        sitemaps = list_daily_sitemaps(start, end)
    except Exception as exc:  # noqa: BLE001
        # Находка 2026-09-27: индекс карт сайта (sitemaps.lefigaro.fr) может
        # быть заблокирован (403) отдельно от остального сайта — это узкий
        # путь, почти не имеющий обычного трафика, поэтому защита источника
        # реагирует на него агрессивнее. Раньше сбой этого ОДНОГО запроса
        # ронял всю задачу, даже если у нас уже есть URL суточных карт почти
        # за весь период из прошлых прогонов (HarvestedDay.sitemap_url).
        # Резерв: собрать список карт из базы и продолжить с тем, что есть —
        # новые (никогда не листингованные) дни при этом не найдутся, но
        # уже известные не теряются.
        job.log(
            f"Индекс карт сайта недоступен ({exc}). Пробуем список карт из базы.",
            level="warning",
        )
        sitemaps = _known_sitemaps_from_db(start, end)
        if sitemaps:
            job.log(f"Из базы восстановлено {len(sitemaps)} карт дней.")
    if not sitemaps:
        job.log("За этот период карты сайта не нашлись — источник их не отдаёт.", "warning")
        job.set_progress(stage="done", message="Нет данных за период")
        return

    job.log(f"Найдено суточных карт: {len(sitemaps)}")

    # Резюме после паузы/перезапуска: дни, уже помеченные done, не листаем
    # заново. Без этого пропуска повторный запуск на полном архиве заново
    # делал бы ~7700 сетевых запросов только на перечитывание карт, даже
    # если реально скачивать нечего — сама статья и так не перекачивается
    # благодаря _existing_urls, но раньше эта проверка наступала СЛИШКОМ
    # ПОЗДНО, после дорогого перелистывания всех карт подряд.
    if not refresh:
        with session_scope() as db:
            done_days = {
                row.day
                for row in db.execute(
                    select(HarvestedDay).where(
                        HarvestedDay.day.in_([day for day, _ in sitemaps]),
                        HarvestedDay.status == DayStatus.DONE.value,
                    )
                ).scalars()
            }
        if done_days:
            sitemaps = [(day, url) for day, url in sitemaps if day not in done_days]
            if sitemaps:
                remaining_first = min(day for day, _ in sitemaps)
                remaining_last = max(day for day, _ in sitemaps)
                job.log(
                    f"РЕЗЮМЕ: уже собрано {len(done_days)} дней, пропускаем их. "
                    f"Продолжаем с {remaining_first} по {remaining_last} "
                    f"({len(sitemaps)} дней осталось)."
                )

    if not sitemaps:
        job.log("Весь запрошенный период уже собран — новых дней нет.")
        job.set_progress(stage="done", current=0, total=0,
                         message="Весь период уже собран")
        return

    job.log(f"НАЧИНАЕМ с {sitemaps[0][0]} по {sitemaps[-1][0]} ({len(sitemaps)} дней в этом прогоне).")

    # Сначала собираем полный список URL, чтобы прогресс-бар знал общий объём
    # с самого начала, а не рос по мере обхода дней.
    job.set_progress(stage="listing", total=len(sitemaps), current=0,
                     message="Составляем список статей…")

    session = _build_session()

    # Дни, чья карта уже когда-то была прочитана (в этом или прошлом
    # прогоне) и сохранена как pending-заглушки, листаются не по сети, а
    # прямо из базы. Находка 2026-09-22: раньше список URL суточной карты
    # жил только в памяти одного прогона и выбрасывался при перезапуске —
    # каждый рестарт заново тратил сетевой запрос на карту, которая уже
    # была прочитана минуту назад.
    remaining_days = [day for day, _ in sitemaps]
    cached_days = _days_with_known_urls(remaining_days) if not refresh else set()
    if cached_days:
        job.log(f"{len(cached_days)} дней уже листинговано ранее — берём список из базы.")
    # Один batch-запрос на все закэшированные дни сразу — см. находку
    # 2026-09-26 у _urls_for_days.
    cached_urls = _urls_for_days(list(cached_days)) if cached_days else {}

    # listing_ok различает "карта прочитана, в ней реально 0 статей" от
    # "карту не удалось прочитать вообще" (таймаут, обрыв соединения и т.п.).
    # Смешение этих случаев — реальная находка 2026-09-22: день с временным
    # сетевым сбоем при листинге получал urls=[] и попадал в ту же ветку,
    # что и по-настоящему пустой день, а значит мог быть помечен done. В
    # связке с пропуском done-дней при резюме это значило бы, что временный
    # сбой сети НАВСЕГДА вычёркивает день из архива без единой статьи и без
    # единой записи об ошибке, которую можно было бы заметить и исправить.
    listing_results: dict[date, tuple[str, list[str], bool]] = {}
    listing_failed_days: list[date] = []
    listing_lock = threading.Lock()
    # Кэшированные дни не проходят через _list_one_day, но должны быть
    # учтены в счётчике прогресса — иначе бар застынет на len(network_days)
    # вместо len(sitemaps).
    listing_done = len(cached_days)
    if cached_days:
        job.set_progress(current=listing_done, message=f"{len(cached_days)} дней взяты из базы")

    def _list_one_day(day: date, xml_url: str) -> None:
        nonlocal listing_done
        listing_ok = True
        # Небольшая пауза перед каждым запросом к sitemaps.lefigaro.fr —
        # находка 2026-09-28: этот поддомен почти не видит обычного
        # браузерного трафика, поэтому даже умеренный параллельный burst
        # запросов к нему выглядит подозрительнее, чем такой же по объёму
        # трафик на www.lefigaro.fr, и блокировка (403) срабатывает за
        # считанные минуты — даже со свежего IP и реалистичными заголовками.
        time.sleep(settings.listing_request_delay)
        try:
            urls = get_articles_from_sitemap(xml_url, session=session)
        except Exception as exc:  # noqa: BLE001
            job.log(f"Не удалось прочитать карту {xml_url}: {exc}", level="warning")
            urls = []
            listing_ok = False
            with listing_lock:
                listing_failed_days.append(day)
        else:
            # Найдено на реальном сборе 2026-09-22: sitemap Le Figaro иногда
            # перечисляет одну статью дважды внутри одной суточной карты
            # (замечено на спортивных статьях — вероятно, повторная
            # публикация/обновление). Без дедупликации здесь вторая вставка
            # той же ссылки как pending падала на UNIQUE constraint и роняла
            # всю задачу. Дедуп делается один раз тут же после листинга,
            # чтобы дальше по цепочке (кэш, скачивание, подсчёт) везде были
            # уже чистые списки без дублей.
            deduped = list(dict.fromkeys(urls))
            if len(deduped) != len(urls):
                job.log(
                    f"{day}: в карте {len(urls) - len(deduped)} дублей ссылок — убраны.",
                    level="warning",
                )
            urls = deduped
            # Сохраняем список сразу же — следующий запуск (даже если этот
            # прервётся на середине скачивания статей) найдёт его в базе и
            # не полезет в сеть за той же картой повторно. total_urls
            # фиксируется в HarvestedDay ТУТ ЖЕ, а не только когда очередь
            # дойдёт до скачивания: иначе при отмене между листингом и
            # скачиванием _days_with_known_urls не нашла бы total_urls для
            # сверки и всё равно перелистила бы карту заново.
            _persist_pending_urls(day, urls)
            _update_day_stats(day, xml_url, len(urls), DayStatus.PENDING.value)
        with listing_lock:
            listing_results[day] = (xml_url, urls, listing_ok)
            listing_done += 1
            done_now = listing_done
        job.set_progress(current=done_now, message=f"{day}: статей в карте — {len(urls)}")

    network_days = [(day, xml_url) for day, xml_url in sitemaps if day not in cached_days]

    # Находка 2026-09-26: листинг раньше шёл строго последовательно, один
    # день за другим — при нескольких тысячах дней, ещё не листингованных
    # ни разу, это ~150-200мс сетевого запроса НА ДЕНЬ, то есть 15-20+ минут
    # только на составление списка, прежде чем начинается хоть одно
    # скачивание статьи. Тот же пул потоков, что скачивает статьи, теперь
    # листингует карты дней параллельно.
    if network_days:
        # Отдельный, куда более узкий пул для листинга — см. находку
        # 2026-09-28 у settings.listing_workers.
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=settings.listing_workers
        ) as listing_executor:
            futures = [
                listing_executor.submit(_list_one_day, day, xml_url)
                for day, xml_url in network_days
            ]
            for future in concurrent.futures.as_completed(futures):
                if job.is_cancelled():
                    return
                future.result()

    per_day: list[tuple[date, str, list[str], bool]] = []
    for day, xml_url in sitemaps:
        if day in cached_days:
            per_day.append((day, xml_url, cached_urls.get(day, []), True))
        else:
            result_xml_url, urls, listing_ok = listing_results[day]
            per_day.append((day, result_xml_url, urls, listing_ok))

    if listing_failed_days:
        job.log(
            f"Не удалось прочитать карты за {len(listing_failed_days)} дней "
            f"(сетевой сбой) — они НЕ считаются собранными и будут "
            f"перечитаны при следующем запуске.",
            level="warning",
        )

    all_urls_count = sum(len(urls) for _, _, urls, _ in per_day)
    # Находка 2026-09-28: на полном архиве (~2.5 млн URL) один общий
    # _existing_urls(all_urls) — это ~5000 чанков по 500 в одной длинной
    # транзакции — на практике стабильно НЕДОСЧИТЫВАЛ уже готовые статьи
    # (реально наблюдалось: известных статей оказалось на ~300К меньше
    # факта), из-за чего целые дни, уже скачанные месяц назад, скачивались
    # заново пачками. Точная причина не установлена (похоже на эффект
    # масштаба самой SQLite-транзакции), но воспроизводится стабильно.
    # Обходной путь, который ПРОВЕРЕН как корректный: не строить один
    # гигантский known на весь период, а проверять статус КАЖДОГО дня
    # отдельно, прямо перед решением, что по нему докачивать — так же,
    # как проверка already-known работает и в модульных тестах.
    known_count = 0 if refresh else _count_known_in_range(start, end)
    pending_total = all_urls_count - known_count

    job.log(
        f"Всего статей за период: {all_urls_count}. "
        f"Уже в базе: {known_count}. К скачиванию (оценка): {pending_total}."
    )

    # Досрочный выход при pending_total<=0 раньше стоял тут и молча
    # завершал прогон — с находкой 2026-09-28 это стало ОПАСНО: если
    # оценка (посчитанная одним агрегатным запросом) хоть немного
    # занижает реальный остаток, весь период ложно помечается собранным
    # и статьи теряются НАВСЕГДА (день дальше пропускается при резюме).
    # Источник истины теперь только один — проверка known ПО КАЖДОМУ
    # дню внутри цикла ниже; она либо находит работу и делает её, либо
    # сама корректно помечает день done. Заводить пул потоков даже когда
    # реально нечего делать стоит недорого (см. находку 2026-09-23) —
    # это не тот путь, где стоит жертвовать корректностью ради скорости.
    job.set_progress(stage="articles", current=0, total=max(pending_total, 0),
                     message="Скачиваем статьи…")

    budget = ErrorBudget(job)
    processed = 0
    batch: list[tuple[str, FetchedArticle | None, str, date]] = []

    # Находка 2026-09-23: пул потоков раньше пересоздавался НА КАЖДЫЙ день
    # (тысячи раз за прогон) — заводить и разбирать 16 ОС-потоков тысячи
    # раз даёт заметные накладные расходы, особенно на Windows. Пул теперь
    # один на весь прогон. Барьер между днями (все потоки ждут самую
    # медленную статью текущего дня, прежде чем перейти к следующему)
    # оставлен как есть — сознательный компромисс: полное снятие барьера
    # (общая очередь на все дни сразу) требует переписывать логику решения
    # "день done/partial" на событийную, а это риск на уже идущем
    # многосуточном сборе. Если этого шага окажется недостаточно —
    # следующий кандидат на оптимизацию именно барьер, не пул.
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=settings.harvest_workers
    ) as executor:
        for day, xml_url, urls, listing_ok in per_day:
            if job.is_cancelled():
                break

            if not listing_ok:
                # Карта не прочиталась — для дня нет надёжного списка статей,
                # done ставить нельзя (см. комментарий у listing_failed_days
                # выше). Оставляем как failed, следующий запуск перечитает.
                _update_day_stats(day, xml_url, len(urls), DayStatus.FAILED.value)
                continue

            # Проверка known ЗДЕСЬ, по одному дню (не по всему периоду
            # разом) — см. находку 2026-09-28 выше про массовое
            # недосчитывание known на большом all_urls.
            known_for_day = set() if refresh else _existing_urls(urls)
            todo = [url for url in urls if url not in known_for_day]
            if not todo:
                _update_day_stats(day, xml_url, len(urls), DayStatus.DONE.value)
                continue

            _update_day_stats(day, xml_url, len(urls), DayStatus.RUNNING.value)
            job.log(f"{day}: скачиваем {len(todo)} статей")

            # Свежая сессия на каждый день. Находка 2026-09-25: одна и та же
            # requests.Session, переиспользуемая часами подряд на тысячах
            # запросов, постепенно накапливает в своём пуле соединения,
            # которые удалённая сторона уже закрыла (видно по CloseWait в
            # netstat) — процессор при этом простаивает, а каждый запрос,
            # которому достаётся такое "полумёртвое" соединение, ощутимо
            # дольше падает и переоткрывается заново. За несколько часов
            # темп проседал с ~6 ст/с до ~1 ст/с без единой ошибки в логе.
            # Пересоздание пула на границе дня держит его коротко живущим.
            session = _build_session()

            futures = [
                executor.submit(_fetch_with_retries, url, session, budget, job)
                for url in todo
            ]
            for future in concurrent.futures.as_completed(futures):
                url, fetched, status = future.result()
                batch.append((url, fetched, status, day))
                processed += 1

                if len(batch) >= FLUSH_EVERY:
                    _persist_batch(batch)
                    batch.clear()

                if processed % 10 == 0 or processed == pending_total:
                    job.set_progress(current=processed,
                                     message=f"{day}: обработано {processed}/{pending_total}")

                if job.is_cancelled():
                    for pending in futures:
                        pending.cancel()
                    break

            _persist_batch(batch)
            batch.clear()

            # Откат 2026-09-25: внутридневной повтор failed-статей (несколько
            # раундов ожидания + ре-скачивание в конце дня) держал уже
            # занятый воркер-пул занятым ещё дольше и на практике при 8-16
            # потоках приводил к затяжным "Пауза на 2 мин" сериям и резкому
            # падению темпа — CPU/GIL и так на пределе от токенизации и
            # записи в SQLite, а повторы добавляли работу поверх этого же
            # исчерпанного пула вместо того, чтобы ждать в стороне. День с
            # failed-статьями просто остаётся partial и подхватывается
            # следующим обычным запуском за тот же период (см. резюме выше).
            #
            # День считается done только если для каждого URL из его sitemap
            # есть РЕАЛЬНЫЙ исход (ok/truncated/premium/empty/failed) — то есть
            # ни одной строки не осталось в статусе pending — и среди исходов
            # нет failed, и задачу не остановили посреди дня.
            #
            # Проверка именно через pending_count, а не через сумму всех строк,
            # потому что с появлением pending-заглушек (см. _persist_pending_urls)
            # сумма строк равна len(urls) СРАЗУ после листинга, ещё до скачивания
            # хотя бы одной статьи — старая проверка "accounted < len(urls)"
            # была бы обманута: день выглядел бы полным по одному только факту,
            # что урлы известны, а не что они скачаны.
            #
            # При паузе часть todo вообще не была начата (futures отменены) —
            # для них строка остаётся pending, и pending_count > 0 это ловит.
            # Устаревшее чтение HarvestedDay.failed_count сразу после
            # RUNNING-отметки (записанной ДО повторных попыток в этом же
            # прогоне) давало неверный ответ — здесь счёт свежий, прямо из
            # Article, на момент принятия решения.
            fresh_counts = _count_by_status(day)
            has_pending = fresh_counts.get(ArticleStatus.PENDING.value, 0) > 0
            has_failed = fresh_counts.get(ArticleStatus.FAILED.value, 0) > 0
            if job.is_cancelled() or has_pending or has_failed:
                day_status = DayStatus.PARTIAL.value
            else:
                day_status = DayStatus.DONE.value
            _update_day_stats(day, xml_url, len(urls), day_status)
            job.set_progress(current=processed)

    _persist_batch(batch)

    if job.is_cancelled():
        job.log(f"Сбор остановлен. Сохранено статей: {processed}.", level="warning")
        return

    job.set_progress(stage="done", current=processed, total=pending_total,
                     message="Сбор завершён")
    job.log(f"Готово. Обработано статей: {processed}.")


def harvest_recent(job: JobHandle, lookback_days: int) -> None:
    """Ночной автосбор: добираем последние дни.

    Перекрытие в несколько дней нужно потому, что суточная карта сайта в
    момент прошлого запуска могла быть ещё неполной.
    """
    from datetime import timedelta

    today = date.today()
    start = today - timedelta(days=lookback_days)
    run_harvest(job, start, today, refresh=False)
