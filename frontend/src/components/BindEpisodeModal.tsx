import { Badge, Button, Group, Modal, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { notifications } from '@mantine/notifications'
import { IconLink, IconUnlink } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../api/client'
import { ErrorState } from './ErrorState'
import { useT } from '../i18n'
import { useFormat } from '../i18n/format'
import { useApiError } from '../i18n/apiError'

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

/**
 * Ручная привязка локального файла к серии.
 *
 * Нужна, когда разбор имени ошибся: у релиз-группы сдвинута нумерация, номера
 * в имени нет вовсе, или серия склеена из двух. Привязка помечается ручной и
 * переживает последующие сканирования.
 */
export function BindEpisodeModal({ opened, onClose, seriesId, episode, episodeLabel }: Props) {
  const { t } = useT()
  const format = useFormat()
  const apiError = useApiError()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('')

  // Поле фильтра работает в двух режимах, и это намеренно. Пустое — список
  // файлов папок тайтла, как было. С текстом — поиск по ВСЕЙ библиотеке на
  // сервере: часть тайтла нередко лежит в стороне (вторая часть «Girls Band
  // Cry» — своя папка в корне фильмов), и в папках тайтла её нет вовсе.
  // Привязка серии к файлу папку тайтла не требует, ограничивать выбор незачем.
  const needle = useDebouncedValue(filter.trim(), 250)[0]

  const files = useQuery({
    queryKey: ['series-files', seriesId, needle],
    queryFn: () =>
      api.get<LocalFile[]>(
        `/library/series/${seriesId}/files${needle ? `?query=${encodeURIComponent(needle)}` : ''}`,
      ),
    enabled: opened,
    placeholderData: (previous) => previous,
  })

  const bind = useMutation({
    mutationFn: (fileId: number) =>
      api.post<unknown>('/library/episode-mappings', {
        series_id: seriesId,
        episode,
        file_id: fileId,
      }),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('bindEpisode.bound', { episode: episodeLabel }) })
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
      queryClient.invalidateQueries({ queryKey: ['series-files', seriesId] })
      onClose()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('bindEpisode.failed'), message: apiError(error) }),
  })

  // Отбор уже сделан сервером, когда запрос непустой; фильтровать повторно
  // нельзя — совпасть могло имя папки, а не файла, и находка бы пропала.
  const visible = files.data ?? []

  return (
    <Modal opened={opened} onClose={onClose} title={t('bindEpisode.title', { episode: episodeLabel })} size="lg">
      <Stack gap="sm">
        <Text size="xs" c="dimmed">
          {t('bindEpisode.hint')}
        </Text>

        {files.isError && (
          <ErrorState error={files.error} title={t('bindEpisode.filesFailed')} onRetry={() => files.refetch()} />
        )}

        <TextInput
          size="xs"
          placeholder={t('bindEpisode.filter')}
          value={filter}
          onChange={(event) => setFilter(event.currentTarget.value)}
        />

        {files.isSuccess && visible.length === 0 && (
          <Text size="sm" c="dimmed">
            {needle ? t('bindEpisode.noMatch') : t('bindEpisode.noFiles')}
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
                    {file.folder} · {format.size(file.size_bytes)}
                    {file.matched_rule_id && t('bindEpisode.rule', { id: file.matched_rule_id })}
                  </Text>
                </Stack>
                <Group gap={4} wrap="nowrap">
                  {file.kind === 'subtitle' && (
                    <Badge size="xs" variant="light" color="blue">
                      {t('bindEpisode.subtitleLang', { lang: file.lang ?? '?' })}
                    </Badge>
                  )}
                  {file.manual_episode != null ? (
                    <Badge size="xs" color="teal" leftSection={<IconLink size={9} />}>
                      {t('bindEpisode.manualEpisode', { n: file.manual_episode })}
                    </Badge>
                  ) : file.episode != null ? (
                    <Badge size="xs" variant="light" color="gray">
                      {t('bindEpisode.parsedEpisode', { n: file.episode })}
                    </Badge>
                  ) : (
                    <Badge size="xs" color="orange" leftSection={<IconUnlink size={9} />}>
                      {t('bindEpisode.noNumber')}
                    </Badge>
                  )}
                </Group>
              </Group>
            </UnstyledButton>
          ))}
        </Stack>

        <Group justify="flex-end">
          <Button size="xs" variant="subtle" color="gray" onClick={onClose}>
            {t('common.close')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
