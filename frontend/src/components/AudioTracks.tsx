import { Alert, Badge, Group, Stack, Table, Text, Tooltip } from '@mantine/core'

import { type AudioTrack } from '../api/library'
import { usePermissions } from '../hooks/usePermissions'
import { useT } from '../i18n'
import { useFormat } from '../i18n/format'
import { langCode } from '../i18n/flags'

interface Props {
  tracks: AudioTrack[]
  /** Показывать ли название тайтла: на его же странице оно лишнее. */
  showTitle?: boolean
  emptyText?: string
}

/**
 * Список звуковых дорожек с проигрывателем.
 *
 * Проигрыватель — обычный `<audio controls>`. Отдельная библиотека здесь ничего
 * не добавила бы: браузер сам даёт кнопки, перемотку и громкость, а форматы у
 * нас те, что он и так понимает. Библиотека понадобится, только если захочется
 * плейлиста и сквозного проигрывания подряд — тогда и стоит её заводить.
 *
 * Перемотка работает потому, что эндпоинт отдаёт файл с поддержкой Range. Без
 * этого ползунок был бы декоративным, и заметить подмену можно было бы только
 * попробовав мотнуть.
 *
 * Дорожки в контейнерах, которых браузер не понимает (`.mka` попадается, если у
 * источника экзотический кодек), показываются, но без проигрывателя: строка со
 * сломанным плеером хуже честной пометки.
 */
export function AudioTracks({ tracks, showTitle = false, emptyText }: Props) {
  const { t } = useT()
  const format = useFormat()
  // Отдача файла — тот же эндпоинт, что и у видео, и то же право `watch`.
  // Без него список дорожек остаётся (он про содержимое библиотеки), а
  // проигрыватель — нет: он всё равно получил бы отказ на первом же байте.
  const canWatch = usePermissions().can('watch')

  if (tracks.length === 0) {
    return <Alert color="gray">{emptyText ?? t('audio.none')}</Alert>
  }

  return (
    <Table.ScrollContainer minWidth={520}>
      <Table verticalSpacing="xs">
        <Table.Thead>
          <Table.Tr>
            {showTitle && <Table.Th>{t('audio.columnTitle')}</Table.Th>}
            <Table.Th w={80}>{t('audio.columnEpisode')}</Table.Th>
            <Table.Th w={200}>{t('audio.columnAuthor')}</Table.Th>
            <Table.Th>{t('audio.columnPlayer')}</Table.Th>
            <Table.Th w={90}>{t('audio.columnSize')}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {tracks.map((track) => (
            <Table.Tr key={track.task_id}>
              {showTitle && (
                <Table.Td>
                  <Text size="sm">{track.series_title}</Text>
                </Table.Td>
              )}
              <Table.Td>
                <Text size="sm">
                  {track.episode != null ? t('audio.episode', { n: track.episode }) : '—'}
                </Text>
              </Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  {track.lang && (
                    <Badge size="xs" variant="light">
                      {langCode(track.lang)}
                    </Badge>
                  )}
                  <Text size="xs" c="dimmed" lineClamp={1}>
                    {track.authors || t('audio.noAuthor')}
                  </Text>
                </Group>
              </Table.Td>
              <Table.Td>
                {!canWatch ? (
                  <Text size="xs" c="dimmed">
                    —
                  </Text>
                ) : track.playable ? (
                  <audio
                    controls
                    preload="none"
                    src={`/api/stream/audio/${track.task_id}`}
                    style={{ width: '100%', minWidth: 220, height: 32 }}
                  />
                ) : (
                  <Tooltip label={t('audio.notPlayableHint', { name: track.filename })}>
                    <Text size="xs" c="dimmed">
                      {t('audio.notPlayable')}
                    </Text>
                  </Tooltip>
                )}
              </Table.Td>
              <Table.Td>
                <Text size="xs" c="dimmed">
                  {format.size(track.size_bytes)}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  )
}

/** Сводка «дорожек N · вес» — для заголовка раздела. */
export function AudioSummary({ tracks }: { tracks: AudioTrack[] }) {
  const { t } = useT()
  const format = useFormat()
  if (tracks.length === 0) return null
  const size = tracks.reduce((sum, track) => sum + track.size_bytes, 0)
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">
        {t('audio.summary', { count: tracks.length, size: format.size(size) })}
      </Text>
    </Stack>
  )
}
