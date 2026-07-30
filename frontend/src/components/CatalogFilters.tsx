import {
  ActionIcon,
  Button,
  Chip,
  Collapse,
  Group,
  MultiSelect,
  NumberInput,
  Select,
  Slider,
  Stack,
  Switch,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core'
import {
  IconFilter,
  IconSearch,
  IconSortAscending,
  IconSortDescending,
  IconWorldSearch,
  IconX,
} from '@tabler/icons-react'
import { useState } from 'react'

import {
  SEASONS,
  SORT_LABELS,
  type CatalogFilters as Filters,
  type GenreItem,
  type SortField,
} from '../api/client'

interface Props {
  filters: Filters
  genres: GenreItem[]
  onChange: (next: Filters) => void
  searchInput: string
  onSearchInput: (value: string) => void
  /** На вкладке онгоингов фильтр «только онгоинги» бессмыслен — он там уже применён. */
  hideAiringToggle?: boolean
  onSearchOnline: () => void
}

const TYPES = [
  { value: '', label: 'Любой тип' },
  { value: 'tv', label: 'ТВ сериал' },
  { value: 'movie', label: 'Фильм' },
  { value: 'ova', label: 'OVA' },
  { value: 'ona', label: 'ONA' },
  { value: 'special', label: 'Спешл' },
  { value: 'tv_special', label: 'ТВ спешл' },
  { value: 'music', label: 'Клип' },
]

export function CatalogFilters({
  filters,
  genres,
  onChange,
  searchInput,
  onSearchInput,
  hideAiringToggle = false,
  onSearchOnline,
}: Props) {
  const [expanded, setExpanded] = useState(false)

  const patch = (part: Partial<Filters>) => onChange({ ...filters, ...part })

  const sort = filters.sort ?? 'id'
  const sortDir = filters.sortDir ?? 'desc'

  // Значение селектора кодирует и поле, и направление: повторный выбор того же поля
  // переворачивает порядок, а подпись сразу говорит, какой он сейчас.
  const sortOptions = (Object.keys(SORT_LABELS) as SortField[]).flatMap((field) => [
    { value: `${field}:desc`, label: SORT_LABELS[field].desc },
    { value: `${field}:asc`, label: SORT_LABELS[field].asc },
  ])

  const applySort = (raw: string | null) => {
    if (!raw) return
    const [field, dir] = raw.split(':') as [SortField, 'asc' | 'desc']
    patch({ sort: field, sortDir: dir })
  }

  const toggleSortDir = () => patch({ sortDir: sortDir === 'desc' ? 'asc' : 'desc' })

  const sortControl = (extraProps: Record<string, unknown>) => (
    <Group gap={4} wrap="nowrap" {...extraProps}>
      <Select
        w={168}
        data={sortOptions}
        value={`${sort}:${sortDir}`}
        onChange={applySort}
        allowDeselect={false}
      />
      <Tooltip label={sortDir === 'desc' ? 'Сейчас по убыванию' : 'Сейчас по возрастанию'}>
        <ActionIcon variant="default" onClick={toggleSortDir} aria-label="Изменить порядок">
          {sortDir === 'desc' ? <IconSortDescending size={16} /> : <IconSortAscending size={16} />}
        </ActionIcon>
      </Tooltip>
    </Group>
  )

  const activeCount =
    (filters.genres?.length ?? 0) +
    (filters.type ? 1 : 0) +
    (filters.yearFrom !== undefined ? 1 : 0) +
    (filters.yearTo !== undefined ? 1 : 0) +
    (filters.season ? 1 : 0) +
    (filters.scoreFrom !== undefined ? 1 : 0) +
    (filters.local ? 1 : 0)

  return (
    <>
      <Group gap="xs" wrap="nowrap">
        <TextInput
          flex={1}
          placeholder="Поиск по названию — русскому, romaji или японскому"
          leftSection={<IconSearch size={16} />}
          value={searchInput}
          onChange={(e) => onSearchInput(e.currentTarget.value)}
          rightSection={
            searchInput ? (
              <ActionIcon variant="subtle" color="gray" onClick={() => onSearchInput('')}>
                <IconX size={14} />
              </ActionIcon>
            ) : null
          }
        />

        {/* Появляется только при непустом запросе: без него искать нечего,
            а каждый вызов — это запрос на сайт. */}
        {searchInput.trim().length > 0 && (
          <Tooltip label="Искать на сайте — ловит то, чего ещё нет в локальном кэше">
            <ActionIcon
              variant="default"
              size="input-sm"
              onClick={onSearchOnline}
              aria-label="Искать на сайте"
            >
              <IconWorldSearch size={17} />
            </ActionIcon>
          </Tooltip>
        )}

        <Tooltip label="Фильтры">
          <Button
            variant={activeCount > 0 ? 'filled' : 'default'}
            leftSection={<IconFilter size={16} />}
            onClick={() => setExpanded((v) => !v)}
          >
            {activeCount > 0 ? activeCount : 'Фильтры'}
          </Button>
        </Tooltip>

        {/* На узком экране селектор сортировки съедает строку поиска — прячем его
            туда же, под кнопку «Фильтры». */}
        {sortControl({ visibleFrom: 'sm' })}
      </Group>

      <Collapse in={expanded}>
        <Group gap="xs" align="flex-end" mt="xs" wrap="wrap">
          <Stack gap={2} hiddenFrom="sm">
            <Text size="sm" fw={500}>
              Сортировка
            </Text>
            {sortControl({})}
          </Stack>

          <Select
            w={160}
            label="Тип"
            data={TYPES}
            value={filters.type ?? ''}
            onChange={(v) => patch({ type: v || undefined })}
            allowDeselect={false}
          />

          <NumberInput
            w={110}
            label="Год от"
            min={1900}
            max={2100}
            value={filters.yearFrom ?? ''}
            onChange={(v) => patch({ yearFrom: v === '' ? undefined : Number(v) })}
          />
          <NumberInput
            w={110}
            label="Год до"
            min={1900}
            max={2100}
            value={filters.yearTo ?? ''}
            onChange={(v) => patch({ yearTo: v === '' ? undefined : Number(v) })}
          />

          {/* Сезон отдельно от года: Anime365 хранит их одной строкой «Осень 2015»,
              мы разбираем её при синхронизации, чтобы можно было искать «все зимние». */}
          <Select
            w={130}
            label="Сезон"
            placeholder="Любой"
            clearable
            data={[...SEASONS]}
            value={filters.season ?? null}
            onChange={(v) => patch({ season: v ?? undefined })}
          />

          <MultiSelect
            flex={1}
            miw={240}
            label="Жанры"
            placeholder="Любые"
            searchable
            clearable
            data={genres.map((g) => ({ value: String(g.id), label: `${g.title} (${g.count})` }))}
            value={(filters.genres ?? []).map(String)}
            onChange={(values) => patch({ genres: values.map(Number) })}
          />

          <Switch
            label="Все жанры сразу"
            description="Иначе достаточно любого"
            checked={filters.genresAnd ?? false}
            onChange={(e) => patch({ genresAnd: e.currentTarget.checked })}
          />
        </Group>

        {/* Оценка MAL — шкала 0..10 с шагом 0.5. Ползунок вместо ввода числа:
            точное значение тут не нужно, а промахнуться мимо диапазона нельзя. */}
        <Stack gap={2} mt="md" maw={420}>
          <Group justify="space-between">
            <Text size="sm" fw={500}>
              Оценка MAL не ниже
            </Text>
            <Text size="sm" c={filters.scoreFrom ? undefined : 'dimmed'}>
              {filters.scoreFrom ? filters.scoreFrom.toFixed(1) : 'любая'}
            </Text>
          </Group>
          <Slider
            min={0}
            max={10}
            step={0.5}
            value={filters.scoreFrom ?? 0}
            onChange={(v) => patch({ scoreFrom: v === 0 ? undefined : v })}
            marks={[
              { value: 0, label: 'любая' },
              { value: 5, label: '5' },
              { value: 7, label: '7' },
              { value: 8, label: '8' },
              { value: 10, label: '10' },
            ]}
            mb="lg"
          />
          {filters.scoreFrom !== undefined && (
            <Text size="xs" c="dimmed">
              Тайтлы без оценки при этом фильтре скрыты.
            </Text>
          )}
        </Stack>

        <Group gap="xs" mt="xs">
          {!hideAiringToggle && (
            <Chip
              checked={filters.isAiring === true}
              onChange={(checked) => patch({ isAiring: checked ? true : undefined })}
              size="xs"
            >
              Только онгоинги
            </Chip>
          )}

          {/* Две взаимоисключающие чипсы вместо одной трёхпозиционной: «что уже
              есть» и «чего не хватает» — разные задачи, и переключаться между
              ними приходится часто. */}
          <Chip
            checked={filters.local === 'only'}
            onChange={(checked) => patch({ local: checked ? 'only' : undefined })}
            size="xs"
            color="teal"
          >
            Есть в библиотеке
          </Chip>
          <Chip
            checked={filters.local === 'missing'}
            onChange={(checked) => patch({ local: checked ? 'missing' : undefined })}
            size="xs"
            color="orange"
          >
            Нет в библиотеке
          </Chip>
          {activeCount > 0 && (
            <Button
              size="xs"
              variant="subtle"
              color="gray"
              onClick={() =>
                onChange({
                  sort: filters.sort,
                  sortDir: filters.sortDir,
                  query: filters.query,
                  isAiring: filters.isAiring,
                })
              }
            >
              Сбросить фильтры
            </Button>
          )}
        </Group>
      </Collapse>
    </>
  )
}
