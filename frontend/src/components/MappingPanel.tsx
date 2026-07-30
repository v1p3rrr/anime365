import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Progress,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconFolderOff, IconFolderPlus, IconLock, IconStar, IconUnlink } from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { SOURCE_COLORS } from '../api/library'
import { titleApi, type LocalCoverage, type TitleMappingInfo } from '../api/title'
import pathClasses from './PathLine.module.css'
import { useT, type TranslationKey } from '../i18n'
import { useFormat } from '../i18n/format'

interface Props {
  seriesId: number
  mappings: TitleMappingInfo[]
  local: LocalCoverage
  onRebind: () => void
}

/** Короткие подписи для разбивки по папкам — там места мало. */
const ROLE_SHORT: Record<string, string> = {
  video: 'видео',
  sub_ru: 'RU',
  sub_ja: 'JA',
  sub_en: 'EN',
}

/**
 * Плашка привязки к локальным папкам.
 *
 * Показывает не только «смаплено», но и ЧЕМ определено и на какое название из
 * .nfo — иначе неверная привязка обнаружится только после того, как файлы уедут
 * не в ту папку.
 */
export function MappingPanel({ seriesId, mappings, local, onRebind }: Props) {
  const queryClient = useQueryClient()
  const { t } = useT()
  const { size } = useFormat()
  const sourceLabel = (key: string) => t(`source.${key}` as TranslationKey)

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
    queryClient.invalidateQueries({ queryKey: ['library-status'] })
    queryClient.invalidateQueries({ queryKey: ['library-unmapped'] })
  }

  const unbind = useMutation({
    mutationFn: titleApi.unbind,
    onSuccess: () => {
      notifications.show({ color: 'teal', message: 'Привязка удалена' })
      refresh()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось отвязать', message: error.message }),
  })

  const makePrimary = useMutation({
    mutationFn: (mappingId: number) => titleApi.makePrimary(mappingId, seriesId),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: 'Основная папка изменена' })
      refresh()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось изменить', message: error.message }),
  })

  if (mappings.length === 0) {
    return (
      <Alert color="orange" title="Нет привязки к локальной папке" icon={<IconFolderOff size={18} />}>
        <Text size="sm" mb="xs">
          Этот тайтл не сопоставлен ни с одной папкой библиотеки. Скачанные файлы будет некуда
          положить, пока привязка не создана.
        </Text>
        <Button size="xs" leftSection={<IconFolderPlus size={14} />} onClick={onRebind}>
          Привязать папку
        </Button>
      </Alert>
    )
  }

  const coverageEntries = Object.entries(local.coverage)
  const multiple = mappings.length > 1

  return (
    <Card withBorder padding="sm" style={{ flexShrink: 0 }}>
      <Stack gap="xs">
        {/* Когда папок несколько, это надо сказать прямо: иначе непонятно,
            почему счётчики не сходятся с содержимым конкретной папки. */}
        {multiple && (
          <Text size="xs" c="dimmed">
            Тайтл лежит в {mappings.length} папках. Загрузки идут в основную,
            остальные учитываются при подсчёте того, что уже есть.
          </Text>
        )}
        {mappings.map((mapping) => (
          <div key={mapping.mapping_id}>
            <Group justify="space-between" wrap="nowrap" align="flex-start">
              <div style={{ minWidth: 0 }}>
                <Group gap={6} wrap="wrap">
                  {/* Прокручивается вбок пальцем: перенос по символам рвал
                      слова посреди («…End/Sea» + «son 01»), а путь читается
                      слева направо и рваным становится нечитаемым. */}
                  <Code className={pathClasses.pathLine} title={mapping.rel_path}>
                    {mapping.rel_path}
                  </Code>
                  {/* Метку показываем только когда папок несколько: при одной
                      она ничего не сообщает и лишь засоряет плашку. */}
                  {multiple && mapping.is_primary && (
                    <Tooltip
                      label={
                        mapping.primary_is_manual
                          ? 'Сюда складываются загрузки. Выбрано вручную'
                          : 'Сюда складываются загрузки. Выбрано автоматически — по числу серий этого тайтла в папке'
                      }
                    >
                      <Badge size="xs" color="blue" leftSection={<IconStar size={10} />}>
                        основная{mapping.primary_is_manual ? '' : ' (авто)'}
                      </Badge>
                    </Tooltip>
                  )}
                  {mapping.season_number != null && (
                    <Badge size="xs" variant="light">
                      сезон {mapping.season_number}
                    </Badge>
                  )}
                  {!mapping.writable && (
                    <Tooltip label="Том смонтирован только для чтения — загрузки недоступны">
                      <Badge size="xs" color="yellow" leftSection={<IconLock size={10} />}>
                        только чтение
                      </Badge>
                    </Tooltip>
                  )}
                </Group>

                <Group gap={6} mt={4} wrap="wrap">
                  {/* Голая цифра рядом с источником читалась непонятно —
                      выносим смысл в подпись и подсказку. */}
                  <Tooltip
                    multiline
                    w={320}
                    label={
                      `Насколько алгоритм уверен в этой связи: ${mapping.confidence.toFixed(0)} из 100. ` +
                      (mapping.matched_on ? `Совпало по: ${mapping.matched_on}` : '')
                    }
                  >
                    <Badge size="sm" variant="light" color={SOURCE_COLORS[mapping.source] ?? 'gray'}>
                      {sourceLabel(mapping.source)}
                      {!mapping.is_manual && ` · точность ${mapping.confidence.toFixed(0)}%`}
                    </Badge>
                  </Tooltip>
                  {mapping.needs_confirmation && (
                    <Badge size="sm" color="yellow">
                      требует подтверждения
                    </Badge>
                  )}
                  <Text size="xs" c="dimmed">
                    {mapping.video_count} видео · {size(mapping.size_bytes)}
                  </Text>
                </Group>

                {/* Что лежит именно в этой папке — иначе при нескольких папках
                    непонятно, откуда взялись суммарные цифры. */}
                {multiple && Object.keys(mapping.counts).length > 0 && (
                  <Text size="xs" c="dimmed" mt={2}>
                    здесь:{' '}
                    {Object.entries(mapping.counts)
                      .map(([role, count]) => `${ROLE_SHORT[role] ?? role} ${count}`)
                      .join(' · ')}
                  </Text>
                )}

                {/* Название из .nfo — то, по чему пользователь глазами проверит,
                    что привязка верная. */}
                {mapping.nfo_title && (
                  <Text size="xs" c="dimmed" mt={2} style={{ wordBreak: 'break-word' }}>
                    в .nfo: {mapping.nfo_title}
                    {mapping.nfo_premiered ? ` · премьера ${mapping.nfo_premiered}` : ''}
                  </Text>
                )}
              </div>

              <Stack gap={2} align="flex-end" style={{ flexShrink: 0 }}>
                {multiple && !mapping.is_primary && (
                  <Tooltip label="Складывать загрузки в эту папку">
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      leftSection={<IconStar size={12} />}
                      onClick={() => makePrimary.mutate(mapping.mapping_id)}
                      loading={makePrimary.isPending}
                    >
                      Сделать основной
                    </Button>
                  </Tooltip>
                )}
                <Tooltip label="Убрать связь с этой папкой">
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="red"
                    leftSection={<IconUnlink size={12} />}
                    onClick={() => unbind.mutate(mapping.mapping_id)}
                    loading={unbind.isPending}
                  >
                    Отвязать
                  </Button>
                </Tooltip>
              </Stack>
            </Group>
          </div>
        ))}

        <Group gap="xs">
          <Button
            size="compact-xs"
            variant="light"
            leftSection={<IconFolderPlus size={12} />}
            onClick={onRebind}
          >
            Привязать ещё папку
          </Button>
        </Group>

        {local.has_local_files && (
          <Stack gap={4} mt={4}>
            {coverageEntries.map(([role, info]) => (
              <div key={role}>
                <Group justify="space-between" gap="xs">
                  <Text size="xs">{info.label}</Text>
                  <Text size="xs" c={info.complete ? 'teal' : 'dimmed'}>
                    {info.have} из {info.total}
                  </Text>
                </Group>
                <Progress
                  size="sm"
                  value={info.total ? (info.have / info.total) * 100 : 0}
                  color={info.complete ? 'teal' : info.have ? 'yellow' : 'gray'}
                />
                {!info.complete && info.missing_ranges && (
                  <Text size="xs" c="dimmed">
                    нет: {info.missing_ranges}
                  </Text>
                )}
                {/* Счётчик считает различные серии, а не файлы. Если серия лежит
                    в двух копиях, об этом надо сказать, а не молча схлопнуть. */}
                {info.duplicate_count > 0 && (
                  <Text size="xs" c="dimmed">
                    в двух копиях: {info.duplicate_ranges}
                  </Text>
                )}
              </div>
            ))}

            {/* Локально серий может быть больше, чем знает сайт — по ТЗ это не ошибка. */}
            {local.local_max_episode > local.episodes_expected && (
              <Text size="xs" c="dimmed">
                Локально есть серия {local.local_max_episode}, хотя на сайте их{' '}
                {local.episodes_expected}.
              </Text>
            )}
          </Stack>
        )}
      </Stack>
    </Card>
  )
}
