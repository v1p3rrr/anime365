import { Alert, Title, Stack } from '@mantine/core'

export function SettingsPage() {
  return (
    <Stack>
      <Title order={3}>Настройки</Title>
      <Alert color="gray" title="Этап 6">
        Форма генерируется из реестра настроек на сервере. У каждого поля будет видно, откуда взято
        значение — по умолчанию, из переменной окружения или изменено здесь — и кнопка сброса.
      </Alert>
    </Stack>
  )
}
