import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { CatalogFilters as Filters, SortField } from '../api/client'

/**
 * Состояние каталога живёт в адресе страницы, а не в `useState`.
 *
 * Две причины, и обе про то, чего раньше не было:
 *
 * 1. **«Назад» со страницы тайтла возвращает туда же, откуда ушли.** Раньше
 *    состояние жило в памяти компонента и при уходе пропадало: человек
 *    выставлял жанры, год и десятую страницу, открывал тайтл — и возвращался
 *    в пустой каталог, отсортированный по умолчанию.
 * 2. **Ссылкой можно поделиться.** «Фэнтези 2023 года с оценкой выше восьми» —
 *    это теперь адрес, а не устная инструкция, что понажимать.
 *
 * ## Почему `replace`, а не `push`
 *
 * Каждая правка фильтра, попав в историю, сделала бы кнопку «назад»
 * бесполезной: она отматывала бы нажатия по одному вместо возврата из тайтла.
 * Поэтому изменения состояния заменяют текущую запись истории, а новая запись
 * появляется только при настоящем переходе — на страницу тайтла и обратно.
 *
 * ## Что в адрес не пишется
 *
 * Значения по умолчанию. Иначе чистый каталог открывался бы с хвостом
 * `?sort=score&dir=desc&page=1`, который ничего не сообщает, но который люди
 * копируют и пересылают.
 */

export const DEFAULT_SORT: SortField = 'score'
export const DEFAULT_SORT_DIR = 'desc'

export interface CatalogState {
  search: string
  filters: Filters
  page: number
}

function readNumber(raw: string | null): number | undefined {
  if (!raw) return undefined
  const value = Number(raw)
  return Number.isFinite(value) ? value : undefined
}

function readList(raw: string | null): string[] | undefined {
  if (!raw) return undefined
  const items = raw.split(',').filter(Boolean)
  return items.length > 0 ? items : undefined
}

export function parseCatalogState(params: URLSearchParams): CatalogState {
  const genres = readList(params.get('genres'))
    ?.map(Number)
    .filter((id) => Number.isFinite(id))

  const local = params.get('local')

  return {
    search: params.get('q') ?? '',
    page: Math.max(1, readNumber(params.get('page')) ?? 1),
    filters: {
      genres: genres?.length ? genres : undefined,
      genresAnd: params.get('genresAnd') === '1' || undefined,
      type: params.get('type') || undefined,
      season: params.get('season') || undefined,
      yearFrom: readNumber(params.get('yearFrom')),
      yearTo: readNumber(params.get('yearTo')),
      scoreFrom: readNumber(params.get('scoreFrom')),
      isAiring: params.get('airing') === '1' || undefined,
      // Значение из адреса не доверяем на слово: чужой набор букв здесь
      // сломал бы запрос к API, а не показал пустой список.
      local: local === 'only' || local === 'missing' ? local : undefined,
      mappingProblem: params.get('problem') === '1' || undefined,
      has: readList(params.get('has')),
      hasAll: params.get('hasAll') === '1' || undefined,
      sort: (params.get('sort') as SortField) || DEFAULT_SORT,
      sortDir: params.get('dir') === 'asc' ? 'asc' : DEFAULT_SORT_DIR,
    },
  }
}

export function serializeCatalogState(state: CatalogState): URLSearchParams {
  const params = new URLSearchParams()
  const { search, filters, page } = state

  if (search) params.set('q', search)
  if (filters.genres?.length) params.set('genres', filters.genres.join(','))
  if (filters.genresAnd) params.set('genresAnd', '1')
  if (filters.type) params.set('type', filters.type)
  if (filters.season) params.set('season', filters.season)
  if (filters.yearFrom != null) params.set('yearFrom', String(filters.yearFrom))
  if (filters.yearTo != null) params.set('yearTo', String(filters.yearTo))
  if (filters.scoreFrom != null) params.set('scoreFrom', String(filters.scoreFrom))
  if (filters.isAiring) params.set('airing', '1')
  if (filters.local) params.set('local', filters.local)
  if (filters.mappingProblem) params.set('problem', '1')
  if (filters.has?.length) params.set('has', filters.has.join(','))
  if (filters.hasAll) params.set('hasAll', '1')

  // Умолчания в адрес не пишем — см. докстроку модуля.
  if (filters.sort && filters.sort !== DEFAULT_SORT) params.set('sort', filters.sort)
  if (filters.sortDir && filters.sortDir !== DEFAULT_SORT_DIR) params.set('dir', filters.sortDir)
  if (page > 1) params.set('page', String(page))

  return params
}

export function useCatalogUrlState() {
  const [params, setParams] = useSearchParams()

  const state = useMemo(() => parseCatalogState(params), [params])

  const update = useCallback(
    (patch: Partial<CatalogState>) => {
      setParams(
        (current) => serializeCatalogState({ ...parseCatalogState(current), ...patch }),
        // Замена, а не новая запись истории: см. докстроку модуля.
        { replace: true },
      )
    },
    [setParams],
  )

  return { state, update }
}
