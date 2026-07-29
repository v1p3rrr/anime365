import {
  ActionIcon,
  Button,
  Chip,
  Collapse,
  Group,
  MultiSelect,
  NumberInput,
  Select,
  Switch,
  TextInput,
  Tooltip,
} from '@mantine/core'
import { IconFilter, IconSearch, IconX } from '@tabler/icons-react'
import { useState } from 'react'

import type { CatalogFilters as Filters, GenreItem } from '../api/client'

interface Props {
  filters: Filters
  genres: GenreItem[]
  onChange: (next: Filters) => void
  searchInput: string
  onSearchInput: (value: string) => void
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

const SORTS = [
  { value: 'id', label: 'Сначала новые' },
  { value: 'year', label: 'По году' },
  { value: 'score', label: 'По оценке' },
  { value: 'title', label: 'По названию' },
]

export function CatalogFilters({ filters, genres, onChange, searchInput, onSearchInput }: Props) {
  const [expanded, setExpanded] = useState(false)

  const patch = (part: Partial<Filters>) => onChange({ ...filters, ...part })

  const activeCount =
    (filters.genres?.length ?? 0) +
    (filters.type ? 1 : 0) +
    (filters.yearFrom !== undefined ? 1 : 0) +
    (filters.yearTo !== undefined ? 1 : 0)

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

        <Tooltip label="Фильтры">
          <Button
            variant={activeCount > 0 ? 'filled' : 'default'}
            leftSection={<IconFilter size={16} />}
            onClick={() => setExpanded((v) => !v)}
          >
            {activeCount > 0 ? activeCount : 'Фильтры'}
          </Button>
        </Tooltip>

        <Select
          w={170}
          data={SORTS}
          value={filters.sort ?? 'id'}
          onChange={(v) => patch({ sort: (v as Filters['sort']) ?? 'id' })}
          allowDeselect={false}
        />
      </Group>

      <Collapse in={expanded}>
        <Group gap="xs" align="flex-end" mt="xs" wrap="wrap">
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

        <Group gap="xs" mt="xs">
          <Chip
            checked={filters.isAiring === true}
            onChange={(checked) => patch({ isAiring: checked ? true : undefined })}
            size="xs"
          >
            Только онгоинги
          </Chip>
          {activeCount > 0 && (
            <Button
              size="xs"
              variant="subtle"
              color="gray"
              onClick={() =>
                onChange({ sort: filters.sort, query: filters.query, isAiring: filters.isAiring })
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
