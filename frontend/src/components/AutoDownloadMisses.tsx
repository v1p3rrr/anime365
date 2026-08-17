import { ActionIcon, Alert, Badge, Button, Card, Group, Stack, Text, Title, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconInfoCircle, IconRefresh, IconTrash } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { autodownloadApi } from '../api/autodownload'
import { useT } from '../i18n'
import { Flag } from './Flag'
import classes from './StripedRows.module.css'

/**
 * Серии, к которым субтитров не нашлось.
 *
 * Список нужен человеку, а не программе. Автоматика и без него вернётся к этим
 * сериям по расписанию; здесь важно другое — видеть, чего мы ждём, и уметь
 * выбросить то, чего заведомо не будет. Иначе сервис годами спрашивает сайт о
 * переводе, которого никто не делает.
 *
 * По числу попыток и дате первого промаха это решение и принимается: две
 * попытки за вчерашний день — нормально, сорок за два месяца — повод убрать.
 */
export function AutoDownloadMisses() {
  const { t } = useT()
  const queryClient = useQueryClient()

  const misses = useQuery({
    queryKey: ['autodownload-misses'],
    queryFn: autodownloadApi.misses,
  })

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['autodownload-misses'] })

  const dismiss = useMutation({
    mutationFn: (id: number) => autodownloadApi.dismissMiss(id),
    onSuccess: invalidate,
  })

  const retry = useMutation({
    mutationFn: autodownloadApi.retryMisses,
    onSuccess: (result) => {
      notifications.show({
        color: 'teal',
        message: t('autodownload.runDone', {
          planned: result.planned,
          queued: result.queued,
        }),
      })
      invalidate()
    },
  })

  const rows = misses.data?.misses ?? []

  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" mb="xs" wrap="wrap" gap="xs">
        <Title order={5}>{t('autodownload.missesTitle')}</Title>
        {rows.length > 0 && (
          <Button
            size="compact-sm"
            variant="light"
            leftSection={<IconRefresh size={14} />}
            loading={retry.isPending}
            onClick={() => retry.mutate()}
          >
            {t('autodownload.missesRetry')}
          </Button>
        )}
      </Group>

      {rows.length === 0 ? (
        <Alert color="gray" icon={<IconInfoCircle size={16} />} p="xs">
          <Text size="xs">{t('autodownload.missesEmpty')}</Text>
        </Alert>
      ) : (
        <Stack gap={2}>
          {rows.map((row) => (
            <Group key={row.id} className={classes.row} gap="xs" wrap="nowrap">
              <Flag lang={row.lang} />
              <Text
                size="sm"
                component={Link}
                to={`/series/${row.series_id}`}
                style={{ flex: 1, wordBreak: 'break-word' }}
              >
                {row.title_ru || row.title_romaji || `#${row.series_id}`}
              </Text>
              <Badge size="xs" variant="light">
                {t('autodownload.missEpisode', { episode: row.episode })}
              </Badge>
              {/* Число попыток — главный признак безнадёжности. */}
              <Tooltip label={t('autodownload.missChecksHint')}>
                <Badge size="xs" variant="light" color={row.checks > 20 ? 'orange' : 'gray'}>
                  {row.checks}
                </Badge>
              </Tooltip>
              <Tooltip label={t('autodownload.missDismiss')}>
                <ActionIcon
                  variant="subtle"
                  color="red"
                  size="sm"
                  loading={dismiss.isPending && dismiss.variables === row.id}
                  onClick={() => dismiss.mutate(row.id)}
                  aria-label={t('autodownload.missDismiss')}
                >
                  <IconTrash size={15} />
                </ActionIcon>
              </Tooltip>
            </Group>
          ))}
        </Stack>
      )}
    </Card>
  )
}
