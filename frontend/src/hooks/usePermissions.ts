import { useQuery } from '@tanstack/react-query'

import { usersApi, type Me } from '../api/users'

/** Категории прав. Держатся в согласии с `Permission` на сервере. */
export type PermissionKey =
  | 'catalog'
  | 'watch'
  | 'device_downloads'
  | 'downloads'
  | 'library'
  | 'settings'

/**
 * Что доступно текущему пользователю.
 *
 * Интерфейс обязан прятать то, на что нет прав, — иначе человек видит кнопки,
 * которые отвечают отказом, и считает это поломкой. Проверка на сервере при
 * этом остаётся главной: здесь мы решаем, что показать, а не что разрешить.
 *
 * Пока ответ не пришёл, считаем, что можно всё. Так интерфейс не мигает
 * пустотой на каждой загрузке страницы, а лишний пункт меню на долю секунды
 * безвреден: нажатие всё равно упрётся в проверку на сервере.
 */
export function usePermissions() {
  const query = useQuery<Me>({
    queryKey: ['me'],
    queryFn: usersApi.me,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const me = query.data
  const can = (permission: PermissionKey) => {
    if (!me) return true
    // У администратора прав не спрашивают — так же, как на сервере: иначе
    // достаточно забыть одну галочку, чтобы он потерял доступ к настройкам.
    return me.is_admin || me.permissions.includes(permission)
  }

  return { me, can, isAdmin: me?.is_admin ?? true, ready: !query.isLoading }
}
