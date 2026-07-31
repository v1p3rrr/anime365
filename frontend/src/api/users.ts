import { api } from './client'

export interface AppUser {
  id: number
  name: string
  is_admin: boolean
  /** У администратора приходит полный набор — он может всё. */
  permissions: string[]
  has_password: boolean
  created_at: string | null
}

export interface UsersResponse {
  users: AppUser[]
  all_permissions: string[]
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
}

export interface UserUpdate {
  is_admin?: boolean
  permissions?: string[]
  password?: string
}

export const usersApi = {
  list: () => api.get<UsersResponse>('/users'),
  create: (payload: UserCreate) => api.post<AppUser>('/users', payload),
  update: (id: number, payload: UserUpdate) => api.put<AppUser>(`/users/${id}`, payload),
  remove: (id: number) => api.del<{ deleted: number }>(`/users/${id}`),
}
