import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

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

  const query = useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/auth/status'),
    // Сессия живёт 30 дней, перепроверять её на каждом фокусе окна незачем.
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  useEffect(() => {
    const onUnauthorized = () => {
      queryClient.invalidateQueries({ queryKey: ['auth-status'] })
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [queryClient])

  return {
    // Пока статус не пришёл, считаем вход выполненным: иначе на секунду
    // мигает форма пароля у тех, у кого пароля нет вовсе.
    authenticated: query.data?.authenticated ?? true,
    passwordRequired: query.data?.password_required ?? false,
    isLoading: query.isLoading,
    refresh: () => queryClient.invalidateQueries({ queryKey: ['auth-status'] }),
    logout: async () => {
      await api.post('/auth/logout')
      // Весь кэш собран под предыдущей сессией — оставлять его после выхода нельзя.
      queryClient.clear()
    },
  }
}
