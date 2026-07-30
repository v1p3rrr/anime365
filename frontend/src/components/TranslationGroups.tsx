import { Accordion, Badge, Button, Group, Loader, Stack, Table, Text, Tooltip } from '@mantine/core'
import { IconPlayerPlay } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import {
  KIND_ICONS,
  KIND_LABELS,
  LANG_COLORS,
  LANG_LABELS,
  titleApi,
  type AuthorGroup,
  type SummarySection,
} from '../api/title'
import { BatchDownload } from './BatchDownload'
import { ErrorState } from './ErrorState'

interface Props {
  sections: SummarySection[]
  langs: string[]
  kinds: string[]
  seriesUrl: string | null
  /** Номера серий, считающихся контентом: нужны батч-загрузке. */
  contentEpisodes: Set<number>
  /** Есть ли спешлы или OVA — тумблер показываем только тогда. */
  hasExtras: boolean
}

/**
 * Сводка переводов за весь тайтл.
 *
 * Всё под аккордеонами, и содержимое монтируется только при раскрытии: у популярного
 * тайтла около двух тысяч переводов, и вываливать их в DOM разом нельзя.
 */
export function TranslationGroups({
  sections,
  langs,
  kinds,
  seriesUrl,
  contentEpisodes,
  hasExtras,
}: Props) {
  const visible = sections.filter((s) => langs.includes(s.lang) && kinds.includes(s.kind))

  // Какие секции пользователь уже открывал. Mantine Accordion сам по себе панели не
  // размонтирует — он рендерит содержимое и прячет его через Collapse. Для тайтла с
  // двумя тысячами переводов это сотни лишних строк в DOM ещё до первого клика,
  // поэтому монтируем содержимое сами и только после раскрытия.
  const [opened, setOpened] = useState<string[]>([])
  const [everOpened, setEverOpened] = useState<Set<string>>(() => new Set())

  const handleChange = (values: string[]) => {
    setOpened(values)
    setEverOpened((prev) => {
      const next = new Set(prev)
      for (const v of values) next.add(v)
      return next
    })
  }

  if (visible.length === 0) {
    return (
      <Text c="dimmed" size="sm">
        Для выбранных языков и типов переводов нет. Измените фильтр выше.
      </Text>
    )
  }

  return (
    <Accordion variant="separated" multiple value={opened} onChange={handleChange}>
      {visible.map((section) => {
        const key = `${section.lang}-${section.kind}`
        return (
          <Accordion.Item key={key} value={key}>
            <Accordion.Control>
              <Group gap="xs" wrap="nowrap">
                <Badge color={LANG_COLORS[section.lang]} variant="filled" size="sm">
                  {LANG_LABELS[section.lang] ?? section.lang}
                </Badge>
                <Text size="sm" fw={500}>
                  {KIND_ICONS[section.kind] ?? ''} {KIND_LABELS[section.kind] ?? section.kind}
                </Text>
                <Text size="xs" c="dimmed">
                  {section.groups.length} авторов · {section.total_translations} переводов
                </Text>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              {everOpened.has(key) ? (
                <AuthorTable
                  groups={section.groups}
                  kind={section.kind}
                  seriesUrl={seriesUrl}
                  contentEpisodes={contentEpisodes}
                  hasExtras={hasExtras}
                />
              ) : null}
            </Accordion.Panel>
          </Accordion.Item>
        )
      })}
    </Accordion>
  )
}

function AuthorTable({
  groups,
  kind,
  seriesUrl,
  contentEpisodes,
  hasExtras,
}: {
  groups: AuthorGroup[]
  kind: string
  seriesUrl: string | null
  contentEpisodes: Set<number>
  hasExtras: boolean
}) {
  return (
    <Table.ScrollContainer minWidth={560}>
      <Table striped highlightOnHover verticalSpacing="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Автор</Table.Th>
            <Table.Th w={190}>Покрытие</Table.Th>
            <Table.Th w={90}>Качество</Table.Th>
            <Table.Th w={120}>Действия</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {groups.map((group) => (
            <AuthorRow
              key={group.authors}
              group={group}
              kind={kind}
              seriesUrl={seriesUrl}
              contentEpisodes={contentEpisodes}
              hasExtras={hasExtras}
            />
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  )
}

function AuthorRow({
  group,
  kind,
  seriesUrl,
  contentEpisodes,
  hasExtras,
}: {
  group: AuthorGroup
  kind: string
  seriesUrl: string | null
  contentEpisodes: Set<number>
  hasExtras: boolean
}) {
  // Признак вшитых субтитров и точные ступени качества доступны только через embed —
  // один запрос на перевод. Поэтому тянем по требованию и только для одного
  // представителя группы: у всех переводов автора набор ступеней практически одинаков.
  const [probe, setProbe] = useState(false)
  const sample = group.translation_ids[0]

  const embedQuery = useQuery({
    queryKey: ['embed', sample],
    queryFn: () => titleApi.embed(sample),
    enabled: probe && sample !== undefined,
    staleTime: 24 * 60 * 60 * 1000,
  })

  const hardsub = kind === 'sub' && embedQuery.data && !embedQuery.data.has_subtitles

  return (
    <Table.Tr>
      <Table.Td>
        <Text size="sm" style={{ wordBreak: 'break-word' }}>
          {group.authors}
        </Text>
        {kind === 'sub' && (
          <>
            {embedQuery.isFetching && (
              <Group gap={4} mt={4}>
                <Loader size={10} />
                <Text size="xs" c="dimmed">
                  проверяю…
                </Text>
              </Group>
            )}
            {embedQuery.data && (
              <Badge size="xs" mt={4} color={hardsub ? 'orange' : 'teal'} variant="light">
                {hardsub ? 'вшитые в видео' : 'отдельный файл'}
              </Badge>
            )}
            {embedQuery.isError && (
              <ErrorState
                compact
                error={embedQuery.error}
                onRetry={() => embedQuery.refetch()}
              />
            )}
            {!probe && (
              <Tooltip
                multiline
                w={260}
                label="Узнать, вшиты ли субтитры в видео или скачиваются отдельным файлом. В каталоге этого признака нет, нужен отдельный запрос — требуется премиум-токен."
              >
                <Button size="compact-xs" variant="subtle" mt={2} onClick={() => setProbe(true)}>
                  вшитые или файлом?
                </Button>
              </Tooltip>
            )}
          </>
        )}
      </Table.Td>

      <Table.Td>
        <Text size="sm" c={group.is_complete ? 'teal' : undefined}>
          {group.coverage || '—'}
        </Text>
        <Text size="xs" c="dimmed">
          {group.count} шт.
        </Text>
      </Table.Td>

      <Table.Td>
        {group.max_height_estimate ? (
          <Tooltip label="Оценка по разрешению исходника. Точные варианты — при загрузке.">
            <Text size="sm">до {Math.min(group.max_height_estimate, 1080)}p</Text>
          </Tooltip>
        ) : (
          <Text size="sm" c="dimmed">
            —
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        <Stack gap={4}>
          {/* В сводке за весь тайтл конкретная серия не выбрана, поэтому кнопка
              одиночной загрузки здесь бессмысленна — она живёт в списке серий.
              Здесь только батч по всем сериям автора. */}
          <BatchDownload
            group={group}
            kind={kind as 'voice' | 'sub' | 'raw'}
            contentEpisodes={contentEpisodes}
            hasExtras={hasExtras}
            disabled={hardsub === true}
            disabledReason={hardsub ? 'Субтитры вшиты в видео, отдельного файла нет' : undefined}
          />
          {/* Ведём на страницу конкретного перевода конкретной серии, а не на общую
              страницу тайтла: у строки со сводкой это самая ранняя серия автора. */}
          {(group.first_url || seriesUrl) && (
            <Tooltip
              label={
                group.first_url
                  ? `Открыть серию ${group.first_episode ?? ''} этого перевода на сайте`
                  : 'Открыть страницу тайтла на сайте'
              }
            >
              <Button
                size="compact-xs"
                variant="subtle"
                leftSection={<IconPlayerPlay size={12} />}
                component="a"
                href={group.first_url ?? seriesUrl ?? undefined}
                target="_blank"
                rel="noreferrer noopener"
              >
                Смотреть
              </Button>
            </Tooltip>
          )}
        </Stack>
      </Table.Td>
    </Table.Tr>
  )
}
