import { ActionIcon, Group, Loader, Menu, Text, Tooltip } from '@mantine/core'
import { IconRefresh } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications } from '@mantine/notifications'

import { catalogApi } from '../api/client'
import { useT, type TranslationKey } from '../i18n'
import { useApiError } from '../i18n/apiError'

/** Ключ словаря по виду джобы, который приходит с сервера. */
const MODE_KEYS: Record<string, string> = {
  catalog_full: 'sync.modeFull',
  catalog_incr: 'sync.modeIncr',
  catalog_airing: 'sync.modeAiring',
}

interface Props {
  /** Сколько тайтлов дал текущий фильтр — чтобы не печатать одно и то же число дважды. */
  filteredTotal?: number
}

export function SyncIndicator({ filteredTotal }: Props) {
  const queryClient = useQueryClient()
  const { t, locale } = useT()
  const apiError = useApiError()

  const statusQuery = useQuery({
    queryKey: ['catalog-status'],
    queryFn: catalogApi.status,
    // Пока синхронизация идёт, опрашиваем чаще, чтобы индикатор был живым.
    refetchInterval: (query) => (query.state.data?.running ? 2000 : 60_000),
  })

  const sync = useMutation({
    mutationFn: catalogApi.sync,
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('sync.done') })
      queryClient.invalidateQueries({ queryKey: ['catalog-ids'] })
      queryClient.invalidateQueries({ queryKey: ['catalog-status'] })
      queryClient.invalidateQueries({ queryKey: ['genres'] })
    },
    onError: (error: Error) => {
      notifications.show({ color: 'red', title: t('sync.failed'), message: apiError(error) })
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
            {MODE_KEYS[running] ? t(MODE_KEYS[running] as TranslationKey) : running}
          </Text>
        </Group>
      ) : filteredTotal !== undefined &&
        status?.total_series !== undefined &&
        filteredTotal !== status.total_series ? (
        <Text size="xs" c="dimmed">
          {t('catalog.ofTotal', { count: status.total_series.toLocaleString(locale) })}
        </Text>
      ) : null}

      <Menu position="bottom-end">
        <Menu.Target>
          <Tooltip label={t('sync.cached', { count: status?.total_series?.toLocaleString(locale) ?? '—' })}>
            <ActionIcon
              variant="default"
              loading={sync.isPending}
              disabled={Boolean(running)}
              aria-label={t('sync.refresh')}
            >
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>{t('sync.refresh')}</Menu.Label>
          <Menu.Item onClick={() => sync.mutate('incremental')}>
            {t('sync.menuIncremental')}
            <Text size="xs" c="dimmed">
              {t('sync.menuIncrementalHint')}
            </Text>
          </Menu.Item>
          <Menu.Item onClick={() => sync.mutate('airing')}>
            {t('sync.menuAiring')}
            <Text size="xs" c="dimmed">
              {t('sync.menuAiringHint')}
            </Text>
          </Menu.Item>
          <Menu.Item onClick={() => sync.mutate('full')}>
            {t('sync.menuFull')}
            <Text size="xs" c="dimmed">
              {t('sync.menuFullHint')}
            </Text>
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  )
}
