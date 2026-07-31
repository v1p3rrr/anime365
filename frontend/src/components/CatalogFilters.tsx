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
  SORT_FIELDS,
  type CatalogFilters as Filters,
  type GenreItem,
  type SortField,
} from '../api/client'
import { useT, type TranslationKey } from '../i18n'
import { LangTag } from './Flag'
import { LANG_COLORS } from '../api/title'
import { splitType, TRANSLATION_TYPES } from '../stores/preferences'

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

/** Типы приходят с сайта как есть; подписи к ним даёт TYPE_LABELS в словаре. */
const TYPE_VALUES = ['', 'tv', 'movie', 'ova', 'ona', 'special', 'tv_special', 'music'] as const

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
  const { t } = useT()

  // Значения типов и сезонов приходят с сайта и уходят в API как есть —
  // переводим только подписи.
  const typeLabel = (value: string) => t(`types.${value}` as TranslationKey)

  const patch = (part: Partial<Filters>) => onChange({ ...filters, ...part })

  const sort = filters.sort ?? 'id'
  const sortDir = filters.sortDir ?? 'desc'

  // Значение селектора кодирует и поле, и направление: повторный выбор того же поля
  // переворачивает порядок, а подпись сразу говорит, какой он сейчас.
  const sortOptions = SORT_FIELDS.flatMap((field) => [
    { value: `${field}:desc`, label: t(`sort.${field}.desc` as TranslationKey) },
    { value: `${field}:asc`, label: t(`sort.${field}.asc` as TranslationKey) },
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
      <Tooltip label={sortDir === 'desc' ? t('catalog.sortDesc') : t('catalog.sortAsc')}>
        <ActionIcon variant="default" onClick={toggleSortDir} aria-label={t('catalog.sortOrder')}>
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
    (filters.local ? 1 : 0) +
    (filters.mappingProblem ? 1 : 0) +
    (filters.has?.length ? 1 : 0)

  return (
    <>
      <Group gap="xs" wrap="nowrap">
        <TextInput
          flex={1}
          placeholder={t('catalog.search')}
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
          <Tooltip label={t('catalog.searchOnline')}>
            <ActionIcon
              variant="default"
              size="input-sm"
              onClick={onSearchOnline}
              aria-label={t('catalog.searchOnline')}
            >
              <IconWorldSearch size={17} />
            </ActionIcon>
          </Tooltip>
        )}

        <Tooltip label={t('catalog.filters')}>
          <Button
            variant={activeCount > 0 ? 'filled' : 'default'}
            leftSection={<IconFilter size={16} />}
            onClick={() => setExpanded((v) => !v)}
          >
            {activeCount > 0 ? activeCount : t('catalog.filters')}
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
              {t('catalog.sortOrder')}
            </Text>
            {sortControl({})}
          </Stack>

          <Select
            w={160}
            label={t('catalog.type')}
            data={TYPE_VALUES.map((v) => ({ value: v, label: typeLabel(v) }))}
            value={filters.type ?? ''}
            onChange={(v) => patch({ type: v || undefined })}
            allowDeselect={false}
          />

          <NumberInput
            w={110}
            label={t('catalog.yearFrom')}
            min={1900}
            max={2100}
            value={filters.yearFrom ?? ''}
            onChange={(v) => patch({ yearFrom: v === '' ? undefined : Number(v) })}
          />
          <NumberInput
            w={110}
            label={t('catalog.yearTo')}
            min={1900}
            max={2100}
            value={filters.yearTo ?? ''}
            onChange={(v) => patch({ yearTo: v === '' ? undefined : Number(v) })}
          />

          {/* Сезон отдельно от года: Anime365 хранит их одной строкой «Осень 2015»,
              мы разбираем её при синхронизации, чтобы можно было искать «все зимние». */}
          <Select
            w={130}
            label={t('catalog.season')}
            placeholder={t('catalog.anySeason')}
            clearable
            data={SEASONS.map((value) => ({ value, label: t(`seasons.${value}` as TranslationKey) }))}
            value={filters.season ?? null}
            onChange={(v) => patch({ season: v ?? undefined })}
          />

          <MultiSelect
            flex={1}
            miw={240}
            label={t('catalog.genres')}
            placeholder={t('catalog.anyGenres')}
            searchable
            clearable
            data={genres.map((g) => ({ value: String(g.id), label: `${g.title} (${g.count})` }))}
            value={(filters.genres ?? []).map(String)}
            onChange={(values) => patch({ genres: values.map(Number) })}
          />

          <Switch
            label={t('catalog.allGenres')}
            description={t('catalog.allGenresHint')}
            checked={filters.genresAnd ?? false}
            onChange={(e) => patch({ genresAnd: e.currentTarget.checked })}
          />
        </Group>

        {/* Оценка MAL — шкала 0..10 с шагом 0.5. Ползунок вместо ввода числа:
            точное значение тут не нужно, а промахнуться мимо диапазона нельзя. */}
        <Stack gap={2} mt="md" maw={420}>
          <Group justify="space-between">
            <Text size="sm" fw={500}>
              {t('catalog.scoreFrom')}
            </Text>
            <Text size="sm" c={filters.scoreFrom ? undefined : 'dimmed'}>
              {filters.scoreFrom ? filters.scoreFrom.toFixed(1) : t('catalog.scoreAny')}
            </Text>
          </Group>
          <Slider
            min={0}
            max={10}
            step={0.5}
            value={filters.scoreFrom ?? 0}
            onChange={(v) => patch({ scoreFrom: v === 0 ? undefined : v })}
            marks={[
              { value: 0, label: t('catalog.scoreAny') },
              { value: 5, label: '5' },
              { value: 7, label: '7' },
              { value: 8, label: '8' },
              { value: 10, label: '10' },
            ]}
            mb="lg"
          />
          {filters.scoreFrom !== undefined && (
            <Text size="xs" c="dimmed">
              {t('catalog.scoreHint')}
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
              {t('catalog.onlyOngoing')}
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
            {t('catalog.inLibrary')}
          </Chip>
          <Chip
            checked={filters.local === 'missing'}
            onChange={(checked) => patch({ local: checked ? 'missing' : undefined })}
            size="xs"
            color="orange"
          >
            {t('catalog.notInLibrary')}
          </Chip>

          {/* Очередь на ручную проверку: привязка нашлась нечётким совпадением
              и ещё не подтверждена. Такую ошибку лучше увидеть здесь, чем
              после того, как файлы уедут не в ту папку. */}
          <Chip
            checked={filters.mappingProblem === true}
            onChange={(checked) => patch({ mappingProblem: checked ? true : undefined })}
            size="xs"
            color="yellow"
          >
            {t('catalog.mappingProblem')}
          </Chip>
        </Group>

        {/* Наличие перевода НА САЙТЕ, а не в библиотеке. Данные собирает фоновая
            джоба, поэтому тайтлы, до которых она не дошла, под фильтр не
            подпадают: утверждать, что перевода нет, мы не вправе — мы не смотрели. */}
        <Text size="sm" fw={500} mt="md" mb={4}>
          {t('catalog.hasTranslation')}
        </Text>
        <Group gap={6} wrap="wrap">
          {TRANSLATION_TYPES.map((type) => {
            const [kind, lang] = splitType(type)
            const active = filters.has ?? []
            return (
              <Chip
                key={type}
                size="xs"
                color={LANG_COLORS[lang]}
                checked={active.includes(type)}
                onChange={(checked) =>
                  patch({
                    has: checked
                      ? [...active, type]
                      : active.filter((value) => value !== type),
                  })
                }
              >
                {t(`kind.${kind}` as TranslationKey)} · <LangTag lang={lang} />
              </Chip>
            )
          })}
          {(filters.has?.length ?? 0) > 1 && (
            <Chip
              size="xs"
              color="gray"
              checked={filters.hasAll === true}
              onChange={(checked) => patch({ hasAll: checked ? true : undefined })}
            >
              {t('catalog.hasAll')}
            </Chip>
          )}
        </Group>

        <Group gap="xs" mt="md">
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
              {t('catalog.resetFilters')}
            </Button>
          )}
        </Group>
      </Collapse>
    </>
  )
}
