import PyInstaller.__main__
import os

# Получаем текущую директорию
current_dir = os.path.dirname(os.path.abspath(__file__))

PyInstaller.__main__.run([
    'gui.py',  # основной файл
    '--name=ParserFigaro',  # имя выходного файла
    '--onefile',  # создать один exe файл
    '--windowed',  # не показывать консоль
    '--icon=NONE',  # можно добавить иконку позже
    '--add-data=parser.py;.',  # добавляем parser.py
    '--clean',  # очистить предыдущую сборку
    '--noconfirm',  # не спрашивать подтверждения
    f'--distpath={os.path.join(current_dir, "dist")}',  # путь для выходного файла
    f'--workpath={os.path.join(current_dir, "build")}',  # путь для временных файлов
    f'--specpath={current_dir}',  # путь для spec файла
]) 