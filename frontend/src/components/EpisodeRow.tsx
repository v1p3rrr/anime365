import { Badge, Button, Group, Menu, Table, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  IconCheck,
  IconChevronDown,
  IconDownload,
  IconExternalLink,
  IconLink,
  IconMusic,
  IconPlayerPlay,
} from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { downloadsApi, type DownloadKind } from '../api/downloads'
import { LANG_COLORS, type EpisodeItem, type EpisodeTranslation } from '../api/title'
import { useT } from '../i18n'
import { BindEpisodeModal } from './BindEpisodeModal'
import { PlayerModal } from './PlayerModal'

interface Props {
  episode: EpisodeItem
  langs: string[]
  kinds: string[]
  seriesUrl: string | null
  seriesId: number
}

/** Что показываем как «есть локально». Порядок фиксирован для читаемости. */
/** Языковые коды не переводятся, «видео» — переводится. */
const ROLE_BADGES: Array<{ role: string; label: string; color: string }> = [
  { role: 'video', label: '', color: 'teal' },
  { role: 'sub_ru', label: 'RU', color: 'blue' },
  { role: 'sub_ja', label: 'JA', color: 'teal' },
  { role: 'sub_en', label: 'EN', color: 'grape' },
]

const ROLE_BY_LANG: Record<string, string> = { ru: 'sub_ru', ja: 'sub_ja', en: 'sub_en' }

export function EpisodeRow({ episode, langs, kinds, seriesUrl, seriesId }: Props) {
  const queryClient = useQueryClient()
  const { t } = useT()
  const [binding, setBinding] = useState(false)
  const [playing, setPlaying] = useState(false)
  const badgeLabel = (badge: { label: string }) => badge.label || t('catalog.badgeVideo')

  const enqueue = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: DownloadKind }) =>
      downloadsApi.enqueue(id, kind),
    onSuccess: (result) => {
      notifications.show({
        color: result.created ? 'teal' : 'gray',
        message: result.created ? t('episode.queued') : t('episode.alreadyQueued'),
      })
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('episode.enqueueFailed'), message: error.message }),
  })

  const available = episode.translations.filter(
    (t) => langs.includes(t.lang) && kinds.includes(t.kind),
  )

  // Показываем только те языки, которые пользователь не выключил.
  const visibleBadges = ROLE_BADGES.filter(
    (badge) =>
      badge.role === 'video' ||
      langs.includes(Object.entries(ROLE_BY_LANG).find(([, r]) => r === badge.role)?.[0] ?? ''),
  )

  // Субтитры звука не содержат: извлекать можно только из озвучки и оригинала.
  const audioSources = available.filter((t) => t.kind !== 'sub')

  const handlePick = (translation: EpisodeTranslation) => {
    const kind = translation.kind === 'sub' ? 'subtitle' : 'video'
    // Уже есть такой тип — предупреждаем, что появится второй файл рядом.
    const targetRole = kind === 'video' ? 'video' : ROLE_BY_LANG[translation.lang]
    if (targetRole && episode.have.includes(targetRole)) {
      notifications.show({
        color: 'yellow',
        title: t('episode.existsTitle'),
        message: t('episode.existsMessage'),
      })
    }
    enqueue.mutate({ id: translation.id, kind })
  }

  return (
    <Table.Tr>
      <Table.Td>
        <Text size="sm">{episode.episode_full || episode.episode_int}</Text>
        {!episode.is_content && (
          <Badge size="xs" variant="light" color="orange">
            {episode.episode_type}
          </Badge>
        )}
        {episode.is_content && episode.episode_type !== 'tv' && (
          <Badge size="xs" variant="light" color="grape">
            {episode.episode_type}
          </Badge>
        )}
      </Table.Td>

      <Table.Td>
        <Group gap={4}>
          {visibleBadges.map((badge) => {
            const have = episode.have.includes(badge.role)
            return (
              <Tooltip
                key={badge.role}
                label={
                  have
                    ? t('episode.haveLocal', { label: badgeLabel(badge) })
                    : t('episode.missing', { label: badgeLabel(badge) })
                }
              >
                <Badge
                  size="xs"
                  variant={have ? 'filled' : 'outline'}
                  color={have ? badge.color : 'gray'}
                  leftSection={have ? <IconCheck size={9} /> : undefined}
                >
                  {badgeLabel(badge)}
                </Badge>
              </Tooltip>
            )
          })}
        </Group>
      </Table.Td>

      <Table.Td>
        <Group gap={4} wrap="nowrap">
          <Menu position="bottom-end">
            <Menu.Target>
              <Button
                size="compact-xs"
                leftSection={<IconDownload size={12} />}
                rightSection={<IconChevronDown size={11} />}
                disabled={available.length === 0}
                loading={enqueue.isPending}
              >
                {t('episode.download')}
              </Button>
            </Menu.Target>
            <Menu.Dropdown mah={340} style={{ overflowY: 'auto' }}>
              {available.length === 0 ? (
                <Menu.Item disabled>{t('episode.noTranslations')}</Menu.Item>
              ) : (
                Object.entries(groupByLangKind(available)).map(([key, items]) => (
                  <div key={key}>
                    <Menu.Label>{key.replace('|', ' · ')}</Menu.Label>
                    {items.map((translation) => (
                      <Menu.Item key={translation.id} onClick={() => handlePick(translation)}>
                        <Group gap={6} wrap="nowrap">
                          <Badge size="xs" color={LANG_COLORS[translation.lang]} variant="light">
                            {translation.lang}
                          </Badge>
                          <Text size="xs" style={{ wordBreak: 'break-word' }}>
                            {translation.authors}
                          </Text>
                          {translation.height && (
                            <Text size="xs" c="dimmed">
                              {Math.min(translation.height, 1080)}p
                            </Text>
                          )}
                        </Group>
                      </Menu.Item>
                    ))}
                  </div>
                ))
              )}
            </Menu.Dropdown>
          </Menu>

          {/* Плеер идёт до ссылки на сайт: смотреть у себя — основной путь,
              а сайт остаётся запасным, если браузер не тянет кодек. */}
          {available.length > 0 && (
            <Tooltip label={t('episode.play')}>
              <Button
                size="compact-xs"
                variant="light"
                color="teal"
                px={6}
                onClick={() => setPlaying(true)}
              >
                <IconPlayerPlay size={12} />
              </Button>
            </Tooltip>
          )}

          {/* Отдельная кнопка, а не пункт в общем меню: звук нужен редко, но
              искать его правым кликом или в конце длинного списка переводов
              никто не станет. */}
          {audioSources.length > 0 && (
            <Menu position="bottom-end">
              <Menu.Target>
                <Tooltip label={t('episode.audio')}>
                  <Button size="compact-xs" variant="subtle" color="grape" px={6}>
                    <IconMusic size={12} />
                  </Button>
                </Tooltip>
              </Menu.Target>
              <Menu.Dropdown mah={340} style={{ overflowY: 'auto' }}>
                <Menu.Label>{t('episode.audioMenu')}</Menu.Label>
                {audioSources.map((translation) => (
                  <Menu.Item
                    key={translation.id}
                    onClick={() => enqueue.mutate({ id: translation.id, kind: 'audio' })}
                  >
                    <Group gap={6} wrap="nowrap">
                      <Badge size="xs" color={LANG_COLORS[translation.lang]} variant="light">
                        {translation.lang}
                      </Badge>
                      <Text size="xs" style={{ wordBreak: 'break-word' }}>
                        {translation.authors}
                      </Text>
                    </Group>
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
          )}

          {seriesUrl && (
            <Tooltip label={t('episode.openOnSite')}>
              <Button
                size="compact-xs"
                variant="subtle"
                px={6}
                component="a"
                href={seriesUrl}
                target="_blank"
                rel="noreferrer noopener"
              >
                <IconExternalLink size={12} />
              </Button>
            </Tooltip>
          )}

          {/* Разбор имени ошибается на сдвинутой нумерации и на файлах без
              номера — тогда серию привязывают к файлу руками. */}
          {episode.episode_num != null && (
            <Tooltip label={t('episode.bind')}>
              <Button
                size="compact-xs"
                variant="subtle"
                color="gray"
                px={6}
                onClick={() => setBinding(true)}
              >
                <IconLink size={12} />
              </Button>
            </Tooltip>
          )}
        </Group>

        <PlayerModal
          opened={playing}
          onClose={() => setPlaying(false)}
          translations={available}
          episodeLabel={episode.episode_full || episode.episode_int}
          seriesUrl={seriesUrl}
        />

        {episode.episode_num != null && (
          <BindEpisodeModal
            opened={binding}
            onClose={() => setBinding(false)}
            seriesId={seriesId}
            episode={episode.episode_num}
            episodeLabel={episode.episode_full || episode.episode_int}
          />
        )}
      </Table.Td>
    </Table.Tr>
  )
}

function groupByLangKind(items: EpisodeTranslation[]): Record<string, EpisodeTranslation[]> {
  const out: Record<string, EpisodeTranslation[]> = {}
  for (const item of items) {
    const key = `${item.lang}|${item.kind}`
    ;(out[key] ??= []).push(item)
  }
  return out
}

