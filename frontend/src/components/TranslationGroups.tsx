import { Accordion, Badge, Button, Group, Loader, Stack, Table, Text, Tooltip } from '@mantine/core'
import { IconPlayerPlay } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import {
  KIND_ICONS,
  LANG_COLORS,
  titleApi,
  type AuthorGroup,
  type LocalCoverage,
  type SummarySection,
} from '../api/title'
import { BatchDownload } from './BatchDownload'
import { ErrorState } from './ErrorState'
import { useT, type TranslationKey } from '../i18n'
import { useKindLabel } from '../i18n/kind'
import { isTypeVisible, translationType, typeRank, type TranslationType } from '../stores/preferences'

interface Props {
  sections: SummarySection[]
  /** Включённые типы переводов — общая настройка отображения. */
  types: TranslationType[]
  /** Покрытие по тайтлу целиком: от него зависят акценты у батч-кнопок. */
  local: LocalCoverage
  seriesUrl: string | null
  /** Номера серий, считающихся контентом: нужны батч-загрузке. */
  contentEpisodes: Set<number>
  /** Есть ли спешлы или OVA — тумблер показываем только тогда. */
}

/**
 * Сводка переводов за весь тайтл.
 *
 * Всё под аккордеонами, и содержимое монтируется только при раскрытии: у популярного
 * тайтла около двух тысяч переводов, и вываливать их в DOM разом нельзя.
 */
export function TranslationGroups({
  sections,
  types,
  local,
  seriesUrl,
  contentEpisodes,
}: Props) {
  const { t } = useT()
  const kindLabel = useKindLabel()
  // Тот же порядок, что и в панели загрузки серии: субтитры раньше видео,
  // озвучка последней. Разными их держать нельзя — это один и тот же выбор,
  // сделанный в двух местах страницы.
  const visible = sections
    .filter((section) => isTypeVisible(section.kind, section.lang, types))
    .sort(
      (a, b) =>
        typeRank(translationType(a.kind, a.lang)) - typeRank(translationType(b.kind, b.lang)),
    )

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
        {t('title.noSectionsForTypes')}
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
                  {t(`lang.${section.lang}` as TranslationKey)}
                </Badge>
                <Text size="sm" fw={500}>
                  {KIND_ICONS[section.kind] ?? ''} {kindLabel(section.kind)}
                </Text>
                <Text size="xs" c="dimmed">
                  {t('groups.summaryLine', { authors: section.groups.length, total: section.total_translations })}
                </Text>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              {everOpened.has(key) ? (
                <AuthorTable
                  groups={section.groups}
                  kind={section.kind}
                  hasVideo={local.coverage.video?.complete ?? false}
                  hasSubs={local.coverage[`sub_${section.lang}`]?.complete ?? false}
                  seriesUrl={seriesUrl}
                  contentEpisodes={contentEpisodes}
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
  hasVideo,
  hasSubs,
  seriesUrl,
  contentEpisodes,
}: {
  groups: AuthorGroup[]
  kind: string
  /** Видео по тайтлу собрано целиком. */
  hasVideo: boolean
  /** Субтитры этого языка собраны целиком. */
  hasSubs: boolean
  seriesUrl: string | null
  contentEpisodes: Set<number>
}) {
  const { t } = useT()

  return (
    <Table.ScrollContainer minWidth={560}>
      <Table striped highlightOnHover verticalSpacing="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t('groups.columnAuthor')}</Table.Th>
            <Table.Th w={190}>{t('groups.columnCoverage')}</Table.Th>
            <Table.Th w={90}>{t('groups.columnQuality')}</Table.Th>
            <Table.Th w={120}>{t('groups.columnActions')}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {groups.map((group) => (
            <AuthorRow
              key={group.authors}
              group={group}
              kind={kind}
              hasVideo={hasVideo}
              hasSubs={hasSubs}
              seriesUrl={seriesUrl}
              contentEpisodes={contentEpisodes}
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
  hasVideo,
  hasSubs,
  seriesUrl,
  contentEpisodes,
}: {
  group: AuthorGroup
  kind: string
  hasVideo: boolean
  hasSubs: boolean
  seriesUrl: string | null
  contentEpisodes: Set<number>
}) {
  const { t } = useT()
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
                  {t('groups.checking')}
                </Text>
              </Group>
            )}
            {embedQuery.data && (
              <Badge size="xs" mt={4} color={hardsub ? 'orange' : 'teal'} variant="light">
                {hardsub ? t('groups.hardsub') : t('groups.softsub')}
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
                label={t('groups.checkHardsub')}
              >
                <Button size="compact-xs" variant="subtle" mt={2} onClick={() => setProbe(true)}>
                  {t('groups.hardsubAsk')}
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
          {t('groups.countShort', { count: group.count })}
        </Text>
        {/* Добавка отдельной строкой: у автора, переведшего одни OVA, основных
            серий ноль — и без этой подписи строка выглядела бы пустой. */}
        {group.extra_count > 0 && (
          <Text size="xs" c="dimmed">
            {t('groups.extras', { count: group.extra_count, coverage: group.extra_coverage })}
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        {group.max_height_estimate ? (
          <Tooltip label={t('groups.qualityHint')}>
            <Text size="sm">{t('groups.upTo', { height: Math.min(group.max_height_estimate, 1080) })}</Text>
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
            // Батч ставит задачи на все серии сразу, поэтому «уже есть» здесь
            // означает собранное целиком, а не наличие у одной серии.
            hasVideo={hasVideo}
            hasSubs={hasSubs}
            disabled={hardsub === true}
            disabledReason={hardsub ? t('groups.hardsubNoFile') : undefined}
          />
          {/* Ведём на страницу конкретного перевода конкретной серии, а не на общую
              страницу тайтла: у строки со сводкой это самая ранняя серия автора. */}
          {(group.first_url || seriesUrl) && (
            <Tooltip
              label={
                group.first_url
                  ? t('groups.openEpisode', { episode: group.first_episode ?? '' })
                  : t('groups.openTitle')
              }
            >
              <Button
                size="compact-xs"
                variant="subtle"
                leftSection={
                  <IconPlayerPlay size={12} />
                }
                component="a"
                href={group.first_url ?? seriesUrl ?? undefined}
                target="_blank"
                rel="noreferrer noopener"
              >
                {t('groups.watch')}
              </Button>
            </Tooltip>
          )}
        </Stack>
      </Table.Td>
    </Table.Tr>
  )
}
