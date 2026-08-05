import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Progress,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  IconAlertTriangle,
  IconChartBar,
  IconDeviceTv,
  IconMusic,
  IconClock,
  IconEye,
  IconFolderOff,
  IconFolders,
  IconLock,
  IconQuestionMark,
  IconRefresh,
} from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  libraryApi,
  SOURCE_COLORS,
  type LibraryRootInfo,
  type UnmappedFolder,
} from '../api/library'
import { AudioLibraryTab } from '../components/AudioLibraryTab'
import { JellyfinAudit } from '../components/JellyfinAudit'
import { ErrorState } from '../components/ErrorState'
import { MapFolderModal } from '../components/MapFolderModal'
import countClasses from '../components/CountBadge.module.css'
import { MappingReport } from '../components/MappingReport'
import { PosterThumb } from '../components/PosterThumb'
import { usePermissions } from '../hooks/usePermissions'
import { useT, type TranslationKey } from '../i18n'
import { useApiError } from '../i18n/apiError'
import { useFormat } from '../i18n/format'
import scrollClasses from './ScrollPage.module.css'
import pathClasses from '../components/PathLine.module.css'


/**
 * Свободное место на носителе, где лежит корень.
 *
 * Показываем свободное, а не занятое: вопрос у пользователя всегда «влезет ли
 * ещё серия», а не «сколько уже занято». Полоса при этом рисует занятое —
 * так привычнее по файловым менеджерам.
 *
 * Оранжевый порог в 10% выбран как «пора чистить», красный в 3% — как «загрузки
 * вот-вот начнут падать»: серия в 1080p весит около 230 МБ, и на терабайтном
 * диске 3% это ещё сотня серий, а на 100-гигабайтном — уже десяток.
 */
function DiskSpace({ root, sharedWith }: { root: LibraryRootInfo; sharedWith?: string }) {
  const { t } = useT()
  const { size } = useFormat()

  if (root.disk_free_bytes == null || root.disk_total_bytes == null) return null

  const used = root.disk_total_bytes - root.disk_free_bytes
  const usedPercent = root.disk_total_bytes > 0 ? (used / root.disk_total_bytes) * 100 : 0
  const freeShare = 100 - usedPercent
  const color = freeShare < 3 ? 'red' : freeShare < 10 ? 'orange' : 'teal'

  return (
    <>
      <Progress value={usedPercent} color={color} size="xs" mt={8} />
      <Text size="xs" c={color === 'teal' ? 'dimmed' : color} mt={4}>
        {t('library.diskFree', {
          free: size(root.disk_free_bytes),
          total: size(root.disk_total_bytes),
        })}
      </Text>
      {sharedWith && (
        // Иначе два корня на одном диске выглядят как два независимых запаса,
        // и пользователь решит, что места вдвое больше, чем есть.
        <Text size="xs" c="dimmed" fs="italic">
          {t('library.diskShared', { other: sharedWith })}
        </Text>
      )}
    </>
  )
}

export function LibraryPage() {
  const queryClient = useQueryClient()
  const { t } = useT()
  const apiError = useApiError()
  const { size } = useFormat()
  const sourceLabel = (key: string) => t(`source.${key}` as TranslationKey)
  // Состояние библиотеки — чтение (`catalog`). Сканирование и правка привязок
  // меняют то, куда лягут файлы, и требуют `library`.
  const canEdit = usePermissions().can('library')
  const [mappingFolder, setMappingFolder] = useState<UnmappedFolder | null>(null)

  const status = useQuery({
    queryKey: ['library-status'],
    queryFn: libraryApi.status,
    // Пока сканирование идёт, опрашиваем чаще — это индикатор прогресса.
    refetchInterval: (query) => (query.state.data?.scanning ? 2000 : 60_000),
  })

  const unmapped = useQuery({ queryKey: ['library-unmapped'], queryFn: libraryApi.unmapped })
  const mappings = useQuery({ queryKey: ['library-mappings'], queryFn: libraryApi.mappings })
  const unmatchedFiles = useQuery({
    queryKey: ['library-unmatched-files'],
    queryFn: libraryApi.unmatchedFiles,
  })

  const scan = useMutation({
    mutationFn: libraryApi.scan,
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('library.scanDone') })
      for (const key of ['library-status', 'library-unmapped', 'library-mappings', 'library-unmatched-files']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('library.scanFailed'), message: apiError(error) }),
  })

  const confirm = useMutation({
    mutationFn: libraryApi.confirmMapping,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-mappings'] })
      queryClient.invalidateQueries({ queryKey: ['library-status'] })
    },
  })

  if (status.isError) {
    return <ErrorState error={status.error} title={t('library.statusFailed')} onRetry={() => status.refetch()} />
  }

  const data = status.data

  // Корни, делящие носитель с другим корнем: у них общее свободное место.
  const sharedMounts = new Map<number, string>()
  for (const root of data?.roots ?? []) {
    if (!root.mount) continue
    const neighbours = (data?.roots ?? []).filter(
      (other) => other.id !== root.id && other.mount === root.mount,
    )
    if (neighbours.length > 0) {
      sharedMounts.set(
        root.id,
        neighbours
          .map((other) => (other.kind === 'series' ? t('library.rootSeries') : t('library.rootMovies')))
          .join(', '),
      )
    }
  }

  const readOnlyRoots = data?.roots.filter((r) => r.available && !r.writable) ?? []
  const missingRoots = data?.roots.filter((r) => !r.available) ?? []
  const mappedPercent = data && data.folders_total > 0
    ? Math.round((data.folders_mapped / data.folders_total) * 100)
    : 0

  return (
    // Страница длинная и должна прокручиваться целиком: жёсткая высота без
    // прокрутки обрезала нижние карточки.
    <Stack gap="md" className={scrollClasses.scrollPage}>
      <Group justify="space-between">
        <Group gap="sm" align="center">
          <Title order={3}>{t('library.title')}</Title>
          {data?.scanning ? (
            <Group gap={6}>
              <Loader size="xs" />
              <Text size="xs" c="dimmed">
                {t('library.scanning')}
              </Text>
            </Group>
          ) : data?.watcher?.state === 'active' ? (
            <Tooltip
              label={t('library.watcherActiveHint', {
                dirs: data.watcher.directories,
                events: data.watcher.events_seen,
              })}
            >
              <Badge size="sm" color="teal" variant="light" leftSection={<IconEye size={12} />}>
                {t('library.watcherActive')}
              </Badge>
            </Tooltip>
          ) : null}
        </Group>
        {canEdit && (
          <Button
            leftSection={<IconRefresh size={16} />}
            onClick={() => scan.mutate()}
            loading={scan.isPending || data?.scanning}
          >
            {t('library.scan')}
          </Button>
        )}
      </Group>

      {missingRoots.map((root) => (
        <Alert key={root.id} color="red" title={t('library.rootMissing')} icon={<IconAlertTriangle size={18} />}>
          {t('library.rootMissingHint', { path: root.path })}
        </Alert>
      ))}

      {/* Молчаливый откат на расписание — худший вариант: пользователь будет
          думать, что изменения подхватываются сразу, а они не подхватываются. */}
      {data?.watcher?.state === 'fallback' && (
        <Alert color="yellow" title={t('library.watcherFallback')} icon={<IconClock size={18} />}>
          {/* Причина приходит КОДОМ, подстановки — отдельно. Раньше сервер
              присылал готовую русскую фразу, и она торчала в английском
              интерфейсе. */}
          {t(`watcherReason.${data.watcher.reason ?? 'unknown'}` as TranslationKey,
            data.watcher.reason_params ?? {})}
          . {t('library.watcherFallbackHint')}
        </Alert>
      )}

      {readOnlyRoots.length > 0 && (
        <Alert color="yellow" title={t('library.readOnly')} icon={<IconLock size={18} />}>
          {t('library.readOnlyHint', { paths: readOnlyRoots.map((r) => r.path).join(', ') })}
        </Alert>
      )}

      <Group gap="md" wrap="wrap">
        {data?.roots.map((root) => (
          <Card key={root.id} withBorder padding="sm" miw={240} style={{ flexShrink: 0 }}>
            <Group gap={6} mb={4}>
              <IconFolders size={16} />
              <Text size="sm" fw={600}>
                {root.kind === 'series' ? t('library.rootSeries') : t('library.rootMovies')}
              </Text>
              {!root.writable && root.available && (
                <Badge size="xs" color="yellow" className={countClasses.count}>
                  {t('library.rootReadOnly')}
                </Badge>
              )}
            </Group>
            <Text size="xs" c="dimmed" className={pathClasses.pathLine} title={root.path}>
              {root.path}
            </Text>
            <Text size="sm" mt={4}>
              {t('library.rootFolders', { count: root.folder_count, size: size(root.total_bytes) })}
            </Text>
            <DiskSpace root={root} sharedWith={sharedMounts.get(root.id)} />
          </Card>
        ))}
      </Group>

      {data && data.folders_total > 0 && (
        // flexShrink: 0 обязателен — во flex-колонке карточка иначе сжимается
        // и текст обрезается по высоте.
        <Card withBorder padding="sm" style={{ flexShrink: 0 }}>
          <Group justify="space-between" mb={4}>
            <Text size="sm" fw={600}>
              {t('library.mapped', { mapped: data.folders_mapped, total: data.folders_total })}
            </Text>
            <Text size="sm" c={data.folders_unmapped > 0 ? 'orange' : 'teal'}>
              {data.folders_unmapped > 0
                ? t('library.unmappedCount', { count: data.folders_unmapped })
                : t('library.allMapped')}
            </Text>
          </Group>
          <Progress value={mappedPercent} color={data.folders_unmapped > 0 ? 'orange' : 'teal'} />
        </Card>
      )}

      <Tabs defaultValue={data && data.folders_unmapped > 0 ? 'unmapped' : 'mappings'}>
        <Tabs.List>
          <Tabs.Tab
            value="unmapped"
            leftSection={<IconFolderOff size={14} />}
            rightSection={
              unmapped.data?.length ? (
                <Badge size="xs" color="orange" className={countClasses.count}>
                  {unmapped.data.length}
                </Badge>
              ) : null
            }
          >
            {t('library.tabUnmapped')}
          </Tabs.Tab>
          <Tabs.Tab
            value="mappings"
            rightSection={
              data?.needs_confirmation ? (
                <Badge size="xs" color="yellow" className={countClasses.count}>
                  {data.needs_confirmation}
                </Badge>
              ) : null
            }
          >
            {t('library.tabMappings')}
          </Tabs.Tab>
          <Tabs.Tab
            value="files"
            leftSection={<IconQuestionMark size={14} />}
            rightSection={
              unmatchedFiles.data?.length ? (
                <Badge size="xs" color="gray" className={countClasses.count}>
                  {unmatchedFiles.data.length}
                </Badge>
              ) : null
            }
          >
            {t('library.tabFiles')}
          </Tabs.Tab>
          <Tabs.Tab value="report" leftSection={<IconChartBar size={14} />}>
            {t('library.tabReport')}
          </Tabs.Tab>
          {/* Сверка обходит библиотеку Jellyfin целиком — это обслуживание, а
              не просмотр, и живёт под тем же правом, что скан и привязки. */}
          {canEdit && (
            <Tabs.Tab value="jellyfin" leftSection={<IconDeviceTv size={14} />}>
              {t('library.tabJellyfin')}
            </Tabs.Tab>
          )}
          <Tabs.Tab value="audio" leftSection={<IconMusic size={14} />}>
            {t('audio.tab')}
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="unmapped" pt="sm">
          {unmapped.data?.length === 0 ? (
            <Alert color="teal">{t('library.allFoldersMapped')}</Alert>
          ) : (
            // На узком экране колонки с фиксированной шириной схлопывались до
            // одного символа. Таблица прокручивается внутри своего контейнера.
            <Table.ScrollContainer minWidth={620}>
            <Table striped highlightOnHover verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t('library.columnFolder')}</Table.Th>
                  <Table.Th w={200}>{t('library.columnNfoTitle')}</Table.Th>
                  <Table.Th w={80}>{t('library.columnVideos')}</Table.Th>
                  <Table.Th w={90}>{t('library.columnSize')}</Table.Th>
                  {canEdit && <Table.Th w={120}>{t('library.columnAction')}</Table.Th>}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {unmapped.data?.map((folder) => (
                  <Table.Tr key={folder.folder_id}>
                    <Table.Td style={{ wordBreak: 'break-word' }}>
                      {folder.rel_path}
                      {folder.season_number != null && (
                        <Badge size="xs" ml={6} variant="light">
                          {t('mapping.seasonNumber', { n: folder.season_number })}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td style={{ wordBreak: 'break-word' }}>
                      {folder.nfo_title ?? (
                        <Text size="xs" c="dimmed">
                          {t('library.noNfo')}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>{folder.video_count || '—'}</Table.Td>
                    <Table.Td>{folder.size_bytes ? size(folder.size_bytes) : '—'}</Table.Td>
                    {canEdit && (
                      <Table.Td>
                        <Button size="compact-xs" onClick={() => setMappingFolder(folder)}>
                          {t('library.bind')}
                        </Button>
                      </Table.Td>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            </Table.ScrollContainer>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="mappings" pt="sm">
          <Table.ScrollContainer minWidth={720}>
          <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('library.columnFolder')}</Table.Th>
                <Table.Th w={240}>{t('library.columnTitle')}</Table.Th>
                <Table.Th w={190}>{t('library.columnSource')}</Table.Th>
                <Table.Th w={110}>{t('library.columnAction')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {mappings.data?.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td style={{ wordBreak: 'break-word' }}>
                    {row.folder}
                    <Text size="xs" c="dimmed">
                      {t('library.videosCount', { count: row.video_count })}
                    </Text>
                  </Table.Td>
                  <Table.Td style={{ wordBreak: 'break-word' }}>
                    {/* Постер тут не украшение: по картинке ошибочная привязка
                        видна мгновенно, а по названию её надо читать. */}
                    <Group gap="xs" wrap="nowrap" align="flex-start">
                      <PosterThumb url={row.poster_url} />
                      <div style={{ minWidth: 0 }}>
                        <Link to={`/series/${row.series_id}`}>
                          {row.series_title ?? `#${row.series_id}`}
                        </Link>
                        <Text size="xs" c="dimmed">
                          {row.series_season ?? row.series_year ?? ''}
                        </Text>
                      </div>
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Tooltip
                      multiline
                      w={300}
                      label={
                        t('library.confidenceHint', { value: row.confidence.toFixed(0) }) +
                    (row.matched_on ? t('library.matchedOn', { on: row.matched_on }) : '')
                      }
                    >
                      <Badge size="sm" variant="light" color={SOURCE_COLORS[row.source] ?? 'gray'}>
                        {sourceLabel(row.source)}
                        {!row.is_manual && ` · ${row.confidence.toFixed(0)}%`}
                      </Badge>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>
                    {/* Подтверждение слабой связи меняет привязку — оно под
                        `library`. Сам факт «требует подтверждения» виден и без
                        права: это состояние библиотеки, а не действие. */}
                    {row.needs_confirmation ? (
                      canEdit ? (
                        <Button
                          size="compact-xs"
                          onClick={() => confirm.mutate(row.id)}
                          loading={confirm.isPending}
                        >
                          {t('library.confirm')}
                        </Button>
                      ) : (
                        <Text size="xs" c="dimmed">
                          {t('mapping.needsConfirmation')}
                        </Text>
                      )
                    ) : (
                      <Text size="xs" c="dimmed">
                        {row.is_manual ? t('library.manual') : t('library.auto')}
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          </Table.ScrollContainer>
        </Tabs.Panel>

        <Tabs.Panel value="files" pt="sm">
          {unmatchedFiles.data?.length === 0 ? (
            <Alert color="teal">{t('library.noUnmatchedFiles')}</Alert>
          ) : (
            <>
              <Text size="xs" c="dimmed" mb="xs">
                {t('library.unmatchedHint')}
              </Text>
              <Table.ScrollContainer minWidth={560}>
                <Table striped verticalSpacing="xs">
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{t('library.columnFile')}</Table.Th>
                      <Table.Th w={220}>{t('library.columnFolder')}</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {unmatchedFiles.data?.map((file) => (
                      <Table.Tr key={file.file_id}>
                        <Table.Td style={{ wordBreak: 'break-word' }}>{file.filename}</Table.Td>
                        <Table.Td style={{ wordBreak: 'break-word' }}>{file.folder}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="report" pt="sm">
          <MappingReport />
        </Tabs.Panel>
        {canEdit && (
          <Tabs.Panel value="jellyfin" pt="sm">
            <JellyfinAudit />
          </Tabs.Panel>
        )}

        <Tabs.Panel value="audio" pt="sm">
          <AudioLibraryTab />
        </Tabs.Panel>

      </Tabs>

      {canEdit && (
        <MapFolderModal folder={mappingFolder} onClose={() => setMappingFolder(null)} />
      )}
    </Stack>
  )
}
