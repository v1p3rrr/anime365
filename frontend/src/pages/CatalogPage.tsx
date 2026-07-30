import { Alert, Group, Loader, Stack, Text, Title } from '@mantine/core'
import { useDebouncedValue, useDisclosure } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'

import { catalogApi, type CatalogFilters as Filters } from '../api/client'
import { CatalogFilters } from '../components/CatalogFilters'
import { CatalogGrid } from '../components/CatalogGrid'
import { ErrorState } from '../components/ErrorState'
import { OnlineSearchModal } from '../components/OnlineSearchModal'
import { SyncIndicator } from '../components/SyncIndicator'
import { useVirtualCatalog } from '../hooks/useVirtualCatalog'

interface Props {
  onlyAiring?: boolean
}

export function CatalogPage({ onlyAiring = false }: Props) {
  const [searchInput, setSearchInput] = useState('')
  // Каждый символ не должен порождать запрос: список id считается по всей выборке.
  const [debouncedSearch] = useDebouncedValue(searchInput, 350)

  // По умолчанию сортируем по оценке: в каталоге 29 тысяч тайтлов, и первым
  // экраном полезнее видеть лучшее, а не самое свежедобавленное.
  const [filters, setFilters] = useState<Filters>({ sort: 'score', sortDir: 'desc' })
  const [onlineOpened, { open: openOnline, close: closeOnline }] = useDisclosure(false)

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

  const idsQuery = useQuery({
    queryKey: ['catalog-ids', effectiveFilters],
    queryFn: () => catalogApi.ids(effectiveFilters),
  })

  const ids = useMemo(() => idsQuery.data?.ids ?? [], [idsQuery.data])
  const { cards, ensureRange } = useVirtualCatalog(ids)

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
    ? 'Каталог ещё не синхронизирован. Нажмите кнопку обновления справа сверху — это занимает около минуты.'
    : debouncedSearch
      ? 'Попробуйте изменить запрос или поискать на сайте — кнопка справа от поля поиска. Локальный кэш обновляется раз в час, совсем свежих тайтлов в нём может не быть.'
      : hasFilters
        ? 'Под выбранные фильтры не подошёл ни один тайтл. Ослабьте условия или сбросьте фильтры.'
        : 'Под текущие условия не подошёл ни один тайтл.'

  return (
    // Колонка на всю доступную высоту: сетка внутри растягивается сама,
    // поэтому подстраивается под любую высоту шапки и блока фильтров.
    <Stack gap="sm" h="100%" style={{ minHeight: 0 }}>
      <Group justify="space-between" align="center">
        <Group gap="sm" align="baseline">
          <Title order={3}>{onlyAiring ? 'Онгоинги' : 'Каталог'}</Title>
          {idsQuery.data && (
            <Text c="dimmed" size="sm">
              найдено: {idsQuery.data.total.toLocaleString('ru')}
            </Text>
          )}
          {idsQuery.isFetching && <Loader size="xs" />}
        </Group>
        <SyncIndicator filteredTotal={idsQuery.data?.total} />
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

      {idsQuery.isError && (
        <ErrorState
          error={idsQuery.error}
          title="Не удалось загрузить каталог"
          onRetry={() => idsQuery.refetch()}
        />
      )}

      {genresQuery.isError && (
        <ErrorState compact error={genresQuery.error} onRetry={() => genresQuery.refetch()} />
      )}

      {idsQuery.isSuccess && ids.length === 0 && (
        <Alert color="gray" title="Ничего не найдено" style={{ flexShrink: 0 }}>
          {emptyReason}
        </Alert>
      )}

      {ids.length > 0 && (
        <CatalogGrid ids={ids} cards={cards} onRangeChange={handleRangeChange} />
      )}
    </Stack>
  )
}
