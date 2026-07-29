/**
 * Тонкая обёртка над fetch.
 *
 * Мутации идут только методами POST/PUT/DELETE — мутаций по GET в API нет намеренно,
 * это часть защиты от CSRF.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
    // Сессионная кука нужна для всех запросов, включая GET.
    credentials: 'same-origin',
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      // Тело может быть не JSON — тогда довольствуемся статусом.
    }
    throw new ApiError(response.status, detail)
  }

  return response.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
}

// --- Типы ответов каталога ---

export interface CatalogCard {
  id: number
  title_ru: string | null
  title_romaji: string | null
  type: string
  type_title: string
  year: number | null
  season: string | null
  number_of_episodes: number
  poster_url_small: string | null
  mal_score: number | null
  is_airing: boolean
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
  isAiring?: boolean
  sort?: 'id' | 'year' | 'score' | 'title'
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
  if (filters.isAiring !== undefined) params.set('is_airing', String(filters.isAiring))
  if (filters.sort) params.set('sort', filters.sort)
  return params.toString()
}

export const catalogApi = {
  ids: (filters: CatalogFilters) => api.get<CatalogIdsResponse>(`/catalog/ids?${buildParams(filters)}`),
  cards: (ids: number[]) => api.post<CatalogCard[]>('/catalog/cards', { ids }),
  genres: () => api.get<GenreItem[]>('/catalog/genres'),
  status: () => api.get<CatalogStatus>('/catalog/status'),
  sync: (mode: 'full' | 'incremental' | 'airing') => api.post<unknown>('/catalog/sync', { mode }),
}
