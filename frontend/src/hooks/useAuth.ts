import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'

import { api, UNAUTHORIZED_EVENT } from '../api/client'

interface AuthStatus {
  password_required: boolean
  authenticated: boolean
}

/**
 * Состояние входа.
 *
 * Спрашиваем сервер, а не смотрим на куку: кука HttpOnly и из JS не видна —
 * именно поэтому её нельзя украсть через XSS.
 */
export function useAuth() {
  const queryClient = useQueryClient()

  // Сервер прямо ответил 401 — или мы сами вышли. Отдельный флаг нужен потому,
  // что одного `invalidateQueries` не хватало: пока перезапрос статуса летит,
  // данных нет, а «нет данных» трактовалось как «вход выполнен». Приложение
  // оставалось на экране, каждая страница получала свой 401, и вместо формы
  // входа человек видел «Требуется вход» в каждой карточке — ровно то, от чего
  // сигнал и заводился.
  const [signedOut, setSignedOut] = useState(false)

  const query = useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/auth/status'),
    // Сессия живёт 30 дней, перепроверять её на каждом фокусе окна незачем.
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const authenticatedByServer = query.data?.authenticated

  // Ответ «вход есть» снимает флаг: так возвращаются после успешного логина и
  // после ложной тревоги (401 мог прилететь от одного эндпоинта на фоне живой
  // сессии — например, при смене прав).
  useEffect(() => {
    if (authenticatedByServer) setSignedOut(false)
  }, [authenticatedByServer])

  useEffect(() => {
    const onUnauthorized = () => {
      setSignedOut(true)
      queryClient.invalidateQueries({ queryKey: ['auth-status'] })
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [queryClient])

  const refresh = useCallback(() => {
    setSignedOut(false)
    return queryClient.invalidateQueries({ queryKey: ['auth-status'] })
  }, [queryClient])

  return {
    // Пока статус не пришёл, считаем вход выполненным: иначе на секунду
    // мигает форма пароля у тех, у кого пароля нет вовсе. Но только пока не
    // было явного отказа — после него ждать нечего.
    authenticated: signedOut ? false : (authenticatedByServer ?? true),
    passwordRequired: query.data?.password_required ?? false,
    isLoading: query.isLoading,
    refresh,
    logout: async () => {
      await api.post('/auth/logout')
      // Весь кэш собран под предыдущей сессией — оставлять его после выхода
      // нельзя. Флаг ставим сами: `clear()` уносит и статус входа, а пустой
      // кэш снова читался бы как «вход выполнен».
      setSignedOut(true)
      queryClient.clear()
    },
  }
}
