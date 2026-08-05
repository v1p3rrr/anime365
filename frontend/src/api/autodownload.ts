import { api } from './client'

/** Языки, для которых автоскачивание вообще имеет смысл. */
export type AutoLang = 'ru' | 'en' | 'ja'

/** Глобальный режим языка. */
export type GlobalMode = 'off' | 'configured' | 'all'

/** Режим языка у конкретного тайтла. `default` — как решено глобально. */
export type TitleMode = 'default' | 'on' | 'off'

export interface LangRule {
  mode: GlobalMode
  /** Нормализованные имена групп: выше — приоритетнее. */
  priorities: string[]
  /** Из списка никого не нашлось — взять любую группу. */
  allow_any_fallback: boolean
  /** Список не используется вовсе. Значения при этом сохраняются. */
  allow_any: boolean
  /** Качать, даже если субтитры уже вшиты в контейнер. */
  ignore_embedded: boolean
}

export interface RulesResponse {
  languages: AutoLang[]
  modes: GlobalMode[]
  rules: Record<AutoLang, LangRule>
}

export interface AuthorSuggestion {
  key: string
  name: string
  /** Сколько переводов этой группы мы видели — по нему и порядок. */
  seen: number
}

/** Настройка языка у тайтла. `null` у переключателя — наследовать общее. */
export interface SeriesLangRule {
  mode: TitleMode
  priorities: string[]
  allow_any_fallback: boolean | null
  allow_any: boolean | null
  ignore_embedded: boolean | null
}

export interface EffectiveRule {
  priorities: string[]
  allow_any_fallback: boolean
  allow_any: boolean
  ignore_embedded: boolean
  /** Список взят у тайтла, а не общий. */
  from_series: boolean
}

export interface SeriesSettings {
  series_id: number
  modes: TitleMode[]
  /** Что задано у тайтла. Языка нет в объекте — своей настройки нет. */
  own: Partial<Record<AutoLang, SeriesLangRule>>
  /**
   * Что из этого следует. Языка нет — качать не будем, и причина может быть
   * в глобальном режиме, а не в настройке тайтла.
   */
  effective: Partial<Record<AutoLang, EffectiveRule>>
}

export interface ConfiguredSeries {
  series_id: number
  title_ru: string | null
  title_romaji: string | null
  poster_url: string | null
  languages: Partial<Record<AutoLang, { mode: TitleMode; has_own_list: boolean }>>
}

export interface RunResult {
  /** Сколько тайтлов дали находки. У одиночного запуска поля нет. */
  series?: number
  planned: number
  queued: number
  skipped?: string
}

export const autodownloadApi = {
  rules: () => api.get<RulesResponse>('/autodownload'),
  setRule: (lang: AutoLang, payload: LangRule) =>
    api.put<RulesResponse>(`/autodownload/${lang}`, payload),
  authors: (lang: AutoLang, query?: string) => {
    const params = new URLSearchParams({ lang })
    if (query) params.set('query', query)
    return api.get<{ authors: AuthorSuggestion[] }>(`/autodownload/authors?${params}`)
  },
  configured: () => api.get<{ series: ConfiguredSeries[] }>('/autodownload/configured'),
  series: (seriesId: number) =>
    api.get<SeriesSettings>(`/autodownload/series/${seriesId}`),
  setSeries: (seriesId: number, lang: AutoLang, payload: SeriesLangRule) =>
    api.put<SeriesSettings>(`/autodownload/series/${seriesId}/${lang}`, payload),
  clearSeries: (seriesId: number, lang: AutoLang) =>
    api.del<SeriesSettings>(`/autodownload/series/${seriesId}/${lang}`),
  runAll: () => api.post<RunResult>('/autodownload/run', {}),
  runSeries: (seriesId: number) =>
    api.post<RunResult>(`/autodownload/series/${seriesId}/run`, {}),
}
