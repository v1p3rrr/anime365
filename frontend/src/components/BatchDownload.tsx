import { Button, Group, Menu, Select, Stack, Switch, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconChevronDown, IconDownload } from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { downloadsApi } from '../api/downloads'
import type { AuthorGroup } from '../api/title'

interface Props {
  group: AuthorGroup
  kind: 'voice' | 'sub' | 'raw'
  /** Номера серий, которые считаются контентом (без трейлеров и рекламы). */
  contentEpisodes: Set<number>
  disabled?: boolean
  disabledReason?: string
}

const HEIGHTS = [
  { value: '1080', label: 'до 1080p (максимум)' },
  { value: '720', label: 'до 720p' },
  { value: '480', label: 'до 480p' },
  { value: '360', label: 'до 360p' },
]

/**
 * Батч-загрузка всех серий одного автора.
 *
 * Качество задаётся как потолок: если у серии такой ступени нет, воркер возьмёт
 * максимальную доступную ниже. Спешлы и OVA по умолчанию не берём — обычно
 * нужен основной сезон.
 */
export function BatchDownload({
  group,
  kind,
  contentEpisodes,
  disabled,
  disabledReason,
}: Props) {
  const queryClient = useQueryClient()
  const [height, setHeight] = useState('1080')
  const [includeExtras, setIncludeExtras] = useState(false)

  const ids = group.translation_ids

  const run = useMutation({
    mutationFn: (what: 'video' | 'subtitle' | 'both') =>
      downloadsApi.batch(ids, what, what === 'subtitle' ? undefined : Number(height)),
    onSuccess: (result) => {
      const parts = [`добавлено: ${result.created}`]
      if (result.skipped) parts.push(`уже в очереди: ${result.skipped}`)
      if (result.failed) parts.push(`не вышло: ${result.failed}`)
      notifications.show({
        color: result.failed ? 'yellow' : 'teal',
        title: 'Батч поставлен в очередь',
        message: parts.join(' · '),
      })
      // Ошибки отдельных задач видны в очереди — туда и отправляем.
      if (result.errors.length > 0) {
        notifications.show({
          color: 'red',
          title: 'Часть переводов не поставилась',
          message: result.errors.slice(0, 3).join('; '),
        })
      }
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Батч не поставился', message: error.message }),
  })

  const label = `${ids.length} шт.`

  return (
    <Menu position="bottom-end" closeOnItemClick={false}>
      <Menu.Target>
        <Tooltip label={disabled ? disabledReason : `Скачать все серии этого автора (${label})`}>
          <Button
            size="compact-xs"
            variant="light"
            leftSection={<IconDownload size={12} />}
            rightSection={<IconChevronDown size={12} />}
            disabled={disabled}
            loading={run.isPending}
          >
            Все
          </Button>
        </Tooltip>
      </Menu.Target>

      <Menu.Dropdown>
        <Stack gap="xs" p="xs" w={250}>
          <Text size="xs" c="dimmed">
            Переводов у автора: {ids.length}
            {contentEpisodes.size > 0 && ` · серий у тайтла: ${contentEpisodes.size}`}
          </Text>

          {kind !== 'sub' && (
            <Select
              size="xs"
              label="Качество"
              description="Если такой ступени нет, возьмём ближайшую ниже"
              data={HEIGHTS}
              value={height}
              onChange={(value) => setHeight(value ?? '1080')}
              allowDeselect={false}
            />
          )}

          <Switch
            size="xs"
            label="Включая спешлы и OVA"
            description="По умолчанию только основные серии"
            checked={includeExtras}
            onChange={(event) => setIncludeExtras(event.currentTarget.checked)}
          />

          <Group gap={4} grow>
            {kind === 'sub' ? (
              <Button size="compact-xs" onClick={() => run.mutate('subtitle')}>
                Все субтитры
              </Button>
            ) : (
              <>
                <Button size="compact-xs" onClick={() => run.mutate('video')}>
                  Все видео
                </Button>
                <Button size="compact-xs" variant="light" onClick={() => run.mutate('both')}>
                  Видео и сабы
                </Button>
              </>
            )}
          </Group>
        </Stack>
      </Menu.Dropdown>
    </Menu>
  )
}
