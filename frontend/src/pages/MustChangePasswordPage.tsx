import { Alert, Button, Card, Center, PasswordInput, Stack, Text, Title } from '@mantine/core'
import { IconKey } from '@tabler/icons-react'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { usersApi } from '../api/users'
import { useT } from '../i18n'
import { useApiError } from '../i18n/apiError'

/** Совпадает с серверным минимумом; сервер всё равно проверит сам. */
const MIN_LENGTH = 10

interface Props {
  /** Пароль задан — перечитать «кто я» и пустить в приложение. */
  onDone: () => void
}

/**
 * Единственный экран, доступный тому, кто вошёл с временным паролём.
 *
 * Это не настойчивая просьба, а состояние: сервер отвечает отказом
 * (`password_change_required`) почти на весь API, пока пароль не сменён.
 * Поэтому здесь нет ни кнопки «потом», ни навигации — показывать пункты
 * меню, каждый из которых ответит ошибкой, значило бы врать.
 *
 * Текущий пароль спрашивается, хотя человек только что его вводил: эндпоинт
 * смены один на все случаи, и ослаблять его ради этого сценария нельзя —
 * иначе оставленная без присмотра вкладка станет захватом учётной записи.
 */
export function MustChangePasswordPage({ onDone }: Props) {
  const { t } = useT()
  const apiError = useApiError()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')

  const change = useMutation({
    mutationFn: () => usersApi.changeOwnPassword(current, next),
    onSuccess: onDone,
  })

  const tooShort = next.length > 0 && next.length < MIN_LENGTH
  const mismatch = repeat.length > 0 && next !== repeat
  const ready = current.length > 0 && next.length >= MIN_LENGTH && next === repeat

  return (
    <Center mih="100vh" p="md">
      <Card withBorder shadow="sm" padding="lg" w="100%" maw={420}>
        <Stack gap="sm">
          <Title order={3}>{t('mustChangePassword.title')}</Title>
          <Text size="sm" c="dimmed">
            {t('mustChangePassword.intro')}
          </Text>

          <PasswordInput
            label={t('mustChangePassword.current')}
            value={current}
            onChange={(event) => setCurrent(event.currentTarget.value)}
            data-autofocus
          />
          <PasswordInput
            label={t('mustChangePassword.next')}
            value={next}
            error={tooShort ? t('mustChangePassword.tooShort', { count: MIN_LENGTH }) : undefined}
            onChange={(event) => setNext(event.currentTarget.value)}
          />
          <PasswordInput
            label={t('mustChangePassword.repeat')}
            value={repeat}
            error={mismatch ? t('mustChangePassword.mismatch') : undefined}
            onChange={(event) => setRepeat(event.currentTarget.value)}
          />

          {change.isError && (
            <Alert color="red" title={t('mustChangePassword.failed')}>
              {apiError(change.error as Error)}
            </Alert>
          )}

          <Button
            leftSection={<IconKey size={16} />}
            onClick={() => change.mutate()}
            loading={change.isPending}
            disabled={!ready}
          >
            {t('mustChangePassword.submit')}
          </Button>
        </Stack>
      </Card>
    </Center>
  )
}
