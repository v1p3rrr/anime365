import { Alert, Group, Loader, Stack, Text, Title } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'

import { catalogApi, type CatalogFilters as Filters } from '../api/client'
import { CatalogFilters } from '../components/CatalogFilters'
import { CatalogGrid } from '../components/CatalogGrid'
import { SyncIndicator } from '../components/SyncIndicator'
import { useVirtualCatalog } from '../hooks/useVirtualCatalog'

interface Props {
  onlyAiring?: boolean
}

export function CatalogPage({ onlyAiring = false }: Props) {
  const [searchInput, setSearchInput] = useState('')
  // Каждый символ не должен порождать запрос: список id считается по всей выборке.
  const [debouncedSearch] = useDebouncedValue(searchInput, 350)

  const [filters, setFilters] = useState<Filters>({ sort: 'id' })

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

  return (
    <Stack gap="sm">
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
        <SyncIndicator />
      </Group>

      <CatalogFilters
        filters={filters}
        genres={genresQuery.data ?? []}
        onChange={setFilters}
        searchInput={searchInput}
        onSearchInput={setSearchInput}
      />

      {idsQuery.isError && (
        <Alert color="red" title="Не удалось загрузить каталог">
          {(idsQuery.error as Error).message}
        </Alert>
      )}

      {idsQuery.isSuccess && ids.length === 0 && (
        <Alert color="gray" title="Ничего не найдено">
          {debouncedSearch
            ? 'Попробуйте изменить запрос. Если тайтла нет в локальном кэше, поищите его на сайте — кнопка появится на этапе 3.'
            : 'Каталог пуст. Запустите синхронизацию — она занимает около минуты.'}
        </Alert>
      )}

      {ids.length > 0 && (
        <CatalogGrid ids={ids} cards={cards} onRangeChange={handleRangeChange} />
      )}
    </Stack>
  )
}
