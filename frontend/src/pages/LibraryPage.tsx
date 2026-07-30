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
  formatBytes,
  libraryApi,
  SOURCE_COLORS,
  SOURCE_LABELS,
  type UnmappedFolder,
} from '../api/library'
import { ErrorState } from '../components/ErrorState'
import { MapFolderModal } from '../components/MapFolderModal'
import { MappingReport } from '../components/MappingReport'
import scrollClasses from './ScrollPage.module.css'
import pathClasses from '../components/PathLine.module.css'

function capitalize(text: string | null | undefined): string {
  return text ? text[0].toUpperCase() + text.slice(1) : ''
}

export function LibraryPage() {
  const queryClient = useQueryClient()
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
      notifications.show({ color: 'teal', message: 'Сканирование завершено' })
      for (const key of ['library-status', 'library-unmapped', 'library-mappings', 'library-unmatched-files']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Сканирование не удалось', message: error.message }),
  })

  const confirm = useMutation({
    mutationFn: libraryApi.confirmMapping,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-mappings'] })
      queryClient.invalidateQueries({ queryKey: ['library-status'] })
    },
  })

  if (status.isError) {
    return <ErrorState error={status.error} title="Не удалось получить состояние библиотеки" onRetry={() => status.refetch()} />
  }

  const data = status.data
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
          <Title order={3}>Библиотека</Title>
          {data?.scanning ? (
            <Group gap={6}>
              <Loader size="xs" />
              <Text size="xs" c="dimmed">
                идёт сканирование
              </Text>
            </Group>
          ) : data?.watcher?.state === 'active' ? (
            <Tooltip
              label={`Отслеживается ${data.watcher.directories} каталогов, событий: ${data.watcher.events_seen}`}
            >
              <Badge size="sm" color="teal" variant="light" leftSection={<IconEye size={12} />}>
                изменения видны сразу
              </Badge>
            </Tooltip>
          ) : null}
        </Group>
        <Button
          leftSection={<IconRefresh size={16} />}
          onClick={() => scan.mutate()}
          loading={scan.isPending || data?.scanning}
        >
          Сканировать
        </Button>
      </Group>

      {missingRoots.map((root) => (
        <Alert key={root.id} color="red" title="Папка недоступна" icon={<IconAlertTriangle size={18} />}>
          {root.path} — проверьте, что том примонтирован в контейнер.
        </Alert>
      ))}

      {/* Молчаливый откат на расписание — худший вариант: пользователь будет
          думать, что изменения подхватываются сразу, а они не подхватываются. */}
      {data?.watcher?.state === 'fallback' && (
        <Alert color="yellow" title="Изменения отслеживаются по расписанию" icon={<IconClock size={18} />}>
          {/* Причина приходит с сервера строчной: там она — часть строки лога.
              В начале предложения её надо поднять, иначе выглядит как обрывок. */}
          {capitalize(data.watcher.reason)}. Библиотека всё равно обновляется, но не мгновенно —
          по расписанию сверочного сканирования. Уменьшите интервал в настройках либо
          устраните причину.
        </Alert>
      )}

      {readOnlyRoots.length > 0 && (
        <Alert color="yellow" title="Только для чтения" icon={<IconLock size={18} />}>
          {readOnlyRoots.map((r) => r.path).join(', ')} смонтированы без права записи.
          Каталог, маппинг и просмотр работают полностью, но загрузка файлов будет недоступна,
          пока том не перемонтирован на запись.
        </Alert>
      )}

      <Group gap="md" wrap="wrap">
        {data?.roots.map((root) => (
          <Card key={root.id} withBorder padding="sm" miw={240} style={{ flexShrink: 0 }}>
            <Group gap={6} mb={4}>
              <IconFolders size={16} />
              <Text size="sm" fw={600}>
                {root.kind === 'series' ? 'Сериалы' : 'Фильмы'}
              </Text>
              {!root.writable && root.available && (
                <Badge size="xs" color="yellow">
                  только чтение
                </Badge>
              )}
            </Group>
            <Text size="xs" c="dimmed" className={pathClasses.pathLine} title={root.path}>
              {root.path}
            </Text>
            <Text size="sm" mt={4}>
              {root.folder_count} папок · {formatBytes(root.total_bytes)}
            </Text>
          </Card>
        ))}
      </Group>

      {data && data.folders_total > 0 && (
        // flexShrink: 0 обязателен — во flex-колонке карточка иначе сжимается
        // и текст обрезается по высоте.
        <Card withBorder padding="sm" style={{ flexShrink: 0 }}>
          <Group justify="space-between" mb={4}>
            <Text size="sm" fw={600}>
              Смаплено {data.folders_mapped} из {data.folders_total}
            </Text>
            <Text size="sm" c={data.folders_unmapped > 0 ? 'orange' : 'teal'}>
              {data.folders_unmapped > 0 ? `без маппинга: ${data.folders_unmapped}` : 'все папки распознаны'}
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
                <Badge size="xs" color="orange" circle>
                  {unmapped.data.length}
                </Badge>
              ) : null
            }
          >
            Без маппинга
          </Tabs.Tab>
          <Tabs.Tab
            value="mappings"
            rightSection={
              data?.needs_confirmation ? (
                <Badge size="xs" color="yellow" circle>
                  {data.needs_confirmation}
                </Badge>
              ) : null
            }
          >
            Связи
          </Tabs.Tab>
          <Tabs.Tab
            value="files"
            leftSection={<IconQuestionMark size={14} />}
            rightSection={
              unmatchedFiles.data?.length ? (
                <Badge size="xs" color="gray" circle>
                  {unmatchedFiles.data.length}
                </Badge>
              ) : null
            }
          >
            Файлы без серии
          </Tabs.Tab>
          <Tabs.Tab value="report" leftSection={<IconChartBar size={14} />}>
            Качество привязок
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="unmapped" pt="sm">
          {unmapped.data?.length === 0 ? (
            <Alert color="teal">Все папки нашли свой тайтл в каталоге.</Alert>
          ) : (
            // На узком экране колонки с фиксированной шириной схлопывались до
            // одного символа. Таблица прокручивается внутри своего контейнера.
            <Table.ScrollContainer minWidth={620}>
            <Table striped highlightOnHover verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Папка</Table.Th>
                  <Table.Th w={200}>Название из .nfo</Table.Th>
                  <Table.Th w={80}>Видео</Table.Th>
                  <Table.Th w={90}>Размер</Table.Th>
                  <Table.Th w={120}>Действие</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {unmapped.data?.map((folder) => (
                  <Table.Tr key={folder.folder_id}>
                    <Table.Td style={{ wordBreak: 'break-word' }}>
                      {folder.rel_path}
                      {folder.season_number != null && (
                        <Badge size="xs" ml={6} variant="light">
                          сезон {folder.season_number}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td style={{ wordBreak: 'break-word' }}>
                      {folder.nfo_title ?? (
                        <Text size="xs" c="dimmed">
                          нет .nfo
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>{folder.video_count || '—'}</Table.Td>
                    <Table.Td>{formatBytes(folder.size_bytes)}</Table.Td>
                    <Table.Td>
                      <Button size="compact-xs" onClick={() => setMappingFolder(folder)}>
                        Привязать
                      </Button>
                    </Table.Td>
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
                <Table.Th>Папка</Table.Th>
                <Table.Th w={240}>Тайтл</Table.Th>
                <Table.Th w={190}>Чем определено</Table.Th>
                <Table.Th w={110}>Действие</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {mappings.data?.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td style={{ wordBreak: 'break-word' }}>
                    {row.folder}
                    <Text size="xs" c="dimmed">
                      {row.video_count} видео
                    </Text>
                  </Table.Td>
                  <Table.Td style={{ wordBreak: 'break-word' }}>
                    <Link to={`/series/${row.series_id}`}>{row.series_title ?? `#${row.series_id}`}</Link>
                    <Text size="xs" c="dimmed">
                      {row.series_season ?? row.series_year ?? ''}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Tooltip
                      multiline
                      w={300}
                      label={
                        `Уверенность алгоритма: ${row.confidence.toFixed(0)} из 100. ` +
                        (row.matched_on ? `Совпало по: ${row.matched_on}` : '')
                      }
                    >
                      <Badge size="sm" variant="light" color={SOURCE_COLORS[row.source] ?? 'gray'}>
                        {SOURCE_LABELS[row.source] ?? row.source}
                        {!row.is_manual && ` · ${row.confidence.toFixed(0)}%`}
                      </Badge>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>
                    {row.needs_confirmation ? (
                      <Button
                        size="compact-xs"
                        onClick={() => confirm.mutate(row.id)}
                        loading={confirm.isPending}
                      >
                        Подтвердить
                      </Button>
                    ) : (
                      <Text size="xs" c="dimmed">
                        {row.is_manual ? 'вручную' : 'авто'}
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
            <Alert color="teal">У всех видеофайлов определён номер серии.</Alert>
          ) : (
            <>
              <Text size="xs" c="dimmed" mb="xs">
                Номер серии не удалось определить по имени файла и по .nfo. Обычно это бонусные
                материалы. Правила разбора имён настраиваются в config/filename_rules.yaml.
              </Text>
              <Table.ScrollContainer minWidth={560}>
                <Table striped verticalSpacing="xs">
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Файл</Table.Th>
                      <Table.Th w={220}>Папка</Table.Th>
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
      </Tabs>

      <MapFolderModal folder={mappingFolder} onClose={() => setMappingFolder(null)} />
    </Stack>
  )
}
