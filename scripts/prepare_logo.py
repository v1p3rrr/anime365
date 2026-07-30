"""Готовит логотип к использованию в интерфейсе.

Что делает:
  * убирает белый фон (делает его прозрачным),
  * обрезает пустые поля по краям,
  * кладёт результат туда, откуда его берёт фронтенд.

Зачем убирать фон, а не оставлять картинку как есть: интерфейс тёмный, и белый
прямоугольник вокруг логотипа выглядел бы наклейкой. Прозрачность решает это
раз и навсегда, в отличие от подгонки цвета подложки под тему.

Запуск:
    python scripts/prepare_logo.py путь/к/logo.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# Куда кладём результат. Именно public/, а не src/assets: импорт из assets
# резолвится при сборке, и без файла проект бы не собрался. Отсутствие
# логотипа не должно ломать сборку — компонент в этом случае показывает текст.
TARGET = Path(__file__).resolve().parents[1] / "frontend" / "public" / "logo.png"

# Порог «это фон». Берём с запасом: у сканов и JPEG белый редко бывает ровно
# 255,255,255 — по краям всегда есть шум сжатия.
WHITE_THRESHOLD = 240


def remove_white_background(image: Image.Image) -> Image.Image:
    """Делает прозрачными пиксели, близкие к белому.

    Полупрозрачность на границе не восстанавливаем: у логотипа резкие края,
    и простое пороговое отсечение даёт тот же результат, что и сложная
    обработка, но без риска съесть светло-зелёные участки.
    """
    image = image.convert("RGBA")
    pixels = image.load()
    width, height = image.size

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD:
                pixels[x, y] = (r, g, b, 0)

    return image


def main() -> int:
    if len(sys.argv) < 2:
        print("Укажите путь к файлу логотипа", file=sys.stderr)
        return 2

    source = Path(sys.argv[1])
    if not source.is_file():
        print(f"Файл не найден: {source}", file=sys.stderr)
        return 2

    image = remove_white_background(Image.open(source))

    # Обрезаем поля по непрозрачному содержимому: у исходника их обычно много,
    # и без обрезки логотип в шапке выглядит меньше, чем занимает места.
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    image.save(TARGET, "PNG", optimize=True)

    print(f"Готово: {TARGET}")
    print(f"Размер: {image.width}x{image.height}, {TARGET.stat().st_size / 1024:.0f} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
