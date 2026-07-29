import { Alert, Title, Stack } from '@mantine/core'

export function QueuePage() {
  return (
    <Stack>
      <Title order={3}>Очередь загрузок</Title>
      <Alert color="gray" title="Этап 5">
        Здесь будут прогресс, скорость, ETA, статусы, ручной ретрай, пауза и отмена, а также
        индикатор работы джобы постобработки.
      </Alert>
    </Stack>
  )
}
