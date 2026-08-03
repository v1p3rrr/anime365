import { api } from './client'

export type TaskStatus =
  | 'queued'
  | 'downloading'
  | 'downloaded'
  | 'postprocessing'
  | 'done'
  | 'failed'
  | 'paused'
  | 'cancelled'
  | 'already_exists'

export interface DownloadTask {
  id: number
  translation_id: number
  series_id: number
  series_title: string
  episode: number | null
  kind: 'video' | 'subtitle'
  authors: string
  lang: string | null
  requested_height: number | null
  resolved_height: number | null
  status: TaskStatus
  attempts: number
  last_error: string | null
  bytes_done: number
  bytes_total: number
  speed_bps: number
  progress: number
  target_path: string | null
  created_at: string | null
  finished_at: string | null
}

export interface QueueState {
  paused: boolean
  counts: Partial<Record<TaskStatus, number>>
  tasks: DownloadTask[]
}

export const STATUS_COLORS: Record<TaskStatus, string> = {
  queued: 'gray',
  downloading: 'blue',
  downloaded: 'cyan',
  postprocessing: 'violet',
  done: 'teal',
  failed: 'red',
  paused: 'yellow',
  cancelled: 'gray',
  already_exists: 'teal',
}

/** Статусы, при которых задача ещё движется — их показываем первыми. */
export const ACTIVE_STATUSES: TaskStatus[] = [
  'downloading',
  'postprocessing',
  'downloaded',
  'queued',
  'paused',
]

/** audio — извлечение звуковой дорожки в отдельный корень фонотеки. */
export type DownloadKind = 'video' | 'subtitle' | 'audio'

export const downloadsApi = {
  list: () => api.get<QueueState>('/downloads'),
  enqueue: (translationId: number, kind: DownloadKind, height?: number) =>
    api.post<{ id: number | null; created: boolean; already_exists?: boolean }>('/downloads', {
      translation_id: translationId,
      kind,
      height,
    }),
  batch: (
    translationIds: number[],
    kind: DownloadKind | 'both',
    height?: number,
    /** Пропустить серии, у которых нужное уже лежит локально. */
    onlyMissing = false,
  ) =>
    api.post<{
      created: number
      skipped: number
      failed: number
      /** Пропущено потому, что уже есть на диске. Не ошибка. */
      already_local?: number
      errors: string[]
    }>('/downloads/batch', {
      translation_ids: translationIds,
      kind,
      height,
      only_missing: onlyMissing,
    }),
  retry: (id: number) => api.post<unknown>(`/downloads/${id}/retry`),
  pause: (id: number) => api.post<unknown>(`/downloads/${id}/pause`),
  resume: (id: number) => api.post<unknown>(`/downloads/${id}/resume`),
  cancel: (id: number) => api.post<unknown>(`/downloads/${id}/cancel`),
  pauseAll: () => api.post<unknown>('/downloads/pause-all'),
  resumeAll: () => api.post<unknown>('/downloads/resume-all'),
  /** Новый порядок очереди — идентификаторы сверху вниз. */
  reorder: (ids: number[]) => api.post<{ reordered: number }>('/downloads/reorder', { ids }),
  /** В конец очереди: активную задачу сперва останавливает сервер. */
  requeue: (id: number) => api.post<unknown>(`/downloads/${id}/requeue`),
  /** Убрать одну запись. Активную сервер сначала отменяет. */
  remove: (id: number) => api.del<{ deleted: boolean }>(`/downloads/${id}`),
  clearFinished: () => api.post<{ deleted: number }>('/downloads/clear-finished'),
  clearFailed: () => api.post<{ deleted: number }>('/downloads/clear-failed'),
}

/**
 * Сколько секунд осталось качать. Только число — форматирование с единицами
 * живёт в `i18n/format`, потому что «МБ/с» и «мин» тоже переводятся, а здесь,
 * в слое API, локали нет.
 */
export function etaSeconds(task: DownloadTask): number {
  if (task.status !== 'downloading' || !task.speed_bps || !task.bytes_total) return 0
  const left = task.bytes_total - task.bytes_done
  return left <= 0 ? 0 : Math.round(left / task.speed_bps)
}
