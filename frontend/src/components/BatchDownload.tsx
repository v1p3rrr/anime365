import { Button, Menu, Select, Stack, Switch, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconChevronDown, IconDownload } from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { downloadsApi } from '../api/downloads'
import { useT } from '../i18n'
import type { AuthorGroup } from '../api/title'

interface Props {
  group: AuthorGroup
  kind: 'voice' | 'sub' | 'raw'
  /** Номера серий, которые считаются контентом (без трейлеров и рекламы). */
  contentEpisodes: Set<number>
  disabled?: boolean
  disabledReason?: string
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
}: Props) {
  const queryClient = useQueryClient()
  const { t } = useT()
  const [height, setHeight] = useState('1080')
  const [includeExtras, setIncludeExtras] = useState(false)

  // Тумблер обязан менять состав задач, а не только своё положение: раньше
  // includeExtras выставлялся, но в батч всё равно уходил полный список.
  const extras = group.extra_translation_ids ?? []
  const ids = includeExtras ? [...group.translation_ids, ...extras] : group.translation_ids

  const run = useMutation({
    mutationFn: (what: 'video' | 'subtitle' | 'both') =>
      downloadsApi.batch(ids, what, what === 'subtitle' ? undefined : Number(height)),
    onSuccess: (result) => {
      const parts = [t('batch.added', { count: result.created })]
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
      notifications.show({ color: 'red', title: t('batch.failedTitle'), message: error.message }),
  })

  const label = String(ids.length)

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
              // только файлы субтитров либо видео вместе с ними.
              <>
                <Button size="compact-xs" onClick={() => run.mutate('subtitle')}>
                  {t('batch.subsOnly')}
                </Button>
                <Button size="compact-xs" variant="light" onClick={() => run.mutate('video')}>
                  {t('batch.videoOnly')}
                </Button>
                <Button size="compact-xs" variant="light" onClick={() => run.mutate('both')}>
                  {t('batch.videoAndSubs')}
                </Button>
              </>
            ) : (
              // Озвучка и RAW — это видеодорожка, отдельного файла субтитров
              // у такого перевода не бывает, поэтому вариант один.
              <Button size="compact-xs" onClick={() => run.mutate('video')}>
                {t('batch.allVideo')}
              </Button>
            )}
          </Stack>
        </Stack>
      </Menu.Dropdown>
    </Menu>
  )
}
