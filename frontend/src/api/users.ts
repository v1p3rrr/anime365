import { api } from './client'

export interface AppUser {
  id: number
  name: string
  is_admin: boolean
  /** У администратора приходит полный набор — он может всё. */
  permissions: string[]
  has_password: boolean
  /** Выданный пароль ещё не сменён владельцем. */
  must_change_password: boolean
  created_at: string | null
}

/** Кто вошёл и что ему можно. Этим интерфейс решает, что показывать. */
export interface Me {
  id: number
  name: string
  is_admin: boolean
  permissions: string[]
  multiuser_enabled: boolean
  /**
   * Пароль выдан временно — работать нельзя, пока не задан свой.
   *
   * Сервер в этом состоянии отвечает отказом почти на всё, поэтому экран
   * смены пароля здесь не «настойчивая просьба», а единственный доступный
   * способ продолжить.
   */
  must_change_password: boolean
}

export interface UsersResponse {
  users: AppUser[]
  all_permissions: string[]
  /** Чем предзаполнить галочки при создании — список приходит с сервера. */
  default_permissions: string[]
  /**
   * Есть ли хоть один не-администратор.
   *
   * Пока его нет, разграничение прав ни на что не влияет, и интерфейс работает
   * как раньше. Показывать это состояние честно важнее, чем скрывать: иначе
   * непонятно, почему выданные права ничего не меняют.
   */
  multiuser_enabled: boolean
}

export interface UserCreate {
  name: string
  is_admin: boolean
  permissions: string[]
  password?: string
  /** Пароль на один раз: владелец задаёт свой при первом входе. */
  temporary_password?: boolean
}

export interface UserUpdate {
  is_admin?: boolean
  permissions?: string[]
  password?: string
  temporary_password?: boolean
}

/**
 * Созданная запись. `password` приходит, только если пароль выдал сервер —
 * и приходит **единственный раз**: в базе лежит один хеш.
 */
export interface CreatedUser extends AppUser {
  password?: string
}

/** Ответ на сброс: открытый пароль показывается ровно один раз. */
export interface PasswordReset {
  id: number
  name: string
  password: string
}

export const usersApi = {
  me: () => api.get<Me>('/users/me'),
  /** Свой пароль меняет любой вошедший, права для этого не нужны. */
  changeOwnPassword: (current: string, next: string) =>
    api.post<{ ok: boolean }>('/users/me/password', { current, new: next }),
  list: () => api.get<UsersResponse>('/users'),
  create: (payload: UserCreate) => api.post<CreatedUser>('/users', payload),
  update: (id: number, payload: UserUpdate) => api.put<AppUser>(`/users/${id}`, payload),
  remove: (id: number) => api.del<{ deleted: number }>(`/users/${id}`),
  /**
   * Сброс пароля: сервер придумывает новый и возвращает его в открытом виде.
   *
   * ⚠️ Значение приходит единственный раз — второй раз узнать его нельзя,
   * можно только сбросить снова. Поэтому интерфейс обязан показать его сразу
   * и не терять при закрытии окна случайно.
   */
  resetPassword: (id: number) =>
    api.post<PasswordReset>(`/users/${id}/password/reset`, {}),
}
