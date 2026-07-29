import { Alert, Title, Stack } from '@mantine/core'

interface Props {
  onlyAiring?: boolean
}

export function CatalogPage({ onlyAiring = false }: Props) {
  return (
    <Stack>
      <Title order={3}>{onlyAiring ? 'Онгоинги' : 'Каталог'}</Title>
      <Alert color="gray" title="Этап 2">
        Здесь будет виртуализированный скролл по полному списку id с окнами по 100 карточек,
        фильтры по жанрам/типу/году и поиск. Сейчас готов только скелет приложения.
      </Alert>
    </Stack>
  )
}
