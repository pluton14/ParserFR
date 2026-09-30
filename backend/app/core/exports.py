"""Выгрузки.

HTML-отчёт с примерами повторяет разметку и стили из
parser.save_examples_to_file, чтобы файлы, полученные из веб-версии,
выглядели так же, как те, что уже лежат у пользователя в examples/.
"""

from __future__ import annotations

from html import escape

HTML_HEAD = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Примеры найденных слов</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
        }
        h2 {
            color: #555;
            margin-top: 30px;
        }
        .info {
            background-color: #e8f4f8;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }
        .example {
            background-color: white;
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .context {
            line-height: 1.6;
            margin: 10px 0;
        }
        .keyword {
            background-color: #ffff00;
            font-weight: bold;
            padding: 2px 4px;
        }
        .link {
            display: block;
            margin-top: 10px;
            color: #0066cc;
            text-decoration: none;
            font-size: 0.9em;
        }
        .link:hover {
            text-decoration: underline;
        }
        .count {
            color: #666;
            font-style: italic;
        }
        .meta {
            color: #888;
            font-size: 0.85em;
        }
    </style>
</head>
<body>
    <h1>Найденные примеры</h1>
"""


def render_examples_html(
    keywords: list[str],
    examples_by_keyword: dict[str, list[dict]],
    start_date: str,
    end_date: str,
    generated_at: str,
) -> str:
    parts = [HTML_HEAD]
    parts.append(
        f"""    <div class="info">
        <p><strong>Период поиска:</strong> {escape(start_date)} - {escape(end_date)}</p>
        <p><strong>Дата создания отчета:</strong> {escape(generated_at)}</p>
    </div>
"""
    )

    for keyword in keywords:
        examples = examples_by_keyword.get(keyword, [])
        parts.append(f"""
    <h2>Слово: "{escape(keyword)}"</h2>
    <p class="count">Всего найдено: {len(examples)} примеров</p>
""")

        for example in examples:
            meta_bits = [bit for bit in (example.get("published_date"), example.get("title")) if bit]
            meta = (
                f'        <div class="meta">{escape(" — ".join(meta_bits))}</div>\n'
                if meta_bits
                else ""
            )
            parts.append(f"""
    <div class="example">
{meta}        <div class="context">
            {escape(example.get("context_before", ""))} <span class="keyword">{escape(example.get("keyword", ""))}</span> {escape(example.get("context_after", ""))}
        </div>
        <a href="{escape(example.get("url", ""))}" target="_blank" class="link">Ссылка на статью</a>
    </div>
""")

    parts.append("""
</body>
</html>
""")
    return "".join(parts)
