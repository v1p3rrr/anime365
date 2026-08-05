import {
  Alert,
  Badge,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  TextInput,
  UnstyledButton,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconSearch } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { libraryApi } from '../api/library'
import { ErrorState } from './ErrorState'
import pathClasses from './PathLine.module.css'
import { useT } from '../i18n'
import { useFormat } from '../i18n/format'
import { useApiError } from '../i18n/apiError'

interface Props {
  opened: boolean
  seriesId: number
  seriesTitle: string
  onClose: () => void
}

/**
 * Выбор локальной папки для тайтла — обратная операция к привязке из библиотеки.
 *
 * Показываем только папки без привязки: привязать уже занятую можно, но это
 * почти всегда ошибка, а для переноса есть кнопка «Отвязать».
 */
export function BindFolderModal({ opened, seriesId, seriesTitle, onClose }: Props) {
  const { t } = useT()
  const format = useFormat()
  const apiError = useApiError()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('')

  // ⚠️ Ищем по всем папкам библиотеки, а не по списку нераспознанных.
  //
  // Раньше окно спрашивало `/library/unmapped`, и получалось бессмысленно:
  // папка, к которой человек хочет привязать тайтл, обычно уже к чему-то
  // привязана — то есть в списке её нет вовсе. А фильтр работал на клиенте по
  // загруженному куску, поэтому поиск ничего не находил и выглядел сломанным.
  const [debounced, setDebounced] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(filter.trim()), 250)
    return () => clearTimeout(timer)
  }, [filter])

  const folders = useQuery({
    queryKey: ['library-folder-search', debounced],
    queryFn: () => libraryApi.searchFolders(debounced),
    enabled: opened,
    placeholderData: (previous) => previous,
  })

  const visible = folders.data ?? []

  const bind = useMutation({
    mutationFn: (folderId: number) => libraryApi.createMapping(folderId, seriesId),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('bindFolder.bound') })
      for (const key of ['title', 'library-status', 'library-unmapped', 'library-mappings']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
      onClose()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('bindFolder.failed'), message: apiError(error) }),
  })

  return (
    <Modal opened={opened} onClose={onClose} title={t('bindFolder.pickTitle')} size="lg">
      <Stack gap="sm">
        <Alert color="gray" p="xs">
          <Text size="sm" style={{ wordBreak: 'break-word' }}>
            {seriesTitle}
          </Text>
        </Alert>

        <TextInput
          data-autofocus
          placeholder={t('bindFolder.filter')}
          leftSection={<IconSearch size={16} />}
          value={filter}
          onChange={(e) => setFilter(e.currentTarget.value)}
        />

        {folders.isError && (
          <ErrorState compact error={folders.error} onRetry={() => folders.refetch()} />
        )}
        {folders.isFetching && <Loader size="sm" />}

        {folders.data?.length === 0 && (
          <Text size="sm" c="dimmed">
{t('bindFolder.allTaken')}
          </Text>
        )}

        <Stack gap={4} mah={380} style={{ overflowY: 'auto' }}>
          {visible.map((folder) => (
            <UnstyledButton
              key={folder.folder_id}
              onClick={() => bind.mutate(folder.folder_id)}
              disabled={bind.isPending}
              p="xs"
              style={{ borderRadius: 6 }}
            >
              <Text size="sm" className={pathClasses.pathLine} title={folder.rel_path}>
                {folder.rel_path}
              </Text>
              <Group gap={6} mt={2}>
                <Badge size="xs" variant="light">
                  {folder.root_kind === 'movie' ? t('bindFolder.kindMovies') : t('bindFolder.kindSeries')}
                </Badge>
                {folder.season_number != null && (
                  <Badge size="xs" variant="light">
                    {t('bindFolder.season', { n: folder.season_number })}
                  </Badge>
                )}
                <Text size="xs" c="dimmed">
                  {t(folder.size_bytes ? 'bindFolder.videosAndSize' : 'bindFolder.videosOnly', { count: folder.video_count, size: format.size(folder.size_bytes) })}
                  {folder.nfo_title ? ` · ${folder.nfo_title}` : ''}
                </Text>
              </Group>
            </UnstyledButton>
          ))}
        </Stack>
      </Stack>
    </Modal>
  )
}
