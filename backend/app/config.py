"""Настройки приложения. Всё переопределяется переменными окружения."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PARSERFR_", env_file=".env", extra="ignore")

    # --- Хранилище ---
    data_dir: Path = Path("./data")
    database_url: str = ""  # по умолчанию собирается из data_dir

    # Находка 2026-09-29: демо-деплой (Replit) не может держать локальный
    # файл SQLite в 3+ ГБ — используем Turso (управляемый libSQL, тот же
    # движок) как удалённую базу. Заданы оба — переключаемся на Turso,
    # не заданы — обычный локальный файл по data_dir, как раньше.
    # validation_alias без префикса PARSERFR_ — это стандартные имена
    # переменных Turso, их можно скопировать из `turso db tokens create`
    # один в один, не переименовывая.
    turso_database_url: str = Field(default="", validation_alias="TURSO_DATABASE_URL")
    turso_auth_token: str = Field(default="", validation_alias="TURSO_AUTH_TOKEN")

    # --- Источник ---
    sitemap_index_url: str = "https://sitemaps.lefigaro.fr/lefigaro.fr/articles.xml"
    article_selector: str = ".fig-paragraph"
    # Находка 2026-09-28: голый "Mozilla/5.0" (унаследован из десктопного
    # parser.py) не похож ни на один настоящий браузер — это чисто сетевая
    # деталь, не часть смысловой логики парсинга, поэтому меняем на
    # реалистичную строку Chrome, чтобы не выделяться лишний раз на фоне
    # участившихся блокировок 403 от антибот-защиты источника.
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    # --- Сбор корпуса ---
    # В десктопной версии было 30 потоков. Замер 2026-09-22: 6 потоков дают
    # ~10 статей/с, 16 — ~27. 16 потоков согласованы с пользователем 2026-09-22.
    # Откат 2026-10-06 к технике сбора 30 сентября по просьбе пользователя:
    # 16 потоков, без ограничителя темпа, простые заголовки (см. DEVLOG).
    # Оба значения меняются из интерфейса (services/pacing.py).
    harvest_workers: int = 16
    # Потолок темпа запросов к источнику, запросов/с. 0 — без ограничения:
    # темп задаётся только числом потоков, как было 30 сентября.
    harvest_rate: float = 0.0
    # Пауза при 403/429: стартовая и потолок, удваивается на каждый повторный бан.
    block_backoff_seconds: int = 300
    block_backoff_max_seconds: int = 3600
    request_timeout: int = 10
    max_retries: int = 3
    retry_delay: float = 2.0
    # Пауза, если источник начал массово отвечать ошибками.
    error_threshold: int = 50
    error_pause_seconds: int = 120

    # Находка 2026-09-28: листинг карт дней бьёт по sitemaps.lefigaro.fr —
    # поддомену почти без обычного браузерного трафика. Параллельный burst
    # запросов туда (даже на harvest_workers=16) стабильно ловил 403 за
    # считанные минуты, тогда как скачивание самих статей с www.lefigaro.fr
    # держалось часами. Листинг намеренно медленнее и уже, чем скачивание.
    listing_workers: int = 3
    listing_request_delay: float = 0.3

    # Внутридневной повтор failed-статей (находка 2026-09-25) откачен
    # 2026-09-25: держал воркер-пул занятым дольше и на 8-16 потоках
    # усиливал серии "Пауза на 2 мин", резко роняя темп. Failed-статьи
    # дня просто остаются partial и добираются обычным следующим запуском.

    # --- Анализ ---
    # Ограничение на количество примеров, которые кладутся в базу,
    # чтобы один частотный термин на большом периоде не раздул результат.
    example_limit_per_keyword: int = 3000
    top_context_words: int = 30

    # --- Ночной автосбор ---
    scheduler_enabled: bool = True
    # Час и минута — в часовом поясе scheduler_timezone. По умолчанию 00:30 по
    # Парижу: прошедший день уже закончился и его sitemap полон, поэтому ночью
    # берём ПРЕДЫДУЩИЙ день (сегодняшний не берётся никогда).
    scheduler_hour: int = 0
    scheduler_minute: int = 30
    scheduler_timezone: str = "Europe/Paris"
    # При запуске сервера сверить базу с сегодняшней датой и добрать пропущенные
    # дни (хостинг засыпает и перезапускается — ночной запуск мог не случиться).
    scheduler_catchup_on_start: bool = True
    # Жёсткий потолок окна автосбора и догона: не больше N дней подряд (включая
    # сегодня), даже если отставание базы больше. Старые дни добирает админ
    # локально — автосбор на хостинге не должен генерировать много запросов
    # к источнику, иначе он заблокирует домен хостинга.
    scheduler_catchup_max_days: int = 3
    # Не запускать догон при старте, если сбор уже запускался за последние N
    # минут (защита от серии перезапусков хостинга).
    scheduler_catchup_cooldown_minutes: int = 60
    # Сколько последних дней добирать ночью (перекрытие на случай, если
    # sitemap за вчера был неполон в момент прошлого запуска).
    scheduler_lookback_days: int = 2

    # --- HTTP ---
    cors_origins: str = "*"

    # Находка 2026-09-29: демо-деплой отдаёт зафиксированный снимок базы
    # без ежедневного досбора. Скрытой вкладки «Корпус» во фронтенде
    # достаточно для обычного пользователя, но эндпоинт запуска сбора
    # всё равно физически доступен по прямому запросу — этот флаг
    # запрещает его на уровне API, а не только прячет кнопку в интерфейсе.
    readonly_demo: bool = False

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'parserfr.db').as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
