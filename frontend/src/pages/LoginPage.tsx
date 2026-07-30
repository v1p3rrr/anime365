import { Alert, Button, Card, PasswordInput, Stack, Text, Title } from '@mantine/core'
import { IconLock } from '@tabler/icons-react'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { useT } from '../i18n'

interface LoginPageProps {
  onSuccess: () => void
}

export function LoginPage({ onSuccess }: LoginPageProps) {
  const [password, setPassword] = useState('')
  const { t } = useT()

  const login = useMutation({
    mutationFn: () => api.post<{ ok: boolean }>('/auth/login', { password }),
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
              <PasswordInput
                label={t('login.password')}
                value={password}
                onChange={(event) => setPassword(event.currentTarget.value)}
                leftSection={<IconLock size={16} />}
                autoFocus
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
