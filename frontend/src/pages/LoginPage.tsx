import { Alert, Button, Card, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { IconLock, IconUser } from '@tabler/icons-react'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { useT } from '../i18n'

interface LoginPageProps {
  onSuccess: () => void
}

export function LoginPage({ onSuccess }: LoginPageProps) {
  const [password, setPassword] = useState('')
  // Логин необязателен: вход по одному общему паролю никуда не делся, и
  // пустое поле означает именно его. Заполненное — вход под конкретным
  // пользователем, и права дальше берутся его.
  const [login_, setLogin] = useState('')
  const { t } = useT()

  const login = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean }>('/auth/login', {
        password,
        // Пустую строку не шлём вовсе: сервер отличает «без логина» от
        // «логин пустой», и второе он отверг бы.
        ...(login_.trim() ? { login: login_.trim() } : {}),
      }),
    onSuccess,
  })

  return (
    <Stack align="center" justify="center" style={{ height: '100dvh' }} p="md">
      <Card withBorder padding="lg" style={{ width: '100%', maxWidth: 360 }}>
        <Stack gap="md">
          <Stack gap={2}>
            <Title order={4}>{t('login.title')}</Title>
            <Text size="xs" c="dimmed">
              {t('login.hint')}
            </Text>
          </Stack>

          {login.isError && (
            <Alert color="red" p="xs">
              <Text size="xs">{(login.error as Error).message}</Text>
            </Alert>
          )}

          <form
            onSubmit={(event) => {
              event.preventDefault()
              login.mutate()
            }}
          >
            <Stack gap="sm">
              <TextInput
                label={t('login.login')}
                description={t('login.loginHint')}
                value={login_}
                onChange={(event) => setLogin(event.currentTarget.value)}
                leftSection={<IconUser size={16} />}
                autoFocus
                autoComplete="username"
              />
              <PasswordInput
                label={t('login.password')}
                value={password}
                onChange={(event) => setPassword(event.currentTarget.value)}
                leftSection={<IconLock size={16} />}
                // Менеджеры паролей должны предлагать сохранение — это общий
                // пароль на сервис, его будут вводить с разных устройств.
                autoComplete="current-password"
              />
              <Button type="submit" loading={login.isPending} disabled={!password}>
                {t('login.submit')}
              </Button>
            </Stack>
          </form>
        </Stack>
      </Card>
    </Stack>
  )
}
