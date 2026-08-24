# --- Этап 1: сборка фронтенда ---
FROM node:22-alpine AS frontend

WORKDIR /build

# Сначала манифесты — слой с зависимостями переиспользуется, пока они не менялись.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install

COPY frontend/ ./
# Vite кладёт результат в ../backend/app/static, поэтому каталог должен существовать.
RUN mkdir -p /backend/app/static && \
    sed -i "s#'../backend/app/static'#'/backend/app/static'#" vite.config.ts && \
    npm run build


# --- Этап 2: рантайм ---
FROM python:3.12-slim AS runtime

# PYTHONUNBUFFERED — иначе логи застревают в буфере и docker logs пустует.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tini — корректная обработка сигналов, иначе SIGTERM не доходит до uvicorn
# и контейнер убивается по таймауту, обрывая загрузки.
#
# Зачистка идёт в ЭТОМ ЖЕ слое, а не отдельным шагом ниже: удаление в
# последующем слое убирает файлы из видимой файловой системы, но образ
# продолжает их нести — они остаются в нижних слоях, и вес не меняется.
#
# Что убираем и почему:
#
#  * **драйверы GPU, которые тянет за собой ffmpeg** — mesa (DRI, Vulkan,
#    Gallium) и llvm, который mesa использует для компиляции шейдеров. Это НЕ
#    части ffmpeg: сам бинарник, ffprobe и все библиотеки libav* остаются.
#    Аппаратное ускорение в контейнере без проброшенного /dev/dri невозможно в
#    принципе, а весят эти пакеты сотни мегабайт. Проверено на реальном файле
#    (HEVC + Opus): ffprobe читает дорожки, `-c:a copy` вытаскивает звук,
#    перекодирование в AAC работает. Если когда-нибудь понадобится аппаратный
#    транскод — убрать эту строку и пересобрать;
#
#  * **perl-base** — помечен Essential, но приложению не нужен: проверено, что
#    после удаления живы ffmpeg, dpkg и все импорты Python. Это и готовый
#    интерпретатор для полезной нагрузки, и источник
#    единственной critical-находки сканера (CVE-2026-12087 в модуле Socket).
#    Удаляем вместе с записью в базе dpkg, иначе сканер продолжит сообщать об
#    уже отсутствующем пакете. Сам dpkg оставляем намеренно: без него сканер не
#    увидит ни одного пакета, и отчёт станет пустым не потому, что чисто, а
#    потому, что нечего читать;
#
#  * **apt** — после сборки не нужен, а внутри контейнера это готовый способ
#    дотащить любой инструмент;
#
#  * **tar** — тоже Essential, и его держит зависимостью сам dpkg, поэтому
#    нужен ещё и `--force-depends`. dpkg от этого теряет способность
#    РАСПАКОВЫВАТЬ пакеты, но не читать свою базу — а нам от него нужно только
#    второе, чтобы сканеру было что перечислять. Проверено: после удаления
#    `dpkg -l` показывает 299 пакетов, приложение и ffmpeg работают. Заодно
#    уходит последняя оставшаяся находка сканера (CVE-2025-45582).
#
#    ⚠️ Порядок важен: **tar удаляется последним**. `dpkg` сам требует `tar` и
#    без него отказывается работать вовсе — «expected program not found in
#    PATH». Убрать tar раньше значит потерять возможность удалить apt;
#
#  * **сам dpkg** — после удаления tar он всё равно ничего не может: ни
#    установить, ни удалить. Единственное, ради чего его стоило держать, —
#    чтобы сканеру было что перечислять, но сканер читает не бинарник, а файл
#    `/var/lib/dpkg/status`. Файл остаётся (это проверяется тут же, `test -s`),
#    и отчёт по-прежнему полный, а средства управления пакетами в контейнере
#    больше нет вовсе;
#
#  * **curl** — не ставим вовсе. Он был нужен только для healthcheck, а тот
#    переписан на Python (см. HEALTHCHECK ниже). Полноценный загрузчик в
#    образе — готовый способ утащить что угодно извне;
#
#  * **биты SUID и SGID** — su, mount, passwd и ещё пять таких же приложению не
#    нужны ни в каком виде. `no-new-privileges: true` в compose и так режет
#    повышение прав, но полагаться на одну защиту не стоит.
#
# ⚠️ Всё удаляется через `dpkg`, а НЕ через `apt-get purge`. Причина стоила
# отладки: после первого же `--force-depends` база dpkg остаётся в состоянии
# «зависимости нарушены», и apt отказывается работать вовсе — падает с «Unmet
# dependencies». В цепочке через `&&` это обрывало всё, что шло дальше, а
# завершающий `; true` (он нужен был для find) возвращал ноль — и сборка молча
# отчитывалась успехом, оставляя в образе и apt, и tar. dpkg таких претензий не
# предъявляет, ему достаточно `--force-depends`.
#
# `|| true` стоит только у find: `-exec ... +` возвращает ненулевой код, когда
# ни один файл не подошёл, а это нормальный исход. Всё остальное обязано
# отработать, иначе сборка должна падать.
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini ffmpeg && \
    dpkg --force-depends --purge \
        mesa-vulkan-drivers mesa-dri mesa-libgallium libllvm19 && \
    dpkg --force-depends --force-remove-essential --purge perl-base && \
    dpkg --force-depends --purge apt && \
    dpkg --force-depends --force-remove-essential --purge tar && \
    rm -f /usr/bin/dpkg /usr/bin/dpkg-deb /usr/bin/dpkg-split \
          /usr/bin/dpkg-query /usr/bin/dpkg-trigger /usr/bin/dpkg-divert \
          /usr/bin/dpkg-statoverride /usr/sbin/dpkg-preconfigure && \
    rm -rf /var/lib/apt/lists /var/lib/apt /etc/apt /var/log/apt && \
    { find / -xdev -perm -4000 -type f -exec chmod u-s {} + 2>/dev/null || true; } && \
    { find / -xdev -perm -2000 -type f -exec chmod g-s {} + 2>/dev/null || true; } && \
    test ! -e /usr/bin/apt && test ! -e /usr/bin/tar &&     test ! -e /usr/bin/perl && test ! -e /usr/bin/dpkg &&     test -s /var/lib/dpkg/status

WORKDIR /app

COPY backend/pyproject.toml ./

# pip остаётся в образе и сам попадает в отчёты сканеров: у 25.0.1 четыре CVE,
# одна из них high. Обновляем его первым же шагом, отдельным слоем — так он не
# переустанавливается при каждой правке списка зависимостей.
RUN pip install --no-cache-dir --upgrade "pip==26.1.2"

# Зависимости ставятся ИЗ pyproject.toml, а не списком, переписанным сюда руками.
#
# Дубль тут был, и был осознанным: считалось, что иначе правка исходников тянет
# переустановку всего. Это перестало быть правдой — `pyproject.toml` копируется
# **выше** этой строки и **до** исходников, поэтому слой ломается только когда
# меняется сам pyproject, то есть ровно когда зависимости и меняются.
#
# А цена дубля продолжала капать. Сперва списки разошлись молча: в образе не
# оказалось `socksio`, и SOCKS5-прокси не работал вовсе (D-66, тест это поймал).
# Потом появился Dependabot — он правит pyproject и не знает про второй список,
# так что каждое его обновление питоновских зависимостей падало на том же тесте
# и требовало ручной правки этого файла.
#
# ⚠️ `chr(10)` вместо переноса строки — намеренно. Строка проходит через
# разбор Dockerfile, потом через `sh -c`, потом через литерал Python; обратный
# слэш на этом пути теряется по-разному в зависимости от того, кто его съел
# первым. Числовой код не зависит ни от одного из трёх.
RUN python -c "import pathlib,tomllib;deps=tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['dependencies'];pathlib.Path('/tmp/requirements.txt').write_text(chr(10).join(deps),encoding='utf-8')" \
    && cat /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY backend/app ./app
# Правила разбора имён кладём внутрь образа. Том ./config — необязательный
# override: без этой копии контейнер, запущенный без bind-mount, падал при
# сканировании библиотеки.
COPY config/filename_rules.yaml ./app/defaults/filename_rules.yaml
COPY --from=frontend /backend/app/static ./app/static

# Непривилегированный пользователь. UID/GID перекрываются через PUID/PGID в compose.
RUN groupadd -g 1000 app && \
    useradd -u 1000 -g 1000 -m -s /bin/bash app && \
    mkdir -p /data /config && \
    chown -R app:app /app /data /config

# Инструменты, которые приложению не нужны, а атакующему пригодились бы.
# Отдельным слоем, потому что часть из них (useradd, groupadd) нужна на шаге
# выше — раньше их удалить нельзя.
#
# Веса это не экономит: файлы остаются в нижних слоях образа. Смысл в другом —
# в работающем контейнере их не будет, а именно там ими и воспользовались бы.
# Сканер по той же причине продолжит числить пакеты, которым эти файлы
# принадлежали: удалены файлы, а не записи в базе dpkg.
#
#  * nsenter, unshare, chroot — работа с пространствами имён, первое, что
#    ищут при попытке выйти за границы контейнера;
#  * mount, umount — тем же целям служат;
#  * su, passwd, chsh, chfn, newgrp, gpasswd, useradd и родня — смена
#    пользователя и правка учётных записей;
#  * gzip — распаковка принесённого извне.
#
# Оболочку (/bin/sh) намеренно оставляем: без неё нельзя зайти в контейнер и
# посмотреть, что происходит, а своих мест, где приложение вызывает шелл, у нас
# нет — subprocess зовёт ffmpeg напрямую, списком аргументов.
RUN rm -f /usr/bin/nsenter /usr/bin/unshare /usr/bin/chroot /usr/sbin/chroot \
          /usr/bin/mount /usr/bin/umount /usr/bin/su /usr/bin/passwd \
          /usr/bin/chsh /usr/bin/chfn /usr/bin/newgrp /usr/bin/gpasswd \
          /usr/bin/gzip /usr/sbin/useradd /usr/sbin/userdel /usr/sbin/usermod \
          /usr/sbin/groupadd /usr/sbin/groupdel /usr/sbin/groupmod

USER app

EXPOSE 8080

# Проверка живости на Python, а не curl: curl — полноценный загрузчик, и держать
# его в образе ради одного GET значит оставить готовый способ утащить что угодно
# извне. Python здесь и так есть, он обязателен.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
