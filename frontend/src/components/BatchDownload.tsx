import { Button, Group, Menu, Select, Stack, Switch, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconChevronDown, IconDownload } from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { downloadsApi } from '../api/downloads'
import { usePermissions } from '../hooks/usePermissions'
import { useT, type TranslationKey } from '../i18n'
import { useApiError } from '../i18n/apiError'
import type { Emphasis } from './DownloadButton'
import type { AuthorGroup } from '../api/title'

interface Props {
  group: AuthorGroup
  kind: 'voice' | 'sub' | 'raw'
  /** Номера серий, которые считаются контентом (без трейлеров и рекламы). */
  contentEpisodes: Set<number>
  disabled?: boolean
  disabledReason?: string
  /** Видео по тайтлу уже собрано целиком — вариант с видео можно приглушить. */
  hasVideo?: boolean
  /** Субтитры этого языка по тайтлу уже собраны целиком. */
  hasSubs?: boolean
}

type BatchWhat = 'subtitle' | 'video' | 'both'

const BATCH_LABEL: Record<BatchWhat, TranslationKey> = {
  subtitle: 'batch.subsOnly',
  video: 'batch.videoOnly',
  both: 'batch.videoAndSubs',
}

const BATCH_STYLE: Record<Emphasis, { variant: string; color?: string }> = {
  primary: { variant: 'filled' },
  secondary: { variant: 'light' },
  dimmed: { variant: 'subtle', color: 'gray' },
}

/**
 * Порядок и яркость вариантов батча — по тем же правилам, что и у отдельной
 * серии. Держать их разными нельзя: это один и тот же выбор, сделанный сразу
 * на весь тайтл вместо одной серии.
 */
function batchActions(
  hasVideo: boolean,
  hasSubs: boolean,
): Array<{ what: BatchWhat; emphasis: Emphasis }> {
  if (!hasVideo && !hasSubs) {
    return [
      { what: 'both', emphasis: 'primary' },
      { what: 'subtitle', emphasis: 'secondary' },
      { what: 'video', emphasis: 'secondary' },
    ]
  }
  if (!hasVideo) {
    return [
      { what: 'video', emphasis: 'primary' },
      { what: 'subtitle', emphasis: 'dimmed' },
      { what: 'both', emphasis: 'dimmed' },
    ]
  }
  if (!hasSubs) {
    return [
      { what: 'subtitle', emphasis: 'primary' },
      { what: 'both', emphasis: 'dimmed' },
      { what: 'video', emphasis: 'dimmed' },
    ]
  }
  return [
    { what: 'subtitle', emphasis: 'dimmed' },
    { what: 'both', emphasis: 'dimmed' },
    { what: 'video', emphasis: 'dimmed' },
  ]
}

/** Ступени качества. Подписи собираются из словаря: «до 720p» переводится. */
const HEIGHTS = ['1080', '720', '480', '360'] as const

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
  hasVideo = false,
  hasSubs = false,
}: Props) {
  const queryClient = useQueryClient()
  const { t } = useT()
  const apiError = useApiError()
  const canDownload = usePermissions().can('downloads')
  const [height, setHeight] = useState('1080')
  const [includeExtras, setIncludeExtras] = useState(false)

  // Тумблер обязан менять состав задач, а не только своё положение: раньше
  // includeExtras выставлялся, но в батч всё равно уходил полный список.
  const extras = group.extra_translation_ids ?? []
  const ids = includeExtras ? [...group.translation_ids, ...extras] : group.translation_ids

  const run = useMutation({
    mutationFn: ({ what, onlyMissing }: { what: BatchWhat; onlyMissing: boolean }) =>
      downloadsApi.batch(
        ids,
        what,
        what === 'subtitle' ? undefined : Number(height),
        onlyMissing,
      ),
    onSuccess: (result) => {
      const parts = [t('batch.added', { count: result.created })]
      if (result.already_local) {
        parts.push(t('batch.alreadyLocal', { count: result.already_local }))
      }
      if (result.skipped) parts.push(t('batch.skipped', { count: result.skipped }))
      if (result.failed) parts.push(t('batch.failed', { count: result.failed }))
      notifications.show({
        color: result.failed ? 'yellow' : 'teal',
        title: t('batch.queued'),
        message: parts.join(' · '),
      })
      // Ошибки отдельных задач видны в очереди — туда и отправляем.
      if (result.errors.length > 0) {
        notifications.show({
          color: 'red',
          title: t('batch.partialFailed'),
          message: result.errors.slice(0, 3).join('; '),
        })
      }
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('batch.failedTitle'), message: apiError(error) }),
  })

  const label = String(ids.length)

  // Сводка переводов остаётся видимой без права на загрузки — по ней смотрят,
  // что вообще существует. Кнопки постановки в очередь при этом не рисуем.
  if (!canDownload) return null

  return (
    <Menu position="bottom-end" closeOnItemClick={false}>
      <Menu.Target>
        <Tooltip label={disabled ? disabledReason : t('batch.tooltip', { count: label })}>
          <Button
            size="compact-xs"
            variant="light"
            leftSection={<IconDownload size={12} />}
            rightSection={<IconChevronDown size={12} />}
            disabled={disabled}
            loading={run.isPending}
          >
            {t('batch.all')}
          </Button>
        </Tooltip>
      </Menu.Target>

      <Menu.Dropdown>
        <Stack gap="xs" p="xs" w={250}>
          <Text size="xs" c="dimmed">
            {t('batch.countLine', { count: ids.length })}
            {contentEpisodes.size > 0 && t('batch.episodesLine', { count: contentEpisodes.size })}
          </Text>

          {kind !== 'sub' && (
            <Select
              size="xs"
              label={t('batch.quality')}
              description={t('batch.qualityHint')}
              data={HEIGHTS.map((h) => ({
                value: h,
                label: h === '1080' ? t('batch.upToMax') : t('batch.upTo', { height: h }),
              }))}
              value={height}
              onChange={(value) => setHeight(value ?? '1080')}
              allowDeselect={false}
            />
          )}

          {/* Показываем, только если спешлы есть У ЭТОГО автора: у тайтла они
              могут быть, а у конкретной команды — нет, и тогда тумблер
              предлагал бы выбор, ничего не меняющий. */}
          {extras.length > 0 && (
            <Switch
              size="xs"
              label={t('batch.includeExtras', { count: extras.length })}
              description={t('batch.includeExtrasHint')}
              checked={includeExtras}
              onChange={(event) => setIncludeExtras(event.currentTarget.checked)}
            />
          )}

          <Stack gap={4}>
            {kind === 'sub' ? (
              // У субтитров есть и своё видео, поэтому предлагаем оба варианта:
              // только файлы субтитров либо видео вместе с ними. Порядок и
              // яркость зависят от того, что уже закрыто по тайтлу целиком:
              // приглушённое — это «можно, но у вас и так есть».
              <>
                {batchActions(hasVideo, hasSubs).map((action) => (
                  // Две кнопки на каждый вариант: «все» и «только те, чего нет
                  // локально». Второе — обычный сценарий дозагрузки, и без него
                  // приходилось ставить весь тайтл и ждать, пока очередь сама
                  // отсеет уже скачанное.
                  <Group key={action.what} gap={4} wrap="nowrap">
                    <Button
                      size="compact-xs"
                      variant={BATCH_STYLE[action.emphasis].variant}
                      color={BATCH_STYLE[action.emphasis].color}
                      style={{ flex: 1 }}
                      onClick={() => run.mutate({ what: action.what, onlyMissing: false })}
                    >
                      {t(BATCH_LABEL[action.what])}
                    </Button>
                    <Tooltip label={t('batch.missingHint')}>
                      <Button
                        size="compact-xs"
                        variant="subtle"
                        color={BATCH_STYLE[action.emphasis].color}
                        onClick={() => run.mutate({ what: action.what, onlyMissing: true })}
                      >
                        {t('batch.missingShort')}
                      </Button>
                    </Tooltip>
                  </Group>
                ))}
              </>
            ) : (
              // Озвучка и RAW — это видеодорожка, отдельного файла субтитров
              // у такого перевода не бывает, поэтому вариант один.
              <Group gap={4} wrap="nowrap">
                <Button
                  size="compact-xs"
                  style={{ flex: 1 }}
                  onClick={() => run.mutate({ what: 'video', onlyMissing: false })}
                >
                  {t('batch.allVideo')}
                </Button>
                <Tooltip label={t('batch.missingHint')}>
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    onClick={() => run.mutate({ what: 'video', onlyMissing: true })}
                  >
                    {t('batch.missingShort')}
                  </Button>
                </Tooltip>
              </Group>
            )}
          </Stack>
        </Stack>
      </Menu.Dropdown>
    </Menu>
  )
}
