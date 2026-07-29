import { ActionIcon, Group, Loader, Menu, Text, Tooltip } from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import { catalogApi } from '../api/client'

const MODE_LABELS: Record<string, string> = {
  catalog_full: 'полная синхронизация',
  catalog_incr: 'поиск новых тайтлов',
  catalog_airing: 'обновление онгоингов',
}

interface Props {
  /** Сколько тайтлов дал текущий фильтр — чтобы не печатать одно и то же число дважды. */
  filteredTotal?: number
}

export function SyncIndicator({ filteredTotal }: Props) {
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
      {/* Число в кэше не дублируем рядом со счётчиком «найдено»: без фильтров они
          совпадают и выглядят как одна цифра, напечатанная дважды. Показываем его
          только когда выборка отфильтрована, иначе — в подсказке у кнопки. */}
      {running ? (
        <Group gap={6}>
          <Loader size="xs" />
          <Text size="xs" c="dimmed">
            {MODE_LABELS[running] ?? running}
          </Text>
        </Group>
      ) : filteredTotal !== undefined &&
        status?.total_series !== undefined &&
        filteredTotal !== status.total_series ? (
        <Text size="xs" c="dimmed">
          из {status.total_series.toLocaleString('ru')}
        </Text>
      ) : null}

      <Menu position="bottom-end">
        <Menu.Target>
          <Tooltip label={`В кэше тайтлов: ${status?.total_series?.toLocaleString('ru') ?? '—'}`}>
            <ActionIcon
              variant="default"
              loading={sync.isPending}
              disabled={Boolean(running)}
              aria-label="Обновить каталог"
            >
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
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
