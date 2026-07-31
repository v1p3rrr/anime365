/**
 * Перевод ошибок сервера.
 *
 * Сервер отдаёт машинный код случая и значения для подстановки, а текст
 * собирается здесь по словарю. Локаль в запросах не передаётся: язык — личная
 * настройка и живёт в браузере, а сервер общий, и делать общее зависимым от
 * личного значит получить чужой язык в логах и в параллельных ответах.
 *
 * Незнакомый код — не ошибка, а обычное дело при обновлении сервера вперёд
 * клиента. Тогда показываем русское сообщение, которое сервер приложил: оно
 * хуже перевода, но несравнимо лучше пустого места.
 */

import { ApiError } from '../api/client'
import { useT, type TranslationKey } from './index'

export function useApiError() {
  const { t } = useT()

  return (error: unknown): string => {
    if (!(error instanceof ApiError)) {
      return error instanceof Error ? error.message : String(error)
    }

    if (!error.code) return error.message

    const key = `apiErrors.${error.code}` as TranslationKey
    const translated = t(key, error.params)
    // `t` возвращает сам ключ, когда его нет в словаре, — значит код новый.
    return translated === key ? error.message : translated
  }
}
