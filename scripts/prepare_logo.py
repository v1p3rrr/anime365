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

# Насколько цвет может отличаться от углового, чтобы всё ещё считаться фоном.
# У этого файла фон не чисто белый: по краям идут серые полосы 190–205, а
# внутри — почти белое. Один порог их не покрывает, поэтому сравниваем не с
# белым, а с цветом, найденным по углам.
TOLERANCE = 60

# Высота готового файла. В шапке логотип показывается около 30 пикселей;
# запас втрое покрывает экраны с удвоенной плотностью, а исходные 1024×295
# весили бы полмегабайта ради картинки размером с строку текста.
MAX_HEIGHT = 96


def remove_background(image: Image.Image) -> Image.Image:
    """Убирает фон заливкой от углов.

    Пороговое отсечение «всё светлее N — фон» здесь не годится: чтобы достать
    серые полосы по краям, порог пришлось бы опустить настолько, что он съел
    бы светлые участки самого рисунка (блики в глазу).

    Заливка убирает только то, что **связано** с краем, поэтому светлое внутри
    логотипа остаётся на месте, каким бы светлым оно ни было.
    """
    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.load()

    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    seeds = [(x, y) for x, y in corners]
    reference = [pixels[x, y][:3] for x, y in corners]

    def is_background(pixel: tuple[int, int, int, int]) -> bool:
        return any(
            abs(pixel[0] - r) <= TOLERANCE
            and abs(pixel[1] - g) <= TOLERANCE
            and abs(pixel[2] - b) <= TOLERANCE
            for r, g, b in reference
        )

    # Итеративная заливка вместо рекурсии: у картинки 1024×299 глубина рекурсии
    # ушла бы за лимит интерпретатора на первой же длинной полосе.
    seen = bytearray(width * height)
    stack = list(seeds)

    while stack:
        x, y = stack.pop()
        if not (0 <= x < width and 0 <= y < height):
            continue
        index = y * width + x
        if seen[index]:
            continue
        seen[index] = 1

        pixel = pixels[x, y]
        if not is_background(pixel):
            continue

        pixels[x, y] = (pixel[0], pixel[1], pixel[2], 0)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    _clear_enclosed(image)
    return image


# Насколько светлым должен быть пиксель, чтобы считаться фоном во втором проходе.
ENCLOSED_MIN_CHANNEL = 175
# И насколько бесцветным. У логотипа два цвета: тёмно-зелёный и светло-зелёный
# (примерно 166,206,57) — у второго разброс каналов около 150, так что он под
# это условие не попадает даже при высокой яркости.
ENCLOSED_MAX_SPREAD = 34


def _clear_enclosed(image: Image.Image) -> None:
    """Убирает фон внутри замкнутых областей — счётчиков букв.

    Заливка от углов до них не доходит: внутренность «о», «е», «D», «6» не
    связана с краем. Поэтому вторым проходом снимаем светлые и при этом
    бесцветные пиксели по всему изображению.

    Проверка на бесцветность обязательна: без неё под раздачу попали бы
    светло-зелёные участки рисунка, у которых яркость тоже высокая.
    """
    pixels = image.load()
    width, height = image.size

    for y in range(height):
        for x in range(width):
            r, g, b, alpha = pixels[x, y]
            if alpha == 0:
                continue
            if min(r, g, b) >= ENCLOSED_MIN_CHANNEL and max(r, g, b) - min(r, g, b) <= ENCLOSED_MAX_SPREAD:
                pixels[x, y] = (r, g, b, 0)


def main() -> int:
    if len(sys.argv) < 2:
        print("Укажите путь к файлу логотипа", file=sys.stderr)
        return 2

    source = Path(sys.argv[1])
    if not source.is_file():
        print(f"Файл не найден: {source}", file=sys.stderr)
        return 2

    image = remove_background(Image.open(source))

    # Обрезаем поля по непрозрачному содержимому: у исходника их обычно много,
    # и без обрезки логотип в шапке выглядит меньше, чем занимает места.
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)

    if image.height > MAX_HEIGHT:
        ratio = MAX_HEIGHT / image.height
        image = image.resize(
            (round(image.width * ratio), MAX_HEIGHT),
            # LANCZOS: у логотипа тонкие штрихи, и при уменьшении более простые
            # фильтры превращают их в кашу.
            Image.Resampling.LANCZOS,
        )

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    image.save(TARGET, "PNG", optimize=True)

    print(f"Готово: {TARGET}")
    print(f"Размер: {image.width}x{image.height}, {TARGET.stat().st_size / 1024:.0f} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
