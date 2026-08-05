import { Badge, Stack, Table, Text, Tooltip } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import { useT } from '../i18n'

interface ProviderRow {
  provider: string
  /** Что стоит у Jellyfin. */
  theirs: string
  /** Что стоит у нас. null — такого поля в каталоге Anime365 нет. */
  ours: number | null
  differs: boolean
}

interface Verdict {
  available: boolean
  reason?: string
  item_id?: string
  path?: string
  ids?: ProviderRow[]
  disagrees?: boolean
}

interface Props {
  seriesId: number
}

/**
 * Значок «Jellyfin считает иначе» с разбором при наведении.
 *
 * ⚠️ Это **подсказка, а не голос**. Решение пользователя: разметку не
 * переголосовывать. Исправлять чужую ошибку идентификации не наша задача —
 * поправить её нужно в Jellyfin, там источник, а у нас лишь отражение. Но и
 * молчать нельзя: пока файлы не уехали не туда, расхождение не видно.
 *
 * Запрос отдельный и ленивый: Jellyfin бывает выключен, недоступен или
 * медленен, и страница тайтла не должна его ждать. Молчит во всех случаях,
 * кроме настоящего расхождения, — значок, который горит всегда, не значит
 * ничего.
 */
export function JellyfinVerdictBadge({ seriesId }: Props) {
  const { t } = useT()

  const verdict = useQuery({
    queryKey: ['jellyfin-verdict', seriesId],
    queryFn: () => api.get<Verdict>(`/library/jellyfin/verdict/${seriesId}`),
    // Индекс Jellyfin на нашей стороне и так кэшируется; здесь достаточно не
    // ходить повторно при каждом возврате на страницу.
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const data = verdict.data
  if (!data?.available || !data.disagrees || !data.ids?.length) return null

  return (
    <Tooltip
      multiline
      w={340}
      label={
        <Stack gap={4}>
          <Text size="xs">{t('jellyfinVerdict.hint')}</Text>
          <Table withRowBorders={false} verticalSpacing={1} horizontalSpacing={6}>
            <Table.Tbody>
              {data.ids.map((row) => (
                <Table.Tr key={row.provider}>
                  <Table.Td>
                    <Text size="xs" fw={row.differs ? 600 : 400}>
                      {row.provider}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs">{row.theirs}</Text>
                  </Table.Td>
                  <Table.Td>
                    {/* Прочерк, а не пусто: «у нас такого поля нет» и «у нас
                        пусто» человек должен различать. */}
                    <Text size="xs" c="dimmed">
                      {row.ours ?? '—'}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text size="xs" c="dimmed">
            {t('jellyfinVerdict.whereToFix')}
          </Text>
        </Stack>
      }
    >
      <Badge size="sm" color="orange" leftSection={<IconAlertTriangle size={11} />}>
        {t('jellyfinVerdict.badge')}
      </Badge>
    </Tooltip>
  )
}
