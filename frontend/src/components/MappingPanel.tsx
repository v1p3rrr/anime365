import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Progress,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconFolderOff, IconFolderPlus, IconLock, IconUnlink } from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { formatBytes, SOURCE_COLORS, SOURCE_LABELS } from '../api/library'
import { titleApi, type LocalCoverage, type TitleMappingInfo } from '../api/title'

interface Props {
  seriesId: number
  mappings: TitleMappingInfo[]
  local: LocalCoverage
  onRebind: () => void
}

/**
 * Плашка привязки к локальным папкам.
 *
 * Показывает не только «смаплено», но и ЧЕМ определено и на какое название из
 * .nfo — иначе неверная привязка обнаружится только после того, как файлы уедут
 * не в ту папку.
 */
export function MappingPanel({ seriesId, mappings, local, onRebind }: Props) {
  const queryClient = useQueryClient()

  const unbind = useMutation({
    mutationFn: titleApi.unbind,
    onSuccess: () => {
      notifications.show({ color: 'teal', message: 'Привязка удалена' })
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
      queryClient.invalidateQueries({ queryKey: ['library-status'] })
      queryClient.invalidateQueries({ queryKey: ['library-unmapped'] })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось отвязать', message: error.message }),
  })

  if (mappings.length === 0) {
    return (
      <Alert color="orange" title="Нет привязки к локальной папке" icon={<IconFolderOff size={18} />}>
        <Text size="sm" mb="xs">
          Этот тайтл не сопоставлен ни с одной папкой библиотеки. Скачанные файлы будет некуда
          положить, пока привязка не создана.
        </Text>
        <Button size="xs" leftSection={<IconFolderPlus size={14} />} onClick={onRebind}>
          Привязать папку
        </Button>
      </Alert>
    )
  }

  const coverageEntries = Object.entries(local.coverage)

  return (
    <Card withBorder padding="sm" style={{ flexShrink: 0 }}>
      <Stack gap="xs">
        {mappings.map((mapping) => (
          <div key={mapping.mapping_id}>
            <Group justify="space-between" wrap="nowrap" align="flex-start">
              <div style={{ minWidth: 0 }}>
                <Group gap={6} wrap="wrap">
                  <Code style={{ wordBreak: 'break-all' }}>{mapping.rel_path}</Code>
                  {mapping.season_number != null && (
                    <Badge size="xs" variant="light">
                      сезон {mapping.season_number}
                    </Badge>
                  )}
                  {!mapping.writable && (
                    <Tooltip label="Том смонтирован только для чтения — загрузки недоступны">
                      <Badge size="xs" color="yellow" leftSection={<IconLock size={10} />}>
                        только чтение
                      </Badge>
                    </Tooltip>
                  )}
                </Group>

                <Group gap={6} mt={4} wrap="wrap">
                  <Tooltip
                    multiline
                    w={300}
                    label={mapping.matched_on ?? 'нет подробностей'}
                  >
                    <Badge size="sm" variant="light" color={SOURCE_COLORS[mapping.source] ?? 'gray'}>
                      {SOURCE_LABELS[mapping.source] ?? mapping.source} ·{' '}
                      {mapping.confidence.toFixed(0)}
                    </Badge>
                  </Tooltip>
                  {mapping.needs_confirmation && (
                    <Badge size="sm" color="yellow">
                      требует подтверждения
                    </Badge>
                  )}
                  <Text size="xs" c="dimmed">
                    {mapping.video_count} видео · {formatBytes(mapping.size_bytes)}
                  </Text>
                </Group>

                {/* Название из .nfo — то, по чему пользователь глазами проверит,
                    что привязка верная. */}
                {mapping.nfo_title && (
                  <Text size="xs" c="dimmed" mt={2} style={{ wordBreak: 'break-word' }}>
                    в .nfo: {mapping.nfo_title}
                    {mapping.nfo_premiered ? ` · премьера ${mapping.nfo_premiered}` : ''}
                  </Text>
                )}
              </div>

              <Group gap={4} wrap="nowrap">
                <Tooltip label="Убрать связь с этой папкой">
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="red"
                    leftSection={<IconUnlink size={12} />}
                    onClick={() => unbind.mutate(mapping.mapping_id)}
                    loading={unbind.isPending}
                  >
                    Отвязать
                  </Button>
                </Tooltip>
              </Group>
            </Group>
          </div>
        ))}

        <Group gap="xs">
          <Button
            size="compact-xs"
            variant="light"
            leftSection={<IconFolderPlus size={12} />}
            onClick={onRebind}
          >
            Привязать ещё папку
          </Button>
        </Group>

        {local.has_local_files && (
          <Stack gap={4} mt={4}>
            {coverageEntries.map(([role, info]) => (
              <div key={role}>
                <Group justify="space-between" gap="xs">
                  <Text size="xs">{info.label}</Text>
                  <Text size="xs" c={info.complete ? 'teal' : 'dimmed'}>
                    {info.have} из {info.total}
                  </Text>
                </Group>
                <Progress
                  size="sm"
                  value={info.total ? (info.have / info.total) * 100 : 0}
                  color={info.complete ? 'teal' : info.have ? 'yellow' : 'gray'}
                />
                {!info.complete && info.missing_ranges && (
                  <Text size="xs" c="dimmed">
                    нет: {info.missing_ranges}
                  </Text>
                )}
              </div>
            ))}

            {/* Локально серий может быть больше, чем знает сайт — по ТЗ это не ошибка. */}
            {local.local_max_episode > local.episodes_expected && (
              <Text size="xs" c="dimmed">
                Локально есть серия {local.local_max_episode}, хотя на сайте их{' '}
                {local.episodes_expected}.
              </Text>
            )}
          </Stack>
        )}
      </Stack>
    </Card>
  )
}
