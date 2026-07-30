import { api } from './client'

export interface LibraryRootInfo {
  id: number
  kind: 'series' | 'movie'
  path: string
  available: boolean
  /** false — корень смонтирован только для чтения, загрузки будут недоступны. */
  writable: boolean
  total_bytes: number
  folder_count: number
  scanned_at: string | null
}

export interface WatcherStatus {
  /** active — изменения видны сразу; fallback — живём на расписании. */
  state: 'active' | 'fallback' | 'disabled'
  reason: string | null
  watched_roots: string[]
  directories: number
  events_seen: number
  last_event_at: string | null
  last_rescan_at: string | null
}

export interface LibraryStatus {
  scanning: boolean
  /** Может отсутствовать, если сервер старее фронтенда — обращаться только через ?. */
  watcher?: WatcherStatus
  roots: LibraryRootInfo[]
  folders_total: number
  folders_mapped: number
  folders_unmapped: number
  needs_confirmation: number
  last_run: {
    status: string
    started_at: string | null
    finished_at: string | null
    stats: Record<string, unknown> | null
    error: string | null
  } | null
}

export interface UnmappedFolder {
  folder_id: number
  name: string
  rel_path: string
  root_kind: string
  season_number: number | null
  video_count: number
  size_bytes: number
  nfo_title: string | null
  nfo_year: number | null
  has_nfo: boolean
  parent_name: string | null
  /** Чем осмысленно заполнить поиск: для сезонной подпапки — название тайтла,
   *  а не «Season 2». */
  search_hint: string | null
}

export interface MappingRow {
  id: number
  folder_id: number
  folder: string
  season_number: number | null
  video_count: number
  series_id: number
  series_title: string | null
  series_year: number | null
  series_season: string | null
  source: string
  confidence: number
  matched_on: string | null
  is_manual: boolean
  needs_confirmation: boolean
}

export interface UnmatchedFile {
  file_id: number
  filename: string
  folder: string
  size_bytes: number
}

/** Понятные подписи для того, чем именно получена связь. */

export const SOURCE_COLORS: Record<string, string> = {
  anilist_id: 'teal',
  mal_id: 'teal',
  original_title: 'green',
  exact_title: 'green',
  premiered: 'blue',
  fuzzy: 'yellow',
  manual: 'grape',
}

export interface MappingReport {
  by_source: Array<{ source: string; count: number }>
  confidence_buckets: Array<{ range: string; count: number }>
  folders_total: number
  folders_mapped: number
  folders_unmapped: number
  manual: number
  needs_confirmation: number
  /** Самые слабые связи — с них начинается ручная проверка. */
  weakest: Array<{
    mapping_id: number
    folder: string
    series_id: number
    series_title: string
    source: string
    confidence: number
    matched_on: string | null
  }>
}

export const libraryApi = {
  status: () => api.get<LibraryStatus>('/library/status'),
  scan: () => api.post<Record<string, unknown>>('/library/scan'),
  unmapped: () => api.get<UnmappedFolder[]>('/library/unmapped?only_with_video=false'),
  unmatchedFiles: () => api.get<UnmatchedFile[]>('/library/unmatched-files'),
  mappingReport: () => api.get<MappingReport>('/library/mapping-report'),
  mappings: () => api.get<MappingRow[]>('/library/mappings'),
  createMapping: (folderId: number, seriesId: number) =>
    api.post<{ id: number }>('/library/mappings', { folder_id: folderId, series_id: seriesId }),
  confirmMapping: (id: number) => api.post<unknown>(`/library/mappings/${id}/confirm`),
}

/**
 * Размер с единицами измерения.
 *
 * Единицы приходят снаружи: в японском и английском они пишутся латиницей,
 * а в русском кириллицей, и зашивать одно из написаний нельзя.
 */
export function formatBytes(bytes: number, units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']): string {
  if (!bytes) return '—'
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value.toFixed(value >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`
}
