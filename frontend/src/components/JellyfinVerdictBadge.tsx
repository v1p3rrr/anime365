import { Badge, Stack, Table, Text, Tooltip } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import { useT } from '../i18n'

interface ProviderRow {
  provider: string
  /** Что стоит у Jellyfin. */
  theirs: string
  /** Что стоит у нас. Строки, где сравнивать нечем, сервер не присылает вовсе. */
  ours: number | null
  /** Считается ли это спором. Только AniList — см. D-98. */
  differs: boolean
  /** Значения просто не совпали. У Shikimori это не спор, но подписать полезно. */
  values_differ: boolean
  /** Названия тайтлов, если идентификатор нашёлся в нашем каталоге. */
  theirs_title?: string
  ours_title?: string
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
      // Шире прежнего: под числами теперь стоят названия, и romaji вроде
      // «Youjo Senki II» на узкой подсказке ломалось бы переносом.
      w={400}
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
                    {/* Название под числом — ради этого подсказка и нужна:
                        «21613 против 135865» не читается, а «Youjo Senki
                        против Youjo Senki II» видно сразу. */}
                    {row.theirs_title && (
                      <Text size="xs" c="dimmed" style={{ lineHeight: 1.2 }}>
                        {row.theirs_title}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="dimmed">
                      {row.ours}
                    </Text>
                    {row.ours_title && (
                      <Text size="xs" c="dimmed" style={{ lineHeight: 1.2 }}>
                        {row.ours_title}
                      </Text>
                    )}
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
