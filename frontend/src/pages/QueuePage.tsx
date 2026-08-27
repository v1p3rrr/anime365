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
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { restrictToVerticalAxis } from '@dnd-kit/modifiers'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import {
  IconAlertTriangle,
  IconClock,
  IconPlayerPause,
  IconPlayerPlay,
  IconArrowDown,
  IconArrowUp,
  IconGripVertical,
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
  etaSeconds,
  STATUS_COLORS,
  type DownloadTask,
  type TaskStatus,
} from '../api/downloads'
import { ErrorState } from '../components/ErrorState'
import { useServerEvents, type ServerEvent } from '../hooks/useServerEvents'
import { useFormat } from '../i18n/format'
import { useT, type TranslationKey } from '../i18n'
import { useApiError } from '../i18n/apiError'
import scrollClasses from './ScrollPage.module.css'

/**
 * Подпись вида задачи.
 *
 * Раньше проверка была бинарной: всё, что не видео, подписывалось субтитрами.
 * Видов три, и звук попадал под чужую подпись — пользователь видел «субтитры»
 * там, где извлекалась дорожка. Неизвестный вид показываем как есть, а не
 * подставляем наугад: так новая разновидность будет заметна, а не притворится
 * старой.
 */
function useKindLabel() {
  const { t } = useT()
  return (kind: string) => {
    if (kind === 'video') return t('queue.kindVideo')
    if (kind === 'subtitle') return t('queue.kindSubtitle')
    if (kind === 'audio') return t('queue.kindAudio')
    return kind
  }
}

/** Подпись статуса. Рядом с `useKindLabel`, потому что нужна в тех же двух местах. */
function useStatusLabel() {
  const { t } = useT()
  return (status: string) => t(`status.${status}` as TranslationKey)
}

/**
 * Накладывает живые байты на задачу из списка — вместе с процентом.
 *
 * ⚠️ Процент приходится считать здесь. События несут `bytes_done`,
 * `bytes_total` и скорость, но не `progress`: тот приезжает только со списком,
 * то есть раз в пятнадцать секунд. Полоса из-за этого стояла на месте, пока
 * подпись прямо под ней бежала, — загрузка выглядела зависшей ровно тогда,
 * когда шла быстрее всего.
 *
 * Формула та же, что у `DownloadTask.progress` на сервере: доля скачанного с
 * потолком в сотню. Второго правила здесь заводить нельзя — полоса и список
 * обязаны показывать одно и то же число.
 */
function withLiveProgress(task: DownloadTask, live?: Partial<DownloadTask>): DownloadTask {
  if (!live) return task
  const merged = { ...task, ...live }
  return {
    ...merged,
    progress:
      merged.bytes_total > 0
        ? Math.min(100, (merged.bytes_done / merged.bytes_total) * 100)
        : merged.progress,
  }
}

export function QueuePage() {
  const { t } = useT()
  const kindLabel = useKindLabel()
  const apiError = useApiError()
  const statusLabel = useStatusLabel()
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
      notifications.show({ color: 'red', title: t('queue.actionFailed'), message: apiError(error) }),
  })

  const pauseAll = useMutation({ mutationFn: downloadsApi.pauseAll, onSuccess: refresh })
  const resumeAll = useMutation({ mutationFn: downloadsApi.resumeAll, onSuccess: refresh })
  const clearFinished = useMutation({
    mutationFn: downloadsApi.clearFinished,
    onSuccess: (result) => {
      notifications.show({ color: 'teal', message: t('queue.cleared', { count: result.deleted }) })
      refresh()
    },
  })

  // Отдельно от очистки завершённых: то про историю успешных, а это про
  // разбор завалов. Часть ошибок повторяется при каждой попытке — вшитые
  // субтитры, удалённый перевод — и без такой кнопки список только растёт.
  const clearFailed = useMutation({
    mutationFn: downloadsApi.clearFailed,
    onSuccess: (result) => {
      notifications.show({ color: 'teal', message: t('queue.cleared', { count: result.deleted }) })
      refresh()
    },
  })

  // Порядок применяем сразу и целиком: сервер перенумеровывает список, и
  // присылать одну перестановку было бы гаданием о том, что у него сейчас.
  const reorder = useMutation({
    mutationFn: (ids: number[]) => downloadsApi.reorder(ids),
    onSuccess: refresh,
    onError: (error: Error) => {
      // Список у клиента мог устареть — тогда перечитываем и показываем как есть.
      notifications.show({ color: 'red', message: apiError(error) })
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
      notifications.show({ color: 'teal', message: t('queue.requeued', { count }) })
      refresh()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('queue.actionFailed'), message: apiError(error) }),
  })

  if (queue.isError) {
    return (
      <ErrorState
        error={queue.error}
        title={t('queue.loadFailed')}
        onRetry={() => queue.refetch()}
      />
    )
  }

  const data = queue.data
  const tasks = data?.tasks ?? []
  const merged = tasks.map((task) => withLiveProgress(task, live[task.id]))

  const active = merged.filter((t) => ACTIVE_STATUSES.includes(t.status))
  const finished = merged.filter((t) => !ACTIVE_STATUSES.includes(t.status))
  const failed = merged.filter((t) => t.status === 'failed')

  const counts = data?.counts ?? {}
  const postprocessing = counts.postprocessing ?? 0

  return (
    <Stack gap="md" className={scrollClasses.scrollPage}>
      <Group justify="space-between" wrap="wrap">
        <Group gap="sm" align="center">
          <Title order={3}>{t('queue.title')}</Title>
          {queue.isFetching && <Loader size="xs" />}
          <Tooltip
            label={
              connected
                ? t('queue.liveOn')
                : t('queue.liveOff')
            }
          >
            <Badge size="sm" variant="light" color={connected ? 'teal' : 'gray'}>
              {connected ? t('queue.online') : t('queue.polling')}
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
              {t('queue.resumeAll')}
            </Button>
          ) : (
            <Button
              size="xs"
              variant="default"
              leftSection={<IconPlayerPause size={14} />}
              onClick={() => pauseAll.mutate()}
              loading={pauseAll.isPending}
            >
              {t('queue.pauseAll')}
            </Button>
          )}
          <Button
            size="xs"
            variant="subtle"
            color="red"
            leftSection={<IconTrash size={14} />}
            onClick={() => clearFailed.mutate()}
            loading={clearFailed.isPending}
            disabled={failed.length === 0}
          >
            {t('queue.clearFailed')}
          </Button>
          <Button
            size="xs"
            variant="subtle"
            color="gray"
            leftSection={<IconTrash size={14} />}
            onClick={() => clearFinished.mutate()}
            loading={clearFinished.isPending}
            disabled={finished.length === 0}
          >
            {t('queue.clearFinished')}
          </Button>
        </Group>
      </Group>

      {data?.paused && (
        <Alert color="yellow">
          {t('queue.pausedNote')}
        </Alert>
      )}

      {/* Упавшие задачи выносим наверх: в длинном списке они иначе теряются,
          а причина отказа — то, ради чего пользователь сюда и заходит. */}
      {failed.length > 0 && (
        <Alert
          color="red"
          title={t('queue.failedTitle', { count: failed.length })}
          icon={<IconAlertTriangle size={18} />}
        >
          <Stack gap={6}>
            {failed.slice(0, 5).map((task) => (
              <div key={task.id}>
                <Text size="sm" style={{ wordBreak: 'break-word' }}>
                  {task.series_title}
                  {task.episode != null && t('queue.episodeSuffix', { n: task.episode })}
                  {` (${kindLabel(task.kind)})`}
                </Text>
                <Text size="xs" c="dimmed" style={{ wordBreak: 'break-word' }}>
                  {task.last_error ?? t('queue.reasonUnknown')}
                </Text>
              </div>
            ))}
            {failed.length > 5 && (
              <Text size="xs" c="dimmed">
                {t('queue.andMore', { count: failed.length - 5 })}
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
                {t('queue.retryAllFailed')}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      {postprocessing > 0 && (
        <Alert color="violet" icon={<Loader size="xs" />}>
          {t('queue.postprocessingNote', { count: postprocessing })}
        </Alert>
      )}

      <Group gap="xs" wrap="wrap">
        {(Object.entries(counts) as [TaskStatus, number][]).map(([status, count]) => (
          <Badge key={status} variant="light" color={STATUS_COLORS[status] ?? 'gray'}>
            {statusLabel(status)}: {count}
          </Badge>
        ))}
      </Group>

      {tasks.length === 0 && (
        <Alert color="gray">{t('queue.empty')}</Alert>
      )}

      {active.length > 0 && (
        <TaskTable
          title={t('queue.active')}
          tasks={active}
          action={action}
          onReorder={(ids) => reorder.mutate(ids)}
        />
      )}
      {finished.length > 0 && <TaskTable title={t('queue.finished')} tasks={finished} action={action} />}
    </Stack>
  )
}

interface TableProps {
  title: string
  tasks: DownloadTask[]
  action: ReturnType<typeof useMutation<unknown, Error, { fn: (id: number) => Promise<unknown>; id: number }>>
  /** Задан только для очереди: историю перетаскивать незачем. */
  onReorder?: (ids: number[]) => void
}

function TaskTable({ title, tasks, action, onReorder }: TableProps) {
  const { t } = useT()

  // Датчики: мышь с порогом в 5 пикселей, чтобы клик по кнопке в строке не
  // превращался в попытку перетаскивания, и касание с задержкой — иначе на
  // телефоне не прокрутить список, каждое движение хватало бы строку.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 6 } }),
    // Клавиатура: очередь должна двигаться и без мыши.
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const handleDragEnd = (event: DragEndEvent) => {
    const { active: dragged, over } = event
    if (!over || dragged.id === over.id || !onReorder) return
    const from = tasks.findIndex((task) => task.id === dragged.id)
    const to = tasks.findIndex((task) => task.id === over.id)
    if (from < 0 || to < 0) return
    onReorder(arrayMove(tasks, from, to).map((task) => task.id))
  }

  const body = (
    <div>
      <Text size="sm" fw={600} mb={4}>
        {title} ({tasks.length})
      </Text>
      <Table.ScrollContainer minWidth={760}>
        <Table striped highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('queue.columnWhat')}</Table.Th>
              <Table.Th w={230}>{t('queue.columnProgress')}</Table.Th>
              <Table.Th w={130}>{t('queue.columnStatus')}</Table.Th>
              <Table.Th w={120}>{t('queue.columnActions')}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} action={action} draggable={!!onReorder} />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </div>
  )

  // Историю не перетаскиваем: порядок там ничего не решает.
  if (!onReorder) return body

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      // Ограничиваем движение вертикалью: список одномерный, и горизонтальный
      // сдвиг строки только мешает целиться. Ограничения родителем нет
      // намеренно: у таблицы внутри Table.ScrollContainer им оказывается
      // прокручиваемая обёртка, и строка упиралась в её край.
      modifiers={[restrictToVerticalAxis]}
      onDragEnd={handleDragEnd}
    >
      <SortableContext items={tasks.map((task) => task.id)} strategy={verticalListSortingStrategy}>
        {body}
      </SortableContext>
    </DndContext>
  )
}

function TaskRow({
  task,
  action,
  draggable = false,
}: {
  task: DownloadTask
  action: TableProps['action']
  draggable?: boolean
}) {
  const { t } = useT()
  const sortable = useSortable({ id: task.id, disabled: !draggable })
  const kindLabel = useKindLabel()
  const format = useFormat()
  const statusLabel = useStatusLabel()
  const canPause = task.status === 'downloading' || task.status === 'queued'
  const canResume = task.status === 'paused'
  // Перезапуск нужен и зависшей загрузке, а не только упавшей: активную
  // сервер сначала остановит. Скачанные байты не теряются — докачка идёт по
  // Range.
  const canRetry = task.status !== 'done' && task.status !== 'already_exists'
  const canCancel = ACTIVE_STATUSES.includes(task.status)
  // Двигать в очереди можно только незавершённое: у завершённого очереди нет.
  const canRequeue = !['done', 'already_exists', 'cancelled', 'failed'].includes(task.status)
  // Время окончания есть только у завершённых — у остальных строка одна.
  const addedAt = format.dateTime(task.created_at)
  const finishedAt = format.dateTime(task.finished_at)

  return (
    <Table.Tr
      ref={sortable.setNodeRef}
      style={{
        // transform от dnd-kit — это сдвиг строки под курсором. CSS.Transform
        // сериализует его так, чтобы не сломать масштаб таблицы.
        transform: CSS.Transform.toString(sortable.transform),
        transition: sortable.transition,
        // Перетаскиваемая строка поднимается над остальными, иначе соседи
        // перекрывают её при наложении.
        zIndex: sortable.isDragging ? 2 : undefined,
        position: sortable.isDragging ? 'relative' : undefined,
        opacity: sortable.isDragging ? 0.85 : undefined,
      }}
    >
      <Table.Td style={{ wordBreak: 'break-word' }}>
        <Link to={`/series/${task.series_id}`}>{task.series_title || `#${task.series_id}`}</Link>
        <Text size="xs" c="dimmed">
          {task.episode != null && t('queue.episodePrefix', { n: task.episode })}
          {kindLabel(task.kind)}
          {task.lang && ` · ${task.lang}`}
          {task.authors && ` · ${task.authors}`}
          {task.resolved_height && ` · ${task.resolved_height}p`}
        </Text>
        {task.last_error && (
          <Text size="xs" c="red" style={{ wordBreak: 'break-word' }}>
            {task.last_error}
            {task.attempts > 0 && t('queue.attemptsCount', { count: task.attempts })}
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
              {t('queue.ofBytes', { done: format.size(task.bytes_done), total: format.size(task.bytes_total) })}
              {task.status === 'downloading' && ` · ${format.speed(task.speed_bps)}`}
              {task.status === 'downloading' && t('queue.remaining', { eta: format.eta(etaSeconds(task)) })}
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
              {format.size(task.bytes_done)}
              {task.status === 'downloading' && ` · ${format.speed(task.speed_bps)}`}
            </Text>
          </>
        ) : (
          <Text size="xs" c="dimmed">
            —
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        <Group gap={4} wrap="nowrap">
          <Badge size="sm" variant="light" color={STATUS_COLORS[task.status] ?? 'gray'}>
            {statusLabel(task.status)}
          </Badge>
          {/* Времена под иконкой, а не строкой в таблице: колонок и так пять,
              а дата нужна изредка — когда разбираются, почему что-то встало. */}
          {addedAt && (
            <Tooltip
              multiline
              w={210}
              withArrow
              // На телефоне наведения нет, поэтому подсказка открывается по
              // тапу. Без `touch` иконка там была бы мёртвой.
              events={{ hover: true, focus: true, touch: true }}
              label={
                <>
                  <Text size="xs">{t('queue.addedAt', { at: addedAt })}</Text>
                  {finishedAt && (
                    <Text size="xs">{t('queue.finishedAt', { at: finishedAt })}</Text>
                  )}
                </>
              }
            >
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                aria-label={t('queue.timesHint')}
                // Курсор-подсказка, а не «нажми меня»: иконка ничего не делает.
                style={{ cursor: 'help' }}
              >
                <IconClock size={14} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Table.Td>

      <Table.Td>
        <Group gap={2} wrap="nowrap">
          {/* Ручка, а не вся строка: иначе не нажать ни одну кнопку внутри —
              любое движение мышью по строке начинало бы перетаскивание. */}
          {draggable && (
            <Tooltip label={t('queue.dragHint')}>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                style={{ cursor: 'grab', touchAction: 'none' }}
                {...sortable.attributes}
                {...sortable.listeners}
                aria-label={t('queue.dragHint')}
              >
                <IconGripVertical size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          {canPause && (
            <Tooltip label={t('queue.pauseHint')}>
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
            <Tooltip label={t('queue.resumeHint')}>
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
            <Tooltip label={t('common.retry')}>
              <ActionIcon
                size="sm"
                variant="subtle"
                onClick={() => action.mutate({ fn: downloadsApi.retry, id: task.id })}
              >
                <IconRefresh size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          {canRequeue && (
            <Tooltip label={t('queue.prioritizeHint')}>
              <ActionIcon
                size="sm"
                variant="subtle"
                onClick={() => action.mutate({ fn: downloadsApi.prioritize, id: task.id })}
              >
                <IconArrowUp size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          {canRequeue && (
            <Tooltip label={t('queue.requeueHint')}>
              <ActionIcon
                size="sm"
                variant="subtle"
                onClick={() => action.mutate({ fn: downloadsApi.requeue, id: task.id })}
              >
                <IconArrowDown size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          {/* Удаление доступно всегда: активную задачу сервер сперва отменит.
              Иначе ошибочные загрузки — например вшитые субтитры, которые
              будут падать при каждой попытке, — оставалось бы только терпеть. */}
          <Tooltip label={t('queue.removeHint')}>
            <ActionIcon
              size="sm"
              variant="subtle"
              color="red"
              onClick={() => action.mutate({ fn: downloadsApi.remove, id: task.id })}
            >
              <IconTrash size={14} />
            </ActionIcon>
          </Tooltip>
          {canCancel && (
            <Tooltip label={t('queue.cancelHint')}>
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
