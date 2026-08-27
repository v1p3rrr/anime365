/**
 * Тонкая обёртка над fetch.
 *
 * Мутации идут только методами POST/PUT/DELETE — мутаций по GET в API нет намеренно,
 * это часть защиты от CSRF.
 */

/** Сигнал «сессия больше не действительна» — слушает useAuth. */
export const UNAUTHORIZED_EVENT = 'a365:unauthorized'

/**
 * Код отказа, который придумываем мы сами, а не сервер.
 *
 * До сервера запрос не дошёл, поэтому кода взять неоткуда — а объяснить надо, и
 * на языке интерфейса. Дальше он идёт по общему пути: `useApiError` ищет
 * `apiErrors.network` в словаре, как и для любого серверного кода.
 */
export const NETWORK_ERROR_CODE = 'network'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /**
     * Машинный код случая, если сервер его прислал.
     *
     * Нужен там, где интерфейс обязан отреагировать иначе, чем просто показать
     * текст: например, отказ «субтитры вшиты» окончателен, и кнопку после него
     * надо погасить. Разбирать ради этого сообщение нельзя — оно переводится.
     */
    public code?: string,
    /**
     * Значения для подстановки в переведённое сообщение (путь, имя, число).
     *
     * Приходят отдельно от текста намеренно: собранную строку не разобрать, а
     * переводы отличаются порядком слов — склеить их из кусков нельзя.
     */
    public params?: Record<string, string | number>,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...init?.headers,
      },
      // Сессионная кука нужна для всех запросов, включая GET.
      credentials: 'same-origin',
    })
  } catch (cause) {
    // fetch бросает голое «Failed to fetch» на любой сетевой проблеме и не
    // говорит ни какой запрос упал, ни почему. Для домашнего сервиса причина
    // почти всегда одна и та же — сервер не отвечает, — и её стоит назвать.
    //
    // ⚠️ Русский текст здесь — запасной вариант ровно в том же смысле, что и
    // сообщение сервера ([D-48](../../../claude/09-decisions.md)): показывается,
    // если словарь почему-то не дал строки. Перевод берётся по коду, иначе
    // англоязычный интерфейс объяснялся бы по-русски — и объяснялся, пока эту
    // строку не связали с уже написанным `apiErrors.network`.
    const reason = (cause as Error).message
    throw new ApiError(
      0,
      `Сервер не ответил на запрос ${path}. Проверьте, что контейнер запущен ` +
        `и доступен по этому адресу. Причина: ${reason}`,
      NETWORK_ERROR_CODE,
      { path, reason },
    )
  }

  if (!response.ok) {
    let detail = response.statusText
    let code: string | undefined
    let params: Record<string, string | number> | undefined
    try {
      const body = await response.json()
      // detail бывает строкой (валидация FastAPI и сторонние ответы) или
      // объектом { code, message, params } — так отвечает наш ApiError.
      // Сообщение в нём русское и служит запасным вариантом: показываем его,
      // когда код клиенту незнаком, чтобы новый код на сервере не оставлял
      // пользователя с пустым местом вместо объяснения.
      if (body.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) {
        detail = body.detail.message ?? detail
        code = body.detail.code
        params = body.detail.params
      } else if (typeof body.detail === 'string') {
        detail = body.detail
      }
    } catch {
      // Тело может быть не JSON — тогда довольствуемся статусом.
    }
    if (response.status === 401) {
      // Сессия истекла посреди работы. Без этого сигнала пользователь увидел бы
      // «Требуется вход» в каждой карточке вместо формы пароля.
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(response.status, detail, code, params)
  }

  return response.json() as Promise<T>
}

// Текстового варианта запроса здесь нет намеренно. Он был — «для субтитров»,
// с собственной, дословно повторённой копией разбора ошибок, — но субтитры к
// нему никогда не ходили: `.ass` забирает плеер прямо по адресу
// (`/api/stream/{id}/subtitles.ass`), потому что плагин определяет формат по
// расширению. Копия разбора при этом жила своей жизнью и успела разойтись с
// оригиналом: сетевой отказ она пропускала наружу голым `TypeError`.

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

// --- Типы ответов каталога ---

/** Что из тайтла есть локально. `total` уже посчитан на сервере. */
export interface LocalBadges {
  total: number
  /** Сколько серий уже выложено. У онгоинга меньше `total` — плана. */
  aired: number
  video: number
  sub_ru: number
  sub_ja: number
  sub_en: number
  /** Занятое место по всем привязанным папкам тайтла. */
  size_bytes: number
}

export interface CatalogCard {
  id: number
  /** Все четыре названия: какое показать, решает язык интерфейса (см. i18n/titles). */
  title_ru: string | null
  title_romaji: string | null
  title_en: string | null
  title_ja: string | null
  type: string
  type_title: string
  year: number | null
  /** Строка вида «Осень 2015» — она же используется при разведении сезонов. */
  season: string | null
  number_of_episodes: number
  poster_url_small: string | null
  mal_score: number | null
  is_airing: boolean
  /** null — тайтла нет в библиотеке. Это не то же самое, что «есть, но пусто». */
  local: LocalBadges | null
  /** Привязка нечёткая и ждёт подтверждения — стоит проверить глазами. */
  mapping_problem: boolean
  /** Анонсирован, но ещё не начал выходить: ни одной серии, и год не в прошлом. */
  announced: boolean
}

export interface CatalogIdsResponse {
  /** Окно идентификаторов, а не вся выборка. */
  ids: number[]
  /** Полный размер выборки — по нему считаются страницы и конец подгрузки. */
  total: number
  offset: number
  limit: number
}

export interface GenreItem {
  id: number
  title: string
  count: number
}

export interface ScanRunInfo {
  kind: string
  status: string
  started_at: string | null
  finished_at: string | null
  stats: Record<string, unknown> | null
  error: string | null
}

export interface CatalogStatus {
  total_series: number
  running: string | null
  recent: ScanRunInfo[]
}

export interface CatalogFilters {
  query?: string
  genres?: number[]
  genresAnd?: boolean
  type?: string
  yearFrom?: number
  yearTo?: number
  /** Зима | Весна | Лето | Осень — независимо от года. */
  season?: string
  isAiring?: boolean
  /** Минимальная оценка MAL. Тайтлы без оценки при этом фильтре не показываются. */
  scoreFrom?: number
  /** only — только то, что есть в библиотеке; missing — только чего нет. */
  local?: 'only' | 'missing'
  /** Только нечёткие привязки, ждущие подтверждения. */
  mappingProblem?: boolean
  /**
   * Какие типы перевода должны быть НА САЙТЕ (не в библиотеке).
   *
   * Данные собирает фоновая джоба; тайтлы, до которых она ещё не дошла, под
   * фильтр не подпадают — мы про них просто ничего не знаем.
   */
  has?: string[]
  /** Требовать все выбранные типы сразу, а не любой из них. */
  hasAll?: boolean
  sort?: SortField
  sortDir?: 'asc' | 'desc'
  /** Смещение окна выдачи. Полный размер выборки приходит в `total`. */
  offset?: number
  /** Сколько идентификаторов запросить за раз. Потолок сервера — 500. */
  limit?: number
}

export type SortField = 'id' | 'year' | 'score' | 'title'

export const SEASONS = ['Зима', 'Весна', 'Лето', 'Осень'] as const

/**
 * Поля сортировки. Подписи живут в словарях (`sort.*`): они зависят и от поля,
 * и от направления — «по году» само по себе не говорит о порядке.
 */
export const SORT_FIELDS: SortField[] = ['id', 'year', 'score', 'title']

function buildParams(filters: CatalogFilters): string {
  const params = new URLSearchParams()
  if (filters.query) params.set('query', filters.query)
  if (filters.genres?.length) {
    for (const g of filters.genres) params.append('genres', String(g))
  }
  if (filters.genresAnd) params.set('genres_and', 'true')
  if (filters.type) params.set('type', filters.type)
  if (filters.yearFrom !== undefined) params.set('year_from', String(filters.yearFrom))
  if (filters.yearTo !== undefined) params.set('year_to', String(filters.yearTo))
  if (filters.season) params.set('season', filters.season)
  if (filters.isAiring !== undefined) params.set('is_airing', String(filters.isAiring))
  if (filters.scoreFrom !== undefined) params.set('score_from', String(filters.scoreFrom))
  if (filters.local) params.set('local', filters.local)
  if (filters.mappingProblem) params.set('mapping_problem', 'true')
  if (filters.has?.length) {
    for (const type of filters.has) params.append('has', type)
    if (filters.hasAll) params.set('has_all', 'true')
  }
  if (filters.sort) params.set('sort', filters.sort)
  if (filters.sortDir) params.set('sort_dir', filters.sortDir)
  if (filters.offset) params.set('offset', String(filters.offset))
  if (filters.limit) params.set('limit', String(filters.limit))
  return params.toString()
}

export const catalogApi = {
  ids: (filters: CatalogFilters) => api.get<CatalogIdsResponse>(`/catalog/ids?${buildParams(filters)}`),
  cards: (ids: number[]) => api.post<CatalogCard[]>('/catalog/cards', { ids }),
  genres: () => api.get<GenreItem[]>('/catalog/genres'),
  status: () => api.get<CatalogStatus>('/catalog/status'),
  sync: (mode: 'full' | 'incremental' | 'airing') => api.post<unknown>('/catalog/sync', { mode }),
}
