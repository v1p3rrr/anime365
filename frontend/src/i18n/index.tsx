/**
 * Язык интерфейса.
 *
 * Хранится в браузере, а не в серверных настройках: сайт могут открыть
 * несколько человек, и язык — личное дело каждого.
 *
 * Русский словарь — источник правды: его тип задаёт форму остальных, поэтому
 * забытый ключ в переводе становится ошибкой сборки, а не пустотой в интерфейсе.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import { ru } from './ru'
import { en } from './en'
import { ja } from './ja'

export type Locale = 'ru' | 'en' | 'ja'

/**
 * Структуру берём у русского словаря, а типы листьев расширяем до `string`.
 *
 * Без расширения `as const` превращает каждую строку в литеральный тип, и
 * перевод обязан совпадать с оригиналом дословно — то есть переводить нельзя.
 * Набор ключей при этом остаётся строгим: пропущенный ключ — ошибка сборки.
 */
type Widen<T> = { [K in keyof T]: T[K] extends string ? string : Widen<T[K]> }

export type Dictionary = Widen<typeof ru>

export const LOCALES: Array<{ value: Locale; label: string }> = [
  { value: 'ru', label: 'Русский' },
  { value: 'en', label: 'English' },
  { value: 'ja', label: '日本語' },
]

const DICTIONARIES: Record<Locale, Dictionary> = { ru, en, ja }

const STORAGE_KEY = 'a365.v1.locale'

function readLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'ru' || stored === 'en' || stored === 'ja') return stored
  } catch {
    // Приватный режим — просто работаем на языке по умолчанию.
  }
  // Русский по умолчанию: сервис писался под русскоязычного пользователя.
  return 'ru'
}

/** Путь к строке вида `catalog.title`. */
type Leaves<T, Prefix extends string = ''> = {
  [K in keyof T & string]: T[K] extends string
    ? `${Prefix}${K}`
    : Leaves<T[K], `${Prefix}${K}.`>
}[keyof T & string]

export type TranslationKey = Leaves<Dictionary>

/** Подстановки вида `{count}` в строке. */
export type Vars = Record<string, string | number>

function lookup(dict: Dictionary, key: string): string {
  const value = key.split('.').reduce<unknown>((acc, part) => {
    if (acc && typeof acc === 'object' && part in acc) return (acc as Record<string, unknown>)[part]
    return undefined
  }, dict)
  // Ключ есть в типах, но нет в словаре — показываем сам ключ, а не пустоту:
  // так пропажу видно сразу, и она не выглядит как «поле без подписи».
  return typeof value === 'string' ? value : key
}

function interpolate(text: string, vars?: Vars): string {
  if (!vars) return text
  return text.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  )
}

interface I18nValue {
  locale: Locale
  setLocale: (next: Locale) => void
  t: (key: TranslationKey, vars?: Vars) => string
}

const I18nContext = createContext<I18nValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readLocale)

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Не сохранилось — язык продержится до перезагрузки, это не повод падать.
    }
    // Атрибут нужен не только для порядка: от него зависят переносы строк
    // и подбор шрифта, а у японского они заметно отличаются.
    document.documentElement.lang = next
  }, [])

  const value = useMemo<I18nValue>(() => {
    const dict = DICTIONARIES[locale]
    return {
      locale,
      setLocale,
      t: (key, vars) => interpolate(lookup(dict, key), vars),
    }
  }, [locale, setLocale])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useT() {
  const context = useContext(I18nContext)
  if (context === null) {
    throw new Error('useT вызван вне I18nProvider')
  }
  return context
}
