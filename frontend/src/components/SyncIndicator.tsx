import { ActionIcon, Badge, Group, Loader, Menu, Text, Tooltip } from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import { catalogApi } from '../api/client'

const MODE_LABELS: Record<string, string> = {
  catalog_full: 'полная синхронизация',
  catalog_incr: 'поиск новых тайтлов',
  catalog_airing: 'обновление онгоингов',
}

export function SyncIndicator() {
  const queryClient = useQueryClient()

  const statusQuery = useQuery({
    queryKey: ['catalog-status'],
    queryFn: catalogApi.status,
    // Пока синхронизация идёт, опрашиваем чаще, чтобы индикатор был живым.
    refetchInterval: (query) => (query.state.data?.running ? 2000 : 60_000),
  })

  const sync = useMutation({
    mutationFn: catalogApi.sync,
    onSuccess: () => {
      notifications.show({ color: 'teal', message: 'Синхронизация завершена' })
      queryClient.invalidateQueries({ queryKey: ['catalog-ids'] })
      queryClient.invalidateQueries({ queryKey: ['catalog-status'] })
      queryClient.invalidateQueries({ queryKey: ['genres'] })
    },
    onError: (error: Error) => {
      notifications.show({ color: 'red', title: 'Синхронизация не удалась', message: error.message })
    },
  })

  const status = statusQuery.data
  const running = status?.running

  return (
    <Group gap="xs">
      {running ? (
        <Group gap={6}>
          <Loader size="xs" />
          <Text size="xs" c="dimmed">
            {MODE_LABELS[running] ?? running}
          </Text>
        </Group>
      ) : (
        <Tooltip label={`В кэше тайтлов: ${status?.total_series?.toLocaleString('ru') ?? '—'}`}>
          <Badge variant="light" color="gray">
            {status?.total_series?.toLocaleString('ru') ?? '—'}
          </Badge>
        </Tooltip>
      )}

      <Menu position="bottom-end">
        <Menu.Target>
          <ActionIcon
            variant="default"
            loading={sync.isPending}
            disabled={Boolean(running)}
            aria-label="Обновить каталог"
          >
            <IconRefresh size={16} />
          </ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>Обновить каталог</Menu.Label>
          <Menu.Item onClick={() => sync.mutate('incremental')}>
            Только новые тайтлы
            <Text size="xs" c="dimmed">
              несколько секунд
            </Text>
          </Menu.Item>
          <Menu.Item onClick={() => sync.mutate('airing')}>
            Обновить онгоинги
            <Text size="xs" c="dimmed">
              около 300 тайтлов
            </Text>
          </Menu.Item>
          <Menu.Item onClick={() => sync.mutate('full')}>
            Полная синхронизация
            <Text size="xs" c="dimmed">
              примерно минута, 29 тысяч тайтлов
            </Text>
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  )
}
