import { api } from './client'

export interface EpisodeTranslation {
  id: number
  lang: string
  kind: string
  authors: string
  height: number | null
}

export interface EpisodeItem {
  id: number
  episode_int: string
  episode_num: number | null
  episode_full: string
  episode_title: string
  episode_type: string
  is_content: boolean
  /** Страница серии на сайте. null — переводов нет, выводить адрес не из чего. */
  url: string | null
  /** Что уже лежит локально: video, sub_ru, sub_ja, sub_en. */
  have: string[]
  /**
   * Языки звука локального видео. Пустой список при наличии видео означает
   * «определить не удалось» — тогда панель загрузки ничего не прячет.
   */
  audio_langs: string[]
  /**
   * Скачанные видеофайлы этой серии — их можно посмотреть прямо с диска.
   * Несколько, когда лежат и озвучка, и оригинал.
   */
  local_videos: LocalVideo[]
  /** Секунды; медиана по переводам серии. null — данных нет. */
  duration: number | null
  /** Переводы этой серии — из них выбирается, что скачать. */
  translations: EpisodeTranslation[]
}

export interface AuthorGroup {
  authors: string
  count: number
  /** Основные серии. Спешлы и OVA лежат отдельно — их берём только по запросу. */
  translation_ids: number[]
  extra_translation_ids: number[]
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
  /** true, пока хоть одна серия не уточнена через embed. */
  is_estimate: boolean
  /** Сколько серий уже уточнено точными ступенями. */
  refined_episodes: number
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
  /** Куда складываются загрузки, когда папок несколько. */
  is_primary: boolean
  /** true — выбрано пользователем, автовыбор больше не вмешивается. */
  primary_is_manual: boolean
  needs_confirmation: boolean
  /** Сколько серий каждой роли лежит именно в этой папке. */
  counts: Record<string, number>
  /** На какое название из .nfo сматчилось — главное для проверки глазами. */
  nfo_title: string | null
  nfo_original_title: string | null
  nfo_premiered: string | null
}

export interface RoleCoverage {
  label: string
  /** Различные номера серий, а не файлы: одна серия может лежать в двух копиях. */
  have: number
  total: number
  complete: boolean
  missing_ranges: string
  duplicate_count: number
  duplicate_ranges: string
}

export interface LocalCoverage {
  coverage: Record<string, RoleCoverage>
  episodes_expected: number
  local_max_episode: number
  has_local_files: boolean
}

/**
 * Куда уедет загрузка, если тайтл ещё не привязан к папке.
 *
 * Заполнено только при отсутствии привязки: папка создаётся сама при первой
 * загрузке, и её имя надо показать до нажатия, а не после.
 */
export interface ProposedFolder {
  folder_name: string
  /** Откуда взято написание: en | romaji | ru. Null — подходящего не нашлось. */
  name_source: string | null
  root_path: string
  root_kind: string
  full_path: string
  /** false — том только для чтения, папку создать не выйдет. */
  writable: boolean
  /**
   * Многосерийный фильм: единой папки не будет вовсе, у каждой части своя.
   * `folder_name` показан для первой — об этом надо сказать вслух, иначе
   * человек ждёт одну папку и получает три.
   */
  per_part: boolean
}

export interface TitlePageData {
  mappings: TitleMappingInfo[]
  proposed_folder: ProposedFolder | null
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
    /** null — решение не принято, действует автоопределение по типу. */
    treat_as_movie: boolean | null
    /** Как тайтл трактуется сейчас: от этого зависит корень для загрузок. */
    effective_is_movie: boolean
  }
  titles: Record<string, string>
  all_titles: string[]
  descriptions: Array<{ source?: string; value?: string }>
  links: Array<{ title?: string; url?: string }>
  genres: Array<{ id: number; title: string }>
  episodes: EpisodeItem[]
  total_episodes: number
  /** Типичная длительность серии в секундах — медиана серийных медиан. */
  typical_duration: number | null
  summary: SummarySection[]
  quality: QualitySummary
}

/** Файл на диске, пригодный для просмотра. */
export interface LocalVideo {
  /** Отдаём по нему, а не по пути: путь от клиента принимать нельзя. */
  file_id: number
  filename: string
  size_bytes: number
  /** Языки звука, если удалось определить. Пусто — не знаем. */
  langs: string[]
}

export interface EmbedInfo {
  translation_id: number
  /** false у перевода с субтитрами означает, что они вшиты в видео. */
  has_subtitles: boolean
  heights: number[]
  /**
   * Ступени, доступные для ПРОСМОТРА. Отдельно от `heights`: в ответе embed
   * это другой список, и совпадение наборов ниоткуда не следует.
   */
  stream_heights: number[]
  /**
   * Вес файла по ступеням: `{ "1080": 229376000 }`. Ключи строковые — так их
   * отдаёт JSON. Ступени без размера просто отсутствуют: узнать вес удаётся
   * не всегда, и это не повод прятать саму ступень.
   */
  sizes: Record<string, number>
  from_cache: boolean
}

/** Узел франшизы: тайтл из линейки, свой или чужой. */
export interface RelationNode {
  mal_id: number
  name: string | null
  /** Русская подпись типа с Шикимори: «TV Сериал», «Фильм». Не код. */
  kind: string | null
  year: number | null
  shikimori_url: string | null
  /** null — тайтла нет в нашем каталоге, внутрь вести некуда. */
  series_id: number | null
  /** Наши названия — чтобы показать на языке интерфейса. */
  titles: Record<string, string> | null
  /** Постер из нашего каталога. null — тайтла у нас нет, показывать нечего. */
  poster_url_small: string | null
}

export interface RelationsResponse {
  nodes: RelationNode[]
  /** false — у тайтла нет mal_id, спрашивать связи не у чего. */
  available: boolean
}

export interface JellyfinLink {
  available: boolean
  /** Ссылка на страницу тайтла в веб-интерфейсе Jellyfin. */
  url?: string
  item_id?: string
  /** Series | Season | Movie — на что именно указывает ссылка. */
  item_type?: string
  /**
   * Почему ссылки нет. Каждая причина чинится по-своему, поэтому их четыре,
   * а не одно «нет»: disabled — тумблер выключен, not_configured — включён,
   * но нет адреса или ключа, not_mapped — тайтл не привязан к папке,
   * not_found — Jellyfin ещё не видел эту папку.
   */
  reason?: 'disabled' | 'not_configured' | 'not_mapped' | 'not_found'
  /** Для not_configured: каких именно настроек не хватает. */
  missing?: Array<'url' | 'api_key'>
}

/**
 * То, чего нет в API Anime365, — приходит с Шикимори отдельным запросом.
 *
 * `available: false` — нормальный исход, а не ошибка: у тайтла может не быть
 * mal_id, а Шикимори может быть недоступен. Страница тайтла от этого не
 * страдает, просто блок студии не рисуется.
 */
export interface ShikimoriDetails {
  available: boolean
  studios?: { id: number; name: string; image: string | null }[]
  /** anons | ongoing | released — дословно как ответил источник. */
  status?: string | null
  aired_on?: string | null
  released_on?: string | null
  /** Сколько серий реально вышло. У Anime365 это план, а не факт. */
  episodes_aired?: number | null
  next_episode_at?: string | null
  rating?: string | null
  duration?: number | null
}

export const titleApi = {
  page: (id: number, refresh = false) =>
    api.get<TitlePageData>(`/series/${id}${refresh ? '?refresh=true' : ''}`),
  embed: (translationId: number, options?: { withSizes?: boolean }) =>
    api.get<EmbedInfo>(
      `/translations/${translationId}/embed${options?.withSizes ? '?with_sizes=true' : ''}`,
    ),
  relations: (seriesId: number) =>
    api.get<RelationsResponse>(`/series/${seriesId}/relations`),
  details: (seriesId: number) => api.get<ShikimoriDetails>(`/series/${seriesId}/details`),
  jellyfin: (seriesId: number) => api.get<JellyfinLink>(`/series/${seriesId}/jellyfin`),
  audio: (seriesId: number) =>
    api.get<{ tracks: import('./library').AudioTrack[] }>(`/series/${seriesId}/audio`),
  unbind: (mappingId: number) => api.del<{ deleted: number }>(`/library/mappings/${mappingId}`),
  makePrimary: (mappingId: number, seriesId: number) =>
    api.post<unknown>(`/library/mappings/${mappingId}/primary`, { series_id: seriesId }),
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
