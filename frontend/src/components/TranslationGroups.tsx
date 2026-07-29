import { Accordion, Badge, Button, Group, Loader, Stack, Table, Text, Tooltip } from '@mantine/core'
import { IconDownload, IconPlayerPlay } from '@tabler/icons-react'
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

interface Props {
  sections: SummarySection[]
  langs: string[]
  kinds: string[]
  seriesUrl: string | null
}

/**
 * Сводка переводов за весь тайтл.
 *
 * Всё под аккордеонами, и содержимое монтируется только при раскрытии: у популярного
 * тайтла около двух тысяч переводов, и вываливать их в DOM разом нельзя.
 */
export function TranslationGroups({ sections, langs, kinds, seriesUrl }: Props) {
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
                <AuthorTable groups={section.groups} kind={section.kind} seriesUrl={seriesUrl} />
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
}: {
  groups: AuthorGroup[]
  kind: string
  seriesUrl: string | null
}) {
  return (
    <Table striped highlightOnHover verticalSpacing="xs" style={{ tableLayout: 'fixed' }}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th style={{ width: '38%' }}>Автор</Table.Th>
          <Table.Th style={{ width: '32%' }}>Покрытие</Table.Th>
          <Table.Th style={{ width: '12%' }}>Качество</Table.Th>
          <Table.Th style={{ width: '18%' }}>Действия</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {groups.map((group) => (
          <AuthorRow
            key={group.authors}
            group={group}
            kind={kind}
            seriesUrl={seriesUrl}
          />
        ))}
      </Table.Tbody>
    </Table>
  )
}

function AuthorRow({
  group,
  kind,
  seriesUrl,
}: {
  group: AuthorGroup
  kind: string
  seriesUrl: string | null
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
            {embedQuery.isFetching && <Loader size={10} mt={4} />}
            {embedQuery.data && (
              <Badge size="xs" mt={4} color={hardsub ? 'orange' : 'teal'} variant="light">
                {hardsub ? 'вшитые в видео' : 'отдельный файл'}
              </Badge>
            )}
            {!probe && !embedQuery.data && (
              <Button size="compact-xs" variant="subtle" mt={2} onClick={() => setProbe(true)}>
                проверить сабы
              </Button>
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
          <Tooltip label="Загрузка появится на этапе 5">
            <Button size="compact-xs" leftSection={<IconDownload size={12} />} disabled>
              Скачать
            </Button>
          </Tooltip>
          {seriesUrl && (
            <Button
              size="compact-xs"
              variant="subtle"
              leftSection={<IconPlayerPlay size={12} />}
              component="a"
              href={seriesUrl}
              target="_blank"
              rel="noreferrer noopener"
            >
              На сайте
            </Button>
          )}
        </Stack>
      </Table.Td>
    </Table.Tr>
  )
}
