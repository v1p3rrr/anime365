import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  TextInput,
  UnstyledButton,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { notifications } from '@mantine/notifications'
import { IconSearch } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { catalogApi } from '../api/client'
import { libraryApi, type UnmappedFolder } from '../api/library'
import { ErrorState } from './ErrorState'
import pathClasses from './PathLine.module.css'
import { useT } from '../i18n'
import { useApiError } from '../i18n/apiError'
import { useTitleName } from '../i18n/titles'

interface Props {
  folder: UnmappedFolder | null
  onClose: () => void
}

/**
 * Ручная привязка папки к тайтлу.
 *
 * Поиск сразу заполняется названием из .nfo, если оно есть: обычно алгоритм не
 * справился не потому, что название бессмысленное, а потому что оно отличается
 * от каталожного написанием.
 */
export function MapFolderModal({ folder, onClose }: Props) {
  const { t } = useT()
  const apiError = useApiError()
  const titleName = useTitleName()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [debounced] = useDebouncedValue(query, 300)

  useEffect(() => {
    // Для сезонной подпапки собственное имя — это «Season 2», искать по нему
    // бессмысленно. Сервер отдаёт подсказку с названием родительского тайтла.
    if (folder) setQuery(folder.search_hint ?? folder.nfo_title ?? folder.name)
  }, [folder])

  const ids = useQuery({
    queryKey: ['map-search', debounced],
    queryFn: () => catalogApi.ids({ query: debounced, sort: 'score', sortDir: 'desc' }),
    enabled: Boolean(folder) && debounced.trim().length > 1,
  })

  const cards = useQuery({
    queryKey: ['map-search-cards', ids.data?.ids?.slice(0, 25)],
    queryFn: () => catalogApi.cards(ids.data!.ids.slice(0, 25)),
    enabled: Boolean(ids.data?.ids?.length),
  })

  const create = useMutation({
    mutationFn: (seriesId: number) => libraryApi.createMapping(folder!.folder_id, seriesId),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('mapFolder.bound') })
      for (const key of ['library-unmapped', 'library-mappings', 'library-status']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
      onClose()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('mapFolder.failed'), message: apiError(error) }),
  })

  return (
    <Modal opened={Boolean(folder)} onClose={onClose} title={t('mapFolder.bindToTitle')} size="lg">
      {folder && (
        <Stack gap="sm">
          <Alert color="gray" p="xs">
            <Text size="sm" className={pathClasses.pathLine} title={folder.rel_path}>
              {folder.rel_path}
            </Text>
            <Text size="xs" c="dimmed">
              {t('mapFolder.videos', { count: folder.video_count })}
              {folder.season_number != null ? t('mapFolder.seasonSuffix', { n: folder.season_number }) : ''}
              {folder.nfo_title
                ? t('mapFolder.withNfo', { title: folder.nfo_title })
                : t('mapFolder.noNfo')}
              {folder.nfo_year ? ` (${folder.nfo_year})` : ''}
            </Text>
            {folder.season_number != null && folder.season_number > 0 && (
              <Text size="xs" c="dimmed" mt={2}>
                {t('mapFolder.seasonHint')}
              </Text>
            )}
          </Alert>

          <TextInput
            data-autofocus
            placeholder={t('mapFolder.searchCatalog')}
            leftSection={<IconSearch size={16} />}
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
          />

          {ids.isError && <ErrorState compact error={ids.error} onRetry={() => ids.refetch()} />}

          {(ids.isFetching || cards.isFetching) && <Loader size="sm" />}

          {ids.data?.total === 0 && (
            <Text size="sm" c="dimmed">
              {t('mapFolder.nothing')}
            </Text>
          )}

          <Stack gap={4} mah={360} style={{ overflowY: 'auto' }}>
            {cards.data?.map((card) => (
              <UnstyledButton
                key={card.id}
                onClick={() => create.mutate(card.id)}
                disabled={create.isPending}
                p="xs"
                style={{ borderRadius: 6 }}
              >
                <Group gap="sm" wrap="nowrap" align="flex-start">
                  {card.poster_url_small && (
                    <img
                      src={card.poster_url_small}
                      alt=""
                      width={40}
                      height={60}
                      style={{ objectFit: 'cover', borderRadius: 4, flexShrink: 0 }}
                      loading="lazy"
                    />
                  )}
                  <div style={{ minWidth: 0 }}>
                    <Text size="sm" fw={500} style={{ wordBreak: 'break-word' }}>
                      {titleName.primary(card, `#${card.id}`)}
                    </Text>
                    {titleName.secondary(card) && (
                      <Text size="xs" c="dimmed" style={{ wordBreak: 'break-word' }}>
                        {titleName.secondary(card)}
                      </Text>
                    )}
                    <Group gap={4} mt={2}>
                      <Badge size="xs" variant="light">
                        {card.type_title}
                      </Badge>
                      {card.season && (
                        <Badge size="xs" variant="light">
                          {card.season}
                        </Badge>
                      )}
                      {card.number_of_episodes > 0 && (
                        <Badge size="xs" variant="light" color="gray">
                          {card.number_of_episodes} {t('common.episodes')}
                        </Badge>
                      )}
                    </Group>
                  </div>
                </Group>
              </UnstyledButton>
            ))}
          </Stack>

          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              {t('common.cancel')}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  )
}
