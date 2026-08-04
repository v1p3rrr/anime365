import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconFolderOff, IconFolderSymlink } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { episodeTargetApi } from '../api/library'
import type { EpisodeItem } from '../api/title'
import { useT } from '../i18n'
import pathClasses from './PathLine.module.css'

interface Props {
  opened: boolean
  seriesId: number
  /** Настоящие серии тайтла — из них и выбираем. */
  episodes: EpisodeItem[]
  /** Открыть сразу на этой серии: вызов из строки серии. */
  initialEpisode?: number | null
  onClose: () => void
}

/**
 * Назначение отдельным сериям своей папки.
 *
 * Не путать с привязкой серии к **файлу** (иконка со ссылкой в строке серии):
 * та связывает серию с тем, что уже лежит на диске. Здесь речь о том, куда
 * положить то, чего ещё нет.
 *
 * Экран нужен потому, что механизм работал только через API: предупреждение о
 * своих папках на странице тайтла было, а создать назначение из интерфейса было
 * нельзя — ссылка «Настроить привязки» вела в окно привязки папки тайтла, то
 * есть обещала больше, чем делала.
 *
 * Сброс раздельный: построчный и «снять все». У длинного сериала назначений
 * бывает два десятка, и убирать их по одному — это два десятка подтверждений.
 */
export function EpisodeTargetsModal({
  opened,
  seriesId,
  episodes,
  initialEpisode = null,
  onClose,
}: Props) {
  const { t } = useT()
  const queryClient = useQueryClient()

  const [episode, setEpisode] = useState<string | null>(
    initialEpisode != null ? String(initialEpisode) : null,
  )
  const [folderId, setFolderId] = useState<string | null>(null)

  // Открытие из строки серии должно подставлять именно её.
  useEffect(() => {
    if (opened && initialEpisode != null) setEpisode(String(initialEpisode))
  }, [opened, initialEpisode])

  const targets = useQuery({
    queryKey: ['episode-targets', seriesId],
    queryFn: () => episodeTargetApi.list(seriesId),
    enabled: opened,
  })

  const folders = useQuery({
    queryKey: ['episode-target-folders', seriesId],
    queryFn: () => episodeTargetApi.folders(seriesId),
    enabled: opened,
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['episode-targets', seriesId] })
    // Плашка на странице тайтла читает тот же список.
    queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
  }

  const assign = useMutation({
    mutationFn: () => episodeTargetApi.set(seriesId, Number(episode), Number(folderId)),
    onSuccess: () => {
      refresh()
      setEpisode(null)
      notifications.show({ message: t('episodeTarget.assigned'), color: 'teal' })
    },
  })

  const clearOne = useMutation({
    mutationFn: (targetId: number) => episodeTargetApi.clear(targetId),
    onSuccess: () => {
      refresh()
      notifications.show({ message: t('episodeTarget.cleared'), color: 'teal' })
    },
  })

  const clearAll = useMutation({
    mutationFn: () => episodeTargetApi.clearAll(seriesId),
    onSuccess: (result) => {
      refresh()
      notifications.show({
        message: t('episodeTarget.clearedAll', { count: result.deleted }),
        color: 'teal',
      })
    },
  })

  const rows = targets.data?.targets ?? []
  // Том только для чтения выбрать нельзя: назначение туда создастся, а вот
  // загрузка в него упадёт — то есть отказ придёт спустя время и не там, где
  // была допущена ошибка.
  const options = (folders.data ?? [])
    .filter((folder) => folder.writable)
    .map((folder) => ({
      value: String(folder.folder_id),
      label: folder.rel_path,
    }))

  // Серии без номера назначить папку нельзя: назначение хранится по номеру.
  const episodeOptions = episodes
    .filter((item) => item.is_content && item.episode_num != null)
    .map((item) => {
      const kind = item.episode_type && item.episode_type !== 'tv' ? item.episode_type : null
      return {
        value: String(item.episode_num),
        // Тип в подписи: спешл и OVA отличаются от обычной серии тем, куда их
        // обычно кладут, и именно ради них назначение чаще всего и делают.
        label: kind ? `${item.episode_full} · ${kind.toUpperCase()}` : item.episode_full,
      }
    })

  const canAssign = episode !== null && folderId !== null

  return (
    <Modal opened={opened} onClose={onClose} title={t('episodeTarget.manageTitle')} size="lg" centered>
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          {t('episodeTarget.explain')}
        </Text>

        <Group align="flex-end" gap="xs" wrap="wrap">
          {/* Выбор из настоящих серий, а не произвольное число: так не задать
              несуществующий номер, и сразу видно, что серия — спешл или OVA,
              а не обычная. Трейлеры и прочее не-содержимое отсеиваем: качать
              их некуда и незачем. */}
          <Select
            label={t('episodeTarget.episodeNumber')}
            placeholder={t('episodeTarget.episodePlaceholder')}
            data={episodeOptions}
            value={episode}
            onChange={setEpisode}
            searchable
            w={220}
          />
          <Select
            label={t('episodeTarget.folder')}
            placeholder={t('episodeTarget.folderPlaceholder')}
            data={options}
            value={folderId}
            onChange={setFolderId}
            searchable
            nothingFoundMessage={t('episodeTarget.noFolders')}
            style={{ flex: 1, minWidth: 220 }}
          />
          <Button
            leftSection={<IconFolderSymlink size={15} />}
            disabled={!canAssign}
            loading={assign.isPending}
            onClick={() => assign.mutate()}
          >
            {t('episodeTarget.assign')}
          </Button>
        </Group>

        {/* ⚠️ Пустой список и «все тома только для чтения» — разные беды, и
            путать их нельзя. Раньше показывалось второе на любой пустой
            выдаче, а чаще всего причина была первая: у тайтла просто нет
            привязанных папок, и окрестность собирать не из чего. Человек шёл
            проверять монтирования вместо того, чтобы привязать папку. */}
        {!folders.isLoading && (folders.data ?? []).length === 0 && (
          <Alert color="gray">{t('episodeTarget.noFoldersAtAll')}</Alert>
        )}
        {!folders.isLoading && (folders.data ?? []).length > 0 && options.length === 0 && (
          <Alert color="yellow">{t('episodeTarget.noWritableFolders')}</Alert>
        )}

        {assign.isError && <Alert color="red">{t('episodeTarget.assignFailed')}</Alert>}

        {rows.length === 0 ? (
          <Alert color="gray">{t('episodeTarget.none')}</Alert>
        ) : (
          <>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={90}>{t('episodeTarget.episodeNumber')}</Table.Th>
                  <Table.Th>{t('episodeTarget.folder')}</Table.Th>
                  <Table.Th w={50} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((target) => (
                  <Table.Tr key={target.id}>
                    <Table.Td>{target.episode}</Table.Td>
                    <Table.Td>
                      <Tooltip label={target.full_path} multiline w={280}>
                        <Text size="xs" className={pathClasses.pathLine}>
                          {target.rel_path}
                        </Text>
                      </Tooltip>
                      {!target.writable && (
                        <Badge size="xs" color="red" variant="light" mt={2}>
                          {t('episodeTarget.readOnlyShort')}
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={t('episodeTarget.clearOne')}>
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          onClick={() => clearOne.mutate(target.id)}
                          aria-label={t('episodeTarget.clearOne')}
                        >
                          <IconFolderOff size={15} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>

            <Group justify="space-between" wrap="wrap" gap="xs">
              <Text size="xs" c="dimmed" style={{ flex: 1, minWidth: 200 }}>
                {t('episodeTarget.clearAllHint')}
              </Text>
              <Button
                variant="light"
                color="red"
                size="compact-sm"
                loading={clearAll.isPending}
                onClick={() => clearAll.mutate()}
              >
                {t('episodeTarget.clearAll')}
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  )
}
