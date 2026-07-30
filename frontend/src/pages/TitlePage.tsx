import {
  Anchor,
  Badge,
  Card,
  Chip,
  Group,
  Image,
  Loader,
  Spoiler,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconExternalLink } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { ALL_LANGS, usePreferences, type Lang } from '../stores/preferences'
import { KIND_LABELS, LANG_COLORS, LANG_LABELS, titleApi } from '../api/title'
import { BindFolderModal } from '../components/BindFolderModal'
import { ErrorState } from '../components/ErrorState'
import { MappingPanel } from '../components/MappingPanel'
import { TranslationGroups } from '../components/TranslationGroups'

export function TitlePage() {
  const { id } = useParams()
  const seriesId = Number(id)
  const { prefs, toggleLang, toggleKind } = usePreferences()
  const [showNonContent, setShowNonContent] = useState(false)
  const [binding, setBinding] = useState(false)

  const query = useQuery({
    queryKey: ['title', seriesId],
    queryFn: () => titleApi.page(seriesId),
    enabled: Number.isFinite(seriesId),
  })

  if (query.isLoading) {
    return (
      <Group justify="center" p="xl">
        <Loader />
      </Group>
    )
  }

  if (query.isError || !query.data) {
    return (
      <ErrorState
        error={query.error ?? new Error('Тайтл не найден')}
        title="Не удалось загрузить тайтл"
        onRetry={() => query.refetch()}
      />
    )
  }

  const data = query.data
  const { series, titles, quality } = data
  const primary = titles.ru || titles.romaji || `#${series.id}`
  const episodes = data.episodes.filter((e) => showNonContent || e.is_content)
  // Номера настоящих серий: трейлеры и реклама в батч-загрузку не идут.
  const contentEpisodes = new Set(
    data.episodes.filter((e) => e.is_content && e.episode_num != null).map((e) => e.episode_num!),
  )
  // Спешлы и OVA у тайтла есть не всегда — тумблер про них показываем только тогда.
  const hasExtras = data.episodes.some(
    (e) => e.is_content && (e.episode_type === 'special' || e.episode_type === 'ova'),
  )
  const hiddenCount = data.episodes.length - data.episodes.filter((e) => e.is_content).length

  return (
    // Страница длинная: описание, сводка переводов и список серий. Прокрутка
    // должна быть у неё самой, иначе нижние блоки обрезаются.
    <Stack gap="md" style={{ overflowY: 'auto', minHeight: 0 }}>
      <Group align="flex-start" wrap="nowrap" gap="md">
        {series.poster_url && (
          <Image
            src={series.poster_url}
            w={180}
            radius="md"
            fit="cover"
            style={{ flexShrink: 0 }}
            alt=""
          />
        )}

        <Stack gap={6} style={{ minWidth: 0, flex: 1 }}>
          <Title order={3} style={{ wordBreak: 'break-word' }}>
            {primary}
          </Title>
          {titles.romaji && titles.ru && (
            <Text c="dimmed" size="sm">
              {titles.romaji}
            </Text>
          )}
          {titles.ja && (
            <Text c="dimmed" size="sm">
              {titles.ja}
            </Text>
          )}

          <Group gap="xs" mt={4}>
            <Badge variant="light">{series.type_title}</Badge>
            {series.year && <Badge variant="light">{series.year}</Badge>}
            {series.season && <Badge variant="light">{series.season}</Badge>}
            {series.is_airing && <Badge color="red">онгоинг</Badge>}
            {series.mal_score != null && <Badge color="yellow">MAL {series.mal_score}</Badge>}
            <Badge color="gray" variant="outline">
              серий: {data.total_episodes}
            </Badge>
          </Group>

          <Group gap={6} mt={4}>
            {data.genres.map((g) => (
              <Badge key={g.id} size="sm" variant="dot">
                {g.title}
              </Badge>
            ))}
          </Group>

          {data.all_titles.length > 0 && (
            <Spoiler maxHeight={0} showLabel="Все названия" hideLabel="Свернуть" mt={4}>
              <Text size="xs" c="dimmed">
                {data.all_titles.join(' · ')}
              </Text>
            </Spoiler>
          )}
        </Stack>
      </Group>

      {data.descriptions.length > 0 && (
        <Spoiler maxHeight={90} showLabel="Читать полностью" hideLabel="Свернуть">
          {data.descriptions.map((d, i) => (
            <Text key={i} size="sm" style={{ whiteSpace: 'pre-line' }}>
              {d.value}
              {d.source && (
                <Text component="span" size="xs" c="dimmed">
                  {' '}
                  — {d.source}
                </Text>
              )}
            </Text>
          ))}
        </Spoiler>
      )}

      {/* Плашка привязки идёт до сводки качества: пользователь должен сначала
          убедиться, что тайтл сопоставлен с верной папкой. */}
      <MappingPanel
        seriesId={series.id}
        mappings={data.mappings ?? []}
        local={data.local ?? { coverage: {}, episodes_expected: 0, local_max_episode: 0, has_local_files: false }}
        onRebind={() => setBinding(true)}
      />

      {data.links.length > 0 && (
        <Group gap="xs">
          {data.links.slice(0, 10).map((l, i) =>
            l.url ? (
              <Anchor key={i} href={l.url} target="_blank" rel="noreferrer noopener" size="xs">
                {l.title} <IconExternalLink size={10} />
              </Anchor>
            ) : null,
          )}
        </Group>
      )}

      {/* flexShrink: 0 — иначе во flex-колонке карточка сжимается и текст
          обрезается по высоте. */}
      <Card withBorder padding="sm" style={{ flexShrink: 0 }}>
        <Text size="sm" fw={600} mb={4}>
          Доступное качество
        </Text>
        {quality.buckets.length === 0 ? (
          <Text size="sm" c="dimmed">
            Нет данных о разрешении.
          </Text>
        ) : quality.uniform ? (
          <Text size="sm">
            Все серии доступны до <b>{quality.uniform}p</b>
          </Text>
        ) : (
          <Stack gap={2}>
            {quality.buckets.map((b) => (
              <Text key={b.height} size="sm">
                <b>{b.height}p</b> — серии {b.episodes}
              </Text>
            ))}
          </Stack>
        )}
        <Text size="xs" c="dimmed" mt={4}>
          Оценка по разрешению исходников, без обращения к каждому переводу.
          {quality.capped_from_4k && ' Исходники есть в 4K, но скачивание ограничено 1080p.'}
          {quality.unknown_episodes > 0 && ` Без данных: ${quality.unknown_episodes} серий.`}
        </Text>
      </Card>

      <Group gap="md" wrap="wrap">
        <Group gap={6}>
          <Text size="sm" fw={500}>
            Языки:
          </Text>
          {ALL_LANGS.map((lang: Lang) => (
            <Chip
              key={lang}
              size="xs"
              checked={prefs.langs.includes(lang)}
              onChange={() => toggleLang(lang)}
              color={LANG_COLORS[lang]}
            >
              {LANG_LABELS[lang]}
            </Chip>
          ))}
        </Group>

        <Group gap={6}>
          <Text size="sm" fw={500}>
            Типы:
          </Text>
          {['voice', 'sub', 'raw'].map((kind) => (
            <Chip
              key={kind}
              size="xs"
              checked={prefs.kinds.includes(kind)}
              onChange={() => toggleKind(kind)}
            >
              {KIND_LABELS[kind]}
            </Chip>
          ))}
        </Group>
      </Group>

      <div>
        <Title order={5} mb="xs">
          Переводы за весь тайтл
        </Title>
        <TranslationGroups
          sections={data.summary}
          langs={prefs.langs}
          kinds={prefs.kinds}
          seriesUrl={series.url}
          contentEpisodes={contentEpisodes}
          hasExtras={hasExtras}
        />
      </div>

      <div>
        <Group justify="space-between" align="center" mb="xs">
          <Title order={5}>Серии</Title>
          {hiddenCount > 0 && (
            <Switch
              size="xs"
              label={`Показать трейлеры и рекламу (${hiddenCount})`}
              checked={showNonContent}
              onChange={(e) => setShowNonContent(e.currentTarget.checked)}
            />
          )}
        </Group>

        {/* На узком экране колонки с фиксированной шириной схлопываются до одного
            символа в строке, поэтому таблица прокручивается внутри контейнера. */}
        <Table.ScrollContainer minWidth={420}>
          <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={100}>№</Table.Th>
                <Table.Th>Название</Table.Th>
                <Table.Th w={110}>Тип</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {episodes.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{e.episode_full || e.episode_int}</Table.Td>
                  <Table.Td style={{ wordBreak: 'break-word' }}>{e.episode_title || '—'}</Table.Td>
                  <Table.Td>
                    <Badge size="xs" variant="light" color={e.is_content ? 'gray' : 'orange'}>
                      {e.episode_type}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </div>

      <BindFolderModal
        opened={binding}
        seriesId={series.id}
        seriesTitle={primary}
        onClose={() => setBinding(false)}
      />
    </Stack>
  )
}
