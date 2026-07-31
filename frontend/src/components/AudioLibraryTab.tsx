import { Accordion, Alert, Badge, Group, Image, Loader, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'

import { libraryApi } from '../api/library'
import { useT } from '../i18n'
import { useFormat } from '../i18n/format'
import { pickTitle } from '../i18n/titles'
import { AudioTracks } from './AudioTracks'
import { PosterPlaceholder } from './PosterPlaceholder'

/**
 * Фонотека целиком: строка на тайтл, внутри — дорожки по сериям.
 *
 * Свёрнуто по умолчанию намеренно. Каждая раскрытая строка означает столько
 * элементов `<audio>`, сколько в ней дорожек, а у длинного сериала их два
 * десятка — раскрывать всё сразу значит создать сотню проигрывателей.
 * `preload="none"` не даёт им тянуть файлы до нажатия, но сами элементы всё
 * равно лишние.
 */
export function AudioLibraryTab() {
  const { t, locale } = useT()
  const format = useFormat()

  const albums = useQuery({ queryKey: ['audio-library'], queryFn: libraryApi.audio })

  if (albums.isLoading) return <Loader size="sm" />
  if (albums.isError) return <Alert color="red">{t('audio.loadFailed')}</Alert>

  const rows = albums.data ?? []
  if (rows.length === 0) {
    return <Alert color="gray">{t('audio.libraryEmpty')}</Alert>
  }

  const total = rows.reduce((sum, album) => sum + album.size_bytes, 0)
  const tracks = rows.reduce((sum, album) => sum + album.track_count, 0)

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        {t('audio.librarySummary', {
          titles: rows.length,
          count: tracks,
          size: format.size(total),
        })}
      </Text>

      <Accordion variant="separated" chevronPosition="left">
        {rows.map((album) => (
          <Accordion.Item key={album.series_id} value={String(album.series_id)}>
            <Accordion.Control>
              <Group gap="sm" wrap="nowrap">
                {/* Маленький постер: строка узнаётся по обложке быстрее, чем
                    по названию, особенно когда тайтлов много. */}
                {album.poster_url ? (
                  <Image src={album.poster_url} w={32} h={45} radius={3} fit="cover" />
                ) : (
                  <div style={{ width: 32, height: 45, flexShrink: 0 }}>
                    <PosterPlaceholder />
                  </div>
                )}
                <Stack gap={2} style={{ minWidth: 0 }}>
                  <Text size="sm" lineClamp={1}>
                    {pickTitle(album.titles, locale, album.fallback_title)}
                  </Text>
                  <Group gap={6}>
                    <Badge size="xs" variant="light">
                      {t('audio.trackCount', { count: album.track_count })}
                    </Badge>
                    <Text size="xs" c="dimmed">
                      {format.size(album.size_bytes)}
                    </Text>
                  </Group>
                </Stack>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              <AudioTracks tracks={album.tracks} />
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion>
    </Stack>
  )
}
