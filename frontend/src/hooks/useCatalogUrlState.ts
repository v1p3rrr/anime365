import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { SORT_FIELDS, type CatalogFilters as Filters, type SortField } from '../api/client'

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

/**
 * Каталог по умолчанию показывает то, что уже лежит в библиотеке.
 *
 * Просьба пользователя, и она про то, чем каталог занят на самом деле: своё
 * смотрят и докачивают каждый день, а по всем 29 тысячам ходят изредка и с
 * поиском. Выключается той же чипсой — состояние «показать всё» пишется в
 * адрес явным `local=all`, иначе им нельзя было бы поделиться.
 *
 * ⚠️ На вкладке онгоингов умолчание другое (`undefined`): там смысл ровно
 * обратный — увидеть, что сейчас выходит, включая то, чего у себя ещё нет.
 */
export const DEFAULT_LOCAL: Filters['local'] = 'only'

/** Значение в адресе, означающее «фильтра нет». */
const LOCAL_ANY = 'all'

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

function readSort(raw: string | null): SortField {
  return (SORT_FIELDS as string[]).includes(raw ?? '') ? (raw as SortField) : DEFAULT_SORT
}

function readList(raw: string | null): string[] | undefined {
  if (!raw) return undefined
  const items = raw.split(',').filter(Boolean)
  return items.length > 0 ? items : undefined
}

export function parseCatalogState(
  params: URLSearchParams,
  defaultLocal: Filters['local'] = DEFAULT_LOCAL,
): CatalogState {
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
      // сломал бы запрос к API, а не показал пустой список. Отсутствие
      // параметра означает умолчание, а не «фильтра нет»: «нет» пишется
      // явным `all`.
      local:
        local === 'only' || local === 'missing'
          ? local
          : local === LOCAL_ANY
            ? undefined
            : defaultLocal,
      mappingProblem: params.get('problem') === '1' || undefined,
      has: readList(params.get('has')),
      hasAll: params.get('hasAll') === '1' || undefined,
      // По списку, а не на слово — по той же причине, что и `local` ниже:
      // чужой набор букв уезжал в запрос, и сервер отвечал 422. Ссылка с
      // испорченным хвостом ломала каталог вместо того, чтобы открыть его
      // сортировкой по умолчанию.
      sort: readSort(params.get('sort')),
      sortDir: params.get('dir') === 'asc' ? 'asc' : DEFAULT_SORT_DIR,
    },
  }
}

export function serializeCatalogState(
  state: CatalogState,
  defaultLocal: Filters['local'] = DEFAULT_LOCAL,
): URLSearchParams {
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
  // Умолчание в адрес не пишем, а вот отказ от него — пишем: без явного `all`
  // ссылка «показать весь каталог» при следующем открытии снова отфильтровалась
  // бы по библиотеке.
  if (filters.local !== defaultLocal) params.set('local', filters.local ?? LOCAL_ANY)
  if (filters.mappingProblem) params.set('problem', '1')
  if (filters.has?.length) params.set('has', filters.has.join(','))
  if (filters.hasAll) params.set('hasAll', '1')

  // Умолчания в адрес не пишем — см. докстроку модуля.
  if (filters.sort && filters.sort !== DEFAULT_SORT) params.set('sort', filters.sort)
  if (filters.sortDir && filters.sortDir !== DEFAULT_SORT_DIR) params.set('dir', filters.sortDir)
  if (page > 1) params.set('page', String(page))

  return params
}

export function useCatalogUrlState(defaultLocal: Filters['local'] = DEFAULT_LOCAL) {
  const [params, setParams] = useSearchParams()

  const state = useMemo(
    () => parseCatalogState(params, defaultLocal),
    [params, defaultLocal],
  )

  const update = useCallback(
    (patch: Partial<CatalogState>) => {
      setParams(
        (current) =>
          serializeCatalogState(
            { ...parseCatalogState(current, defaultLocal), ...patch },
            defaultLocal,
          ),
        // Замена, а не новая запись истории: см. докстроку модуля.
        { replace: true },
      )
    },
    [setParams, defaultLocal],
  )

  return { state, update }
}
