import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Progress,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  IconAlertTriangle,
  IconPlayerPause,
  IconPlayerPlay,
  IconRefresh,
  IconTrash,
  IconX,
} from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  ACTIVE_STATUSES,
  downloadsApi,
  formatEta,
  formatSpeed,
  STATUS_COLORS,
  STATUS_LABELS,
  type DownloadTask,
  type TaskStatus,
} from '../api/downloads'
import { formatBytes } from '../api/library'
import { ErrorState } from '../components/ErrorState'
import { useServerEvents, type ServerEvent } from '../hooks/useServerEvents'
import scrollClasses from './ScrollPage.module.css'

export function QueuePage() {
  const queryClient = useQueryClient()
  // Прогресс приходит событиями и живёт отдельно от списка: перезапрашивать
  // весь список несколько раз в секунду незачем.
  const [live, setLive] = useState<Record<number, Partial<DownloadTask>>>({})

  const queue = useQuery({
    queryKey: ['downloads'],
    queryFn: downloadsApi.list,
    refetchInterval: 15_000,
  })

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      if (event.kind === 'download.progress') {
        const id = event.task_id as number
        setLive((prev) => ({
          ...prev,
          [id]: {
            bytes_done: event.bytes_done as number,
            bytes_total: event.bytes_total as number,
            speed_bps: event.speed_bps as number,
          },
        }))
      } else if (event.kind === 'download.status') {
        // Смена статуса меняет и набор доступных действий — проще перезапросить.
        setLive((prev) => {
          const next = { ...prev }
          delete next[event.task_id as number]
          return next
        })
        queryClient.invalidateQueries({ queryKey: ['downloads'] })
      }
    },
    [queryClient],
  )

  const connected = useServerEvents(['download.progress', 'download.status'], handleEvent)

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['downloads'] })

  const action = useMutation({
    mutationFn: ({ fn, id }: { fn: (id: number) => Promise<unknown>; id: number }) => fn(id),
    onSuccess: refresh,
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не получилось', message: error.message }),
  })

  const pauseAll = useMutation({ mutationFn: downloadsApi.pauseAll, onSuccess: refresh })
  const resumeAll = useMutation({ mutationFn: downloadsApi.resumeAll, onSuccess: refresh })
  const clearFinished = useMutation({
    mutationFn: downloadsApi.clearFinished,
    onSuccess: (result) => {
      notifications.show({ color: 'teal', message: `Убрано записей: ${result.deleted}` })
      refresh()
    },
  })

  const retryAll = useMutation({
    mutationFn: async () => {
      const ids = (queue.data?.tasks ?? [])
        .filter((task) => task.status === 'failed')
        .map((task) => task.id)
      await Promise.all(ids.map((id) => downloadsApi.retry(id)))
      return ids.length
    },
    onSuccess: (count) => {
      notifications.show({ color: 'teal', message: `Возвращено в очередь: ${count}` })
      refresh()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не получилось', message: error.message }),
  })

  if (queue.isError) {
    return (
      <ErrorState
        error={queue.error}
        title="Не удалось загрузить очередь"
        onRetry={() => queue.refetch()}
      />
    )
  }

  const data = queue.data
  const tasks = data?.tasks ?? []
  const merged = tasks.map((task) => ({ ...task, ...(live[task.id] ?? {}) }))

  const active = merged.filter((t) => ACTIVE_STATUSES.includes(t.status))
  const finished = merged.filter((t) => !ACTIVE_STATUSES.includes(t.status))
  const failed = merged.filter((t) => t.status === 'failed')

  const counts = data?.counts ?? {}
  const postprocessing = counts.postprocessing ?? 0

  return (
    <Stack gap="md" className={scrollClasses.scrollPage}>
      <Group justify="space-between" wrap="wrap">
        <Group gap="sm" align="center">
          <Title order={3}>Очередь загрузок</Title>
          {queue.isFetching && <Loader size="xs" />}
          <Tooltip
            label={
              connected
                ? 'Прогресс обновляется в реальном времени'
                : 'Живое обновление недоступно, список обновляется раз в 15 секунд'
            }
          >
            <Badge size="sm" variant="light" color={connected ? 'teal' : 'gray'}>
              {connected ? 'онлайн' : 'опрос'}
            </Badge>
          </Tooltip>
        </Group>

        <Group gap="xs">
          {data?.paused ? (
            <Button
              size="xs"
              leftSection={<IconPlayerPlay size={14} />}
              onClick={() => resumeAll.mutate()}
              loading={resumeAll.isPending}
            >
              Возобновить всё
            </Button>
          ) : (
            <Button
              size="xs"
              variant="default"
              leftSection={<IconPlayerPause size={14} />}
              onClick={() => pauseAll.mutate()}
              loading={pauseAll.isPending}
            >
              Пауза
            </Button>
          )}
          <Button
            size="xs"
            variant="subtle"
            color="gray"
            leftSection={<IconTrash size={14} />}
            onClick={() => clearFinished.mutate()}
            loading={clearFinished.isPending}
            disabled={finished.length === 0}
          >
            Очистить завершённые
          </Button>
        </Group>
      </Group>

      {data?.paused && (
        <Alert color="yellow">
          Очередь на паузе. Незавершённые файлы сохранены и докачаются с того же места.
        </Alert>
      )}

      {/* Упавшие задачи выносим наверх: в длинном списке они иначе теряются,
          а причина отказа — то, ради чего пользователь сюда и заходит. */}
      {failed.length > 0 && (
        <Alert
          color="red"
          title={`Не удалось скачать: ${failed.length}`}
          icon={<IconAlertTriangle size={18} />}
        >
          <Stack gap={6}>
            {failed.slice(0, 5).map((task) => (
              <div key={task.id}>
                <Text size="sm" style={{ wordBreak: 'break-word' }}>
                  {task.series_title}
                  {task.episode != null && `, серия ${task.episode}`}
                  {task.kind === 'subtitle' ? ' (субтитры)' : ' (видео)'}
                </Text>
                <Text size="xs" c="dimmed" style={{ wordBreak: 'break-word' }}>
                  {task.last_error ?? 'причина неизвестна'}
                </Text>
              </div>
            ))}
            {failed.length > 5 && (
              <Text size="xs" c="dimmed">
                и ещё {failed.length - 5} — весь список ниже
              </Text>
            )}
            <Group gap="xs" mt={4}>
              <Button
                size="xs"
                variant="light"
                color="red"
                leftSection={<IconRefresh size={14} />}
                onClick={() => retryAll.mutate()}
                loading={retryAll.isPending}
              >
                Повторить все неудачные
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      {postprocessing > 0 && (
        <Alert color="violet" icon={<Loader size="xs" />}>
          Идёт раскладка файлов по папкам: {postprocessing}
        </Alert>
      )}

      <Group gap="xs" wrap="wrap">
        {(Object.entries(counts) as [TaskStatus, number][]).map(([status, count]) => (
          <Badge key={status} variant="light" color={STATUS_COLORS[status] ?? 'gray'}>
            {STATUS_LABELS[status] ?? status}: {count}
          </Badge>
        ))}
      </Group>

      {tasks.length === 0 && (
        <Alert color="gray">
          Очередь пуста. Загрузки ставятся со страницы тайтла — кнопкой у конкретного перевода
          или батчем в сводке.
        </Alert>
      )}

      {active.length > 0 && <TaskTable title="В работе" tasks={active} action={action} />}
      {finished.length > 0 && <TaskTable title="Завершённые" tasks={finished} action={action} />}
    </Stack>
  )
}

interface TableProps {
  title: string
  tasks: DownloadTask[]
  action: ReturnType<typeof useMutation<unknown, Error, { fn: (id: number) => Promise<unknown>; id: number }>>
}

function TaskTable({ title, tasks, action }: TableProps) {
  return (
    <div>
      <Text size="sm" fw={600} mb={4}>
        {title} ({tasks.length})
      </Text>
      <Table.ScrollContainer minWidth={760}>
        <Table striped highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Что</Table.Th>
              <Table.Th w={230}>Прогресс</Table.Th>
              <Table.Th w={130}>Статус</Table.Th>
              <Table.Th w={120}>Действия</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} action={action} />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </div>
  )
}

function TaskRow({ task, action }: { task: DownloadTask; action: TableProps['action'] }) {
  const canPause = task.status === 'downloading' || task.status === 'queued'
  const canResume = task.status === 'paused'
  const canRetry = task.status === 'failed' || task.status === 'cancelled'
  const canCancel = ACTIVE_STATUSES.includes(task.status)

  return (
    <Table.Tr>
      <Table.Td style={{ wordBreak: 'break-word' }}>
        <Link to={`/series/${task.series_id}`}>{task.series_title || `#${task.series_id}`}</Link>
        <Text size="xs" c="dimmed">
          {task.episode != null && `серия ${task.episode} · `}
          {task.kind === 'video' ? 'видео' : 'субтитры'}
          {task.lang && ` · ${task.lang}`}
          {task.authors && ` · ${task.authors}`}
          {task.resolved_height && ` · ${task.resolved_height}p`}
        </Text>
        {task.last_error && (
          <Text size="xs" c="red" style={{ wordBreak: 'break-word' }}>
            {task.last_error}
            {task.attempts > 0 && ` (попыток: ${task.attempts})`}
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        {/* Объём бывает неизвестен: при сжатом ответе content-length описывает
            сжатые байты, и полоса врала бы. Тогда показываем только скачанное. */}
        {task.bytes_total > 0 ? (
          <>
            <Progress
              value={task.progress}
              size="sm"
              color={STATUS_COLORS[task.status] ?? 'blue'}
              animated={task.status === 'downloading'}
            />
            <Text size="xs" c="dimmed">
              {formatBytes(task.bytes_done)} из {formatBytes(task.bytes_total)}
              {task.status === 'downloading' && ` · ${formatSpeed(task.speed_bps)}`}
              {task.status === 'downloading' && ` · осталось ${formatEta(task)}`}
            </Text>
          </>
        ) : task.bytes_done > 0 ? (
          <>
            <Progress
              value={100}
              size="sm"
              color={STATUS_COLORS[task.status] ?? 'blue'}
              animated={task.status === 'downloading'}
            />
            <Text size="xs" c="dimmed">
              {formatBytes(task.bytes_done)}
              {task.status === 'downloading' && ` · ${formatSpeed(task.speed_bps)}`}
            </Text>
          </>
        ) : (
          <Text size="xs" c="dimmed">
            —
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        <Badge size="sm" variant="light" color={STATUS_COLORS[task.status] ?? 'gray'}>
          {STATUS_LABELS[task.status] ?? task.status}
        </Badge>
      </Table.Td>

      <Table.Td>
        <Group gap={2} wrap="nowrap">
          {canPause && (
            <Tooltip label="Пауза, файл сохранится">
              <ActionIcon
                size="sm"
                variant="subtle"
                onClick={() => action.mutate({ fn: downloadsApi.pause, id: task.id })}
              >
                <IconPlayerPause size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          {canResume && (
            <Tooltip label="Продолжить с того же места">
              <ActionIcon
                size="sm"
                variant="subtle"
                onClick={() => action.mutate({ fn: downloadsApi.resume, id: task.id })}
              >
                <IconPlayerPlay size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          {canRetry && (
            <Tooltip label="Повторить">
              <ActionIcon
                size="sm"
                variant="subtle"
                onClick={() => action.mutate({ fn: downloadsApi.retry, id: task.id })}
              >
                <IconRefresh size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          {canCancel && (
            <Tooltip label="Отменить, недокачанное удалится">
              <ActionIcon
                size="sm"
                variant="subtle"
                color="red"
                onClick={() => action.mutate({ fn: downloadsApi.cancel, id: task.id })}
              >
                <IconX size={14} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Table.Td>
    </Table.Tr>
  )
}
