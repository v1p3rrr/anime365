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
  /** Ёмкость носителя, где лежит корень. null — корень недоступен. */
  disk_total_bytes: number | null
  disk_free_bytes: number | null
  /**
   * Ключ монтирования. У корней на одном носителе он совпадает, и свободное
   * место у них общее — показывать его дважды значит обещать вдвое больше.
   */
  mount: string | null
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
  /** Код причины: перевод на клиенте, подстановки в reason_params. */
  reason_params?: Record<string, string | number>
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

export interface JellyfinAuditFile {
  id: number
  filename: string
  rel_path: string
  folder: string
  root_kind: string
  size_bytes: number
}

/**
 * Размеченное объединение, а не набор необязательных полей: цифры существуют
 * ровно тогда, когда сверка состоялась. Так тип сам не даёт показать «не
 * распознано 0» там, где на самом деле не удалось спросить.
 */
export type JellyfinAuditResult =
  | {
      available: true
      our_files: number
      jellyfin_files: number
      missing_count: number
      missing: JellyfinAuditFile[]
      truncated: boolean
      by_folder: Array<{ folder: string; count: number }>
    }
  | {
      available: false
      /** `unreachable` — «не смогли спросить», это не то же, что «всё хорошо». */
      reason: 'disabled' | 'not_configured' | 'library_empty' | 'unreachable'
    }

/** Извлечённая звуковая дорожка одной серии. */
export interface AudioTrack {
  task_id: number
  translation_id: number
  series_id: number
  series_title: string
  episode: number | null
  authors: string
  lang: string | null
  filename: string
  size_bytes: number
  /** Контейнер, который браузер умеет открыть. `.mka` — не умеет. */
  playable: boolean
  created_at: string | null
}

/** Тайтл со своими дорожками — так фонотека и показывается. */
export interface AudioAlbum {
  series_id: number
  titles: Record<string, string>
  poster_url: string | null
  /** На случай, если тайтл исчез из каталога, а дорожки остались. */
  fallback_title: string
  track_count: number
  size_bytes: number
  tracks: AudioTrack[]
}

/** Коллекция Jellyfin — только то, что нужно для выбора. */
export interface JellyfinCollection {
  id: string
  name: string
  /** Тайтл уже в этой коллекции. Приходит, только когда спрашивали про тайтл. */
  contains?: boolean
}

export const libraryApi = {
  status: () => api.get<LibraryStatus>('/library/status'),
  scan: () => api.post<Record<string, unknown>>('/library/scan'),
  unmapped: () => api.get<UnmappedFolder[]>('/library/unmapped?only_with_video=false'),
  /**
   * Поиск по ВСЕМ папкам библиотеки.
   *
   * Для привязки тайтла к папке список нераспознанного не годится:
   * нужная папка обычно уже к чему-то привязана и в него не попадает.
   * Отбор идёт на сервере — папок тысячи.
   */
  searchFolders: (query: string) =>
    api.get<UnmappedFolder[]>(`/library/folders?query=${encodeURIComponent(query)}`),
  unmatchedFiles: () => api.get<UnmatchedFile[]>('/library/unmatched-files'),
  mappingReport: () => api.get<MappingReport>('/library/mapping-report'),
  jellyfinAudit: () => api.get<JellyfinAuditResult>('/library/jellyfin-audit'),
  audio: () => api.get<AudioAlbum[]>('/library/audio'),
  jellyfinCollections: (seriesId?: number) =>
    api.get<{ available: boolean; collections: JellyfinCollection[]; unreachable?: boolean }>(
      seriesId != null
        ? `/library/jellyfin/collections?series_id=${seriesId}`
        : '/library/jellyfin/collections',
    ),
  createCollection: (name: string, seriesIds: number[]) =>
    api.post<{ created: number; missing: number[] }>('/library/jellyfin/collection', {
      name,
      series_ids: seriesIds,
    }),
  addToCollection: (collectionId: string, seriesIds: number[]) =>
    api.post<{ added: number; missing: number[] }>('/library/jellyfin/collections/add', {
      collection_id: collectionId,
      series_ids: seriesIds,
    }),
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


/** Серия, которой назначена своя папка вместо общей папки тайтла. */
export interface EpisodeTarget {
  id: number
  episode: number
  folder_id: number
  rel_path: string
  root_path: string
  full_path: string
  writable: boolean
}

/** Папка-кандидат для назначения серии: окрестность тайтла, а не вся библиотека. */
export interface TargetFolderOption {
  folder_id: number
  rel_path: string
  name: string
  season_number: number | null
  video_count: number
  /** Том только для чтения показываем, но выбрать не даём. */
  writable: boolean
  /** Привязана ли папка к самому тайтлу — такие показываем первыми. */
  is_title_folder: boolean
}

export const episodeTargetApi = {
  list: (seriesId: number) =>
    api.get<{ targets: EpisodeTarget[] }>(`/library/episode-targets/${seriesId}`),
  folders: (seriesId: number) =>
    api.get<TargetFolderOption[]>(`/library/episode-targets/${seriesId}/folders`),
  clearAll: (seriesId: number) =>
    api.del<{ deleted: number }>(`/library/episode-targets/series/${seriesId}`),
  set: (seriesId: number, episode: number, folderId: number) =>
    api.post<{ id: number; created: boolean }>('/library/episode-targets', {
      series_id: seriesId,
      episode,
      folder_id: folderId,
    }),
  clear: (targetId: number) =>
    api.del<{ deleted: number }>(`/library/episode-targets/${targetId}`),
}
