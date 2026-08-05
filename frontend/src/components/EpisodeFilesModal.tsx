import { Alert, Badge, Button, Code, Group, Loader, Modal, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconFileSearch } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { ErrorState } from './ErrorState'
import { useT } from '../i18n'
import { useApiError } from '../i18n/apiError'
import { useFormat } from '../i18n/format'

interface EpisodeFile {
  file_id: number
  filename: string
  /** Полный путь на диске — по нему файл ищут руками. */
  path: string
  folder: string
  role: string
  kind: 'video' | 'subtitle' | 'metadata'
  lang: string | null
  size_bytes: number
  is_manual: boolean
  matched_rule_id: string | null
  release_group: string | null
  anime365_translation_id: number | null
  streams: { audio_languages?: string[]; subtitle_languages?: string[] } | null
  /** Непусто при пустых `streams` означает «смотрели, ничего не нашли». */
  probed_at: string | null
}

interface MediaStream {
  kind: string | null
  codec: string | null
  language: string | null
  title: string | null
  resolution?: string
  channels?: number
}

interface MediaInfo {
  container: string | null
  duration: number | null
  streams: MediaStream[]
}

interface Props {
  opened: boolean
  onClose: () => void
  seriesId: number
  episode: number
  episodeLabel: string
}

/**
 * Что лежит на диске за одной серией.
 *
 * Значок в строке серии показывает наличие, но не отвечает на вопросы «какой
 * именно это файл» и «что у него внутри» — а они и возникают, когда что-то
 * смапилось не туда.
 *
 * ffprobe запускается **по кнопке и по одному файлу**. Замерено: он читает
 * заголовок, а не файл целиком (0,37 МБ из 37-мегабайтного), но дорог не
 * объём, а характер — `fork/exec` и мелкие чтения вразнобой по диску, с
 * которого одновременно качают и стримят. Массовый прогон остаётся фоновой
 * джобой; здесь человек ждёт осознанно.
 */
export function EpisodeFilesModal({ opened, onClose, seriesId, episode, episodeLabel }: Props) {
  const { t } = useT()
  const format = useFormat()
  const apiError = useApiError()
  const queryClient = useQueryClient()

  const files = useQuery({
    queryKey: ['episode-files', seriesId, episode],
    queryFn: () =>
      api.get<EpisodeFile[]>(`/library/series/${seriesId}/episodes/${episode}/files`),
    enabled: opened,
  })

  // Разбор не хранится в базе целиком — только языки. Кодеки и длительность
  // держим в памяти окна: они нужны ровно здесь и сейчас, а гонять их через
  // скан и базу ради одного взгляда незачем.
  const [media, setMedia] = useState<Record<number, MediaInfo>>({})

  const probe = useMutation({
    mutationFn: (fileId: number) =>
      api.post<{ file_id: number; media: MediaInfo }>(`/library/files/${fileId}/probe`),
    onSuccess: (result) => {
      setMedia((prev) => ({ ...prev, [result.file_id]: result.media }))
      queryClient.invalidateQueries({ queryKey: ['episode-files', seriesId, episode] })
      notifications.show({ color: 'teal', message: t('episodeFiles.probed') })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('episodeFiles.probeFailed'), message: apiError(error) }),
  })

  const tracks = (file: EpisodeFile) => {
    const audio = file.streams?.audio_languages ?? []
    const subs = file.streams?.subtitle_languages ?? []
    if (audio.length === 0 && subs.length === 0) return null
    return (
      <Group gap={4} wrap="wrap">
        {audio.map((lang) => (
          <Badge key={`a-${lang}`} size="xs" variant="light" color="teal">
            {t('episodeFiles.audio', { lang })}
          </Badge>
        ))}
        {subs.map((lang) => (
          <Badge key={`s-${lang}`} size="xs" variant="light" color="blue">
            {t('episodeFiles.subtitle', { lang })}
          </Badge>
        ))}
      </Group>
    )
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t('episodeFiles.title', { episode: episodeLabel })}
      size="lg"
    >
      <Stack gap="sm">
        {files.isLoading && <Loader size="sm" />}

        {files.isError && (
          <ErrorState error={files.error} title={t('episodeFiles.failed')} onRetry={() => files.refetch()} />
        )}

        {files.isSuccess && files.data.length === 0 && (
          <Alert color="gray">{t('episodeFiles.empty')}</Alert>
        )}

        {(files.data ?? []).map((file) => {
          const hasTracks = tracks(file)
          return (
            <Stack key={file.file_id} gap={4} p="xs" style={{ borderRadius: 6 }}>
              <Group gap="xs" justify="space-between" wrap="nowrap">
                <Text size="sm" fw={500} style={{ minWidth: 0 }} truncate>
                  {file.filename}
                </Text>
                <Group gap={4} wrap="nowrap">
                  <Badge size="xs" variant="light">
                    {file.role}
                  </Badge>
                  {file.is_manual && (
                    <Badge size="xs" color="teal">
                      {t('episodeFiles.manual')}
                    </Badge>
                  )}
                </Group>
              </Group>

              {/* Путь целиком и с переносом: его копируют, чтобы найти файл
                  руками, и обрезанный путь для этого бесполезен. */}
              <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {file.path}
              </Code>

              <Group gap="xs" wrap="wrap">
                <Text size="xs" c="dimmed">
                  {format.size(file.size_bytes)}
                </Text>
                {file.matched_rule_id && (
                  <Text size="xs" c="dimmed">
                    {t('episodeFiles.rule', { id: file.matched_rule_id })}
                  </Text>
                )}
                {file.release_group && (
                  <Text size="xs" c="dimmed">
                    {t('episodeFiles.group', { name: file.release_group })}
                  </Text>
                )}
                {file.anime365_translation_id != null && (
                  <Text size="xs" c="dimmed">
                    {t('episodeFiles.translation', { id: file.anime365_translation_id })}
                  </Text>
                )}
              </Group>

              {hasTracks}

              {/* Кнопка у **любого** файла, включая субтитры: ffprobe читает
                  и `.ass`, и `.srt`, а его ответ отличает настоящие субтитры от
                  пустого или битого файла. Прятать её у файлов с известными
                  дорожками тоже нельзя — данные из `.nfo` бывают неверными
                  (у Fate HF у видеодорожки стоял язык `ita`). */}
              {file.kind !== 'metadata' && (
                <Group gap="xs">
                  {!hasTracks && (
                    <Text size="xs" c="dimmed">
                      {file.probed_at
                        ? t('episodeFiles.probedNothing')
                        : t('episodeFiles.noTracks')}
                    </Text>
                  )}
                  <Button
                    size="compact-xs"
                    variant={hasTracks ? 'subtle' : 'light'}
                    color={hasTracks ? 'gray' : undefined}
                    leftSection={<IconFileSearch size={13} />}
                    loading={probe.isPending && probe.variables === file.file_id}
                    onClick={() => probe.mutate(file.file_id)}
                  >
                    {hasTracks ? t('episodeFiles.reprobe') : t('episodeFiles.probe')}
                  </Button>
                </Group>
              )}

              {media[file.file_id] && (
                <Stack gap={2}>
                  <Text size="xs" c="dimmed">
                    {[
                      media[file.file_id].container,
                      media[file.file_id].duration
                        ? t('episodeFiles.duration', {
                            // `format.duration` возвращает null на пустом входе,
                            // но сюда мы попадаем только при непустом.
                            value: format.duration(media[file.file_id].duration) ?? '',
                          })
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </Text>
                  {media[file.file_id].streams.map((stream, index) => (
                    <Text key={index} size="xs" c="dimmed">
                      {[
                        stream.kind,
                        stream.codec,
                        stream.resolution,
                        stream.channels ? t('episodeFiles.channels', { n: stream.channels }) : null,
                        stream.language,
                        stream.title,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </Text>
                  ))}
                </Stack>
              )}
            </Stack>
          )
        })}

        <Group justify="flex-end">
          <Button size="xs" variant="subtle" color="gray" onClick={onClose}>
            {t('common.close')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
