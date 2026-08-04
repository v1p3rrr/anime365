import { Button, Card, Group, PasswordInput, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconKey } from '@tabler/icons-react'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { usersApi } from '../api/users'
import { usePermissions } from '../hooks/usePermissions'
import { useT } from '../i18n'
import { useApiError } from '../i18n/apiError'

/** Минимум совпадает с серверным: сервер всё равно проверит сам. */
const MIN_LENGTH = 8

/**
 * Смена собственного пароля.
 *
 * Доступна каждому, кто вошёл, и никаких прав не требует: свой пароль —
 * единственное, чем человек обязан распоряжаться сам. Пока этого не было,
 * поменять пароль мог только администратор и только чужой, то есть обычный
 * пользователь навсегда оставался с тем, что ему однажды продиктовали.
 *
 * Старый пароль спрашивается обязательно — иначе оставленная без присмотра
 * вкладка превращается в захват учётной записи.
 */
export function OwnPasswordCard() {
  const { t } = useT()
  const apiError = useApiError()
  const { me } = usePermissions()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')

  const change = useMutation({
    mutationFn: () => usersApi.changeOwnPassword(current, next),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('ownPassword.changed') })
      setCurrent('')
      setNext('')
      setRepeat('')
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: t('ownPassword.failed'),
        message: apiError(error),
        autoClose: 12000,
      }),
  })

  // Несовпадение показываем до отправки: сервер второе поле не видит вовсе,
  // и опечатку в нём он поймать не может в принципе.
  const mismatch = repeat.length > 0 && next !== repeat
  const tooShort = next.length > 0 && next.length < MIN_LENGTH
  const ready = current.length > 0 && next.length >= MIN_LENGTH && next === repeat

  return (
    <Card withBorder padding="sm">
      <Group gap={6} mb={4}>
        <IconKey size={16} />
        <Text fw={600}>{t('ownPassword.title')}</Text>
      </Group>
      <Text size="xs" c="dimmed" mb="sm">
        {me ? t('ownPassword.hint', { name: me.name }) : t('ownPassword.hintNoName')}
      </Text>

      <Stack gap="xs">
        <PasswordInput
          label={t('ownPassword.current')}
          value={current}
          onChange={(event) => setCurrent(event.currentTarget.value)}
          autoComplete="current-password"
        />
        <PasswordInput
          label={t('ownPassword.next')}
          description={t('ownPassword.minLength', { count: MIN_LENGTH })}
          error={tooShort ? t('ownPassword.tooShort', { count: MIN_LENGTH }) : undefined}
          value={next}
          onChange={(event) => setNext(event.currentTarget.value)}
          autoComplete="new-password"
        />
        <PasswordInput
          label={t('ownPassword.repeat')}
          error={mismatch ? t('ownPassword.mismatch') : undefined}
          value={repeat}
          onChange={(event) => setRepeat(event.currentTarget.value)}
          autoComplete="new-password"
        />
        <Group justify="flex-end">
          <Button
            size="xs"
            onClick={() => change.mutate()}
            loading={change.isPending}
            disabled={!ready}
          >
            {t('ownPassword.submit')}
          </Button>
        </Group>
      </Stack>
    </Card>
  )
}
