import { Badge, Group, Loader, Modal, Stack, Text, UnstyledButton } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { ErrorState } from './ErrorState'
import { useT } from '../i18n'
import { useTitleName } from '../i18n/titles'

interface OnlineResult {
  id: number
  title_ru: string | null
  title_romaji: string | null
  title_en: string | null
  title_ja: string | null
  type: string
  year: number | null
  poster_url_small: string | null
  /** Тайтла не было в локальном кэше до этого поиска. */
  was_new: boolean
}

interface Props {
  query: string
  opened: boolean
  onClose: () => void
}

/**
 * Поиск прямо на сайте — для того, чего ещё нет в локальном кэше.
 *
 * Кэш обновляется раз в час, поэтому совсем свежих тайтлов в нём может не быть.
 * Найденное сервер сразу кладёт в кэш, так что результат открывается обычной
 * страницей тайтла, а не отдельным «онлайн-режимом».
 */
export function OnlineSearchModal({ query, opened, onClose }: Props) {
  const navigate = useNavigate()
  const { t } = useT()
  const titleName = useTitleName()

  const search = useQuery({
    queryKey: ['search-online', query],
    queryFn: () => api.get<OnlineResult[]>(`/catalog/search-online?query=${encodeURIComponent(query)}`),
    // Запрос уходит на сайт, поэтому только по явному открытию окна.
    enabled: opened && query.length > 0,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const newCount = search.data?.filter((r) => r.was_new).length ?? 0

  return (
    <Modal opened={opened} onClose={onClose} title={t('onlineSearch.title', { query })} size="lg">
      <Stack gap="sm">
        {search.isFetching && (
          <Group gap="xs">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              {t('onlineSearch.asking')}
            </Text>
          </Group>
        )}

        {search.isError && (
          <ErrorState error={search.error} title={t('onlineSearch.failed')} onRetry={() => search.refetch()} />
        )}

        {search.isSuccess && search.data.length === 0 && (
          <Text size="sm" c="dimmed">
            {t('onlineSearch.nothing')}
          </Text>
        )}

        {newCount > 0 && (
          <Text size="xs" c="teal">
            {t('onlineSearch.newCount', { count: newCount })}
          </Text>
        )}

        <Stack gap={4} mah={420} style={{ overflowY: 'auto' }}>
          {search.data?.map((result) => (
            <UnstyledButton
              key={result.id}
              onClick={() => {
                onClose()
                navigate(`/series/${result.id}`)
              }}
              p={6}
              style={{ borderRadius: 6 }}
            >
              <Group gap="sm" wrap="nowrap">
                {result.poster_url_small && (
                  <img
                    src={result.poster_url_small}
                    alt=""
                    width={40}
                    height={60}
                    loading="lazy"
                    style={{ objectFit: 'cover', borderRadius: 4, flexShrink: 0 }}
                  />
                )}
                <Stack gap={0} style={{ minWidth: 0 }}>
                  <Group gap={6} wrap="nowrap">
                    <Text size="sm" fw={500} truncate>
                      {titleName.primary(result, `#${result.id}`)}
                    </Text>
                    {result.was_new && (
                      <Badge size="xs" color="teal" variant="light">
                        {t('onlineSearch.isNew')}
                      </Badge>
                    )}
                  </Group>
                  <Text size="xs" c="dimmed" truncate>
                    {[titleName.secondary(result), result.type, result.year]
                      .filter(Boolean)
                      .join(' · ')}
                  </Text>
                </Stack>
              </Group>
            </UnstyledButton>
          ))}
        </Stack>
      </Stack>
    </Modal>
  )
}
