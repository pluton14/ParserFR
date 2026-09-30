import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
import threading
import queue
from parser import parse_figaro_articles, read_keywords_from_file
from tkcalendar import DateEntry
import json
import os

class StatisticsViewer(tk.Toplevel):
    """Окно для красивого просмотра JSON файлов статистики."""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Просмотр статистики")
        self.geometry("900x700")
        
        # Переменные
        self.current_data = None
        self.current_keyword_index = 0
        self.keywords = []
        
        self.create_widgets()
    
    def create_widgets(self):
        # Кнопка для выбора файла
        top_frame = ttk.Frame(self, padding=10)
        top_frame.pack(fill="x")
        
        ttk.Button(top_frame, text="Открыть JSON файл", command=self.load_json_file).pack(side="left", padx=5)
        
        self.file_label = ttk.Label(top_frame, text="Файл не выбран", foreground="gray")
        self.file_label.pack(side="left", padx=10)
        
        # Информационная панель
        info_frame = ttk.LabelFrame(self, text="Информация о файле", padding=10)
        info_frame.pack(fill="x", padx=10, pady=5)
        
        self.info_text = ttk.Label(info_frame, text="", justify="left")
        self.info_text.pack(anchor="w")
        
        # Панель навигации по ключевым словам
        nav_frame = ttk.Frame(self, padding=5)
        nav_frame.pack(fill="x", padx=10, pady=5)
        
        self.prev_button = ttk.Button(nav_frame, text="◄ Предыдущее", command=self.show_previous, state="disabled")
        self.prev_button.pack(side="left", padx=5)
        
        self.keyword_label = ttk.Label(nav_frame, text="", font=("Arial", 12, "bold"))
        self.keyword_label.pack(side="left", expand=True)
        
        self.next_button = ttk.Button(nav_frame, text="Следующее ►", command=self.show_next, state="disabled")
        self.next_button.pack(side="right", padx=5)
        
        # Основная область с прокруткой
        main_frame = ttk.Frame(self)
        main_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Canvas и scrollbar
        self.canvas = tk.Canvas(main_frame, bg="white")
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=self.canvas.yview)
        
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        
        # Контейнер для содержимого
        self.content_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.content_frame, anchor="nw")
        
        self.content_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
    
    def load_json_file(self):
        """Открывает диалог выбора JSON файла и загружает его."""
        filename = filedialog.askopenfilename(
            title="Выберите JSON файл статистики",
            initialdir="statistics",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        
        if not filename:
            return
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                self.current_data = json.load(f)
            
            # Обновляем метку с именем файла
            self.file_label.config(text=os.path.basename(filename), foreground="black")
            
            # Получаем список ключевых слов
            self.keywords = self.current_data.get("keywords", [])
            self.current_keyword_index = 0
            
            # Активируем кнопки навигации
            if len(self.keywords) > 1:
                self.prev_button.config(state="normal")
                self.next_button.config(state="normal")
            
            # Отображаем информацию
            self.display_info()
            self.display_keyword_stats()
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить файл:\n{str(e)}")
    
    def display_info(self):
        """Отображает общую информацию о файле."""
        if not self.current_data:
            return
        
        timestamp = self.current_data.get("timestamp", "Неизвестно")
        period = self.current_data.get("search_period", {})
        start_date = period.get("start_date", "?")
        end_date = period.get("end_date", "?")
        total_articles = self.current_data.get("total_processed_articles", 0)
        keywords_count = len(self.keywords)
        
        info = f"📅 Дата создания: {timestamp}\n"
        info += f"📆 Период поиска: {start_date} — {end_date}\n"
        info += f"📊 Всего обработано статей: {total_articles:,}".replace(",", " ") + "\n"
        info += f"🔑 Количество ключевых слов: {keywords_count}"
        
        self.info_text.config(text=info)
    
    def display_keyword_stats(self):
        """Отображает статистику для текущего ключевого слова."""
        if not self.current_data or not self.keywords:
            return
        
        # Очищаем предыдущее содержимое
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        
        keyword = self.keywords[self.current_keyword_index]
        self.keyword_label.config(text=f"Слово: {keyword} ({self.current_keyword_index + 1}/{len(self.keywords)})")
        
        stats = self.current_data.get("statistics", {}).get(keyword, {})
        
        # Основная статистика
        main_frame = ttk.LabelFrame(self.content_frame, text="Основная статистика", padding=10)
        main_frame.pack(fill="x", pady=5)
        
        articles_with_word = stats.get("articles_with_word", 0)
        total_articles = self.current_data.get("total_processed_articles", 0)
        percentage = stats.get("percentage", 0)
        
        main_text = f"Найдено в {articles_with_word} из {total_articles} статей ({percentage:.2f}%)"
        ttk.Label(main_frame, text=main_text, font=("Arial", 11, "bold")).pack(anchor="w")
        
        # Левый контекст
        left_frame = ttk.LabelFrame(self.content_frame, text="Топ-20 слов слева", padding=10)
        left_frame.pack(fill="both", expand=True, pady=5)
        
        left_context = stats.get("left_context_words", {})
        if left_context:
            # Создаем таблицу
            tree_left = ttk.Treeview(left_frame, columns=("word", "count", "percent"), show="headings", height=10)
            tree_left.heading("word", text="Слово")
            tree_left.heading("count", text="Количество")
            tree_left.heading("percent", text="Процент")
            
            tree_left.column("word", width=200)
            tree_left.column("count", width=100)
            tree_left.column("percent", width=100)
            
            # Заполняем таблицу
            total_occurrences = sum(left_context.values())
            sorted_words = sorted(left_context.items(), key=lambda x: x[1], reverse=True)[:20]
            
            for word, count in sorted_words:
                percent = (count / total_occurrences * 100) if total_occurrences > 0 else 0
                tree_left.insert("", "end", values=(word, count, f"{percent:.1f}%"))
            
            tree_left.pack(fill="both", expand=True)
            
            # Scrollbar для таблицы
            scrollbar_left = ttk.Scrollbar(left_frame, orient="vertical", command=tree_left.yview)
            tree_left.configure(yscrollcommand=scrollbar_left.set)
            scrollbar_left.pack(side="right", fill="y")
        else:
            ttk.Label(left_frame, text="Нет данных", foreground="gray").pack()
        
        # Правый контекст
        right_frame = ttk.LabelFrame(self.content_frame, text="Топ-20 слов справа", padding=10)
        right_frame.pack(fill="both", expand=True, pady=5)
        
        right_context = stats.get("right_context_words", {})
        if right_context:
            # Создаем таблицу
            tree_right = ttk.Treeview(right_frame, columns=("word", "count", "percent"), show="headings", height=10)
            tree_right.heading("word", text="Слово")
            tree_right.heading("count", text="Количество")
            tree_right.heading("percent", text="Процент")
            
            tree_right.column("word", width=200)
            tree_right.column("count", width=100)
            tree_right.column("percent", width=100)
            
            # Заполняем таблицу
            total_occurrences = sum(right_context.values())
            sorted_words = sorted(right_context.items(), key=lambda x: x[1], reverse=True)[:20]
            
            for word, count in sorted_words:
                percent = (count / total_occurrences * 100) if total_occurrences > 0 else 0
                tree_right.insert("", "end", values=(word, count, f"{percent:.1f}%"))
            
            tree_right.pack(fill="both", expand=True)
            
            # Scrollbar для таблицы
            scrollbar_right = ttk.Scrollbar(right_frame, orient="vertical", command=tree_right.yview)
            tree_right.configure(yscrollcommand=scrollbar_right.set)
            scrollbar_right.pack(side="right", fill="y")
        else:
            ttk.Label(right_frame, text="Нет данных", foreground="gray").pack()
    
    def show_previous(self):
        """Показывает предыдущее ключевое слово."""
        if self.keywords:
            self.current_keyword_index = (self.current_keyword_index - 1) % len(self.keywords)
            self.display_keyword_stats()
    
    def show_next(self):
        """Показывает следующее ключевое слово."""
        if self.keywords:
            self.current_keyword_index = (self.current_keyword_index + 1) % len(self.keywords)
            self.display_keyword_stats()


class CustomProgressBar(ttk.Progressbar):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        # Создаем метку с прозрачным фоном
        self.percentage_label = ttk.Label(master, text="0.00%", foreground='black')
        self.percentage_label.place(in_=self, relx=0.5, rely=0.5, anchor="center")
    
    def update_percentage(self, value):
        self.percentage_label.configure(text=f"{value:.2f}%")
        # Меняем цвет текста на белый, когда прогресс больше 50%
        if value > 50:
            self.percentage_label.configure(foreground='black')
        else:
            self.percentage_label.configure(foreground='black')

class StatisticsFrame(ttk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.current_index = 0
        self.results = {}
        self.total_processed_articles = 0
        self.start_date = None
        self.end_date = None
        self.create_widgets()
    
    def create_widgets(self):
        # Создаем фрейм для навигации
        nav_frame = ttk.Frame(self)
        nav_frame.pack(fill="x", pady=5)
        
        self.prev_button = ttk.Button(nav_frame, text="←", width=3, command=self.show_previous)
        self.prev_button.pack(side="left", padx=5)
        
        self.word_label = ttk.Label(nav_frame, text="", font=("Arial", 12, "bold"))
        self.word_label.pack(side="left", expand=True)
        
        self.next_button = ttk.Button(nav_frame, text="→", width=3, command=self.show_next)
        self.next_button.pack(side="right", padx=5)
        
        # Добавляем кнопки сохранения
        self.save_examples_button = ttk.Button(nav_frame, text="Сохранить примеры", command=self.save_examples)
        self.save_examples_button.pack(side="right", padx=5)
        
        self.save_button = ttk.Button(nav_frame, text="Сохранить статистику", command=self.save_statistics)
        self.save_button.pack(side="right", padx=5)
        
        # Добавляем метку с общим количеством статей
        self.total_articles_label = ttk.Label(self, text="", font=("Arial", 14, "bold"))
        self.total_articles_label.pack(pady=10)
        
        # Создаем фрейм с прокруткой
        scroll_frame = ttk.Frame(self)
        scroll_frame.pack(fill="both", expand=True, pady=5)
        
        # Создаем canvas и scrollbar
        self.canvas = tk.Canvas(scroll_frame)
        self.scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=self.canvas.yview)
        
        # Настраиваем canvas
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # Размещаем scrollbar и canvas
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        
        # Создаем фрейм для контента внутри canvas
        self.content_frame = ttk.Frame(self.canvas)
        self.canvas_frame = self.canvas.create_window((0, 0), window=self.content_frame, anchor="nw")
        
        # Настраиваем прокрутку
        self.content_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # Фрейм для статистики
        stats_frame = ttk.LabelFrame(self.content_frame, text="Статистика", padding=10)
        stats_frame.pack(fill="both", expand=True, pady=5)
        
        # Общая информация
        self.total_label = ttk.Label(stats_frame, text="", font=("Arial", 12, "bold"))
        self.total_label.pack(anchor="w", pady=10)
        
        # Левый контекст
        self.left_context_frame = ttk.LabelFrame(stats_frame, text="Топ-20 слов слева", padding=5)
        self.left_context_frame.pack(fill="both", expand=True, pady=5)
        
        self.left_context_label = ttk.Label(self.left_context_frame, text="", justify="left")
        self.left_context_label.pack(anchor="w")
        
        # Правый контекст
        self.right_context_frame = ttk.LabelFrame(stats_frame, text="Топ-20 слов справа", padding=5)
        self.right_context_frame.pack(fill="both", expand=True, pady=5)
        
        self.right_context_label = ttk.Label(self.right_context_frame, text="", justify="left")
        self.right_context_label.pack(anchor="w")
    
    def _on_frame_configure(self, event=None):
        # Обновляем область прокрутки при изменении размера фрейма
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
    
    def _on_canvas_configure(self, event):
        # Обновляем ширину внутреннего фрейма при изменении размера canvas
        self.canvas.itemconfig(self.canvas_frame, width=event.width)
    
    def set_results(self, results, total_processed_articles, start_date=None, end_date=None):
        self.results = results
        self.total_processed_articles = total_processed_articles
        self.start_date = start_date
        self.end_date = end_date
        self.current_index = 0
        # Обновляем метку с общим количеством статей
        self.total_articles_label.config(
            text=f"Всего обработано статей: {self.total_processed_articles:,}".replace(",", " ")
        )
        self.update_display()
    
    def show_previous(self):
        if self.results:
            self.current_index = (self.current_index - 1) % len(self.results)
            self.update_display()
    
    def show_next(self):
        if self.results:
            self.current_index = (self.current_index + 1) % len(self.results)
            self.update_display()
    
    def update_display(self):
        if not self.results:
            return
            
        keywords = list(self.results.keys())
        current_keyword = keywords[self.current_index]
        data = self.results[current_keyword]
        
        # Обновляем заголовок
        self.word_label.config(text=f"Слово: {current_keyword}")
        
        # Обновляем общую информацию
        articles_with_word = data['statistics'].articles_with_word
        
        # Рассчитываем процент
        articles_percent = (articles_with_word / self.total_processed_articles * 100) if self.total_processed_articles > 0 else 0
        
        self.total_label.config(
            text=f"Найдено в {articles_with_word} из {self.total_processed_articles} статей ({articles_percent:.2f}%)"
        )
        
        # Обновляем статистику левого контекста
        left_context_text = ""
        for word, count in data['statistics'].left_context_words.most_common(20):
            percentage = (count / data['statistics'].total_occurrences) * 100 if data['statistics'].total_occurrences > 0 else 0
            left_context_text += f"• {word}: {count} раз ({percentage:.1f}%)\n"
        self.left_context_label.config(text=left_context_text if left_context_text else "Нет данных")
        
        # Обновляем статистику правого контекста
        right_context_text = ""
        for word, count in data['statistics'].right_context_words.most_common(20):
            percentage = (count / data['statistics'].total_occurrences) * 100 if data['statistics'].total_occurrences > 0 else 0
            right_context_text += f"• {word}: {count} раз ({percentage:.1f}%)\n"
        self.right_context_label.config(text=right_context_text if right_context_text else "Нет данных")
        
        # Обновляем область прокрутки
        self._on_frame_configure()

    def save_statistics(self):
        """Сохраняет текущую статистику в файл."""
        if not self.results:
            return
            
        try:
            # Импортируем функцию из parser
            from parser import save_statistics_to_file
            
            # Получаем текущие результаты
            keywords = list(self.results.keys())
            
            # Используем сохраненные даты, если они есть
            from datetime import date
            start_date = self.start_date if self.start_date else date.today()
            end_date = self.end_date if self.end_date else date.today()
            
            success = save_statistics_to_file(
                self.results, 
                keywords, 
                start_date, 
                end_date, 
                self.total_processed_articles
            )
            
            if success:
                # Показываем сообщение об успешном сохранении
                messagebox.showinfo("Сохранение", "Статистика успешно сохранена!")
            else:
                messagebox.showerror("Ошибка", "Ошибка при сохранении статистики")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при сохранении статистики:\n{str(e)}")
    
    def save_examples(self):
        """Сохраняет все найденные примеры в HTML файл."""
        if not self.results:
            return
            
        try:
            # Импортируем функцию из parser
            from parser import save_examples_to_file
            
            # Получаем текущие результаты
            keywords = list(self.results.keys())
            
            # Используем сохраненные даты, если они есть
            from datetime import date
            start_date = self.start_date if self.start_date else date.today()
            end_date = self.end_date if self.end_date else date.today()
            
            success = save_examples_to_file(
                self.results, 
                keywords, 
                start_date, 
                end_date
            )
            
            if success:
                messagebox.showinfo("Сохранение", "Примеры успешно сохранены!")
            else:
                messagebox.showerror("Ошибка", "Ошибка при сохранении примеров")
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при сохранении примеров:\n{str(e)}")

class ParserGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Parser Figaro")
        
        # Устанавливаем минимальный размер окна
        self.root.minsize(800, 800)
        
        # Делаем окно растягиваемым
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        
        # Создаем основной контейнер
        main_container = ttk.Frame(self.root)
        main_container.grid(row=0, column=0, sticky="nsew", padx=10, pady=5)
        main_container.grid_rowconfigure(4, weight=1)  # Делаем фрейм статистики растягиваемым
        main_container.grid_columnconfigure(0, weight=1)
        
        # Настройка стиля
        self.style = ttk.Style()
        self.style.configure("Custom.Horizontal.TProgressbar",
                           troughcolor='#E0E0E0',
                           background='#4CAF50',
                           thickness=25)
        
        # Создаем очередь для обновления GUI из другого потока
        self.queue = queue.Queue()
        
        # Переменные для отслеживания прогресса
        self.current_progress = 0
        self.target_progress = 0
        self.total_articles = 0
        self.is_processing = False
        self.processed_articles = 0
        
        # Переменные для расчета времени
        self.start_time = None
        self.time_coefficient = 0  # секунд на статью
        self.last_coefficient_update = 0  # количество статей при последнем обновлении коэффициента
        self.processing_started = False  # флаг начала обработки статей
        
        # Переменные для отслеживания прогресса XML
        self.xml_progress_text = ""
        self.xml_current = 0
        self.xml_total = 0
        
        # Создаем и размещаем элементы интерфейса
        self.create_widgets(main_container)
        
        # Запускаем обработчик очереди
        self.process_queue()
        
        # Запускаем плавное обновление прогресс-бара
        self.update_progress_smoothly()
        
        # Устанавливаем начальный размер окна
        self.root.geometry("1000x900")
    
    def create_widgets(self, container):
        # Фрейм для выбора файла
        file_frame = ttk.LabelFrame(container, text="Выбор файла со словами", padding=10)
        file_frame.grid(row=0, column=0, sticky="ew", pady=5)
        
        self.file_path = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.file_path, width=50).pack(side="left", padx=5)
        ttk.Button(file_frame, text="Обзор", command=self.browse_file).pack(side="left", padx=5)
        
        # Фрейм для выбора дат
        date_frame = ttk.LabelFrame(container, text="Период поиска", padding=10)
        date_frame.grid(row=1, column=0, sticky="ew", pady=5)
        
        # Начальная дата
        start_frame = ttk.Frame(date_frame)
        start_frame.pack(fill="x", pady=5)
        ttk.Label(start_frame, text="От:").pack(side="left", padx=5)
        self.start_date = DateEntry(start_frame, width=12, background='darkblue',
                                  foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
        self.start_date.pack(side="left", padx=5)
        
        # Конечная дата
        end_frame = ttk.Frame(date_frame)
        end_frame.pack(fill="x", pady=5)
        ttk.Label(end_frame, text="До:").pack(side="left", padx=5)
        self.end_date = DateEntry(end_frame, width=12, background='darkblue',
                                foreground='white', borderwidth=2, date_pattern='yyyy-mm-dd')
        self.end_date.pack(side="left", padx=5)
        
        # Кнопки
        buttons_frame = ttk.Frame(container)
        buttons_frame.grid(row=2, column=0, pady=10)
        
        ttk.Button(buttons_frame, text="Начать обработку", command=self.start_processing).pack(side="left", padx=5)
        ttk.Button(buttons_frame, text="📊 Открыть статистику", command=self.open_statistics_viewer).pack(side="left", padx=5)
        
        # Прогресс бар
        progress_frame = ttk.LabelFrame(container, text="Прогресс обработки", padding=10)
        progress_frame.grid(row=3, column=0, sticky="ew", pady=5)
        
        # Добавляем метку для прогресса XML
        self.xml_progress_label = ttk.Label(progress_frame, text="", font=("Arial", 9))
        self.xml_progress_label.pack(fill="x", pady=(0, 5))
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = CustomProgressBar(progress_frame, 
                                           style="Custom.Horizontal.TProgressbar",
                                           variable=self.progress_var,
                                           maximum=100,
                                           mode='determinate')
        self.progress_bar.pack(fill="x", pady=5)
        
        # Фрейм для информации о прогрессе
        info_frame = ttk.Frame(progress_frame)
        info_frame.pack(fill="x", pady=5)
        
        self.progress_label = ttk.Label(info_frame, text="")
        self.progress_label.pack(side="left")
        
        self.time_label = ttk.Label(info_frame, text="")
        self.time_label.pack(side="right")
        
        # Фрейм для статистики
        self.stats_frame = StatisticsFrame(container)
        self.stats_frame.grid(row=4, column=0, sticky="nsew", pady=5)
    
    def update_progress_smoothly(self):
        if self.is_processing:
            if self.current_progress < self.target_progress:
                # Плавно увеличиваем текущий прогресс
                step = max(1, (self.target_progress - self.current_progress) / 10)
                self.current_progress = min(self.target_progress, self.current_progress + step)
                if self.total_articles > 0:
                    # Используем processed_articles для расчета процентов
                    progress = min(99.99, (self.processed_articles / self.total_articles) * 100)
                    self.progress_var.set(progress)
                    self.progress_bar.update_percentage(progress)
                    
                    # Обновляем информацию о прогрессе
                    self.progress_label.config(
                        text=f"{self.processed_articles}/{self.total_articles} статей"
                    )
                    
                    # Обновляем оставшееся время только если началась обработка статей
                    if self.processing_started and self.time_coefficient > 0:
                        remaining_articles = self.total_articles - self.processed_articles
                        remaining_seconds = remaining_articles * self.time_coefficient
                        hours = int(remaining_seconds // 3600)
                        minutes = int((remaining_seconds % 3600) // 60)
                        self.time_label.config(
                            text=f"Осталось времени: {hours}ч {minutes}м"
                        )
        self.root.after(20, self.update_progress_smoothly)
    
    def browse_file(self):
        filename = filedialog.askopenfilename(
            title="Выберите файл со словами",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if filename:
            self.file_path.set(filename)
    
    def open_statistics_viewer(self):
        """Открывает окно для просмотра JSON файлов статистики."""
        StatisticsViewer(self.root)
    
    def start_processing(self):
        # Очищаем предыдущие результаты
        self.progress_var.set(0)
        self.progress_bar.update_percentage(0)
        self.progress_label.config(text="Подготовка к обработке...")
        self.time_label.config(text="")
        self.current_progress = 0
        self.target_progress = 0
        self.total_articles = 0
        self.processed_articles = 0
        self.is_processing = False
        self.time_coefficient = 0
        self.last_coefficient_update = 0
        self.start_time = None
        self.processing_started = False
        
        # Сбрасываем прогресс XML
        self.xml_progress_text = ""
        self.xml_current = 0
        self.xml_total = 0
        self.xml_progress_label.config(text="")
        
        # Получаем параметры
        file_path = self.file_path.get()
        start_date = self.start_date.get_date()
        end_date = self.end_date.get_date()
        
        if not file_path:
            self.progress_label.config(text="❌ Выберите файл со словами!")
            return
        
        try:
            keywords = read_keywords_from_file(file_path)
            if not keywords:
                self.progress_label.config(text="❌ Файл не содержит слов для поиска!")
                return
        except Exception as e:
            self.progress_label.config(text=f"❌ Ошибка чтения файла: {str(e)}")
            return
        
        # Запускаем обработку в отдельном потоке
        self.is_processing = True
        self.start_time = datetime.now()
        thread = threading.Thread(
            target=self.run_parser,
            args=(keywords, start_date, end_date),
            daemon=True
        )
        thread.start()
    
    def run_parser(self, keywords, start_date, end_date):
        try:
            results = parse_figaro_articles(keywords, start_date, end_date, self.queue)
            # Передаем общее количество обработанных статей в StatisticsFrame
            self.stats_frame.set_results(results, self.total_articles)
            self.is_processing = False
            # Устанавливаем 100% при завершении
            self.progress_var.set(100)
            self.progress_bar.update_percentage(100)
            self.progress_label.config(text="✅ Обработка завершена")
            self.time_label.config(text="")
        except Exception as e:
            self.is_processing = False
            self.progress_label.config(text=f"❌ Ошибка при обработке: {str(e)}")
            self.time_label.config(text="")
    
    def process_queue(self):
        try:
            while True:
                message = self.queue.get_nowait()
                if message["type"] == "start_processing":
                    self.start_time = datetime.now()
                    self.processing_started = True
                    self.progress_label.config(text="Начинаем обработку статей...")
                elif message["type"] == "xml_progress":
                    self.xml_current = message["current"]
                    self.xml_total = message["total"]
                    self.xml_progress_text = message["text"]
                    self.xml_progress_label.config(text=self.xml_progress_text)
                elif message["type"] == "progress":
                    current = message["current"]
                    total = message["total"]
                    if total > 0:
                        self.total_articles = total
                    self.target_progress = current
                    self.processed_articles = current
                    
                    # Обновляем коэффициент времени каждые 100 статей
                    if self.processing_started and current - self.last_coefficient_update >= 100:
                        elapsed_time = (datetime.now() - self.start_time).total_seconds()
                        self.time_coefficient = elapsed_time / current
                        self.last_coefficient_update = current
                elif message["type"] == "results":
                    # Обрабатываем полученные результаты
                    results = message["results"]
                    keywords = message["keywords"]
                    total_articles = message.get("total_articles", self.total_articles)
                    start_date = message["start_date"]
                    end_date = message["end_date"]
                    
                    # Сохраняем результаты
                    from parser import save_statistics_to_file, save_examples_to_file
                    save_statistics_to_file(results, keywords, start_date, end_date, total_articles)
                    save_examples_to_file(results, keywords, start_date, end_date)
                    
                    # Передаем результаты в StatisticsFrame с датами
                    self.stats_frame.set_results(results, total_articles, start_date, end_date)
                elif message["type"] == "start":
                    pass  # Убираем установку текста, так как теперь используем start_processing
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_queue)

if __name__ == "__main__":
    root = tk.Tk()
    app = ParserGUI(root)
    root.mainloop() 