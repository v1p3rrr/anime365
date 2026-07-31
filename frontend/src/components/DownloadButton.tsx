import { Button, Group, Loader, Menu, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconChevronDown, IconDownload } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import { downloadsApi } from '../api/downloads'
import { titleApi, type EmbedInfo } from '../api/title'
import { useT } from '../i18n'
import { useApiError } from '../i18n/apiError'
import { useFormat } from '../i18n/format'

/**
 * Насколько кнопка выделена.
 *
 * `dimmed` означает не «нельзя», а «это у вас уже есть»: скачать второй раз
 * можно, просто незачем. Блокировать такое нельзя — бывает, что файл битый и
 * его как раз хотят перекачать.
 */
export type Emphasis = 'primary' | 'secondary' | 'dimmed'

interface Props {
  translationId: number
  /** `both` ставит две задачи разом — видео и субтитры к нему. */
  kind: 'video' | 'subtitle' | 'both'
  /** Оценка максимума по разрешению исходника — до запроса точных ступеней. */
  estimatedHeight?: number | null
  label?: string
  emphasis?: Emphasis
  disabled?: boolean
  disabledReason?: string
  /**
   * Сообщает наверх всё, что удалось узнать о переводе из embed.
   *
   * Через это панель узнаёт про вшитые субтитры, не запрашивая embed отдельно:
   * данные всё равно приходят при открытии выпадашки качества.
   */
  onEmbed?: (info: EmbedInfo) => void
  /** Сервер отказал: субтитры вшиты в видео, отдельного файла нет. */
  onHardsub?: () => void
}

const DEFAULT_HEIGHT = 1080

const STYLE: Record<Emphasis, { variant: string; color?: string }> = {
  primary: { variant: 'filled' },
  secondary: { variant: 'light' },
  dimmed: { variant: 'subtle', color: 'gray' },
}

/**
 * Кнопка загрузки со сплитом выбора качества.
 *
 * Основное действие — 1080 (фактически максимум: 4K скачать нельзя). Точный
 * список ступеней и вес файла известны только из embed, а это запрос на перевод,
 * поэтому выпадашка тянет их лениво — при открытии.
 */
export function DownloadButton({
  translationId,
  kind,
  estimatedHeight,
  label,
  emphasis = 'primary',
  disabled,
  disabledReason,
  onEmbed,
  onHardsub,
}: Props) {
  const { t } = useT()
  const apiError = useApiError()
  const format = useFormat()
  const queryClient = useQueryClient()
  const [menuOpened, setMenuOpened] = useState(false)

  const withQuality = kind !== 'subtitle'

  const embed = useQuery({
    // Ключ учитывает запрос размеров: ответ без них не должен выдаваться за ответ с ними.
    queryKey: ['embed', translationId, 'sizes'],
    queryFn: () => titleApi.embed(translationId, { withSizes: true }),
    enabled: menuOpened && withQuality,
    staleTime: 24 * 60 * 60 * 1000,
  })

  // Про вшитые субтитры панель узнаёт отсюда: отдельный запрос ради одного
  // признака был бы лишним, данные и так приходят с выпадашкой качества.
  useEffect(() => {
    if (embed.data) onEmbed?.(embed.data)
  }, [embed.data, onEmbed])

  const enqueue = useMutation({
    mutationFn: (height?: number) =>
      kind === 'both'
        ? // Две задачи на один перевод: одиночный эндпоинт принимает ровно один вид.
          downloadsApi
            .batch([translationId], 'both', height)
            .then((r) => ({ created: r.created > 0 }))
        : downloadsApi.enqueue(translationId, kind, height),
    onSuccess: (result) => {
      notifications.show({
        color: result.created ? 'teal' : 'gray',
        message: result.created ? t('episode.queued') : t('episode.alreadyQueued'),
      })
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
    onError: (error: Error) => {
      // Отказ «субтитры вшиты» окончателен: отдельного файла не существует,
      // и предлагать повторить попытку бессмысленно.
      if (error instanceof ApiError && error.code === 'hardsub') {
        onHardsub?.()
      }
      notifications.show({
        color: 'red',
        title: t('downloadButton.enqueueFailed'),
        message: apiError(error),
      })
    },
  })

  const style = STYLE[emphasis]
  const text = label ?? t('downloadButton.download')

  if (!withQuality) {
    return (
      <Tooltip label={disabled ? disabledReason : t('downloadButton.subtitles')}>
        {/* Обёртка нужна, чтобы подсказка работала и на выключенной кнопке:
            отключённый элемент событий мыши не получает. */}
        <span>
          <Button
            size="compact-xs"
            variant={style.variant}
            color={style.color}
            leftSection={<IconDownload size={12} />}
            onClick={() => enqueue.mutate(undefined)}
            loading={enqueue.isPending}
            disabled={disabled}
          >
            {text}
          </Button>
        </span>
      </Tooltip>
    )
  }

  const heights = embed.data?.heights ?? []
  const sizes = embed.data?.sizes ?? {}

  return (
    <Group gap={0} wrap="nowrap">
      <Tooltip
        label={
          disabled ? disabledReason : t('downloadButton.defaultHint', { height: DEFAULT_HEIGHT })
        }
      >
        <Button
          size="compact-xs"
          variant={style.variant}
          color={style.color}
          leftSection={<IconDownload size={12} />}
          onClick={() => enqueue.mutate(DEFAULT_HEIGHT)}
          loading={enqueue.isPending}
          disabled={disabled}
          style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
        >
          {text}
        </Button>
      </Tooltip>

      <Menu opened={menuOpened} onChange={setMenuOpened} position="bottom-end">
        <Menu.Target>
          <Button
            size="compact-xs"
            px={4}
            variant={style.variant}
            color={style.color}
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
              {t('downloadButton.loadingHeights')}
            </Menu.Item>
          )}
          {embed.isError && (
            <Menu.Item disabled c="red">
              {t('downloadButton.heightsFailed', { reason: apiError(embed.error) })}
            </Menu.Item>
          )}
          {heights.map((height) => {
            const size = sizes[String(height)]
            return (
              <Menu.Item key={height} onClick={() => enqueue.mutate(height)}>
                <Group gap="xs" justify="space-between" wrap="nowrap">
                  <Text size="sm">{height}p</Text>
                  {/* Вес узнаётся отдельным запросом к CDN и приходит не всегда —
                      его отсутствие не повод прятать саму ступень. */}
                  {size ? (
                    <Text size="xs" c="dimmed">
                      {format.size(size)}
                    </Text>
                  ) : null}
                </Group>
              </Menu.Item>
            )
          })}
          {!embed.isFetching && !embed.isError && heights.length === 0 && (
            <Menu.Item disabled>
              <Text size="xs">
                {estimatedHeight
                  ? t('downloadButton.heightsEstimate', {
                      height: Math.min(estimatedHeight, DEFAULT_HEIGHT),
                    })
                  : t('downloadButton.heightsUnknown')}
              </Text>
            </Menu.Item>
          )}
        </Menu.Dropdown>
      </Menu>
    </Group>
  )
}
