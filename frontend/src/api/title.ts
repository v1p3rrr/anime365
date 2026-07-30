import { api } from './client'

export interface EpisodeItem {
  id: number
  episode_int: string
  episode_num: number | null
  episode_full: string
  episode_title: string
  episode_type: string
  is_content: boolean
}

export interface AuthorGroup {
  authors: string
  count: number
  translation_ids: number[]
  coverage: string
  is_complete: boolean
  max_height_estimate: number | null
  /** Страница самой ранней серии этого перевода на сайте — туда ведёт «Смотреть». */
  first_url: string | null
  first_episode: number | null
}

export interface SummarySection {
  lang: string
  kind: string
  total_translations: number
  groups: AuthorGroup[]
}

export interface QualityBucket {
  height: number
  episodes: string
  count: number
}

export interface QualitySummary {
  is_estimate: boolean
  uniform: number | null
  buckets: QualityBucket[]
  unknown_episodes: number
  /** Источник был 4K, но скачать можно максимум 1080 — UI поясняет, почему цифра ниже. */
  capped_from_4k: boolean
}

export interface TitleMappingInfo {
  mapping_id: number
  folder_id: number
  rel_path: string
  root_path: string
  root_kind: string
  /** false — том смонтирован только для чтения, загрузки в него невозможны. */
  writable: boolean
  season_number: number | null
  video_count: number
  size_bytes: number
  source: string
  confidence: number
  matched_on: string | null
  is_manual: boolean
  needs_confirmation: boolean
  /** На какое название из .nfo сматчилось — главное для проверки глазами. */
  nfo_title: string | null
  nfo_original_title: string | null
  nfo_premiered: string | null
}

export interface RoleCoverage {
  label: string
  have: number
  total: number
  complete: boolean
  missing_ranges: string
}

export interface LocalCoverage {
  coverage: Record<string, RoleCoverage>
  episodes_expected: number
  local_max_episode: number
  has_local_files: boolean
}

export interface TitlePageData {
  mappings: TitleMappingInfo[]
  local: LocalCoverage
  series: {
    id: number
    type: string
    type_title: string
    year: number | null
    season: string | null
    number_of_episodes: number
    poster_url: string | null
    url: string | null
    mal_score: number | null
    is_airing: boolean
  }
  titles: Record<string, string>
  all_titles: string[]
  descriptions: Array<{ source?: string; value?: string }>
  links: Array<{ title?: string; url?: string }>
  genres: Array<{ id: number; title: string }>
  episodes: EpisodeItem[]
  total_episodes: number
  summary: SummarySection[]
  quality: QualitySummary
}

export interface EmbedInfo {
  translation_id: number
  /** false у перевода с субтитрами означает, что они вшиты в видео. */
  has_subtitles: boolean
  heights: number[]
  from_cache: boolean
}

export const titleApi = {
  page: (id: number, refresh = false) =>
    api.get<TitlePageData>(`/series/${id}${refresh ? '?refresh=true' : ''}`),
  embed: (translationId: number) => api.get<EmbedInfo>(`/translations/${translationId}/embed`),
  unbind: (mappingId: number) => api.del<{ deleted: number }>(`/library/mappings/${mappingId}`),
}

export const LANG_LABELS: Record<string, string> = {
  ru: 'Русский',
  en: 'Английский',
  ja: 'Японский',
}

export const KIND_LABELS: Record<string, string> = {
  voice: 'Озвучка',
  sub: 'Субтитры',
  raw: 'Оригинал (RAW)',
}

/** Цвета разводят языки и типы визуально, как требует ТЗ. */
export const LANG_COLORS: Record<string, string> = {
  ru: 'blue',
  en: 'grape',
  ja: 'teal',
}

export const KIND_ICONS: Record<string, string> = {
  voice: '🔊',
  sub: '💬',
  raw: '🎬',
}
