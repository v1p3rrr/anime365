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
import { useMemo, useState } from 'react'

import { formatBytes, libraryApi } from '../api/library'
import { ErrorState } from './ErrorState'
import pathClasses from './PathLine.module.css'

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
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('')

  const folders = useQuery({
    queryKey: ['library-unmapped'],
    queryFn: libraryApi.unmapped,
    enabled: opened,
  })

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    const list = folders.data ?? []
    if (!needle) return list.slice(0, 60)
    return list.filter((f) => f.rel_path.toLowerCase().includes(needle)).slice(0, 60)
  }, [folders.data, filter])

  const bind = useMutation({
    mutationFn: (folderId: number) => libraryApi.createMapping(folderId, seriesId),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: 'Папка привязана' })
      for (const key of ['title', 'library-status', 'library-unmapped', 'library-mappings']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
      onClose()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось привязать', message: error.message }),
  })

  return (
    <Modal opened={opened} onClose={onClose} title="Выбрать папку для тайтла" size="lg">
      <Stack gap="sm">
        <Alert color="gray" p="xs">
          <Text size="sm" style={{ wordBreak: 'break-word' }}>
            {seriesTitle}
          </Text>
        </Alert>

        <TextInput
          data-autofocus
          placeholder="Фильтр по пути папки"
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
            Свободных папок нет — все уже привязаны. Чтобы перенести привязку, сначала отвяжите
            папку от прежнего тайтла.
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
                  {folder.root_kind === 'movie' ? 'фильмы' : 'сериалы'}
                </Badge>
                {folder.season_number != null && (
                  <Badge size="xs" variant="light">
                    сезон {folder.season_number}
                  </Badge>
                )}
                <Text size="xs" c="dimmed">
                  {folder.video_count} видео · {formatBytes(folder.size_bytes)}
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
