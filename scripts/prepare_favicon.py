"""Вырезает «365» из логотипа и делает из него иконку вкладки.

Зачем отдельная иконка, а не тот же логотип: логотип вытянутый (333×96), а
вкладка показывает квадрат 16–32 пикселя. Ужатый целиком он превращается в
неразличимую зелёную полосу — на такой вкладке не найти сервис среди двадцати
других. «365» же читается и в 16 пикселей: цифры крупные и контрастные.

Область не задана числами вручную, а **находится по цвету**: «365» и галочка
над ним нарисованы светлым жёлто-зелёным, а всё остальное в этой части
картинки — тёмно-зелёное или прозрачное. Так скрипт переживёт замену логотипа
на версию с другими полями.

Запуск (после prepare_logo.py):
    python scripts/prepare_favicon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "public" / "logo.png"
TARGET_PNG = ROOT / "frontend" / "public" / "favicon.png"
TARGET_ICO = ROOT / "frontend" / "public" / "favicon.ico"

#: Где искать «365»: правая часть картинки, верхняя половина.
#: Ниже начинается «Downloader», нарисованный тем же светлым цветом.
SEARCH_LEFT = 0.6
SEARCH_BOTTOM = 0.55

#: Поле вокруг вырезанного, в долях от большей стороны. Без него цифры упираются
#: в край иконки, и в 16 пикселях это выглядит обрезанным.
PADDING = 0.08

#: Размеры, которые кладём в .ico. 16 — вкладка, 32 — панель закладок,
#: 48 — ярлык на рабочем столе, если страницу сохранят как приложение.
ICO_SIZES = [(16, 16), (32, 32), (48, 48)]


def is_light_green(pixel: tuple[int, int, int, int]) -> bool:
    """Светлый жёлто-зелёный, которым нарисованы «365» и галочка."""
    red, green, blue, alpha = pixel
    return alpha > 60 and green > 150 and red > 120 and blue < 140


def find_badge(image: Image.Image) -> tuple[int, int, int, int]:
    """Рамка вокруг «365» — по светлым пикселям в правом верхнем углу."""
    width, height = image.size
    pixels = image.load()
    left_bound = int(width * SEARCH_LEFT)
    bottom_bound = int(height * SEARCH_BOTTOM)

    xs: list[int] = []
    ys: list[int] = []
    for y in range(bottom_bound):
        for x in range(left_bound, width):
            if is_light_green(pixels[x, y]):
                xs.append(x)
                ys.append(y)

    if not xs:
        raise SystemExit(
            "Не нашёл «365» в правом верхнем углу логотипа. "
            "Если логотип заменили, поправьте SEARCH_LEFT/SEARCH_BOTTOM."
        )
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def square(image: Image.Image) -> Image.Image:
    """Вписывает картинку в прозрачный квадрат, не растягивая её."""
    width, height = image.size
    side = int(max(width, height) * (1 + PADDING * 2))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image, ((side - width) // 2, (side - height) // 2), image)
    return canvas


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Нет файла {SOURCE}. Сначала запустите prepare_logo.py")

    logo = Image.open(SOURCE).convert("RGBA")
    box = find_badge(logo)
    badge = logo.crop(box)

    # Обрезаем то, что осталось прозрачным по краям после вырезания рамки:
    # у галочки тонкий хвост, и он оставляет пустую полосу.
    bbox = badge.getbbox()
    if bbox:
        badge = badge.crop(bbox)

    icon = square(badge)
    icon.save(TARGET_PNG)
    icon.save(TARGET_ICO, sizes=ICO_SIZES)

    print(f"«365» найден в {box}, размер после обрезки {badge.size}")
    print(f"Готово: {TARGET_PNG.relative_to(ROOT)} и {TARGET_ICO.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
