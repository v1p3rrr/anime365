"""Заголовки безопасности, проверка сессии и защита от CSRF.

Всё сделано middleware, а не зависимостями роутеров: зависимость легко забыть
повесить на новый эндпоинт, и он молча окажется открытым. Здесь по умолчанию
закрыто всё, а исключения перечислены явным списком.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp

from app.core.security import SESSION_COOKIE, password_required, sessions

log = logging.getLogger(__name__)
access_log = logging.getLogger("app.access")

# Пути, доступные без сессии. Всё остальное требует входа, когда пароль задан.
PUBLIC_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/login",
        "/api/auth/status",
        "/api/auth/logout",
    }
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# `unsafe-inline` для стилей приходится оставить: Mantine ставит инлайновые
# style-атрибуты на элементы. Для скриптов его нет — именно скрипты опасны.
# `connect-src 'self'` не даёт утащить данные на посторонний хост, если XSS
# всё-таки случится, `img-src` расширен под постеры Anime365.
CSP = (
    "default-src 'self'; "
    # 'wasm-unsafe-eval' нужен рендереру субтитров: ASS отрисовывает libass,
    # собранный в WebAssembly. Это НЕ 'unsafe-eval' — разрешается только
    # компиляция wasm, выполнение произвольного JS по-прежнему запрещено.
    "script-src 'self' 'wasm-unsafe-eval'; "
    # Рендерер работает в отдельном воркере, чтобы не морозить интерфейс
    # на тяжёлых кадрах с караоке и надписями.
    "worker-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://smotret-anime.app https://smotret-anime.org "
    "https://smotret-anime.online https://smotret-anime.net; "
    "connect-src 'self'; "
    # Видео идёт только через наш прокси: прямые ссылки CDN в браузер не
    # попадают, потому что содержат токен.
    "media-src 'self' blob:; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    # Ничего из этого приложению не нужно, а разрешено по умолчанию.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
}


#: С какой длительности запрос считается медленным и пишется предупреждением.
#:
#: Замер отдельной строкой нужен потому, что uvicorn пишет свою строку доступа
#: БЕЗ времени ответа: видно, что запрос был и чем кончился, но не видно, сколько
#: пользователь его ждал. Из-за этого «библиотека грузится десятки секунд»
#: приходилось проверять косвенно — по паузам между проверками здоровья
#: контейнера, а это доказывает лишь то, что процесс был чем-то занят.
SLOW_REQUEST_MS = int(os.environ.get("A365_SLOW_REQUEST_MS") or 1000)

#: Пути, которые в норме не интересны: их дёргают машины, а не человек.
QUIET_PATHS = frozenset({"/api/health"})


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Строка доступа со временем ответа.

    ⚠️ Меряется время до **начала** ответа, а не до последнего байта тела.
    Для BaseHTTPMiddleware иначе и не выйдет: ответ здесь потоковый. Нас это
    устраивает — интересна работа сервера (база, сеть, разбор), а не скорость
    канала до браузера. Для видео разница принципиальна: отдача часового файла
    длится часами, и её длительность ничего не говорит о нагрузке.

    Самое внешнее middleware, поэтому в замер попадают и остальные: проверка
    сессии, CSRF, сжатие ответа.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        client = request.client.host if request.client else "-"

        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            # Само исключение залогирует Starlette; здесь важна длительность:
            # упавший через тридцать секунд запрос и упавший сразу — разные беды.
            access_log.warning("%s %s — исключение за %.0f мс (%s)", request.method, target, elapsed, client)
            raise

        elapsed = (time.perf_counter() - started) * 1000
        if elapsed >= SLOW_REQUEST_MS:
            level = logging.WARNING
        elif request.url.path in QUIET_PATHS:
            level = logging.DEBUG
        else:
            level = logging.INFO

        access_log.log(
            level, "%s %s %s за %.0f мс (%s)", request.method, target, response.status_code, elapsed, client
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """Пускает дальше только с валидной сессией — либо когда пароль не задан.

    Статику и SPA-маршруты не закрываем: без index.html пользователю негде
    ввести пароль. Ценность за паролем — API, и закрыт именно он.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path

        if not path.startswith("/api/") or path in PUBLIC_PATHS:
            return await call_next(request)

        # Документация API показывает структуру запросов, включая настройки —
        # держим её за тем же паролем.
        if not password_required():
            return await call_next(request)

        session = sessions.get(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return JSONResponse({"detail": "Требуется вход"}, status_code=401)

        request.state.user_id = session.user_id
        return await call_next(request)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Отбивает межсайтовые изменяющие запросы.

    Кука сессии помечена SameSite=Strict, и одного этого почти достаточно, но
    заголовки — независимая вторая проверка: она переживает старый браузер и
    не полагается на то, что кука вообще участвует в запросе.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in SAFE_METHODS or not request.url.path.startswith("/api/"):
            return await call_next(request)

        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None:
            if fetch_site not in ("same-origin", "same-site", "none"):
                log.warning("Отклонён межсайтовый запрос на %s (%s)", request.url.path, fetch_site)
                return JSONResponse({"detail": "Межсайтовый запрос отклонён"}, status_code=403)
            return await call_next(request)

        # Старый браузер или curl: Sec-Fetch-Site не пришёл, сверяем Origin.
        # Его отсутствие тоже допустимо — так выглядят запросы не из браузера,
        # а для них CSRF-угрозы нет: чужой сайт не может убрать заголовок.
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            log.warning("Отклонён запрос с чужим Origin %s на %s", origin, request.url.path)
            return JSONResponse({"detail": "Межсайтовый запрос отклонён"}, status_code=403)

        return await call_next(request)


def install(app: ASGIApp) -> None:
    """Порядок важен: Starlette выполняет middleware в обратном порядке добавления.

    Нужно, чтобы заголовки безопасности навешивались и на ответы 401/403,
    поэтому SecurityHeaders добавляется последним и оказывается снаружи.
    """
    # Сжатие добавляется ПЕРВЫМ, то есть оказывается самым внутренним — ближе
    # всех к обработчику. Это не стилистика: остальные наши middleware сделаны
    # на BaseHTTPMiddleware, а он превращает любой ответ в потоковый и теряет
    # Content-Length. Снаружи от них GZip не знает размера тела и жмёт всё
    # подряд, включая ответы в сотню байт, где сжатие дороже выигрыша.
    #
    # Замер, ради которого это вообще появилось: бандл интерфейса весит 876 КБ
    # и уходил как есть — StaticFiles сам ничего не сжимает, а Accept-Encoding
    # без middleware просто игнорируется. Со сжатием — 266 КБ, минус 70%.
    # На загрузке и разборе этого файла уходило около 370 мс до первого
    # запроса к API, то есть больше половины времени открытия страницы тайтла.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # Замер добавляется последним и оказывается самым внешним: время должно
    # включать всё, что сервер делает с запросом, а не только работу обработчика.
    app.add_middleware(AccessLogMiddleware)
