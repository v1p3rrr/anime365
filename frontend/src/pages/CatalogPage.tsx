import { Alert, Group, Loader, Pagination, Stack, Text, Title } from '@mantine/core'
import { useDebouncedValue, useDisclosure } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { catalogApi, type CatalogFilters as Filters } from '../api/client'
import { type BadgeKey } from '../components/CatalogCard'
import { CatalogFilters } from '../components/CatalogFilters'
import { CatalogGrid } from '../components/CatalogGrid'
import { ErrorState } from '../components/ErrorState'
import { OnlineSearchModal } from '../components/OnlineSearchModal'
import { SyncIndicator } from '../components/SyncIndicator'
import { useCatalogIds } from '../hooks/useCatalogIds'
import { useCatalogUrlState } from '../hooks/useCatalogUrlState'
import { useVirtualCatalog } from '../hooks/useVirtualCatalog'
import { usePreferences } from '../stores/preferences'
import { useT } from '../i18n'

interface Props {
  onlyAiring?: boolean
}

export function CatalogPage({ onlyAiring = false }: Props) {
  // Состояние каталога живёт в адресе страницы: иначе «назад» со страницы
  // тайтла возвращает в пустой каталог, а ссылкой на подборку не поделиться.
  // По умолчанию сортируем по оценке: в каталоге 29 тысяч тайтлов, и первым
  // экраном полезнее видеть лучшее, а не самое свежедобавленное.
  const { state, update } = useCatalogUrlState()
  const { filters, page } = state

  // Поле ввода — отдельным состоянием, чтобы адрес не переписывался на каждую
  // букву. В адрес уходит уже отлежавшееся значение.
  const [searchInput, setSearchInput] = useState(state.search)
  // Каждый символ не должен порождать запрос: список id считается по всей выборке.
  const [debouncedSearch] = useDebouncedValue(searchInput, 350)

  useEffect(() => {
    if (debouncedSearch !== state.search) update({ search: debouncedSearch, page: 1 })
  }, [debouncedSearch, state.search, update])

  const setFilters = useCallback(
    (next: Filters) => {
      // Смена фильтра начинает выборку заново: страница 7 в новой выборке —
      // это другие тайтлы, и оставлять её значило бы показать пустоту.
      update({ filters: next, page: 1 })
    },
    [update],
  )
  const setPage = useCallback((next: number) => update({ page: next }), [update])

  const [onlineOpened, { open: openOnline, close: closeOnline }] = useDisclosure(false)
  const { prefs } = usePreferences()
  const { t, locale } = useT()

  // Значки наличия идут от тех же типов переводов, что и кнопки загрузки:
  // значок субтитров, для которых нельзя ничего скачать, обещал бы несбыточное.
  // Видео здесь всегда — это не тип перевода, а сам файл.
  const visibleBadges: BadgeKey[] = [
    'video',
    ...(['sub_ja', 'sub_ru', 'sub_en'] as const).filter((type) => prefs.types.includes(type)),
  ]

  const effectiveFilters = useMemo<Filters>(
    () => ({
      ...filters,
      query: debouncedSearch || undefined,
      isAiring: onlyAiring ? true : filters.isAiring,
    }),
    [filters, debouncedSearch, onlyAiring],
  )

  const genresQuery = useQuery({
    queryKey: ['genres'],
    queryFn: catalogApi.genres,
    staleTime: 30 * 60 * 1000,
  })

  const catalog = useCatalogIds({
    filters: effectiveFilters,
    mode: prefs.paging,
    pageSize: prefs.pageSize,
    page,
  })

  const ids = catalog.ids
  const { cards, ensureRange } = useVirtualCatalog(ids)
  const pageCount = Math.max(1, Math.ceil(catalog.total / prefs.pageSize))

  const handleRangeChange = useCallback(
    (start: number, end: number) => ensureRange(start, end),
    [ensureRange],
  )

  // Тот же запрос, что у индикатора синхронизации, — react-query отдаёт его из кэша.
  const statusQuery = useQuery({
    queryKey: ['catalog-status'],
    queryFn: catalogApi.status,
    staleTime: 30_000,
  })

  // «Ничего не найдено» имеет три разные причины, и советы к ним противоположные.
  // Раньше здесь всегда предлагалась синхронизация — при полном каталоге и
  // слишком узком фильтре это отправляло пользователя не туда.
  const catalogIsEmpty = (statusQuery.data?.total_series ?? 0) === 0
  const hasFilters =
    Boolean(
      filters.type ||
        filters.season ||
        filters.genres?.length ||
        filters.local ||
        filters.mappingProblem,
    ) ||
    filters.yearFrom !== undefined ||
    filters.yearTo !== undefined ||
    filters.scoreFrom !== undefined

  const emptyReason = catalogIsEmpty
    ? t('catalog.emptyNotSynced')
    : debouncedSearch
      ? t('catalog.emptySearch')
      : hasFilters
        ? t('catalog.emptyFilters')
        : t('catalog.emptyOther')

  return (
    // Колонка на всю доступную высоту: сетка внутри растягивается сама,
    // поэтому подстраивается под любую высоту шапки и блока фильтров.
    <Stack gap="sm" h="100%" style={{ minHeight: 0 }}>
      <Group justify="space-between" align="center">
        <Group gap="sm" align="baseline">
          <Title order={3}>{onlyAiring ? t('catalog.ongoing') : t('catalog.title')}</Title>
          {!catalog.isLoading && (
            <Text c="dimmed" size="sm">
              {t('catalog.found', { count: catalog.total.toLocaleString(locale) })}
            </Text>
          )}
          {(catalog.isLoading || catalog.isLoadingMore) && <Loader size="xs" />}
        </Group>
        <SyncIndicator filteredTotal={catalog.total} />
      </Group>

      <CatalogFilters
        filters={filters}
        genres={genresQuery.data ?? []}
        onChange={setFilters}
        searchInput={searchInput}
        onSearchInput={setSearchInput}
        hideAiringToggle={onlyAiring}
        onSearchOnline={openOnline}
      />

      <OnlineSearchModal
        query={searchInput.trim()}
        opened={onlineOpened}
        onClose={closeOnline}
      />

      {catalog.isError && (
        <ErrorState
          error={catalog.error}
          title={t('catalog.loadFailed')}
          onRetry={catalog.refetch}
        />
      )}

      {genresQuery.isError && (
        <ErrorState compact error={genresQuery.error} onRetry={() => genresQuery.refetch()} />
      )}

      {!catalog.isLoading && !catalog.isError && ids.length === 0 && (
        <Alert color="gray" title={t('common.nothingFound')} style={{ flexShrink: 0 }}>
          {emptyReason}
        </Alert>
      )}

      {ids.length > 0 && (
        <CatalogGrid
          ids={ids}
          cards={cards}
          onRangeChange={handleRangeChange}
          visibleBadges={visibleBadges}
          hasMore={catalog.hasMore}
          onLoadMore={catalog.loadMore}
          // В режиме подгрузки прокрутку не трогаем: список растёт снизу,
          // и возврат наверх отменял бы то, ради чего человек и крутил.
          scrollResetKey={prefs.paging === 'pages' ? page : undefined}
        />
      )}

      {/* Номера страниц только в своём режиме: при подгрузке прокруткой они
          противоречили бы происходящему — список уже вырос. */}
      {prefs.paging === 'pages' && pageCount > 1 && (
        <Group justify="center" style={{ flexShrink: 0 }}>
          <Pagination
            total={pageCount}
            value={page}
            onChange={setPage}
            // 1 … 98 99 [100] 101 102 … 300: две соседние с каждой стороны плюс
            // первая и последняя. Больше не помещается на 375 пикселях.
            siblings={2}
            boundaries={1}
            size="sm"
          />
        </Group>
      )}
    </Stack>
  )
}
