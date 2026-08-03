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
import {
  IconFolderOff,
  IconFolderPlus,
  IconFolderSymlink,
  IconLock,
  IconStar,
  IconUnlink,
} from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { SOURCE_COLORS } from '../api/library'
import {
  titleApi,
  type LocalCoverage,
  type ProposedFolder,
  type TitleMappingInfo,
} from '../api/title'
import pathClasses from './PathLine.module.css'
import { usePermissions } from '../hooks/usePermissions'
import { useT, type TranslationKey } from '../i18n'
import { useApiError } from '../i18n/apiError'
import { useFormat } from '../i18n/format'

interface Props {
  seriesId: number
  mappings: TitleMappingInfo[]
  local: LocalCoverage
  /** Куда уедет загрузка при отсутствии привязки. */
  proposedFolder: ProposedFolder | null
  onRebind: () => void
  /** Открыть окно назначения папок отдельным сериям. */
  onManageEpisodeTargets: () => void
}

/** Короткие подписи для разбивки по папкам — там места мало. */
/** Языковые коды одинаковы везде; «видео» берётся из словаря. */
const ROLE_SHORT: Record<string, string> = {
  video: '',
  sub_ru: 'ru',
  sub_ja: 'ja',
  sub_en: 'en',
}

/**
 * Плашка привязки к локальным папкам.
 *
 * Показывает не только «смаплено», но и ЧЕМ определено и на какое название из
 * .nfo — иначе неверная привязка обнаружится только после того, как файлы уедут
 * не в ту папку.
 */
export function MappingPanel({
  seriesId,
  mappings,
  local,
  proposedFolder,
  onRebind,
  onManageEpisodeTargets,
}: Props) {
  const queryClient = useQueryClient()
  const { t } = useT()
  const apiError = useApiError()
  const { size } = useFormat()
  // Сама плашка — сведения о привязке, их видит каждый, кто видит тайтл.
  // Кнопки правят привязку и решают, куда лягут файлы: это право `library`.
  const canEdit = usePermissions().can('library')
  const sourceLabel = (key: string) => t(`source.${key}` as TranslationKey)

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
    queryClient.invalidateQueries({ queryKey: ['library-status'] })
    queryClient.invalidateQueries({ queryKey: ['library-unmapped'] })
  }

  const unbind = useMutation({
    mutationFn: titleApi.unbind,
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('mapping.unbound') })
      refresh()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('mapping.unbindFailed'), message: apiError(error) }),
  })

  const makePrimary = useMutation({
    mutationFn: (mappingId: number) => titleApi.makePrimary(mappingId, seriesId),
    onSuccess: () => {
      notifications.show({ color: 'teal', message: t('mapping.primaryChanged') })
      refresh()
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('title.treatFailed'), message: apiError(error) }),
  })

  if (mappings.length === 0) {
    // Папка создастся сама, поэтому плашка не пугает «некуда положить», а
    // показывает имя будущей папки. Привязать свою по-прежнему можно — просто
    // это больше не обязательное условие для загрузки.
    const proposed = proposedFolder
    return (
      <Alert
        color={proposed && proposed.writable ? 'blue' : 'orange'}
        title={t('mapping.noneTitle')}
        icon={<IconFolderOff size={18} />}
      >
        {proposed ? (
          <>
            <Text size="sm">
              {proposed.writable ? t('mapping.willCreate') : t('mapping.rootReadOnly')}
            </Text>
            <div className={pathClasses.path}>{proposed.full_path}</div>
            {/* У многосерийного фильма единой папки нет: показанное имя — это
                первая часть, у остальных отличается номером. */}
            {proposed.per_part && (
              <Text size="xs" c="dimmed">
                {t('mapping.perPartHint')}
              </Text>
            )}
            {/* Источник известен точно, поэтому называем его, а не пересказываем
                правило приоритетов: имя пользователь и так видит выше. */}
            {proposed.name_source && (
              <Text size="xs" c="dimmed" mb="xs">
                {t('mapping.willCreateHint', {
                  source: t(`nameSource.${proposed.name_source}` as TranslationKey),
                })}
              </Text>
            )}
          </>
        ) : (
          <Text size="sm" mb="xs">
            {t('mapping.noneHint')}
          </Text>
        )}
        {canEdit && (
          <Button size="xs" leftSection={<IconFolderPlus size={14} />} onClick={onRebind}>
            {proposed ? t('mapping.bindOther') : t('mapping.bindFolder')}
          </Button>
        )}
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
            {t('mapping.multiple', { count: mappings.length })}
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
                          ? t('mapping.primaryHintManual')
                          : t('mapping.primaryHintAuto')
                      }
                    >
                      <Badge size="xs" color="blue" leftSection={<IconStar size={10} />}>
                        {mapping.primary_is_manual
                          ? t('mapping.primaryManual')
                          : t('mapping.primaryAuto')}
                      </Badge>
                    </Tooltip>
                  )}
                  {mapping.season_number != null && (
                    <Badge size="xs" variant="light">
                      {t('mapping.seasonNumber', { n: mapping.season_number })}
                    </Badge>
                  )}
                  {!mapping.writable && (
                    <Tooltip label={t('mapping.readOnlyHint')}>
                      <Badge size="xs" color="yellow" leftSection={<IconLock size={10} />}>
                        {t('mapping.readOnly')}
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
                      t('mapping.confidenceHint', { value: mapping.confidence.toFixed(0) }) +
                      (mapping.matched_on ? t('mapping.matchedOn', { what: mapping.matched_on }) : '')
                    }
                  >
                    <Badge size="sm" variant="light" color={SOURCE_COLORS[mapping.source] ?? 'gray'}>
                      {sourceLabel(mapping.source)}
                      {!mapping.is_manual && ` · ${t('mapping.accuracy', { value: mapping.confidence.toFixed(0) })}`}
                    </Badge>
                  </Tooltip>
                  {mapping.needs_confirmation && (
                    <Badge size="sm" color="yellow">
                      {t('mapping.needsConfirmation')}
                    </Badge>
                  )}
                  <Text size="xs" c="dimmed">
                    {t('mapping.videosAndSize', {
                      count: mapping.video_count,
                      size: size(mapping.size_bytes),
                    })}
                  </Text>
                </Group>

                {/* Что лежит именно в этой папке — иначе при нескольких папках
                    непонятно, откуда взялись суммарные цифры. */}
                {multiple && Object.keys(mapping.counts).length > 0 && (
                  <Text size="xs" c="dimmed" mt={2}>
                    {t('mapping.here', { what: '' })}
                    {Object.entries(mapping.counts)
                      .map(
                        ([role, count]) =>
                          // Строка собирается через join — флаг-компонент сюда
                          // не вставить, поэтому остаётся код языка.
                          `${ROLE_SHORT[role]?.toUpperCase() || t('mapping.roleVideo')} ${count}`,
                      )
                      .join(' · ')}
                  </Text>
                )}

                {/* Название из .nfo — то, по чему пользователь глазами проверит,
                    что привязка верная. */}
                {mapping.nfo_title && (
                  <Text size="xs" c="dimmed" mt={2} style={{ wordBreak: 'break-word' }}>
                    {t('mapping.nfoTitle', { title: mapping.nfo_title })}
                    {mapping.nfo_premiered ? t('mapping.premiered', { date: mapping.nfo_premiered }) : ''}
                  </Text>
                )}
              </div>

              <Stack gap={2} align="flex-end" style={{ flexShrink: 0 }}>
                {canEdit && multiple && !mapping.is_primary && (
                  <Tooltip label={t('mapping.makePrimaryHint')}>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      leftSection={<IconStar size={12} />}
                      onClick={() => makePrimary.mutate(mapping.mapping_id)}
                      loading={makePrimary.isPending}
                    >
                      {t('mapping.makePrimary')}
                    </Button>
                  </Tooltip>
                )}
                {canEdit && (
                  <Tooltip label={t('mapping.unbindHint')}>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      color="red"
                      leftSection={<IconUnlink size={12} />}
                      onClick={() => unbind.mutate(mapping.mapping_id)}
                      loading={unbind.isPending}
                    >
                      {t('common.unbind')}
                    </Button>
                  </Tooltip>
                )}
              </Stack>
            </Group>
          </div>
        ))}

        {canEdit && (
          <Group gap="xs">
            <Button
              size="compact-xs"
              variant="light"
              leftSection={<IconFolderPlus size={12} />}
              onClick={onRebind}
            >
              {t('mapping.bindAnother')}
            </Button>
            {/* Единственная точка входа, пока назначений нет: плашка со списком
                появляется только когда они уже есть, и первое создать было бы
                неоткуда. */}
            <Button
              size="compact-xs"
              variant="subtle"
              leftSection={<IconFolderSymlink size={12} />}
              onClick={onManageEpisodeTargets}
            >
              {t('mapping.episodeFolders')}
            </Button>
          </Group>
        )}

        {local.has_local_files && (
          <Stack gap={4} mt={4}>
            {coverageEntries.map(([role, info]) => (
              <div key={role}>
                <Group justify="space-between" gap="xs">
                  {/* Подпись по коду роли, а не строкой с сервера: там она
                      только по-русски и в английском интерфейсе торчала как есть. */}
                  <Text size="xs">{t(`coverageRole.${role}` as TranslationKey)}</Text>
                  <Text size="xs" c={info.complete ? 'teal' : 'dimmed'}>
                    {t('mapping.haveOf', { have: info.have, total: info.total })}
                  </Text>
                </Group>
                <Progress
                  size="sm"
                  value={info.total ? (info.have / info.total) * 100 : 0}
                  color={info.complete ? 'teal' : info.have ? 'yellow' : 'gray'}
                />
                {!info.complete && info.missing_ranges && (
                  <Text size="xs" c="dimmed">
                    {t('mapping.missingRanges', { ranges: info.missing_ranges })}
                  </Text>
                )}
                {/* Счётчик считает различные серии, а не файлы. Если серия лежит
                    в двух копиях, об этом надо сказать, а не молча схлопнуть. */}
                {info.duplicate_count > 0 && (
                  <Text size="xs" c="dimmed">
                    {t('mapping.duplicateRanges', { ranges: info.duplicate_ranges })}
                  </Text>
                )}
              </div>
            ))}

            {/* Локально серий может быть больше, чем знает сайт — по ТЗ это не ошибка. */}
            {local.local_max_episode > local.episodes_expected && (
              <Text size="xs" c="dimmed">
                {t('mapping.moreLocalEpisodes', {
                  local: local.local_max_episode,
                  expected: local.episodes_expected,
                })}
              </Text>
            )}
          </Stack>
        )}
      </Stack>
    </Card>
  )
}
