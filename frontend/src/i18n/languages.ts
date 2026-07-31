/**
 * Названия языков на языке интерфейса.
 *
 * `Intl.DisplayNames` встроен в браузер, поэтому справочник не нужен ни свой,
 * ни библиотекой. Важнее другое: названия не ложатся на наши файлы локализации.
 * Иначе каждый возможный код языка пришлось бы переводить трижды — а после
 * перехода на ISO-справочник на бэкенде в дорожках может оказаться любой из
 * 183 языков с двухбуквенным кодом.
 */

/** Для неизвестного кода `Intl` возвращает сам код — такая «подсказка» бесполезна. */
export function languageName(code: string, locale: string): string | null {
  try {
    const name = new Intl.DisplayNames([locale], { type: 'language' }).of(code)
    return name && name.toLowerCase() !== code.toLowerCase() ? name : null
  } catch {
    return null
  }
}

/**
 * Список языков одной строкой: «японский, английский».
 *
 * Коды, для которых названия нет, показываем как есть в верхнем регистре —
 * потерять язык хуже, чем показать его кодом.
 */
export function languageList(codes: string[], locale: string): string {
  return codes.map((code) => languageName(code, locale) ?? code.toUpperCase()).join(', ')
}
