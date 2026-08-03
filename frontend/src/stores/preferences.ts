/**
 * Настройки отображения — у каждого свои, в браузере.
 *
 * Сайт могут одновременно открыть несколько человек, а серверные настройки общие,
 * поэтому вкусовые вещи (какие типы переводов показывать) живут в localStorage.
 * Ключи под версионированным префиксом: когда появятся аккаунты, их можно будет
 * перенести в серверный профиль миграцией, не переписывая UI.
 */

import { useCallback, useEffect, useState } from 'react'

const PREFIX = 'a365.v1.'

export type Lang = 'ru' | 'en' | 'ja'
export const ALL_LANGS: Lang[] = ['ru', 'en', 'ja']

/**
 * Тип перевода — это пара «вид × язык», а не два независимых признака.
 *
 * Прежняя модель «языки × виды» их разделяла, и выключить английскую озвучку,
 * оставив английские субтитры, было нельзя: галочка `en` гасила и то и другое.
 * Здесь шесть значений — ровно те сочетания, которые встречаются в данных.
 */
export type TranslationType = 'sub_ja' | 'sub_ru' | 'sub_en' | 'raw_ja' | 'voice_ru' | 'voice_en'

/**
 * Порядок = приоритет показа, сверху вниз.
 *
 * Субтитры идут раньше видео потому, что перевод с субтитрами несёт и саму
 * японскую дорожку: скачав его, получаешь и оригинал. Озвучка в конце — она
 * заменяет оригинал целым вторым файлом, а не дополняет библиотеку.
 *
 * Разделы, уже закрытые локальными файлами, этот порядок не меняют — они просто
 * уходят в конец списка независимо от того, где стоят здесь.
 */
export const TRANSLATION_TYPES: TranslationType[] = [
  'sub_ja',
  'sub_ru',
  'sub_en',
  'raw_ja',
  'voice_ru',
  'voice_en',
]

/** Позиция типа в порядке приоритета. Неизвестные сочетания — в самый конец. */
export function typeRank(type: TranslationType | null): number {
  const index = type === null ? -1 : TRANSLATION_TYPES.indexOf(type)
  return index < 0 ? TRANSLATION_TYPES.length : index
}

/**
 * Ключ типа по виду и языку перевода.
 *
 * `null` означает сочетание, которого нет в списке (например `voice` + `ja`).
 * Такие не прячутся и не сортируются: скрыть по незнанию хуже, чем показать
 * лишнее — пользователь как минимум увидит, что вариант существует.
 */
export function translationType(kind: string, lang: string): TranslationType | null {
  const key = `${kind}_${lang}`
  return (TRANSLATION_TYPES as string[]).includes(key) ? (key as TranslationType) : null
}

/** Виден ли перевод при текущем наборе включённых типов. */
export function isTypeVisible(kind: string, lang: string, enabled: TranslationType[]): boolean {
  const type = translationType(kind, lang)
  return type === null || enabled.includes(type)
}

/**
 * Как листать каталог.
 *
 * `scroll` — подгрузка следующей порции при прокрутке; так каталог смотрят
 * почти всегда, поэтому режим по умолчанию.
 * `pages` — нумерованные страницы. Нужны, когда важно вернуться в то же место
 * или дать кому-то ссылку на конкретную страницу.
 */
export type PagingMode = 'scroll' | 'pages'

export interface Preferences {
  /**
   * Включённые типы переводов. Действуют везде, где переводы вообще видны:
   * сводка и батч-кнопки на странице тайтла, панель загрузки серии и значки
   * наличия на карточках каталога. Выключить все сразу нельзя — пустой список
   * означал бы пустой интерфейс без объяснения причины.
   */
  types: TranslationType[]
  /** Способ листания каталога. */
  paging: PagingMode
  /** Сколько тайтлов запрашивать за раз. Меньше — легче слабому устройству. */
  pageSize: number
  /**
   * Показывать ромадзи главным названием, а перевод — вторым.
   *
   * Отдельно от языка интерфейса намеренно: это разные вопросы. Язык
   * интерфейса — на каком языке подписи и кнопки; здесь — под каким названием
   * человек привык узнавать тайтл. Многие читают интерфейс по-русски, но
   * ищут и обсуждают аниме по ромадзи.
   */
  preferRomaji: boolean
}

/** Допустимые размеры порции. Ограничены сверху лимитом сервера. */
export const PAGE_SIZES = [50, 100, 200] as const

const DEFAULTS: Preferences = {
  // По ТЗ значок английских субтитров по умолчанию выключен, а вместе с ним
  // прячется и весь английский раздел — иначе значка нет, а кнопки есть.
  types: ['sub_ja', 'sub_ru', 'raw_ja', 'voice_ru'],
  paging: 'scroll',
  pageSize: 200,
  preferRomaji: false,
}

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    return raw === null ? fallback : (JSON.parse(raw) as T)
  } catch {
    // Испорченное значение не должно ломать страницу.
    return fallback
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value))
  } catch {
    // Приватный режим или переполненное хранилище — просто работаем без сохранения.
  }
}

/**
 * Переносит настройки со старой модели «языки × виды».
 *
 * Без переноса у того, кто уже выключал языки, набор молча схлопнулся бы к
 * значениям по умолчанию, и это выглядело бы как потеря настроек, а не как
 * обновление.
 */
function readTypes(): TranslationType[] {
  const stored = read<TranslationType[] | null>('types', null)
  if (stored && stored.length > 0) return stored

  const langs = read<string[] | null>('langs', null)
  const kinds = read<string[] | null>('kinds', null)
  const showEn = read<boolean | null>('showEnBadges', null)
  if (!langs && !kinds) return DEFAULTS.types

  const migrated = TRANSLATION_TYPES.filter((type) => {
    const [kind, lang] = splitType(type)
    if (langs && !langs.includes(lang)) return false
    if (kinds && !kinds.includes(kind)) return false
    // Английские субтитры раньше управлялись отдельным тогглом значков.
    if (type === 'sub_en' && showEn === false) return false
    return true
  })

  return migrated.length > 0 ? migrated : DEFAULTS.types
}

/** `voice_ru` → `['voice', 'ru']`. Вид может содержать подчёркивание только в языке. */
export function splitType(type: TranslationType): [kind: string, lang: string] {
  const at = type.lastIndexOf('_')
  return [type.slice(0, at), type.slice(at + 1)]
}

export function usePreferences() {
  const [prefs, setPrefs] = useState<Preferences>(() => ({
    types: readTypes(),
    paging: read('paging', DEFAULTS.paging),
    // Чужое значение из будущей версии не должно ломать запрос: сервер
    // откажет на слишком большом лимите, и каталог останется пустым.
    pageSize: PAGE_SIZES.includes(read('pageSize', DEFAULTS.pageSize) as never)
      ? read('pageSize', DEFAULTS.pageSize)
      : DEFAULTS.pageSize,
    preferRomaji: read('preferRomaji', DEFAULTS.preferRomaji),
  }))

  useEffect(() => {
    for (const [key, value] of Object.entries(prefs)) write(key, value)
  }, [prefs])

  const toggleType = useCallback((type: TranslationType) => {
    setPrefs((prev) => {
      const next = prev.types.includes(type)
        ? prev.types.filter((value) => value !== type)
        : // Сохраняем канонический порядок, а не порядок нажатий: иначе список
          // в настройках перетасовывался бы на каждый клик.
          TRANSLATION_TYPES.filter((value) => value === type || prev.types.includes(value))
      // Последний включённый тип выключить нельзя: пустой список означал бы
      // страницу тайтла без единой кнопки загрузки и без объяснения почему.
      if (next.length === 0) return prev
      return { ...prev, types: next }
    })
  }, [])

  const setPaging = useCallback((paging: PagingMode) => {
    setPrefs((prev) => ({ ...prev, paging }))
  }, [])

  const setPageSize = useCallback((pageSize: number) => {
    setPrefs((prev) => ({ ...prev, pageSize }))
  }, [])

  const setPreferRomaji = useCallback((preferRomaji: boolean) => {
    setPrefs((prev) => ({ ...prev, preferRomaji }))
  }, [])

  return { prefs, setPrefs, toggleType, setPaging, setPageSize, setPreferRomaji }
}


/**
 * Предпочтение ромадзи — отдельным маленьким хранилищем.
 *
 * Почему не через `usePreferences`: тот держит своё состояние в каждом
 * вызывающем компоненте, и переключатель в настройках не дошёл бы до карточек
 * каталога и заголовков — они обновились бы только после перезагрузки
 * страницы. Названия показываются буквально везде, поэтому здесь нужна
 * подписка, а не копия.
 *
 * Реализация нарочно крошечная: заводить внешнюю библиотеку состояния ради
 * одного булева значения незачем.
 */
const ROMAJI_KEY = 'preferRomaji'
const romajiListeners = new Set<(value: boolean) => void>()

export function getPreferRomaji(): boolean {
  return read(ROMAJI_KEY, DEFAULTS.preferRomaji)
}

export function setPreferRomajiGlobal(value: boolean): void {
  write(ROMAJI_KEY, value)
  for (const listener of romajiListeners) listener(value)
}

export function useRomajiPreference(): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState(getPreferRomaji)

  useEffect(() => {
    romajiListeners.add(setValue)
    // Ещё и storage: настройку могли поменять в соседней вкладке.
    const onStorage = (event: StorageEvent) => {
      if (event.key === PREFIX + ROMAJI_KEY) setValue(getPreferRomaji())
    }
    window.addEventListener('storage', onStorage)
    return () => {
      romajiListeners.delete(setValue)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  return [value, setPreferRomajiGlobal]
}
