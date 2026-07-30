/**
 * Настройки отображения — у каждого свои, в браузере.
 *
 * Сайт могут одновременно открыть несколько человек, а серверные настройки общие,
 * поэтому вкусовые вещи (какие языки показывать, какие типы переводов) живут в
 * localStorage. Ключи под версионированным префиксом: когда появятся аккаунты,
 * их можно будет перенести в серверный профиль миграцией, не переписывая UI.
 */

import { useCallback, useEffect, useState } from 'react'

const PREFIX = 'a365.v1.'

export type Lang = 'ru' | 'en' | 'ja'
export const ALL_LANGS: Lang[] = ['ru', 'en', 'ja']

export interface Preferences {
  /** ТЗ: должно быть нельзя выключить все три языка сразу. */
  langs: Lang[]
  kinds: string[]
  /**
   * Показывать ли значок английских субтитров на карточках каталога.
   *
   * Значка «озвучка по языкам» здесь нет намеренно: у видеофайла роль всегда
   * `video`, язык дорожки при сопоставлении не сохраняется. Тоггл без данных
   * был бы обманом — см. claude/08-open-questions.md.
   */
  showEnBadges: boolean
}

const DEFAULTS: Preferences = {
  langs: ['ru', 'en', 'ja'],
  kinds: ['voice', 'sub', 'raw'],
  // По ТЗ значок английских субтитров по умолчанию выключен.
  showEnBadges: false,
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

export function usePreferences() {
  const [prefs, setPrefs] = useState<Preferences>(() => ({
    langs: read('langs', DEFAULTS.langs),
    kinds: read('kinds', DEFAULTS.kinds),
    showEnBadges: read('showEnBadges', DEFAULTS.showEnBadges),
  }))

  useEffect(() => {
    for (const [key, value] of Object.entries(prefs)) write(key, value)
  }, [prefs])

  const toggleLang = useCallback((lang: Lang) => {
    setPrefs((prev) => {
      const next = prev.langs.includes(lang)
        ? prev.langs.filter((l) => l !== lang)
        : [...prev.langs, lang]
      // Запрет из ТЗ: последний включённый язык выключить нельзя.
      if (next.length === 0) return prev
      return { ...prev, langs: next }
    })
  }, [])

  const toggleEnBadges = useCallback(() => {
    setPrefs((prev) => ({ ...prev, showEnBadges: !prev.showEnBadges }))
  }, [])

  const toggleKind = useCallback((kind: string) => {
    setPrefs((prev) => {
      const next = prev.kinds.includes(kind)
        ? prev.kinds.filter((k) => k !== kind)
        : [...prev.kinds, kind]
      if (next.length === 0) return prev
      return { ...prev, kinds: next }
    })
  }, [])

  return { prefs, setPrefs, toggleLang, toggleKind, toggleEnBadges }
}
