import {
  Alert,
  Button,
  Card,
  Group,
  PasswordInput,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core'
import { IconLock, IconUser } from '@tabler/icons-react'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { LOCALES, useT, type Locale } from '../i18n'

interface LoginPageProps {
  onSuccess: () => void
}

export function LoginPage({ onSuccess }: LoginPageProps) {
  const [password, setPassword] = useState('')
  const [login_, setLogin] = useState('')
  const { t, locale, setLocale } = useT()

  const login = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean }>('/auth/login', {
        login: login_.trim(),
        password,
      }),
    onSuccess,
  })

  return (
    <Stack align="center" justify="center" style={{ height: '100dvh' }} p="md">
      {/* Логотип и здесь: до входа человек иначе видит безымянную форму пароля
          и не понимает, к чему именно она — особенно если сервисов на сервере
          несколько. */}
      <Logo height={56} />
      <Card withBorder padding="lg" style={{ width: '100%', maxWidth: 360 }}>
        <Stack gap="md">
          {/* Язык и тема доступны до входа: они хранятся в браузере и сервера
              не касаются, а читать форму на чужом языке или в слепящей теме
              человеку приходится ровно тогда, когда он ещё не вошёл. */}
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Tooltip label={t('nav.language')}>
              <Select
                size="xs"
                w={110}
                data={LOCALES}
                value={locale}
                onChange={(value) => value && setLocale(value as Locale)}
                allowDeselect={false}
                aria-label={t('nav.language')}
              />
            </Tooltip>
            <ThemeToggle />
          </Group>

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
              <Button type="submit" loading={login.isPending} disabled={!password || !login_.trim()}>
                {t('login.submit')}
              </Button>
            </Stack>
          </form>
        </Stack>
      </Card>
    </Stack>
  )
}
