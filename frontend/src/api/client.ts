/**
 * Тонкая обёртка над fetch.
 *
 * Мутации идут только методами POST/PUT/DELETE — мутаций по GET в API нет намеренно,
 * это часть защиты от CSRF.
 */

/** Сигнал «сессия больше не действительна» — слушает useAuth. */
export const UNAUTHORIZED_EVENT = 'a365:unauthorized'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
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
    throw new ApiError(
      0,
      `Сервер не ответил на запрос ${path}. Проверьте, что контейнер запущен ` +
        `и доступен по этому адресу. Причина: ${(cause as Error).message}`,
    )
  }

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      // Тело может быть не JSON — тогда довольствуемся статусом.
    }
    if (response.status === 401) {
      // Сессия истекла посреди работы. Без этого сигнала пользователь увидел бы
      // «Требуется вход» в каждой карточке вместо формы пароля.
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(response.status, detail)
  }

  return response.json() as Promise<T>
}

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
  video: number
  sub_ru: number
  sub_ja: number
  sub_en: number
}

export interface CatalogCard {
  id: number
  title_ru: string | null
  title_romaji: string | null
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
}

export interface CatalogIdsResponse {
  ids: number[]
  total: number
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
  sort?: SortField
  sortDir?: 'asc' | 'desc'
}

export type SortField = 'id' | 'year' | 'score' | 'title'

export const SEASONS = ['Зима', 'Весна', 'Лето', 'Осень'] as const

/** Подписи зависят от направления, иначе «По году» ничего не говорит о порядке. */
export const SORT_LABELS: Record<SortField, { desc: string; asc: string }> = {
  id: { desc: 'Сначала новые', asc: 'Сначала старые' },
  year: { desc: 'Год: новее', asc: 'Год: старее' },
  score: { desc: 'Оценка: выше', asc: 'Оценка: ниже' },
  title: { desc: 'Название: Я–А', asc: 'Название: А–Я' },
}

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
  if (filters.sort) params.set('sort', filters.sort)
  if (filters.sortDir) params.set('sort_dir', filters.sortDir)
  return params.toString()
}

export const catalogApi = {
  ids: (filters: CatalogFilters) => api.get<CatalogIdsResponse>(`/catalog/ids?${buildParams(filters)}`),
  cards: (ids: number[]) => api.post<CatalogCard[]>('/catalog/cards', { ids }),
  genres: () => api.get<GenreItem[]>('/catalog/genres'),
  status: () => api.get<CatalogStatus>('/catalog/status'),
  sync: (mode: 'full' | 'incremental' | 'airing') => api.post<unknown>('/catalog/sync', { mode }),
}
