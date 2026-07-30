import { Badge, Button, Group, Menu, Table, Text, Tooltip } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconCheck, IconChevronDown, IconDownload, IconExternalLink } from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { downloadsApi } from '../api/downloads'
import { LANG_COLORS, LANG_LABELS, type EpisodeItem, type EpisodeTranslation } from '../api/title'

interface Props {
  episode: EpisodeItem
  langs: string[]
  kinds: string[]
  seriesUrl: string | null
}

/** Что показываем как «есть локально». Порядок фиксирован для читаемости. */
const ROLE_BADGES: Array<{ role: string; label: string; color: string }> = [
  { role: 'video', label: 'Видео', color: 'teal' },
  { role: 'sub_ru', label: 'RU', color: 'blue' },
  { role: 'sub_ja', label: 'JA', color: 'teal' },
  { role: 'sub_en', label: 'EN', color: 'grape' },
]

const ROLE_BY_LANG: Record<string, string> = { ru: 'sub_ru', ja: 'sub_ja', en: 'sub_en' }

export function EpisodeRow({ episode, langs, kinds, seriesUrl }: Props) {
  const queryClient = useQueryClient()

  const enqueue = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: 'video' | 'subtitle' }) =>
      downloadsApi.enqueue(id, kind),
    onSuccess: (result) => {
      notifications.show({
        color: result.created ? 'teal' : 'gray',
        message: result.created ? 'Добавлено в очередь' : 'Уже в очереди',
      })
      queryClient.invalidateQueries({ queryKey: ['downloads'] })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось поставить', message: error.message }),
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

  const handlePick = (translation: EpisodeTranslation) => {
    const kind = translation.kind === 'sub' ? 'subtitle' : 'video'
    // Уже есть такой тип — предупреждаем, что появится второй файл рядом.
    const targetRole = kind === 'video' ? 'video' : ROLE_BY_LANG[translation.lang]
    if (targetRole && episode.have.includes(targetRole)) {
      notifications.show({
        color: 'yellow',
        title: 'Такой файл уже есть',
        message:
          'Существующий не перезаписывается — новый ляжет рядом отдельным файлом ' +
          'с именем автора перевода.',
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
                label={have ? `${badge.label}: есть локально` : `${badge.label}: нет`}
              >
                <Badge
                  size="xs"
                  variant={have ? 'filled' : 'outline'}
                  color={have ? badge.color : 'gray'}
                  leftSection={have ? <IconCheck size={9} /> : undefined}
                >
                  {badge.label}
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
                Скачать
              </Button>
            </Menu.Target>
            <Menu.Dropdown mah={340} style={{ overflowY: 'auto' }}>
              {available.length === 0 ? (
                <Menu.Item disabled>нет переводов на выбранных языках</Menu.Item>
              ) : (
                Object.entries(groupByLangKind(available)).map(([key, items]) => (
                  <div key={key}>
                    <Menu.Label>{describeGroup(key)}</Menu.Label>
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

          {seriesUrl && (
            <Tooltip label="Открыть серию на сайте">
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
        </Group>
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

function describeGroup(key: string): string {
  const [lang, kind] = key.split('|')
  const kindLabel = kind === 'sub' ? 'субтитры' : kind === 'voice' ? 'озвучка' : 'оригинал'
  return `${LANG_LABELS[lang] ?? lang} · ${kindLabel}`
}
