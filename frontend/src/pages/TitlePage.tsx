import { Alert, Title, Stack } from '@mantine/core'
import { useParams } from 'react-router-dom'

export function TitlePage() {
  const { id } = useParams()

  return (
    <Stack>
      <Title order={3}>Тайтл #{id}</Title>
      <Alert color="gray" title="Этап 3">
        Здесь будут описание, эпизоды, сводка переводов по языкам и типам с диапазонами покрытия,
        плашка маппинга и кнопки загрузки.
      </Alert>
    </Stack>
  )
}
