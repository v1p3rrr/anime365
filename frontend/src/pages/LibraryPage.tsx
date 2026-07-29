import { Alert, Title, Stack } from '@mantine/core'

export function LibraryPage() {
  return (
    <Stack>
      <Title order={3}>Библиотека</Title>
      <Alert color="gray" title="Этап 4">
        Здесь будут состояние слежения за файловой системой, размеры корней, нераспознанные папки
        и файлы, а также ручной маппинг.
      </Alert>
    </Stack>
  )
}
