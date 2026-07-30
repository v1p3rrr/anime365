import { Badge, Group, Loader, Modal, Stack, Text, UnstyledButton } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { ErrorState } from './ErrorState'

interface OnlineResult {
  id: number
  title_ru: string | null
  title_romaji: string | null
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
    <Modal opened={opened} onClose={onClose} title={`Поиск на сайте: «${query}»`} size="lg">
      <Stack gap="sm">
        {search.isFetching && (
          <Group gap="xs">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              Запрашиваем Anime365…
            </Text>
          </Group>
        )}

        {search.isError && (
          <ErrorState error={search.error} title="Поиск не удался" onRetry={() => search.refetch()} />
        )}

        {search.isSuccess && search.data.length === 0 && (
          <Text size="sm" c="dimmed">
            На сайте тоже ничего не нашлось. Попробуйте romaji или японское написание.
          </Text>
        )}

        {newCount > 0 && (
          <Text size="xs" c="teal">
            Новых для локального кэша: {newCount}. Они уже сохранены и доступны в каталоге.
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
                      {result.title_ru || result.title_romaji || `#${result.id}`}
                    </Text>
                    {result.was_new && (
                      <Badge size="xs" color="teal" variant="light">
                        новое
                      </Badge>
                    )}
                  </Group>
                  <Text size="xs" c="dimmed" truncate>
                    {[result.title_romaji, result.type, result.year].filter(Boolean).join(' · ')}
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
