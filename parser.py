import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from datetime import datetime
import concurrent.futures
from functools import partial
import sys
from threading import Lock
import time
from collections import deque, Counter, defaultdict
import json
import os
import signal
import atexit
import re


def tokenize_french_text(text):
    """
    Токенизирует французский текст с учетом особенностей языка:
    - Апострофы разделяют слова: j'ai -> ['j', 'ai']
    - Пробелы, тире (—), двоеточие, запятая, точка с запятой, кавычки,
      восклицательный и вопросительные знаки разделяют слова
    - Диакритические знаки сохраняются
    """
    # Заменяем все разделители (кроме апострофов) на пробелы
    # Используем \s для пробелов, добавляем другие разделители
    # Em-dash (—), en-dash (–), дефис (-) для тире
    # Различные типы кавычек: " " « » ' '
    separators = r'[\s\—\–\:\,\;\!\?\"\"\«\»\'\'\(\)\[\]\{\}\.\…]'
    text = re.sub(separators, ' ', text)
    
    # Теперь разделяем по апострофам, но сохраняем каждую часть
    words = []
    for part in text.split():
        if part:  # Игнорируем пустые строки
            # Разделяем по апострофу
            subparts = part.split("'")
            for subpart in subparts:
                if subpart:  # Добавляем только непустые части
                    words.append(subpart.lower())
    
    return words


class ProgressBar:
    def __init__(self, total, queue=None):
        self.total = total
        self.processed = 0
        self.lock = Lock()
        self.bar_length = 50
        self.processed_urls = set()
        self.queue = queue

    def update(self, url=None, n=1):
        with self.lock:
            if url is not None:
                if url not in self.processed_urls:
                    self.processed += n
                    self.processed_urls.add(url)
                    if self.queue:
                        self.queue.put({"type": "progress", "current": self.processed, "total": self.total})
                    else:
                        self._update_display()
            else:
                self.processed += n
                if self.queue:
                    self.queue.put({"type": "progress", "current": self.processed, "total": self.total})
                else:
                    self._update_display()

    def _update_display(self):
        progress = min(self.processed / self.total, 1.0)
        percent = round(progress * 100, 2)
        filled_length = int(self.bar_length * progress)
        bar = '█' * filled_length + '-' * (self.bar_length - filled_length)
        sys.stdout.write(f'\r|{bar}| {percent:.2f}% ({self.processed}/{self.total})')
        sys.stdout.flush()

    def get_processed_count(self):
        return len(self.processed_urls)


def get_articles_count(xml_url):
    try:
        response = requests.get(xml_url, timeout=10)
        root = ET.fromstring(response.content)
        return len(root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc'))
    except Exception as e:
        print(f"\n⚠️ Ошибка подсчёта статей в {xml_url}: {e}")
        return 0


def get_total_articles_count(date_xmls):
    total_articles = 0
    consecutive_errors = 0
    remaining_xmls = date_xmls.copy()

    while remaining_xmls:
        print("\n🔎 Подсчёт общего количества статей...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
            future_to_xml = {executor.submit(get_articles_count, xml): xml for xml in remaining_xmls}
            remaining_xmls = []

            for future in concurrent.futures.as_completed(future_to_xml):
                xml_url = future_to_xml[future]
                try:
                    count = future.result()
                    if count > 0:
                        total_articles += count
                        consecutive_errors = 0  # Сбрасываем счетчик при успехе
                    else:
                        remaining_xmls.append(xml_url)
                        consecutive_errors += 1
                except Exception as e:
                    print(f"\n⚠️ Ошибка при подсчёте статей в {xml_url}: {e}")
                    remaining_xmls.append(xml_url)
                    consecutive_errors += 1

                if consecutive_errors >= 10:
                    print("\n⚠️ Слишком много ошибок подсчёта подряд. Пауза на 2 минуты...")
                    time.sleep(120)  # 2 минуты паузы
                    consecutive_errors = 0  # Сбрасываем счетчик после паузы

    return total_articles


def extract_date_from_url(url):
    try:
        # Извлекаем дату из формата: .../articles/YYYY-MM-DD.xml
        date_str = url.split('/articles/')[-1].replace('.xml', '')
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        return None


class WordStatistics:
    def __init__(self):
        self.left_context_words = Counter()  # Счетчик слов слева
        self.right_context_words = Counter()  # Счетчик слов справа
        self.total_occurrences = 0
        self.articles_with_word = 0  # Количество статей, где найдено слово
        self.examples = []  # Список всех найденных примеров с контекстом
        self.is_phrase = False  # Флаг, указывающий является ли ключевое слово словосочетанием

    def add_context(self, words, keyword_index, article_url=None, is_phrase=False, phrase_length=1, 
                   original_text="", keyword=""):
        """Добавляет контекст для слова или словосочетания.
        
        Args:
            words: список слов в статье
            keyword_index: индекс начала слова/словосочетания
            article_url: URL статьи
            is_phrase: является ли ключевое слово словосочетанием
            phrase_length: длина словосочетания (количество слов)
            original_text: оригинальный текст статьи (для сохранения примеров)
            keyword: ключевое слово для поиска
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
        
        # Сохраняем пример с контекстом (берем 10 слов слева и справа)
        context_size = 10
        start_idx = max(0, keyword_index - context_size)
        end_idx = min(len(words), keyword_index + phrase_length + context_size)
        
        context_before = ' '.join(words[start_idx:keyword_index])
        found_words = ' '.join(words[keyword_index:keyword_index + phrase_length])
        context_after = ' '.join(words[keyword_index + phrase_length:end_idx])
        
        self.examples.append({
            'url': article_url,
            'context_before': context_before,
            'keyword': found_words,
            'context_after': context_after
        })
        
        self.total_occurrences += 1

    def print_statistics(self):
        print(f"\n📊 Найдено в {self.articles_with_word} статьях")
        
        print("\nТоп-20 слов слева:")
        top_left = self.left_context_words.most_common(20)
        for word, count in top_left:
            percentage = (count / self.total_occurrences) * 100
            print(f"- {word}: {count} раз ({percentage:.1f}%)")
        
        print("\nТоп-20 слов справа:")
        top_right = self.right_context_words.most_common(20)
        for word, count in top_right:
            percentage = (count / self.total_occurrences) * 100
            print(f"- {word}: {count} раз ({percentage:.1f}%)")


def extract_category_from_url(url):
    try:
        # Извлекаем категорию из URL
        # Пример URL: https://www.lefigaro.fr/politique/article-2024-01-01
        parts = url.split('/')
        if len(parts) >= 4:  # Убеждаемся, что URL имеет нужную структуру
            category = parts[3]  # Берем часть после домена
            # Преобразуем категорию в более читаемый формат
            category = category.replace('-', ' ').title()
            return category
    except:
        pass
    return None


def split_keyword(keyword):
    """Split a keyword into individual words, preserving the original for statistics."""
    return keyword.lower().split(), keyword


def process_article(article_url, keywords, progress_bar, word_stats_dict, retry_count=0, max_retries=3):
    consecutive_errors = 0
    while retry_count < max_retries:
        try:
            response = requests.get(article_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)

            if response.status_code == 500:
                # Premium article, skip it
                progress_bar.update(url=article_url)
                return {keyword: 0 for keyword in keywords}, None
            elif response.status_code != 200:
                # Other error, retry
                consecutive_errors += 1
                if consecutive_errors >= 50:
                    print("\n⚠️ Слишком много ошибок подряд. Пауза на 2 минуты...")
                    time.sleep(120)  # 2 minutes timeout
                    consecutive_errors = 0
                time.sleep(2)  # Small delay before retry
                retry_count += 1
                continue

            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            content = ' '.join(p.get_text(' ', strip=True)
                               for p in soup.select('.fig-paragraph'))

            # Используем новую токенизацию для французского текста
            words = tokenize_french_text(content)
            
            # Словарь для хранения результатов по каждому слову
            word_counts = {keyword: 0 for keyword in keywords}
            
            # Собираем статистику соседних слов для каждого ключевого слова
            for i in range(len(words)):
                for keyword in keywords:
                    keyword_words, original_keyword = split_keyword(keyword)
                    is_phrase = len(keyword_words) > 1
                    
                    # Проверяем, есть ли достаточно слов для проверки словосочетания
                    if i + len(keyword_words) <= len(words):
                        # Проверяем совпадение всех слов в словосочетании
                        if all(words[i + j] == word for j, word in enumerate(keyword_words)):
                            word_counts[original_keyword] += 1
                            # Добавляем контекст с учетом того, является ли ключевое слово словосочетанием
                            word_stats_dict[original_keyword].add_context(
                                words, 
                                i, 
                                article_url=article_url,
                                is_phrase=is_phrase,
                                phrase_length=len(keyword_words),
                                original_text=content,
                                keyword=original_keyword
                            )

            progress_bar.update(url=article_url)
            return word_counts, article_url

        except requests.exceptions.RequestException as e:
            consecutive_errors += 1
            if consecutive_errors >= 50:
                print("\n⚠️ Слишком много ошибок подряд. Пауза на 2 минуты...")
                time.sleep(120)  # 2 minutes timeout
                consecutive_errors = 0
            time.sleep(2)  # Small delay before retry
            retry_count += 1
            continue
        except Exception as e:
            print(f"\n⚠️ Неожиданная ошибка статьи {article_url}: {e}")
            progress_bar.update(url=article_url)
            return {keyword: 0 for keyword in keywords}, None

    # If we've exhausted all retries, return the article URL for later processing
    return {keyword: 0 for keyword in keywords}, article_url


def process_single_xml(xml_url, keyword, progress_bar, deferred_articles=None):
    try:
        response = requests.get(xml_url, timeout=10)
        root = ET.fromstring(response.content)
        namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

        articles = [loc.text for url in root.findall('ns:url', namespace)
                    if (loc := url.find('ns:loc', namespace)) is not None]

        print(f"\n🔍 Обрабатывается XML: {xml_url} | Статей: {len(articles)}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
            futures = []
            for article_url in articles:
                future = executor.submit(
                    process_article,
                    article_url=article_url,
                    keywords=[keyword],
                    progress_bar=progress_bar
                )
                futures.append(future)

            results = []
            total_words = 0
            failed_articles = []
            for future in concurrent.futures.as_completed(futures):
                word_counts, url = future.result()
                if any(count > 0 for count in word_counts.values()):
                    results.append((url, word_counts))
                    total_words += sum(word_counts.values())
                elif url is not None and deferred_articles is not None:
                    deferred_articles.append(url)

        # Retry failed articles
        if failed_articles:
            print(f"\n🔄 Повторная попытка обработки {len(failed_articles)} статей...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
                retry_futures = []
                for article_url in failed_articles:
                    future = executor.submit(
                        process_article,
                        article_url=article_url,
                        keywords=[keyword],
                        progress_bar=progress_bar,
                        retry_count=1
                    )
                    retry_futures.append(future)

                for future in concurrent.futures.as_completed(retry_futures):
                    word_counts, url = future.result()
                    if any(count > 0 for count in word_counts.values()):
                        results.append((url, word_counts))
                        total_words += sum(word_counts.values())
                    elif url is not None and deferred_articles is not None:
                        deferred_articles.append(url)

        return results, total_words

    except Exception as e:
        print(f"\n🚨 Критическая ошибка в XML {xml_url}: {e}")
        return [], 0


def get_articles_from_xml(xml_url):
    try:
        response = requests.get(xml_url, timeout=10)
        root = ET.fromstring(response.content)
        namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        return [loc.text.strip() for url in root.findall('ns:url', namespace)
                if (loc := url.find('ns:loc', namespace)) is not None]
    except Exception as e:
        print(f"\n⚠️ Ошибка получения статей из XML {xml_url}: {e}")
        return []


def read_keywords_from_file(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            # Читаем слова, убираем пустые строки и лишние пробелы
            return [line.strip() for line in file if line.strip()]
    except FileNotFoundError:
        print(f"❌ Файл {filename} не найден")
        return []
    except Exception as e:
        print(f"❌ Ошибка при чтении файла {filename}: {e}")
        return []


def get_application_dir():
    """Определяет директорию приложения, работает как для скрипта, так и для exe."""
    if getattr(sys, 'frozen', False):
        # Если запущено как exe (PyInstaller)
        application_path = os.path.dirname(sys.executable)
    else:
        # Если запущено как скрипт
        application_path = os.path.dirname(os.path.abspath(__file__))
    return application_path


# Глобальные переменные для хранения последних результатов
last_results = None
last_keywords = None
last_start_date = None
last_end_date = None
save_lock = False  # Флаг для предотвращения повторного сохранения

def save_statistics_to_file(results, keywords, start_date, end_date, total_processed_articles):
    """Сохраняет только статистику в JSON файл."""
    try:
        # Сохраняем в директорию statistics
        script_dir = get_application_dir()
        statistics_dir = os.path.join(script_dir, "statistics")
        if not os.path.exists(statistics_dir):
            os.makedirs(statistics_dir, exist_ok=True)
        
        filename = os.path.join(statistics_dir, f"statistics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
        print(f"\nСохранение статистики в файл: {filename}")
        
        # Подготавливаем только статистику
        statistics_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "search_period": {
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d")
            },
            "total_processed_articles": total_processed_articles,
            "keywords": keywords,
            "statistics": {}
        }
        
        # Собираем статистику для каждого ключевого слова
        for keyword in keywords:
            if keyword in results:
                stats = results[keyword]['statistics']
                articles_with_word = stats.articles_with_word
                percentage = (articles_with_word / total_processed_articles * 100) if total_processed_articles > 0 else 0
                
                statistics_data["statistics"][keyword] = {
                    "articles_with_word": articles_with_word,
                    "total_processed_articles": total_processed_articles,
                    "percentage": round(percentage, 2),
                    "left_context_words": dict(stats.left_context_words.most_common(30)),
                    "right_context_words": dict(stats.right_context_words.most_common(30))
                }
            else:
                statistics_data["statistics"][keyword] = {
                    "articles_with_word": 0,
                    "total_processed_articles": total_processed_articles,
                    "percentage": 0,
                    "left_context_words": {},
                    "right_context_words": {}
                }
        
        # Сохраняем данные
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(statistics_data, f, ensure_ascii=False, indent=2)
        
        print(f"Статистика успешно сохранена в файл: {filename}")
        return True
    except Exception as e:
        print(f"Ошибка при сохранении статистики: {str(e)}")
        return False


def save_examples_to_file(results, keywords, start_date, end_date):
    """Сохраняет все найденные примеры с выделением и ссылками в HTML файл."""
    try:
        # Сохраняем в директорию examples
        script_dir = get_application_dir()
        examples_dir = os.path.join(script_dir, "examples")
        if not os.path.exists(examples_dir):
            os.makedirs(examples_dir, exist_ok=True)
        
        filename = os.path.join(examples_dir, f"examples_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
        
        print(f"\nСохранение примеров в файл: {filename}")
        
        # Создаем HTML документ
        html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Примеры найденных слов</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #555;
            margin-top: 30px;
        }}
        .info {{
            background-color: #e8f4f8;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .example {{
            background-color: white;
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .context {{
            line-height: 1.6;
            margin: 10px 0;
        }}
        .keyword {{
            background-color: #ffff00;
            font-weight: bold;
            padding: 2px 4px;
        }}
        .link {{
            display: block;
            margin-top: 10px;
            color: #0066cc;
            text-decoration: none;
            font-size: 0.9em;
        }}
        .link:hover {{
            text-decoration: underline;
        }}
        .count {{
            color: #666;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <h1>Найденные примеры</h1>
    <div class="info">
        <p><strong>Период поиска:</strong> {start_date.strftime("%Y-%m-%d")} - {end_date.strftime("%Y-%m-%d")}</p>
        <p><strong>Дата создания отчета:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    </div>
"""
        
        # Добавляем примеры для каждого ключевого слова
        for keyword in keywords:
            if keyword in results:
                stats = results[keyword]['statistics']
                examples = stats.examples
                
                html_content += f"""
    <h2>Слово: "{keyword}"</h2>
    <p class="count">Всего найдено: {len(examples)} примеров</p>
"""
                
                # Добавляем каждый пример
                for i, example in enumerate(examples, 1):
                    html_content += f"""
    <div class="example">
        <div class="context">
            {example['context_before']} <span class="keyword">{example['keyword']}</span> {example['context_after']}
        </div>
        <a href="{example['url']}" target="_blank" class="link">Ссылка на статью</a>
    </div>
"""
        
        html_content += """
</body>
</html>
"""
        
        # Сохраняем HTML файл
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"Примеры успешно сохранены в файл: {filename}")
        return True
    except Exception as e:
        print(f"Ошибка при сохранении примеров: {str(e)}")
        return False

def signal_handler(signum, frame):
    """Обработчик сигнала для сохранения статистики."""
    print("\n\nПолучен сигнал прерывания. Сохранение текущей статистики...")
    if last_results is not None:
        # Подсчитываем общее количество обработанных статей
        total_processed = 0
        for keyword in last_keywords:
            if keyword in last_results:
                total_processed = len(set(ex['url'] for ex in last_results[keyword]['statistics'].examples))
                break
        save_statistics_to_file(last_results, last_keywords, last_start_date, last_end_date, total_processed)
        save_examples_to_file(last_results, last_keywords, last_start_date, last_end_date)
    sys.exit(0)

def exit_handler():
    """Обработчик завершения программы."""
    print("\n\nЗавершение программы. Сохранение текущей статистики...")
    if last_results is not None:
        # Подсчитываем общее количество обработанных статей
        total_processed = 0
        for keyword in last_keywords:
            if keyword in last_results:
                total_processed = len(set(ex['url'] for ex in last_results[keyword]['statistics'].examples))
                break
        save_statistics_to_file(last_results, last_keywords, last_start_date, last_end_date, total_processed)
        save_examples_to_file(last_results, last_keywords, last_start_date, last_end_date)

# Регистрируем обработчики сигналов
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Регистрируем обработчик завершения программы
atexit.register(exit_handler)

def parse_figaro_articles(keywords, start_date, end_date, queue=None):
    global last_results, last_keywords, last_start_date, last_end_date
    
    # Получение списка XML
    main_xml_url = 'https://sitemaps.lefigaro.fr/lefigaro.fr/articles.xml'
    response = requests.get(main_xml_url)
    root = ET.fromstring(response.content)
    namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

    date_xmls = []
    for sitemap in root.findall('ns:sitemap', namespace):
        if (loc := sitemap.find('ns:loc', namespace)) is not None:
            xml_url = loc.text.strip()
            if (date := extract_date_from_url(xml_url)) and start_date <= date <= end_date:
                date_xmls.append(xml_url)

    total_xmls = len(date_xmls)
    if queue:
        queue.put({"type": "xml_progress", "current": 0, "total": total_xmls, "text": f"\n🔎 Найдено XML файлов для обработки: {total_xmls}"})
    else:
        print(f"\n🔎 Найдено XML файлов для обработки: {total_xmls}")

    # Собираем все URL статей
    if queue:
        queue.put({"type": "result", "text": "\n🔎 Сбор списка статей..."})
    else:
        print("\n🔎 Сбор списка статей...")
    
    all_articles = set()
    processed_xmls = 0
    for xml in date_xmls:
        articles = get_articles_from_xml(xml)
        all_articles.update(articles)
        processed_xmls += 1
        if queue:
            queue.put({"type": "xml_progress", "current": processed_xmls, "total": total_xmls, 
                      "text": f"Обработан XML файл {processed_xmls}/{total_xmls}"})

    total_articles = len(all_articles)
    if total_articles == 0:
        if queue:
            queue.put({"type": "result", "text": "\n❌ Не найдено статей для обработки"})
        else:
            print("\n❌ Не найдено статей для обработки")
        return {}

    progress_bar = ProgressBar(total_articles, queue)
    if queue:
        queue.put({"type": "result", "text": f"\n📊 Всего найдено статей: {total_articles}"})
        queue.put({"type": "xml_progress", "current": total_xmls, "total": total_xmls, 
                  "text": f"✅ Все XML файлы обработаны ({total_xmls}/{total_xmls})"})
        # Сигнал о начале обработки статей для отсчета времени
        queue.put({"type": "start_processing"})
    else:
        print(f"\n📊 Всего найдено статей: {total_articles}")
        print(f"✅ Все XML файлы обработаны ({total_xmls}/{total_xmls})")

    # Инициализируем статистику для каждого ключевого слова
    word_stats_dict = {keyword: WordStatistics() for keyword in keywords}
    
    # Основная обработка
    results = {keyword: [] for keyword in keywords}
    total_words = {keyword: 0 for keyword in keywords}
    consecutive_xml_errors = 0
    deferred_articles = []
    max_retries = 5

    while progress_bar.get_processed_count() < total_articles and max_retries > 0:
        if queue:
            queue.put({"type": "result", "text": f"\n🔄 Попытка обработки статей (осталось попыток: {max_retries})"})
            queue.put({"type": "result", "text": f"📊 Обработано статей: {progress_bar.get_processed_count()}/{total_articles}"})
        else:
            print(f"\n🔄 Попытка обработки статей (осталось попыток: {max_retries})")
            print(f"📊 Обработано статей: {progress_bar.get_processed_count()}/{total_articles}")
        
        # Получаем список необработанных статей
        unprocessed_articles = [url for url in all_articles if url not in progress_bar.processed_urls]
        if not unprocessed_articles:
            if queue:
                queue.put({"type": "result", "text": "\n✅ Все статьи обработаны"})
            else:
                print("\n✅ Все статьи обработаны")
            break

        if queue:
            queue.put({"type": "result", "text": f"\n🔄 Обработка {len(unprocessed_articles)} необработанных статей..."})
        else:
            print(f"\n🔄 Обработка {len(unprocessed_articles)} необработанных статей...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
            futures = []
            for article_url in unprocessed_articles:
                future = executor.submit(
                    process_article,
                    article_url=article_url,
                    keywords=keywords,
                    progress_bar=progress_bar,
                    word_stats_dict=word_stats_dict
                )
                futures.append(future)

            for future in concurrent.futures.as_completed(futures):
                try:
                    word_counts, url = future.result()
                    for keyword, count in word_counts.items():
                        if count > 0:
                            results[keyword].append((url, count))
                            total_words[keyword] += count
                    consecutive_xml_errors = 0
                except Exception as e:
                    if queue:
                        queue.put({"type": "result", "text": f"\n⚠️ Ошибка обработки статьи {url}: {e}"})
                    else:
                        print(f"\n⚠️ Ошибка обработки статьи {url}: {e}")
                    consecutive_xml_errors += 1
                    deferred_articles.append(url)
                    
                    if consecutive_xml_errors >= 50:
                        if queue:
                            queue.put({"type": "result", "text": "\n⚠️ Слишком много ошибок подряд. Пауза на 2 минуты..."})
                        else:
                            print("\n⚠️ Слишком много ошибок подряд. Пауза на 2 минуты...")
                        # Обработка отложенных статей во время паузы
                        if deferred_articles:
                            if queue:
                                queue.put({"type": "result", "text": f"\n🔄 Обработка {len(deferred_articles)} отложенных статей во время паузы..."})
                            else:
                                print(f"\n🔄 Обработка {len(deferred_articles)} отложенных статей во время паузы...")
                            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as retry_executor:
                                retry_futures = []
                                for article_url in deferred_articles:
                                    if article_url not in progress_bar.processed_urls:
                                        future = retry_executor.submit(
                                            process_article,
                                            article_url=article_url,
                                            keywords=keywords,
                                            progress_bar=progress_bar,
                                            word_stats_dict=word_stats_dict,
                                            retry_count=1
                                        )
                                        retry_futures.append(future)

                                for retry_future in concurrent.futures.as_completed(retry_futures):
                                    word_counts, url = retry_future.result()
                                    for keyword, count in word_counts.items():
                                        if count > 0 and url not in progress_bar.processed_urls:
                                            results[keyword].append((url, count))
                                            total_words[keyword] += count
                            
                            deferred_articles.clear()
                        
                        time.sleep(120)
                        consecutive_xml_errors = 0

        max_retries -= 1
        if progress_bar.get_processed_count() < total_articles:
            if queue:
                queue.put({"type": "result", "text": f"\n⚠️ Не все статьи обработаны. Осталось: {total_articles - progress_bar.get_processed_count()}"})
            else:
                print(f"\n⚠️ Не все статьи обработаны. Осталось: {total_articles - progress_bar.get_processed_count()}")
            if max_retries > 0:
                if queue:
                    queue.put({"type": "result", "text": "Повторная попытка обработки..."})
                else:
                    print("Повторная попытка обработки...")
                time.sleep(30)
            else:
                if queue:
                    queue.put({"type": "result", "text": "⚠️ Достигнут лимит попыток обработки"})
                else:
                    print("⚠️ Достигнут лимит попыток обработки")

    if progress_bar.get_processed_count() < total_articles:
        if queue:
            queue.put({"type": "result", "text": f"\n⚠️ ВНИМАНИЕ: Не все статьи были обработаны!"})
            queue.put({"type": "result", "text": f"Обработано: {progress_bar.get_processed_count()}/{total_articles} статей"})
        else:
            print(f"\n⚠️ ВНИМАНИЕ: Не все статьи были обработаны!")
            print(f"Обработано: {progress_bar.get_processed_count()}/{total_articles} статей")
    else:
        if queue:
            queue.put({"type": "result", "text": "\n✅ Все статьи успешно обработаны!"})
        else:
            print("\n✅ Все статьи успешно обработаны!")

    # Формируем итоговые результаты
    all_results = {}
    for keyword in keywords:
        # Подсчитываем количество уникальных статей, где найдено слово
        unique_articles = len(set(ex['url'] for ex in word_stats_dict[keyword].examples))
        word_stats_dict[keyword].articles_with_word = unique_articles
        
        all_results[keyword] = {
            'articles': results[keyword],
            'total_words': total_words[keyword],
            'statistics': word_stats_dict[keyword]
        }
        if not queue:
            print(f"\n📈 Статистика для слова '{keyword}':")
            print(f"Найдено в {unique_articles} статьях из {total_articles} ({(unique_articles/total_articles*100):.2f}%)")
            word_stats_dict[keyword].print_statistics()
            print("\n" + "="*50)
            
            # Обновляем глобальные переменные для возможного прерывания
            last_results = all_results.copy()  # Создаем копию для безопасности
            last_keywords = keywords.copy()
            last_start_date = start_date
            last_end_date = end_date

    # Отправляем результаты через очередь или сохраняем напрямую
    if queue:
        queue.put({
            "type": "results",
            "results": all_results,
            "keywords": keywords,
            "start_date": start_date,
            "end_date": end_date,
            "total_articles": total_articles
        })
    else:
        # Если нет очереди, сохраняем напрямую
        save_statistics_to_file(all_results, keywords, start_date, end_date, total_articles)
        save_examples_to_file(all_results, keywords, start_date, end_date)

    return all_results