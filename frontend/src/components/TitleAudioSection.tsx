import { Card, Group, Loader, Text } from '@mantine/core'
import { IconMusic } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'

import { titleApi } from '../api/title'
import { useT } from '../i18n'
import { AudioSummary, AudioTracks } from './AudioTracks'

/**
 * Звуковые дорожки этого тайтла.
 *
 * Название тайтла в строках не показываем: и так понятно, чья это страница.
 *
 * Раздел скрывается целиком, когда дорожек нет. Пустая карточка «здесь ничего»
 * на странице, где и так много блоков, только добавляет шума: извлечение звука
 * — редкий сценарий, и большинство тайтлов его не касается.
 */
export function TitleAudioSection({ seriesId }: { seriesId: number }) {
  const { t } = useT()

  const audio = useQuery({
    queryKey: ['title-audio', seriesId],
    queryFn: () => titleApi.audio(seriesId),
  })

  if (audio.isLoading) return <Loader size="xs" />
  const tracks = audio.data?.tracks ?? []
  if (tracks.length === 0) return null

  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" mb="xs" wrap="wrap" gap="xs">
        <Group gap={6}>
          <IconMusic size={16} />
          <Text fw={600} size="sm">
            {t('audio.section')}
          </Text>
        </Group>
        <AudioSummary tracks={tracks} />
      </Group>
      <AudioTracks tracks={tracks} />
    </Card>
  )
}
