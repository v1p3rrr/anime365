import {
  Anchor,
  Badge,
  Button,
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
  Tooltip,
} from '@mantine/core'
import { IconExternalLink } from '@tabler/icons-react'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { api } from '../api/client'
import { ALL_LANGS, usePreferences, type Lang } from '../stores/preferences'
import { LANG_COLORS, titleApi } from '../api/title'
import { BindFolderModal } from '../components/BindFolderModal'
import { EpisodeRow } from '../components/EpisodeRow'
import { ErrorState } from '../components/ErrorState'
import { MappingPanel } from '../components/MappingPanel'
import { TranslationGroups } from '../components/TranslationGroups'
import { useT, type TranslationKey } from '../i18n'
import scrollClasses from './ScrollPage.module.css'

export function TitlePage() {
  const { id } = useParams()
  const seriesId = Number(id)
  const { prefs, toggleLang, toggleKind } = usePreferences()
  const queryClient = useQueryClient()
  const { t } = useT()
  const [showNonContent, setShowNonContent] = useState(false)
  const [binding, setBinding] = useState(false)

  const query = useQuery({
    queryKey: ['title', seriesId],
    queryFn: () => titleApi.page(seriesId),
    enabled: Number.isFinite(seriesId),
  })

  // Автоматически развести «фильм» и «сериал» нельзя: у тайтла с type=movie
  // бывает несколько эпизодов — это связка фильмов, и Radarr фильмом её не
  // считает. Решение за пользователем, от него зависит корень для загрузок.
  const treatAs = useMutation({
    mutationFn: (asMovie: boolean) =>
      api.put<unknown>(`/series/${seriesId}/treat-as-movie`, { treat_as_movie: asMovie }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
      notifications.show({ color: 'teal', message: 'Трактовка тайтла изменена' })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: 'Не удалось изменить', message: error.message }),
  })

  // ffprobe открывает каждый файл, поэтому только по кнопке и только для
  // тех, у кого данных о дорожках ещё нет.
  const probe = useMutation({
    mutationFn: () =>
      api.post<{ candidates: number; probed: number; failed: number; reason: string | null }>(
        `/library/series/${seriesId}/probe-streams`,
      ),
    onSuccess: (result) => {
      notifications.show({
        color: result.probed ? 'teal' : 'yellow',
        title: 'Дорожки определены',
        message:
          result.candidates === 0
            ? 'У всех файлов данные о дорожках уже есть — из .nfo.'
            : `Обработано ${result.probed} из ${result.candidates}` +
              (result.failed ? `, не вышло: ${result.failed}` : ''),
      })
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: 'Не удалось определить дорожки',
        message: error.message,
        autoClose: 12000,
      }),
  })

  const refine = useMutation({
    mutationFn: () =>
      api.post<{ episodes: number; refined: number; failed: number; reason: string | null }>(
        `/series/${seriesId}/refine-quality`,
      ),
    onSuccess: (result) => {
      // Если не вышло вообще ничего, это не «частичный успех», а отказ —
      // и у него есть конкретная причина, которую сервер уже назвал.
      const allFailed = result.refined === 0 && result.failed > 0
      notifications.show({
        color: allFailed ? 'red' : result.failed ? 'yellow' : 'teal',
        title: allFailed ? 'Уточнить качество не удалось' : 'Качество уточнено',
        message: allFailed
          ? (result.reason ?? 'Ни одна серия не ответила.') +
            ' Чаще всего дело в токене: точные ступени отдаются только с активной подпиской.'
          : `Серий обработано: ${result.refined} из ${result.episodes}` +
            (result.failed ? `, не удалось: ${result.failed}` : ''),
        autoClose: allFailed ? 12000 : 5000,
      })
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: 'Не удалось уточнить качество',
        message: error.message,
        autoClose: 12000,
      }),
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
  const hiddenCount = data.episodes.length - data.episodes.filter((e) => e.is_content).length

  return (
    // Страница длинная: описание, сводка переводов и список серий. Прокрутка
    // должна быть у неё самой, иначе нижние блоки обрезаются.
    <Stack gap="md" className={scrollClasses.scrollPage}>
      <Group align="flex-start" wrap="nowrap" gap="md">
        {series.poster_url && (
          // На 375px постер шириной 180 оставлял под заголовок около 150 пикселей,
          // и длинные названия ломались посреди слова. Сужаем его на узких экранах.
          <Image
            src={series.poster_url}
            w={{ base: 108, sm: 180 }}
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
            <Tooltip
              label={
                series.effective_is_movie
                  ? 'Загрузки уйдут в корень фильмов'
                  : 'Загрузки уйдут в корень сериалов'
              }
            >
              <Badge
                variant="light"
                color={series.treat_as_movie != null ? 'teal' : undefined}
                style={{ cursor: 'pointer' }}
                onClick={() => treatAs.mutate(!series.effective_is_movie)}
              >
                {series.effective_is_movie ? 'как фильм' : 'как сериал'}
                {series.treat_as_movie != null && ' ·  вручную'}
              </Badge>
            </Tooltip>
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
        // Высота задана кратной строке (4 × 22px) при явном line-height: иначе
        // обрезка приходится на середину пятой строки и выглядит как поломка.
        <Spoiler maxHeight={88} showLabel="Читать полностью" hideLabel="Свернуть">
          {data.descriptions.map((d, i) => (
            <Text key={i} size="sm" lh="22px" style={{ whiteSpace: 'pre-line' }}>
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

      <Card withBorder padding="sm">
        <Group justify="space-between" mb={4} wrap="wrap" gap="xs">
          <Text size="sm" fw={600}>
            Доступное качество
          </Text>
          <Tooltip label="Определить языки дорожек через ffprobe — для файлов без данных в .nfo">
            <Button
              size="compact-xs"
              variant="subtle"
              color="gray"
              onClick={() => probe.mutate()}
              loading={probe.isPending}
            >
              Дорожки файлов
            </Button>
          </Tooltip>
          {quality.buckets.length > 0 && quality.is_estimate && (
            <Tooltip label="Запросит точные ступени по одному переводу на серию">
              <Button
                size="compact-xs"
                variant="light"
                onClick={() => refine.mutate()}
                loading={refine.isPending}
              >
                Уточнить качество
              </Button>
            </Tooltip>
          )}
        </Group>
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
          {quality.is_estimate
            ? 'Оценка по разрешению исходников, без обращения к каждому переводу.'
            : 'Точные ступени скачивания, полученные по каждой серии.'}
          {quality.is_estimate &&
            quality.refined_episodes > 0 &&
            ` Уточнено серий: ${quality.refined_episodes}.`}
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
              {t(`lang.${lang}` as TranslationKey)}
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
              {t(`kind.${kind}` as TranslationKey)}
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
        {/* Колонка с названием эпизода убрана: Anime365 отдаёт episodeTitle
            пустым почти всегда, а место нужнее под отметки наличия и кнопки. */}
        <Table.ScrollContainer minWidth={460}>
          <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={120}>Серия</Table.Th>
                <Table.Th w={180}>Есть локально</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {episodes.map((e) => (
                <EpisodeRow
                  key={e.id}
                  episode={e}
                  langs={prefs.langs}
                  kinds={prefs.kinds}
                  seriesUrl={series.url}
                  seriesId={series.id}
                />
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
