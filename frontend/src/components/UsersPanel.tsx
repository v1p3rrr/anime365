import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Modal,
  PasswordInput,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  IconArrowBackUp,
  IconDeviceFloppy,
  IconKey,
  IconTrash,
  IconUserPlus,
} from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { usersApi, type AppUser } from '../api/users'
import { IssuedPasswordModal } from './IssuedPasswordModal'
import { useT } from '../i18n'
import { type TranslationKey } from '../i18n'

/**
 * Пользователи и их права.
 *
 * Права проверяются на сервере — спрятанная кнопка ограничением не является.
 * Эта форма только заводит записи; отказ в доступе живёт в эндпоинтах.
 *
 * Пока все заведённые пользователи — администраторы, разграничение ни на что не
 * влияет. Об этом сказано прямо: иначе выданные права выглядели бы сломанными.
 *
 * ## Правки копятся до нажатия «Сохранить»
 *
 * Сначала здесь было автосохранение — каждый переключатель сразу уходил на
 * сервер. Отменено по решению пользователя: права критичны, и случайный клик не
 * должен вступать в силу немедленно.
 *
 * Поэтому изменения складываются в черновик, кнопка сохранения показывает, что
 * несохранённое есть, а рядом стоит отмена — она возвращает всё к тому, что на
 * сервере. Перезагрузка страницы делает то же самое, но полагаться на неё как
 * на способ отменить правку нельзя: это не действие, а обходной путь.
 *
 * Удаление тоже копится в черновике — по решению пользователя. Довод сильный:
 * это самое необратимое действие здесь, и применяться сразу оно не должно тем
 * более. В черновике удаление можно отменить той же стрелкой, что и остальное,
 * а подтверждение спрашивается уже при сохранении — когда видно, что именно
 * уйдёт.
 */
interface Props {
  /**
   * Сообщает наружу, есть ли несохранённые правки. Нужно странице настроек:
   * она держит предупреждение об уходе, а черновик пользователей живёт здесь.
   * Без этого правки по пользователям терялись бы молча — при том что именно
   * их и просили не применять сразу.
   */
  onDirtyChange?: (dirty: boolean) => void
}

export function UsersPanel({ onDirtyChange }: Props = {}) {
  const { t } = useT()
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)

  const users = useQuery({ queryKey: ['users'], queryFn: usersApi.list })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['users'] })

  // Кого пометили на удаление. Отдельно от draft: там правки полей, а это
  // другое действие, и смешивать их в одну структуру значило бы описывать
  // «удалён» как ещё одно поле.
  const [toRemove, setToRemove] = useState<number[]>([])
  const [confirming, setConfirming] = useState(false)

  // Выданный сервером пароль: показывается один раз и нигде не хранится.
  const [issued, setIssued] = useState<{ name: string; password: string } | null>(null)

  const reset = useMutation({
    mutationFn: (id: number) => usersApi.resetPassword(id),
    onSuccess: (result) => {
      setIssued({ name: result.name, password: result.password })
      invalidate()
    },
  })

  // Черновик: id пользователя -> что в нём поменяли. Пустая запись означает
  // «изменений нет» и в него не попадает.
  const [draft, setDraft] = useState<Record<number, Partial<AppUser>>>({})

  const edit = (user: AppUser, patch: Partial<AppUser>) => {
    setDraft((current) => {
      const merged = { ...current[user.id], ...patch }
      // Возврат к серверному значению снимает правку, а не записывает её
      // обратно: иначе «поменял и вернул» осталось бы несохранённым навсегда.
      const changed = Object.entries(merged).filter(([key, value]) =>
        JSON.stringify(value) !== JSON.stringify(user[key as keyof AppUser]),
      )
      const next = { ...current }
      if (changed.length === 0) delete next[user.id]
      else next[user.id] = Object.fromEntries(changed) as Partial<AppUser>
      return next
    })
  }

  const dirtyCount = Object.keys(draft).length + toRemove.length

  useEffect(() => {
    onDirtyChange?.(dirtyCount > 0)
  }, [dirtyCount, onDirtyChange])

  const save = useMutation({
    mutationFn: async () => {
      // Последовательно, а не пачкой: у сервера нет группового эндпоинта, а
      // параллельные запросы к SQLite ради трёх строк не нужны.
      //
      // Правки идут раньше удалений: если человек снял админа с одного и
      // удаляет другого, порядок «сначала снять, потом удалить» может упереться
      // в защиту последнего администратора, а обратный — нет.
      for (const [id, patch] of Object.entries(draft)) {
        if (toRemove.includes(Number(id))) continue
        await usersApi.update(Number(id), {
          is_admin: patch.is_admin,
          permissions: patch.permissions,
        })
      }
      for (const id of toRemove) await usersApi.remove(id)
    },
    onSuccess: () => {
      setDraft({})
      setToRemove([])
      setConfirming(false)
      invalidate()
      notifications.show({ message: t('users.saved'), color: 'teal' })
    },
  })

  const data = users.data
  // Считаем по черновику: иначе, сняв админа у одного и не сохранив, второй
  // остался бы разблокированным — и можно было бы снять обоих разом.
  const admins =
    data?.users.filter((u) => ({ ...u, ...draft[u.id] }).is_admin).length ?? 0

  return (
    <Card withBorder padding="md">
      <Group justify="space-between" mb="sm" wrap="wrap" gap="xs">
        <div>
          <Text fw={600}>{t('users.title')}</Text>
          {/* Названо вслух: кнопки «Сохранить» у этой панели нет, и без подписи
              это читается как недоделка, а не как замысел. */}
          <Text size="xs" c="dimmed">
            {dirtyCount > 0 ? t('users.unsaved', { count: dirtyCount }) : t('users.savedHint')}
          </Text>
        </div>
        <Group gap="xs">
          {/* Отмена — маленькой иконкой рядом с сохранением: она нужна редко,
              но искать её в перезагрузке страницы неправильно. */}
          <Tooltip label={t('users.revert')}>
            <ActionIcon
              variant="subtle"
              disabled={dirtyCount === 0}
              onClick={() => {
                setDraft({})
                setToRemove([])
              }}
              aria-label={t('users.revert')}
            >
              <IconArrowBackUp size={16} />
            </ActionIcon>
          </Tooltip>
          <Button
            size="compact-sm"
            disabled={dirtyCount === 0}
            loading={save.isPending}
            leftSection={<IconDeviceFloppy size={14} />}
            onClick={() => (toRemove.length > 0 ? setConfirming(true) : save.mutate())}
          >
            {t('common.save')}
          </Button>
          <Button
            size="compact-sm"
            variant="light"
            leftSection={<IconUserPlus size={14} />}
            onClick={() => setCreating(true)}
          >
            {t('users.add')}
          </Button>
        </Group>
      </Group>

      {data && !data.multiuser_enabled && (
        // Не предупреждение об ошибке, а объяснение: права можно выдавать уже
        // сейчас, но пока все администраторы — они ничего не меняют.
        <Alert color="blue" mb="sm">
          {t('users.singleUserHint')}
        </Alert>
      )}

      {users.isError && <Alert color="red">{t('users.loadFailed')}</Alert>}

      <Table.ScrollContainer minWidth={520}>
        <Table verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('users.columnName')}</Table.Th>
              <Table.Th w={110}>{t('users.columnAdmin')}</Table.Th>
              <Table.Th>{t('users.columnPermissions')}</Table.Th>
              <Table.Th w={50} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data?.users.map((user) => {
              // Показываем черновик поверх серверного значения — иначе
              // переключатель отскакивал бы обратно до сохранения.
              const shown = { ...user, ...draft[user.id] }
              return (
                <UserRow
                  key={user.id}
                  user={shown}
                  dirty={Boolean(draft[user.id])}
                  allPermissions={data.all_permissions}
                  isLastAdmin={shown.is_admin && admins <= 1}
                  onToggleAdmin={(value) => edit(user, { is_admin: value })}
                  onPermissions={(value) => edit(user, { permissions: value })}
                  markedForRemoval={toRemove.includes(user.id)}
                  onRemove={() =>
                    setToRemove((current) =>
                      current.includes(user.id)
                        ? current.filter((id) => id !== user.id)
                        : [...current, user.id],
                    )
                  }
                  onResetPassword={() => reset.mutate(user.id)}
                  resetting={reset.isPending && reset.variables === user.id}
                />
              )
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      {save.isError && (
        <Alert color="red" mt="sm">
          {t('users.changeFailed')}
        </Alert>
      )}

      {/* Удаление необратимо и делается одним нажатием на маленькую иконку —
          промахнуться легко, а вернуть пользователя нечем. */}
      <Modal
        opened={confirming}
        onClose={() => setConfirming(false)}
        title={t('users.removeTitle')}
        centered
      >
        <Stack gap="sm">
          {/* Спрашиваем при сохранении, а не при клике на корзину: здесь уже
              видно, кто именно уйдёт, — а пометить и передумать можно было
              сколько угодно раз до этого. */}
          <Text size="sm">
            {t('users.removeConfirm', {
              names: (data?.users ?? [])
                .filter((user) => toRemove.includes(user.id))
                .map((user) => user.name)
                .join(', '),
            })}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </Button>
            <Button color="red" loading={save.isPending} onClick={() => save.mutate()}>
              {t('common.save')}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <CreateUserModal
        opened={creating}
        allPermissions={data?.all_permissions ?? []}
        defaultPermissions={data?.default_permissions ?? []}
        onClose={() => setCreating(false)}
        onCreated={invalidate}
        onIssued={(name, password) => setIssued({ name, password })}
      />

      <IssuedPasswordModal
        password={issued?.password ?? null}
        name={issued?.name ?? ''}
        onClose={() => setIssued(null)}
      />
    </Card>
  )
}

interface RowProps {
  user: AppUser
  /** Есть ли у строки несохранённые правки — иначе их не отличить от применённых. */
  dirty: boolean
  /** Помечен на удаление: строка гасится, а корзина превращается в отмену. */
  markedForRemoval: boolean
  allPermissions: string[]
  isLastAdmin: boolean
  onToggleAdmin: (value: boolean) => void
  onPermissions: (value: string[]) => void
  onRemove: () => void
  /** Сброс пароля идёт мимо черновика: он возвращает значение на показ. */
  onResetPassword: () => void
  resetting: boolean
}

function UserRow({
  user,
  dirty,
  markedForRemoval,
  allPermissions,
  isLastAdmin,
  onToggleAdmin,
  onPermissions,
  onRemove,
  onResetPassword,
  resetting,
}: RowProps) {
  const { t } = useT()

  // Последнего администратора нельзя ни разжаловать, ни удалить — иначе
  // управлять правами станет некому, и починить это можно будет только руками
  // в базе. Сервер это тоже не даст, но кнопка, которая всегда отказывает, —
  // плохая кнопка.
  const lockReason = isLastAdmin ? t('users.lastAdmin') : null

  return (
    // Помеченную на удаление строку гасим и зачёркиваем: до сохранения она ещё
    // существует, и делать вид, что её уже нет, значило бы врать.
    <Table.Tr style={markedForRemoval ? { opacity: 0.45 } : undefined}>
      <Table.Td>
        <Group gap={6} wrap="nowrap">
          <Text
            size="sm"
            style={markedForRemoval ? { textDecoration: 'line-through' } : undefined}
          >
            {user.name}
          </Text>
          {markedForRemoval ? (
            <Badge size="xs" color="red" variant="light">
              {t('users.rowRemoving')}
            </Badge>
          ) : (
            dirty && (
              <Badge size="xs" color="yellow" variant="light">
                {t('users.rowUnsaved')}
              </Badge>
            )
          )}
        </Group>
        {!user.has_password && (
          <Text size="xs" c="dimmed">
            {t('users.noPassword')}
          </Text>
        )}
        {user.must_change_password && (
          <Badge size="xs" color="orange" variant="light">
            {t('users.temporaryPassword')}
          </Badge>
        )}
      </Table.Td>

      <Table.Td>
        <Tooltip label={lockReason} disabled={!lockReason}>
          <span>
            <Switch
              size="sm"
              checked={user.is_admin}
              disabled={Boolean(lockReason)}
              onChange={(event) => onToggleAdmin(event.currentTarget.checked)}
            />
          </span>
        </Tooltip>
      </Table.Td>

      <Table.Td>
        {user.is_admin ? (
          <Badge size="xs" variant="light" color="teal">
            {t('users.everything')}
          </Badge>
        ) : (
          <Group gap={4} wrap="wrap">
            {allPermissions.map((permission) => (
              <Checkbox
                key={permission}
                size="xs"
                checked={user.permissions.includes(permission)}
                label={t(`users.permission.${permission}` as TranslationKey)}
                onChange={(event) =>
                  onPermissions(
                    event.currentTarget.checked
                      ? [...user.permissions, permission]
                      : user.permissions.filter((p) => p !== permission),
                  )
                }
              />
            ))}
          </Group>
        )}
      </Table.Td>

      <Table.Td>
        <Group gap={2} wrap="nowrap">
          {/* Сброс применяется сразу, в обход черновика, и это осознанно:
              он возвращает открытый пароль, который надо показать немедленно.
              Отложить такое действие «до сохранения» нельзя — пришлось бы
              хранить выданный пароль в памяти формы до неопределённого
              момента. Необратимости здесь нет: сбросить можно ещё раз. */}
          <Tooltip label={t('users.resetPasswordHint')}>
            <ActionIcon
              variant="subtle"
              color="gray"
              loading={resetting}
              disabled={markedForRemoval}
              onClick={onResetPassword}
              aria-label={t('users.resetPassword')}
            >
              <IconKey size={15} />
            </ActionIcon>
          </Tooltip>

          <Tooltip label={lockReason} disabled={!lockReason}>
            <span>
              <ActionIcon
                variant="subtle"
                color={markedForRemoval ? 'gray' : 'red'}
                disabled={Boolean(lockReason)}
                onClick={onRemove}
                aria-label={markedForRemoval ? t('users.undoRemove') : t('users.remove')}
              >
                {markedForRemoval ? <IconArrowBackUp size={15} /> : <IconTrash size={15} />}
              </ActionIcon>
            </span>
          </Tooltip>
        </Group>
      </Table.Td>
    </Table.Tr>
  )
}

interface ModalProps {
  opened: boolean
  allPermissions: string[]
  /** Чем предзаполнить галочки — список приходит с сервера. */
  defaultPermissions: string[]
  onClose: () => void
  onCreated: () => void
  /** Сервер выдал пароль: показать его один раз. */
  onIssued: (name: string, password: string) => void
}

function CreateUserModal({
  opened,
  allPermissions,
  defaultPermissions,
  onClose,
  onCreated,
  onIssued,
}: ModalProps) {
  const { t } = useT()
  const [name, setName] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  // Галочки предзаполнены умолчаниями с сервера: держать этот список на
  // клиенте значило бы иметь вторую правду, которая однажды разойдётся.
  const [permissions, setPermissions] = useState<string[]>(defaultPermissions)
  const [temporary, setTemporary] = useState(true)
  const [password, setPassword] = useState('')

  const create = useMutation({
    mutationFn: () =>
      usersApi.create({
        name: name.trim(),
        is_admin: isAdmin,
        permissions,
        // Временный пароль придумывает сервер, поле ввода при этом скрыто.
        password: temporary ? undefined : password || undefined,
        temporary_password: temporary,
      }),
    onSuccess: (user) => {
      onCreated()
      if (user.password) onIssued(user.name, user.password)
      reset()
      onClose()
    },
  })

  function reset() {
    setName('')
    setIsAdmin(false)
    setPermissions(defaultPermissions)
    setTemporary(true)
    setPassword('')
  }

  return (
    <Modal opened={opened} onClose={onClose} title={t('users.add')} centered>
      <Stack gap="sm">
        <TextInput
          label={t('users.columnName')}
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          maxLength={64}
          data-autofocus
        />

        <Switch
          label={t('users.columnAdmin')}
          description={t('users.adminHint')}
          checked={isAdmin}
          onChange={(event) => setIsAdmin(event.currentTarget.checked)}
        />

        {/* Права администратора не показываем: у него их и так все, а набор
            галочек рядом с включённым «администратор» читался бы как
            ограничение, которого нет. */}
        {!isAdmin && (
          <Stack gap={4}>
            <Text size="sm">{t('users.columnPermissions')}</Text>
            {allPermissions.map((permission) => (
              <Checkbox
                key={permission}
                size="sm"
                checked={permissions.includes(permission)}
                label={t(`users.permission.${permission}` as TranslationKey)}
                description={t(`users.permissionHint.${permission}` as TranslationKey)}
                onChange={(event) =>
                  setPermissions(
                    event.currentTarget.checked
                      ? [...permissions, permission]
                      : permissions.filter((p) => p !== permission),
                  )
                }
              />
            ))}
          </Stack>
        )}

        <Switch
          label={t('users.temporaryPassword')}
          description={t('users.temporaryPasswordHint')}
          checked={temporary}
          onChange={(event) => setTemporary(event.currentTarget.checked)}
        />

        {/* Поле ввода только для постоянного пароля. Временный придумывает
            сервер: придуманный руками «временный» пароль на практике
            оказывается словарным, а живёт он до первого входа. */}
        {!temporary && (
          <PasswordInput
            label={t('users.password')}
            description={t('users.passwordHint')}
            value={password}
            onChange={(event) => setPassword(event.currentTarget.value)}
          />
        )}

        {create.isError && <Alert color="red">{t('users.createFailed')}</Alert>}

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => create.mutate()}
            loading={create.isPending}
            disabled={name.trim().length === 0}
          >
            {t('users.create')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
