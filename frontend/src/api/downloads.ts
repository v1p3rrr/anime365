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

export const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: 'в очереди',
  downloading: 'скачивается',
  downloaded: 'скачано',
  postprocessing: 'раскладывается',
  done: 'готово',
  failed: 'ошибка',
  paused: 'пауза',
  cancelled: 'отменено',
  already_exists: 'уже было',
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
    api.post<{ id: number; created: boolean }>('/downloads', {
      translation_id: translationId,
      kind,
      height,
    }),
  batch: (translationIds: number[], kind: DownloadKind | 'both', height?: number) =>
    api.post<{ created: number; skipped: number; failed: number; errors: string[] }>(
      '/downloads/batch',
      { translation_ids: translationIds, kind, height },
    ),
  retry: (id: number) => api.post<unknown>(`/downloads/${id}/retry`),
  pause: (id: number) => api.post<unknown>(`/downloads/${id}/pause`),
  resume: (id: number) => api.post<unknown>(`/downloads/${id}/resume`),
  cancel: (id: number) => api.post<unknown>(`/downloads/${id}/cancel`),
  pauseAll: () => api.post<unknown>('/downloads/pause-all'),
  resumeAll: () => api.post<unknown>('/downloads/resume-all'),
  clearFinished: () => api.post<{ deleted: number }>('/downloads/clear-finished'),
}

export function formatSpeed(bps: number): string {
  if (!bps) return '—'
  const mb = bps / (1024 * 1024)
  return mb >= 1 ? `${mb.toFixed(1)} МБ/с` : `${(bps / 1024).toFixed(0)} КБ/с`
}

export function formatEta(task: DownloadTask): string {
  if (task.status !== 'downloading' || !task.speed_bps || !task.bytes_total) return '—'
  const left = task.bytes_total - task.bytes_done
  if (left <= 0) return '—'
  const seconds = Math.round(left / task.speed_bps)
  if (seconds < 60) return `${seconds} с`
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`
  return `${(seconds / 3600).toFixed(1)} ч`
}
