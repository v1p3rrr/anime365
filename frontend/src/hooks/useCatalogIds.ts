/**
 * Идентификаторы каталога порциями.
 *
 * Раньше список приходил целиком — 29 тысяч чисел, около 200 КБ на каждую смену
 * фильтра. Виртуальному списку этого хватало, но разбор такого массива заметен
 * на слабом устройстве, а нужен из него один экран.
 *
 * Режима два, и они устроены по-разному не случайно:
 *
 * `scroll` копит порции одну за другой — список растёт по мере прокрутки, и
 * `total` нужен только чтобы понять, когда останавливаться.
 *
 * `pages` держит ровно одну порцию и заменяет её при переходе. Здесь `total`
 * важнее: по нему считается количество страниц.
 */

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { catalogApi, type CatalogFilters } from '../api/client'
import type { PagingMode } from '../stores/preferences'

interface Options {
  filters: CatalogFilters
  mode: PagingMode
  pageSize: number
  /** Номер страницы с единицы. Используется только в режиме `pages`. */
  page: number
}

export interface CatalogIdsState {
  ids: number[]
  total: number
  isLoading: boolean
  isError: boolean
  error: unknown
  refetch: () => void
  /** Есть ли что подгружать дальше. Только для режима `scroll`. */
  hasMore: boolean
  /** Идёт ли догрузка очередной порции. */
  isLoadingMore: boolean
  loadMore: () => void
}

export function useCatalogIds({ filters, mode, pageSize, page }: Options): CatalogIdsState {
  const scroll = useInfiniteQuery({
    queryKey: ['catalog-ids', 'scroll', filters, pageSize],
    queryFn: ({ pageParam }) =>
      catalogApi.ids({ ...filters, offset: pageParam as number, limit: pageSize }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((sum, chunk) => sum + chunk.ids.length, 0)
      // Пустая порция означает конец, даже если total почему-то больше:
      // иначе подгрузка зациклится на последней странице.
      if (last.ids.length === 0 || loaded >= last.total) return undefined
      return loaded
    },
    enabled: mode === 'scroll',
  })

  const paged = useQuery({
    queryKey: ['catalog-ids', 'page', filters, pageSize, page],
    queryFn: () => catalogApi.ids({ ...filters, offset: (page - 1) * pageSize, limit: pageSize }),
    enabled: mode === 'pages',
    // Прошлая страница остаётся на экране, пока грузится новая: иначе при
    // каждом переходе сетка схлопывается и страница прыгает вверх.
    placeholderData: (previous) => previous,
  })

  const ids = useMemo(() => {
    if (mode === 'pages') return paged.data?.ids ?? []
    return scroll.data?.pages.flatMap((chunk) => chunk.ids) ?? []
  }, [mode, paged.data, scroll.data])

  if (mode === 'pages') {
    return {
      ids,
      total: paged.data?.total ?? 0,
      isLoading: paged.isLoading,
      isError: paged.isError,
      error: paged.error,
      refetch: () => void paged.refetch(),
      hasMore: false,
      isLoadingMore: false,
      loadMore: () => {},
    }
  }

  return {
    ids,
    total: scroll.data?.pages[0]?.total ?? 0,
    isLoading: scroll.isLoading,
    isError: scroll.isError,
    error: scroll.error,
    refetch: () => void scroll.refetch(),
    hasMore: scroll.hasNextPage,
    isLoadingMore: scroll.isFetchingNextPage,
    loadMore: () => {
      // Повторный вызов во время загрузки TanStack игнорирует сам, но проверка
      // дешевле, чем полагаться на это при каждом кадре прокрутки.
      if (scroll.hasNextPage && !scroll.isFetchingNextPage) void scroll.fetchNextPage()
    },
  }
}
