import { Button, Group, Loader, Menu, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconChevronDown, IconDownload } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { downloadsApi } from '../api/downloads'
import { titleApi } from '../api/title'
import { useT } from '../i18n'

interface Props {
  translationId: number
  kind: 'video' | 'subtitle'
  /** Оценка максимума по разрешению исходника — до запроса точных ступеней. */
  estimatedHeight?: number | null
  disabled?: boolean
  disabledReason?: string
}

const DEFAULT_HEIGHT = 1080

/**
 * Сплит-кнопка загрузки.
 *
 * Основное действие — 1080 (фактически максимум: 4K скачать нельзя). Точный
 * список ступеней известен только из embed, а это запрос на перевод, поэтому
 * выпадашка тянет его лениво — при открытии.
 */
export function DownloadButton({
  translationId,
  kind,
  estimatedHeight,
  disabled,
  disabledReason,
}: Props) {
  const { t } = useT()
  const queryClient = useQueryClient()
  const [menuOpened, setMenuOpened] = useState(false)

  const embed = useQuery({
    queryKey: ['embed', translationId],
    queryFn: () => titleApi.embed(translationId),
    enabled: menuOpened && kind === 'video',
    staleTime: 24 * 60 * 60 * 1000,
  })

  const enqueue = useMutation({
    mutationFn: (height?: number) => downloadsApi.enqueue(translationId, kind, height),
    onSuccess: (result) => {
      notifications.show({
        color: result.created ? 'teal' : 'gray',
        message: result.created ? t('episode.queued') : t('episode.alreadyQueued'),
      })
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: t('downloadButton.enqueueFailed'),
        message: error.message,
      }),
  })

  if (kind === 'subtitle') {
    return (
      <Tooltip label={disabled ? disabledReason : t('downloadButton.subtitles')}>
        <Button
          size="compact-xs"
          leftSection={<IconDownload size={12} />}
          onClick={() => enqueue.mutate(undefined)}
          loading={enqueue.isPending}
          disabled={disabled}
        >
          Скачать
        </Button>
      </Tooltip>
    )
  }

  const heights = embed.data?.heights ?? []

  return (
    <Group gap={0} wrap="nowrap">
      <Tooltip label={disabled ? disabledReason : `Скачать в ${DEFAULT_HEIGHT}p или ниже`}>
        <Button
          size="compact-xs"
          leftSection={<IconDownload size={12} />}
          onClick={() => enqueue.mutate(DEFAULT_HEIGHT)}
          loading={enqueue.isPending}
          disabled={disabled}
          style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
        >
          {DEFAULT_HEIGHT}p
        </Button>
      </Tooltip>

      <Menu opened={menuOpened} onChange={setMenuOpened} position="bottom-end">
        <Menu.Target>
          <Button
            size="compact-xs"
            px={4}
            disabled={disabled}
            style={{ borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
            aria-label={t('downloadButton.otherQuality')}
          >
            <IconChevronDown size={12} />
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>{t('downloadButton.quality')}</Menu.Label>
          {embed.isFetching && (
            <Menu.Item disabled leftSection={<Loader size={10} />}>
              узнаю доступные…
            </Menu.Item>
          )}
          {embed.isError && (
            <Menu.Item disabled c="red">
              не удалось узнать: {(embed.error as Error).message}
            </Menu.Item>
          )}
          {heights.map((height) => (
            <Menu.Item key={height} onClick={() => enqueue.mutate(height)}>
              {height}p
            </Menu.Item>
          ))}
          {!embed.isFetching && !embed.isError && heights.length === 0 && (
            <Menu.Item disabled>
              <Text size="xs">
                ступени неизвестны
                {estimatedHeight ? `, по оценке до ${Math.min(estimatedHeight, 1080)}p` : ''}
              </Text>
            </Menu.Item>
          )}
        </Menu.Dropdown>
      </Menu>
    </Group>
  )
}
