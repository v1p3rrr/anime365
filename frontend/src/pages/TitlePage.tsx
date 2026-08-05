import {
  Alert,
  Anchor,
  AspectRatio,
  Badge,
  Box,
  Divider,
  Menu,
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
import {
  IconAlertTriangle,
  IconChevronDown,
  IconDeviceTv,
  IconExternalLink,
  IconMovie,
  IconDownload,
  IconWand,
} from '@tabler/icons-react'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { api } from '../api/client'
import { splitType, TRANSLATION_TYPES, usePreferences } from '../stores/preferences'
import { PosterPlaceholder } from '../components/PosterPlaceholder'
import { TitleQuickLinks } from '../components/TitleQuickLinks'
import { RelatedTitles } from '../components/RelatedTitles'
import { EpisodeTargetsAlert } from '../components/EpisodeTargetsAlert'
import { EpisodeTargetsModal } from '../components/EpisodeTargetsModal'
import { TitleAudioSection } from '../components/TitleAudioSection'
import { episodeTargetApi, type EpisodeTarget } from '../api/library'
import { useVisibleWidthVar } from '../hooks/useVisibleWidthVar'
import { usePermissions } from '../hooks/usePermissions'
import { LANG_COLORS, titleApi } from '../api/title'
import { BindFolderModal } from '../components/BindFolderModal'
import { EpisodeRow } from '../components/EpisodeRow'
import { ErrorState } from '../components/ErrorState'
import { MappingPanel } from '../components/MappingPanel'
import { TranslationGroups } from '../components/TranslationGroups'
import { TitleDetails } from '../components/TitleDetails'
import { useSeriesTypeLabel } from '../i18n/seriesType'
import { useT, type TranslationKey } from '../i18n'
import { useSeasonLabel } from '../i18n/season'
import { TitleAutoDownloadModal } from '../components/TitleAutoDownloadModal'
import { LANG_BY_TYPE } from '../components/AutoDownloadTab'
import type { AutoLang } from '../api/autodownload'
import { LangTag } from '../components/Flag'
import { useApiError } from '../i18n/apiError'
import { useTitleName } from '../i18n/titles'
import { useFormat } from '../i18n/format'
import scrollClasses from './ScrollPage.module.css'

/**
 * Названия внешних ресурсов приходят с Anime365 по-русски. Латиница здесь
 * уместнее: это имена собственные, и на всех трёх языках интерфейса их пишут
 * одинаково — «Шикимори» в английской версии страницы читалось бы как
 * недоделанный перевод.
 *
 * Переименовываем только то, у чего есть общепринятое латинское написание;
 * остальные подписи отдаём как пришли, чтобы новая ссылка не пропала.
 */
const LINK_TITLES: Record<string, string> = {
  Шикимори: 'Shikimori',
}

function linkTitle(title: string | undefined): string {
  return LINK_TITLES[(title ?? '').trim()] ?? title ?? ''
}

export function TitlePage() {
  const { id } = useParams()
  const seriesId = Number(id)
  const { prefs, toggleType } = usePreferences()
  const { can } = usePermissions()
  const panelWidthRef = useVisibleWidthVar('--episode-panel-width')
  const [posterFailed, setPosterFailed] = useState(false)

  // Отдельным запросом, а не в составе страницы: назначения меняются чаще
  // самой страницы, и перетягивать ради них всё остальное незачем.
  const targetsQuery = useQuery({
    queryKey: ['episode-targets', seriesId],
    queryFn: () => episodeTargetApi.list(seriesId),
  })
  const targetsByEpisode = useMemo(() => {
    const map = new Map<number, EpisodeTarget>()
    for (const target of targetsQuery.data?.targets ?? []) map.set(target.episode, target)
    return map
  }, [targetsQuery.data])
  const queryClient = useQueryClient()
  const { t } = useT()
  const seasonLabel = useSeasonLabel()
  const [autoDownloadOpen, setAutoDownloadOpen] = useState(false)
  // Вкладки языков в окне автозагрузки — только те, что человек
  // не выключил у себя в настройках субтитров.
  const autoLangs = (['ru', 'en', 'ja'] as AutoLang[]).filter((lang) =>
    prefs.types.some((type) => LANG_BY_TYPE[type] === lang),
  )
  const seriesTypeLabel = useSeriesTypeLabel()
  const apiError = useApiError()
  const titleName = useTitleName()
  const format = useFormat()
  const [showNonContent, setShowNonContent] = useState(false)
  const [binding, setBinding] = useState(false)
  // Отдельно от `binding`: та модалка привязывает папку ТАЙТЛА, а ссылка в
  // плашке обещает настройку папок ОТДЕЛЬНЫХ СЕРИЙ. Раньше обе вели в одно
  // окно, и ссылка обещала больше, чем делала.
  const [episodeTargets, setEpisodeTargets] = useState(false)
  // Какую серию подставить при открытии — когда пришли из её строки.
  const [targetEpisode, setTargetEpisode] = useState<number | null>(null)

  const query = useQuery({
    queryKey: ['title', seriesId],
    queryFn: () => titleApi.page(seriesId),
    enabled: Number.isFinite(seriesId),
    // Пока переводы догружаются фоном, перезапрашиваем страницу. Раньше она
    // просто ждала ответа Anime365 — до тринадцати секунд на популярном
    // тайтле; теперь шапка, серии и локальное наличие видны сразу, а сводка
    // переводов появляется, когда приедет.
    refetchInterval: (q) => (q.state.data?.translations_pending ? 1500 : false),
  })

  // Автоматически развести «фильм» и «сериал» нельзя: у тайтла с type=movie
  // бывает несколько эпизодов — это связка фильмов, и Radarr фильмом её не
  // считает. Решение за пользователем, от него зависит корень для загрузок.
  const treatAs = useMutation({
    // null возвращает автоопределение по типу из каталога — сервер понимает
    // именно его, а не отсутствие поля.
    mutationFn: (asMovie: boolean | null) =>
      api.put<unknown>(`/series/${seriesId}/treat-as-movie`, { treat_as_movie: asMovie }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
      notifications.show({ color: 'teal', message: t('title.treatChanged') })
    },
    onError: (error: Error) =>
      notifications.show({ color: 'red', title: t('title.treatFailed'), message: apiError(error) }),
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
        title: t('title.probeDone'),
        message:
          result.candidates === 0
            ? t('title.probeNoCandidates')
            : t('title.probeResult', { probed: result.probed, total: result.candidates }) +
              (result.failed ? t('title.probeFailedCount', { count: result.failed }) : ''),
      })
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: t('title.probeFailed'),
        message: apiError(error),
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
        title: allFailed ? t('title.refineFailed') : t('title.refineDone'),
        message: allFailed
          ? (result.reason ?? t('title.refineNoAnswer')) + t('title.refineNoneReason')
          : t('title.refineResult', { refined: result.refined, total: result.episodes }) +
            (result.failed ? t('title.refineFailedCount', { count: result.failed }) : ''),
        autoClose: allFailed ? 12000 : 5000,
      })
      queryClient.invalidateQueries({ queryKey: ['title', seriesId] })
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: t('title.refineFailed'),
        message: apiError(error),
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
        error={query.error ?? new Error(t('title.notFound'))}
        title={t('title.loadFailed')}
        onRetry={() => query.refetch()}
      />
    )
  }

  const data = query.data
  const { series, titles, quality } = data
  const primary = titleName.primary(titles, `#${series.id}`)
  // Под заголовком показываем другие написания — но только те, что от него
  // отличаются, иначе при японском интерфейсе одна и та же строка шла бы дважды.
  const alternates = [titles.romaji, titles.ja, titles.ru].filter(
    (value, index, list) => value && value !== primary && list.indexOf(value) === index,
  )
  const episodes = data.episodes.filter((e) => showNonContent || e.is_content)
  // Номера настоящих серий: трейлеры и реклама в батч-загрузку не идут.
  const contentEpisodes = new Set(
    data.episodes.filter((e) => e.is_content && e.episode_num != null).map((e) => e.episode_num!),
  )
  const hiddenCount = data.episodes.length - data.episodes.filter((e) => e.is_content).length
  // То же правило, что на сервере (см. is_announced): ни одной серии-контента,
  // не идёт сейчас, год не в прошлом. Дублировать его сюда пришлось потому, что
  // карточка каталога и страница тайтла считаются разными эндпоинтами, а
  // тащить признак через всю страницу ради одного условия — дороже.
  const isAnnounced =
    !data.episodes.some((e) => e.is_content) &&
    !series.is_airing &&
    (series.year == null || series.year >= new Date().getFullYear())
  // Частей у тайтла: по нему решается, связка это или одиночный фильм.
  const contentEpisodeCount = data.episodes.filter((e) => e.is_content).length

  return (
    // Страница длинная: описание, сводка переводов и список серий. Прокрутка
    // должна быть у неё самой, иначе нижние блоки обрезаются.
    <Stack gap="md" className={scrollClasses.scrollPage}>
      {/* Jellyfin в папке фильма различает три случая: альтернативные версии
          одного фильма, части одного файла (part1/part2 — склеиваются) и разные
          фильмы, которым положены отдельные папки. Несколько частей, названных
          фильмом, попадут в первую категорию: вторая будет считаться другой
          версией первой, а не самостоятельным фильмом. Молча так делать нельзя. */}
      {series.effective_is_movie && contentEpisodeCount > 1 && (
        <Alert color="yellow" icon={<IconAlertTriangle size={18} />} style={{ flexShrink: 0 }}>
          <Text size="sm">
            {t('title.movieCollectionWarning', { count: contentEpisodeCount })}
          </Text>
        </Alert>
      )}

      <Group align="flex-start" wrap="nowrap" gap="md">
        {/* На 375px постер шириной 180 оставлял под заголовок около 150 пикселей,
            и длинные названия ломались посреди слова. Сужаем его на узких экранах.
            Блок остаётся на месте и без картинки: иначе заголовок прыгает влево,
            когда постера нет или он не загрузился. */}
        <Box w={{ base: 108, sm: 180 }} style={{ flexShrink: 0 }}>
          {series.poster_url && !posterFailed ? (
            <Image
              src={series.poster_url}
              radius="md"
              fit="cover"
              alt=""
              onError={() => setPosterFailed(true)}
            />
          ) : (
            <AspectRatio ratio={2 / 3}>
              <PosterPlaceholder />
            </AspectRatio>
          )}
        </Box>

        <Stack gap={6} style={{ minWidth: 0, flex: 1 }}>
          <Title order={3} style={{ wordBreak: 'break-word' }}>
            {primary}
          </Title>
          {alternates.map((value) => (
            <Text key={value} c="dimmed" size="sm">
              {value}
            </Text>
          ))}

          <Group gap="xs" mt={4}>
            {/* Не значок, а кнопка с меню: состояния три, а не два — «фильм»,
                «сериал» и «определять автоматически», — и вернуться к третьему
                прежний тумблер не давал вовсе. Кнопка ещё и отделяет управление
                от соседних значков: те только показывают, а этот меняет
                поведение загрузок, и путать их нельзя. */}
            {/* «Фильм или сериал» решает, в какой корень лягут файлы, поэтому
                это право `library`. Без него состояние остаётся видимым —
                значком вместо кнопки: знать, куда пойдут загрузки, полезно и
                тому, кто не может это менять. */}
            {!can('library') ? (
              <Tooltip
                label={
                  series.effective_is_movie ? t('title.asMovieHint') : t('title.asSeriesHint')
                }
              >
                <Badge
                  variant="light"
                  color={series.treat_as_movie != null ? 'teal' : 'gray'}
                  leftSection={
                    series.effective_is_movie ? <IconMovie size={12} /> : <IconDeviceTv size={12} />
                  }
                >
                  {series.effective_is_movie ? t('title.asMovie') : t('title.asSeries')}
                </Badge>
              </Tooltip>
            ) : (
            <Menu position="bottom-start" withinPortal>
              <Menu.Target>
                <Tooltip
                  label={
                    series.effective_is_movie
                      ? t('title.asMovieHint')
                      : t('title.asSeriesHint')
                  }
                >
                  <Button
                    size="compact-xs"
                    variant={series.treat_as_movie != null ? 'filled' : 'default'}
                    color={series.treat_as_movie != null ? 'teal' : undefined}
                    leftSection={
                      series.effective_is_movie ? (
                        <IconMovie size={13} />
                      ) : (
                        <IconDeviceTv size={13} />
                      )
                    }
                    rightSection={<IconChevronDown size={11} />}
                    loading={treatAs.isPending}
                  >
                    {series.effective_is_movie ? t('title.asMovie') : t('title.asSeries')}
                  </Button>
                </Tooltip>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{t('title.treatMode')}</Menu.Label>
                <Menu.Item
                  leftSection={<IconDeviceTv size={14} />}
                  onClick={() => treatAs.mutate(false)}
                  fw={series.treat_as_movie === false ? 600 : undefined}
                >
                  {t('title.treatAsSeriesAction')}
                </Menu.Item>
                <Menu.Item
                  leftSection={<IconMovie size={14} />}
                  onClick={() => treatAs.mutate(true)}
                  fw={series.treat_as_movie === true ? 600 : undefined}
                >
                  {t('title.treatAsMovieAction')}
                </Menu.Item>
                <Menu.Divider />
                {/* Возврат к автоопределению: сервер принимает null именно для
                    этого, но выбрать его раньше было нечем. */}
                <Menu.Item
                  leftSection={<IconWand size={14} />}
                  onClick={() => treatAs.mutate(null)}
                  fw={series.treat_as_movie === null ? 600 : undefined}
                >
                  {t('title.treatAuto')}
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            )}

            <Divider orientation="vertical" />

            <Badge variant="light">{seriesTypeLabel(series.type, series.type_title)}</Badge>
            {series.year && <Badge variant="light">{series.year}</Badge>}
            {series.season && <Badge variant="light">{seasonLabel(series.season)}</Badge>}
            {series.is_airing && <Badge color="red">{t('catalog.cardOngoing')}</Badge>}
            {series.mal_score != null && <Badge color="yellow">MAL {series.mal_score}</Badge>}
            <Badge color="gray" variant="outline">
              {t('title.episodesTotal', { count: data.total_episodes })}
            </Badge>
            {/* Медиана по переводам, поэтому «примерно»: точной цифры у тайтла
                нет, у разных релизов хронометраж различается на минуты. */}
            {format.duration(data.typical_duration) && (
              <Tooltip label={t('title.durationHint')}>
                <Badge color="gray" variant="outline">
                  ≈ {format.duration(data.typical_duration)}
                </Badge>
              </Tooltip>
            )}
          </Group>

          {/* Студия, «Анонс» и ход онгоинга — отдельным запросом к Шикимори.
              Блока может не быть вовсе: у тайтла может не быть mal_id. */}
          <TitleDetails seriesId={series.id} availableEpisodes={data.total_episodes} />

          <Group gap={6} mt={4}>
            {data.genres.map((g) => (
              <Badge key={g.id} size="sm" variant="dot">
                {g.title}
              </Badge>
            ))}
          </Group>

          {data.all_titles.length > 0 && (
            <Spoiler maxHeight={0} showLabel={t('title.allTitles')} hideLabel={t('title.collapse')} mt={4}>
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
        <Spoiler maxHeight={88} showLabel={t('title.readMore')} hideLabel={t('title.collapse')}>
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
      {/* Под описанием и до плашки привязки: это справка о тайтле, а не
          действие. Свёрнут, потому что у крупных франшиз линейка длинная. */}
      <EpisodeTargetsModal
        opened={episodeTargets}
        seriesId={series.id}
        episodes={data.episodes}
        initialEpisode={targetEpisode}
        onClose={() => {
          setEpisodeTargets(false)
          setTargetEpisode(null)
        }}
      />

      <EpisodeTargetsAlert
        targets={targetsQuery.data?.targets ?? []}
        onManage={() => setEpisodeTargets(true)}
      />

      <RelatedTitles seriesId={series.id} />

      <MappingPanel
        seriesId={series.id}
        mappings={data.mappings ?? []}
        local={data.local ?? { coverage: {}, episodes_expected: 0, local_max_episode: 0, has_local_files: false }}
        proposedFolder={data.proposed_folder}
        onRebind={() => setBinding(true)}
        onManageEpisodeTargets={() => setEpisodeTargets(true)}
      />

      {/* Переходы наружу — до справочного списка ссылок: это действия
          («посмотреть у себя», «открыть исходную страницу»), а не сноски. */}
      <TitleQuickLinks seriesId={series.id} title={primary} anime365Url={series.url} />

      {/* Автозагрузка ставит задачи в общую очередь, поэтому кнопка живёт под
          тем же правом, что и сами загрузки. */}
      {can('downloads') && (
        <Group>
          <Button
            variant="light"
            size="compact-sm"
            leftSection={<IconDownload size={14} />}
            onClick={() => setAutoDownloadOpen(true)}
          >
            {t('autodownload.title')}
          </Button>
        </Group>
      )}

      <TitleAutoDownloadModal
        seriesId={series.id}
        title={primary}
        opened={autoDownloadOpen}
        onClose={() => setAutoDownloadOpen(false)}
        langs={autoLangs}
      />

      {data.links.length > 0 && (
        <Group gap="xs">
          {data.links.slice(0, 10).map((l, i) =>
            l.url ? (
              <Anchor key={i} href={l.url} target="_blank" rel="noreferrer noopener" size="xs">
                {linkTitle(l.title)} <IconExternalLink size={10} />
              </Anchor>
            ) : null,
          )}
        </Group>
      )}

      <Card withBorder padding="sm">
        <Group justify="space-between" mb={4} wrap="wrap" gap="xs">
          <Text size="sm" fw={600}>
            {t('title.quality')}
          </Text>
          {/* ffprobe читает файлы библиотеки и переписывает наши данные о
              дорожках — это `library`. */}
          {can('library') && (
            <Tooltip label={t('title.probeHint')}>
              <Button
                size="compact-xs"
                variant="subtle"
                color="gray"
                onClick={() => probe.mutate()}
                loading={probe.isPending}
              >
                {t('title.probeStreams')}
              </Button>
            </Tooltip>
          )}
          {/* «Уточнить качество» ходит в embed по каждой серии — расход чужого
              API ради подготовки к загрузке, поэтому право `downloads`. */}
          {can('downloads') && quality.buckets.length > 0 && quality.is_estimate && (
            <Tooltip label={t('title.refineHint')}>
              <Button
                size="compact-xs"
                variant="light"
                onClick={() => refine.mutate()}
                loading={refine.isPending}
              >
                {t('title.refine')}
              </Button>
            </Tooltip>
          )}
        </Group>
        {quality.buckets.length === 0 ? (
          <Text size="sm" c="dimmed">
            {t('title.qualityNone')}
          </Text>
        ) : quality.uniform ? (
          <Text size="sm">
            {t('title.qualityUniform', { height: quality.uniform })}
          </Text>
        ) : (
          <Stack gap={2}>
            {quality.buckets.map((b) => (
              <Text key={b.height} size="sm">
                {t('title.qualityBucket', { height: b.height, episodes: b.episodes })}
              </Text>
            ))}
          </Stack>
        )}
        <Text size="xs" c="dimmed" mt={4}>
          {quality.is_estimate
            ? t('title.qualityEstimate')
            : t('title.qualityExact')}
          {quality.is_estimate &&
            quality.refined_episodes > 0 &&
            t('title.qualityRefined', { count: quality.refined_episodes })}
          {quality.capped_from_4k && t('title.qualityCapped4k')}
          {quality.unknown_episodes > 0 && t('title.qualityUnknown', { count: quality.unknown_episodes })}
        </Text>
      </Card>

      {/* Быстрый фильтр — тот же набор, что и в настройках, и та же память:
          менять видимость чаще всего хочется прямо здесь, глядя на переводы,
          а не уходя на другую страницу. Порядок фишек — порядок приоритета. */}
      <Group gap={6} wrap="wrap">
        <Text size="sm" fw={500}>
          {t('title.translationTypes')}
        </Text>
        {TRANSLATION_TYPES.map((type) => {
          const [kind, lang] = splitType(type)
          return (
            <Chip
              key={type}
              size="xs"
              checked={prefs.types.includes(type)}
              onChange={() => toggleType(type)}
              color={LANG_COLORS[lang]}
            >
              {t(`kind.${kind}` as TranslationKey)} · <LangTag lang={lang} />
            </Chip>
          )
        })}
      </Group>

      <div>
        <Group gap="xs" mb="xs" align="center">
          <Title order={5}>{t('title.translationsAll')}</Title>
          {/* Переводы едут отдельно от страницы. Сказать об этом обязательно:
              пустая сводка без объяснения читается как «переводов нет», а это
              совсем другой смысл. */}
          {data.translations_pending && (
            <Group gap={6} align="center">
              <Loader size="xs" />
              <Text size="xs" c="dimmed">
                {t('title.translationsLoading')}
              </Text>
            </Group>
          )}
        </Group>
        <TranslationGroups
          sections={data.summary}
          types={prefs.types}
          local={data.local}
          seriesUrl={series.url}
          contentEpisodes={contentEpisodes}
        />
      </div>

      {/* Раздела серий нет вовсе, когда на Anime365 нет ни одной записи —
          даже трейлера. Пустая таблица с заголовками колонок выглядит как
          сломавшаяся страница, а не как «ещё ничего не вышло». */}
      {data.episodes.length > 0 ? (
      <div>
        <Group justify="space-between" align="center" mb="xs">
          <Title order={5}>{t('title.episodes')}</Title>
          {hiddenCount > 0 && (
            <Switch
              size="xs"
              label={t('title.showNonContent', { count: hiddenCount })}
              checked={showNonContent}
              onChange={(e) => setShowNonContent(e.currentTarget.checked)}
            />
          )}
        </Group>

        {/* На узком экране колонки с фиксированной шириной схлопываются до одного
            символа в строке, поэтому таблица прокручивается внутри контейнера. */}
        {/* Колонка с названием эпизода убрана: Anime365 отдаёт episodeTitle
            пустым почти всегда, а место нужнее под отметки наличия и кнопки. */}
        {/* Обёртка нужна ради замера: панель загрузки внутри таблицы должна
            быть шириной с видимую область, а не с саму таблицу. */}
        <div ref={panelWidthRef}>
          <Table.ScrollContainer minWidth={460}>
            <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={120}>{t('title.columnEpisode')}</Table.Th>
                <Table.Th w={180}>{t('title.columnLocal')}</Table.Th>
                <Table.Th>{t('title.columnActions')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {episodes.map((e) => (
                <EpisodeRow
                  key={e.id}
                  episode={e}
                  types={prefs.types}
                  target={
                    e.episode_num != null ? targetsByEpisode.get(e.episode_num) : undefined
                  }
                  seriesUrl={series.url}
                  seriesId={series.id}
                  onAssignFolder={(episodeNum) => {
                    setTargetEpisode(episodeNum)
                    setEpisodeTargets(true)
                  }}
                />
              ))}
            </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </div>
      </div>
      ) : (
        /* Записей нет. У анонса это ожидаемо и уже сказано чипом выше, поэтому
           повторяться незачем — а вот у вышедшего тайтла это значит, что на
           Anime365 его просто ещё не выложили, и об этом стоит сказать прямо. */
        !isAnnounced && (
          <Alert color="gray">
            {series.effective_is_movie ? t('title.noEpisodesMovie') : t('title.noEpisodesYet')}
          </Alert>
        )
      )}

      <BindFolderModal
        opened={binding}
        seriesId={series.id}
        seriesTitle={primary}
        onClose={() => setBinding(false)}
      />

      {/* Дорожки — в самом низу: это не то, ради чего открывают страницу
          тайтла, но искать их в отдельном экране было бы страннее. */}
      <TitleAudioSection seriesId={series.id} />

    </Stack>
  )
}
