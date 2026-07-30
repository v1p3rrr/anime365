import { Badge, Button, Group, Modal, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconLink, IconUnlink } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { ErrorState } from './ErrorState'

interface LocalFile {
  file_id: number
  filename: string
  folder: string
  kind: 'video' | 'subtitle'
  lang: string | null
  size_bytes: number
  /** Номер серии по разбору имени. */
  episode: number | null
  matched_rule_id: string | null
  is_unmatched: boolean
  /** Номер серии, назначенный вручную. Автоскан его не трогает. */
  manual_episode: number | null
}

interface Props {
  opened: boolean
  onClose: () => void
  seriesId: number
  /** Серия, к которой привязываем. */
  episode: number
  episodeLabel: string
}

function formatSize(bytes: number): string {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(1)} ГБ`
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(0)} МБ`
  return `${(bytes / 1024).toFixed(0)} КБ`
}

/**
 * Ручная привязка локального файла к серии.
 *
 * Нужна, когда разбор имени ошибся: у релиз-группы сдвинута нумерация, номера
 * в имени нет вовсе, или серия склеена из двух. Привязка помечается ручной и
 * переживает последующие сканирования.
 */
export function BindEpisodeModal({ opened, onClose, seriesId, episode, episodeLabel }: Props) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('')

  const files = useQuery({
    queryKey: ['series-files', seriesId],
    queryFn: () => api.get<LocalFile[]>(`/library/series/${seriesId}/files`),
    enabled: opened,
  })

  const bind = useMutation({
    mutationFn: (fileId: number) =>
      api.post<unknown>('/library/episode-mappings', {
        series_id: seriesId,
        episode,
        file_id: fileId,
      }),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: `Файл привязан к серии ${episodeLabel}` })
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
      queryClient.invalidateQueries({ queryKey: ['series-files', seriesId] })
      onClose()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось привязать', message: error.message }),
  })

  const visible = (files.data ?? []).filter((f) =>
    filter ? f.filename.toLowerCase().includes(filter.toLowerCase()) : true,
  )

  return (
    <Modal opened={opened} onClose={onClose} title={`Привязать файл к серии ${episodeLabel}`} size="lg">
      <Stack gap="sm">
        <Text size="xs" c="dimmed">
          Файлы из папок, привязанных к этому тайтлу. Ручная привязка помечается и не
          перетирается сканированием — в отличие от разбора имени.
        </Text>

        {files.isError && (
          <ErrorState error={files.error} title="Не удалось получить файлы" onRetry={() => files.refetch()} />
        )}

        <TextInput
          size="xs"
          placeholder="Фильтр по имени файла"
          value={filter}
          onChange={(event) => setFilter(event.currentTarget.value)}
        />

        {files.isSuccess && visible.length === 0 && (
          <Text size="sm" c="dimmed">
            {files.data.length === 0
              ? 'В папках этого тайтла нет файлов. Сначала привяжите папку и запустите сканирование.'
              : 'Под фильтр ничего не подошло.'}
          </Text>
        )}

        <Stack gap={4} mah={420} style={{ overflowY: 'auto' }}>
          {visible.map((file) => (
            <UnstyledButton
              key={file.file_id}
              onClick={() => bind.mutate(file.file_id)}
              disabled={bind.isPending}
              p={6}
              style={{ borderRadius: 6 }}
            >
              <Group gap="xs" wrap="nowrap" justify="space-between">
                <Stack gap={0} style={{ minWidth: 0 }}>
                  <Text size="sm" truncate>
                    {file.filename}
                  </Text>
                  <Text size="xs" c="dimmed" truncate>
                    {file.folder} · {formatSize(file.size_bytes)}
                    {file.matched_rule_id && ` · правило ${file.matched_rule_id}`}
                  </Text>
                </Stack>
                <Group gap={4} wrap="nowrap">
                  {file.kind === 'subtitle' && (
                    <Badge size="xs" variant="light" color="blue">
                      сабы {file.lang ?? '?'}
                    </Badge>
                  )}
                  {file.manual_episode != null ? (
                    <Badge size="xs" color="teal" leftSection={<IconLink size={9} />}>
                      вручную: {file.manual_episode}
                    </Badge>
                  ) : file.episode != null ? (
                    <Badge size="xs" variant="light" color="gray">
                      серия {file.episode}
                    </Badge>
                  ) : (
                    <Badge size="xs" color="orange" leftSection={<IconUnlink size={9} />}>
                      номер не определён
                    </Badge>
                  )}
                </Group>
              </Group>
            </UnstyledButton>
          ))}
        </Stack>

        <Group justify="flex-end">
          <Button size="xs" variant="subtle" color="gray" onClick={onClose}>
            Закрыть
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
