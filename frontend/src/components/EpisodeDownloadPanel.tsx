import {
  Accordion,
  Alert,
  Badge,
  Box,
  Button,
  Group,
  ScrollArea,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconMusic, IconPhoto, IconTextCaption } from '@tabler/icons-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'

import { downloadsApi, type DownloadKind } from '../api/downloads'
import {
  LANG_COLORS,
  type EmbedInfo,
  type EpisodeItem,
  type EpisodeTranslation,
} from '../api/title'
import { useT, type TranslationKey } from '../i18n'
import { useKindLabel } from '../i18n/kind'
import { LangTag } from './Flag'
import { useApiError } from '../i18n/apiError'
import {
  isTypeVisible,
  translationType,
  typeRank,
  type TranslationType,
} from '../stores/preferences'
import { DownloadButton, type Emphasis } from './DownloadButton'

interface Props {
  episode: EpisodeItem
  /** Включённые типы переводов — общая настройка отображения. */
  types: TranslationType[]
}

/**
 * Панель загрузки под строкой серии.
 *
 * Заменяет прежнее меню-простыню: у популярного тайтла на серию приходится
 * несколько десятков переводов, и плоский список из них не давал ответить на
 * единственный вопрос, который здесь вообще задают, — «чего мне не хватает».
 *
 * Поэтому переводы разложены по разделам «тип × язык», а разделы разделены на
 * недостающие и уже закрытые. Закрытые не удалены, а убраны под кнопку: скачать
 * второй вариант озвучки — законное желание, просто не то, ради которого сюда
 * заходят в девяти случаях из десяти.
 */

type Kind = 'voice' | 'sub' | 'raw'

/** Почему раздел признан закрытым. Показывается подсказкой у значка. */
type Covered = 'haveSub' | 'haveAudio' | 'hasOriginal' | null

interface Section {
  key: string
  kind: Kind
  lang: string
  items: EpisodeTranslation[]
  covered: Covered
}

const KIND_ICON: Record<Kind, typeof IconPhoto> = {
  raw: IconPhoto,
  voice: IconMusic,
  sub: IconTextCaption,
}

/** Что можно скачать у перевода с субтитрами. */
type SubAction = 'subtitle' | 'both' | 'video'

const ACTION_LABEL: Record<SubAction, TranslationKey> = {
  subtitle: 'episode.actionSubs',
  both: 'episode.actionVideoSubs',
  video: 'episode.actionVideo',
}

const COVERED_HINT: Record<Exclude<Covered, null>, TranslationKey> = {
  haveSub: 'episode.coveredHaveSub',
  haveAudio: 'episode.coveredHaveAudio',
  hasOriginal: 'episode.coveredHasOriginal',
}

export function EpisodeDownloadPanel({ episode, types }: Props) {
  const { t } = useT()
  const kindLabel = useKindLabel()
  const apiError = useApiError()
  const queryClient = useQueryClient()
  const [showCovered, setShowCovered] = useState(false)

  // Переводы, у которых субтитры вшиты в видео. Узнаём двумя путями: из embed,
  // когда пользователь открыл выпадашку качества, и из отказа сервера при
  // попытке скачать. Признак окончательный — вшитое остаётся вшитым.
  const [hardsub, setHardsub] = useState<Set<number>>(() => new Set())

  const markHardsub = useCallback((id: number) => {
    setHardsub((prev) => (prev.has(id) ? prev : new Set(prev).add(id)))
  }, [])

  const noteEmbed = useCallback(
    (info: EmbedInfo) => {
      if (!info.has_subtitles) markHardsub(info.translation_id)
    },
    [markHardsub],
  )

  // «Уже в очереди» — не ошибка, а частый и нормальный ответ: на серию легко
  // нажать дважды. Поэтому сообщение нейтральное, а не тревожное.
  const notifyQueued = (created: number) => {
    notifications.show({
      color: created > 0 ? 'teal' : 'gray',
      message: created > 0 ? t('episode.queued') : t('episode.alreadyQueued'),
    })
    queryClient.invalidateQueries({ queryKey: ['downloads'] })
  }

  const enqueue = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: DownloadKind }) =>
      downloadsApi.enqueue(id, kind),
    onSuccess: (result) => {
      // «Уже скачано» — это не «уже в очереди»: файл лежит на диске, и
      // повторная попытка не поставила бы задачу вовсе.
      if (result.already_exists) {
        notifications.show({ color: 'gray', message: t('episode.audioAlreadyHave') })
        return
      }
      notifyQueued(result.created ? 1 : 0)
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: t('episode.enqueueFailed'),
        message: apiError(error),
      }),
  })

  const sections = useMemo(() => buildSections(episode, types), [episode, types])

  /**
   * Порядок и акценты кнопок у перевода с субтитрами.
   *
   * Первой идёт та, которая закрывает то, чего не хватает. Приглушённая не
   * значит «нельзя»: скачать повторно можно, просто незачем — и это должно быть
   * видно до нажатия, а не после появления второго файла рядом.
   */
  const subActions = (
    lang: string,
    isHardsub: boolean,
  ): Array<{ kind: SubAction; emphasis: Emphasis; disabled?: boolean }> => {
    // Вшитые субтитры отдельным файлом не существуют — скачать можно только
    // видео, и оно уже несёт их в картинке.
    if (isHardsub) {
      return [
        { kind: 'video', emphasis: 'primary' },
        { kind: 'subtitle', emphasis: 'dimmed', disabled: true },
        { kind: 'both', emphasis: 'dimmed', disabled: true },
      ]
    }

    const hasVideo = episode.have.includes('video')
    const hasSub = episode.have.includes(`sub_${lang}`)

    if (!hasVideo && !hasSub) {
      // Нет ничего — одним действием закрывается и то и другое.
      return [
        { kind: 'both', emphasis: 'primary' },
        { kind: 'subtitle', emphasis: 'secondary' },
        { kind: 'video', emphasis: 'secondary' },
      ]
    }
    if (!hasVideo) {
      // Субтитры есть, видео нет — не хватает именно видео.
      return [
        { kind: 'video', emphasis: 'primary' },
        { kind: 'subtitle', emphasis: 'dimmed' },
        { kind: 'both', emphasis: 'dimmed' },
      ]
    }
    if (!hasSub) {
      // Видео есть — нужен только файл субтитров, второй видеофайл ни к чему.
      return [
        { kind: 'subtitle', emphasis: 'primary' },
        { kind: 'both', emphasis: 'dimmed' },
        { kind: 'video', emphasis: 'dimmed' },
      ]
    }
    // Есть и то и другое: раздел и так уедет в «уже есть», выделять нечего.
    return [
      { kind: 'subtitle', emphasis: 'dimmed' },
      { kind: 'both', emphasis: 'dimmed' },
      { kind: 'video', emphasis: 'dimmed' },
    ]
  }

  const missing = sections.filter((section) => section.covered === null)
  const covered = sections.filter((section) => section.covered !== null)
  const shown = showCovered ? [...missing, ...covered] : missing

  if (sections.length === 0) {
    return (
      <Text size="xs" c="dimmed" py="xs">
        {t('episode.noTranslations')}
      </Text>
    )
  }

  const renderSection = (section: Section) => {

    return (
      <Accordion.Item key={section.key} value={section.key}>
        {/* Иконка идёт внутрь подписи, а не в проп `icon`: при левом шевроне
            Mantine отправляет её в конец строки, где она отрывается от текста
            и читается как самостоятельная кнопка. */}
        <Accordion.Control>
          <Group gap="xs" wrap="nowrap">
            <SectionIcon kind={section.kind} lang={section.lang} />
            <Text size="sm">
              {kindLabel(section.kind)} · <LangTag lang={section.lang} />
            </Text>
            <Text size="xs" c="dimmed">
              {t('episode.variants', { count: section.items.length })}
            </Text>
            {section.covered && (
              <Tooltip label={t(COVERED_HINT[section.covered])} multiline w={240}>
                <Badge size="xs" variant="light" color="teal">
                  {t('episode.covered')}
                </Badge>
              </Tooltip>
            )}
          </Group>
        </Accordion.Control>

        <Accordion.Panel>
          {/* Звук у переводов с субтитрами.
              Одна кнопка на весь раздел, а не по кнопке в каждой строке:
              перевод с субтитрами — это то же видео с той же японской
              дорожкой, что и у равки и у соседнего перевода с другими
              субтитрами. Различать версии субтитров для звука незачем, а
              кнопка в каждой строке предлагала бы скачать одно и то же по
              нескольку раз. У озвучек наоборот — там студии разные, и кнопка
              стоит у каждой. */}
          {section.kind === 'sub' && section.items.length > 0 && (
            <Group gap={6} mb={6}>
              <Tooltip label={t('episode.audioFromSubHint')} multiline w={260}>
                <Button
                  size="compact-xs"
                  variant="subtle"
                  color="grape"
                  leftSection={<IconMusic size={12} />}
                  onClick={() =>
                    // Берём первый перевод раздела: дорожка у них одна и та же,
                    // а повторное извлечение отсекается на сервере — он считает
                    // японский звук общим для всей серии.
                    enqueue.mutate({ id: section.items[0].id, kind: 'audio' })
                  }
                >
                  {t('episode.audioFromSub')}
                </Button>
              </Tooltip>
            </Group>
          )}

          {/* Разделы у популярных тайтлов доходят до нескольких десятков
              переводов — без ограничения высоты панель выталкивала бы список
              серий на экран вниз. */}
          <ScrollArea.Autosize mah={260} type="auto">
            <Stack gap={4} pr="xs">
              {section.items.map((item) => (
                <Group key={item.id} gap="xs" wrap="wrap" justify="space-between">
                  <Group gap={6} wrap="nowrap" style={{ minWidth: 0, flex: '1 1 180px' }}>
                    <Text size="xs" style={{ wordBreak: 'break-word' }}>
                      {item.authors}
                    </Text>
                    {item.height && (
                      <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
                        ~{Math.min(item.height, 1080)}p
                      </Text>
                    )}
                  </Group>

                  <Group gap={4} wrap="nowrap">
                    {section.kind === 'sub' ? (
                      subActions(section.lang, hardsub.has(item.id)).map((action) => (
                        <DownloadButton
                          key={action.kind}
                          translationId={item.id}
                          kind={action.kind}
                          estimatedHeight={item.height}
                          label={t(ACTION_LABEL[action.kind])}
                          emphasis={action.emphasis}
                          disabled={action.disabled}
                          disabledReason={t('episode.hardsubReason')}
                          onEmbed={noteEmbed}
                          onHardsub={() => markHardsub(item.id)}
                        />
                      ))
                    ) : (
                      <>
                        <DownloadButton
                          translationId={item.id}
                          kind="video"
                          estimatedHeight={item.height}
                          onEmbed={noteEmbed}
                        />
                        {/* Только звук — для фонотеки: видео при этом уходит
                            во временную папку и удаляется. */}
                        <Tooltip label={t('episode.actionAudioOnly')}>
                          <Button
                            size="compact-xs"
                            variant="subtle"
                            color="grape"
                            px={6}
                            onClick={() => enqueue.mutate({ id: item.id, kind: 'audio' })}
                            aria-label={t('episode.actionAudioOnly')}
                          >
                            <IconMusic size={12} />
                          </Button>
                        </Tooltip>
                      </>
                    )}
                  </Group>
                </Group>
              ))}
            </Stack>
          </ScrollArea.Autosize>
        </Accordion.Panel>
      </Accordion.Item>
    )
  }

  return (
    <Stack gap="xs" py="xs">
      <LocalSummary episode={episode} />

      {missing.length === 0 && (
        <Alert color="teal" p="xs">
          <Text size="xs">{t('episode.nothingMissing')}</Text>
        </Alert>
      )}

      <Accordion
        multiple
        variant="separated"
        // Раскрыт ровно один раздел — первый недостающий. Раскрывать все сразу
        // значит вывалить на экран сотню строк; не раскрывать ни одного —
        // заставить сделать лишний клик там, где выбор очевиден.
        defaultValue={missing.length > 0 ? [missing[0].key] : []}
        chevronPosition="left"
      >
        {shown.map(renderSection)}
      </Accordion>

      {covered.length > 0 && (
        <Box>
          <Button
            size="compact-xs"
            variant="subtle"
            color="gray"
            onClick={() => setShowCovered((value) => !value)}
          >
            {showCovered
              ? t('episode.hideCovered')
              : t('episode.showCovered', { count: covered.length })}
          </Button>
        </Box>
      )}
    </Stack>
  )
}

function SectionIcon({ kind, lang }: { kind: Kind; lang: string }) {
  const Icon = KIND_ICON[kind]
  return <Icon size={16} color={`var(--mantine-color-${LANG_COLORS[lang] ?? 'gray'}-5)`} />
}

/** Строка «что уже лежит локально» — от неё зависит всё остальное в панели. */
function LocalSummary({ episode }: { episode: EpisodeItem }) {
  const { t } = useT()

  const hasVideo = episode.have.includes('video')
  const subs = episode.have
    .filter((role) => role.startsWith('sub_'))
    .map((role) => role.slice(4))

  if (!hasVideo && subs.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        {t('episode.localNothing')}
      </Text>
    )
  }

  const audio = episode.audio_langs

  return (
    <Group gap={6} wrap="wrap">
      <Text size="xs" c="dimmed">
        {t('episode.localHave')}
      </Text>
      {hasVideo && (
        <Badge size="xs" variant="light" color="teal">
          {t('catalog.badgeVideo')} ·{' '}
          {audio.length > 0 ? (
            audio.map((lang, index) => (
              <span key={lang}>
                {index > 0 && ', '}
                <LangTag lang={lang} />
              </span>
            ))
          ) : (
            t('episode.audioUnknown')
          )}
        </Badge>
      )}
      {subs.map((lang) => (
        <Badge key={lang} size="xs" variant="light" color="blue">
          {t('kind.sub')} · <LangTag lang={lang} />
        </Badge>
      ))}
    </Group>
  )
}

/**
 * Раскладывает переводы серии по разделам и решает, какие из них уже закрыты.
 *
 * Правило одно: раздел закрыт, если локальные файлы уже дают то, что он даёт.
 * Для субтитров это прямое совпадение по языку. Для видео — язык звука, и здесь
 * есть несимметричность, которая выглядит неочевидно, но она осознанная:
 * получив оригинальную дорожку, человек смотрит с субтитрами, и озвучка ему уже
 * не нужна — она не дополнит библиотеку, а положит рядом второй файл на 200 МБ.
 * В обратную сторону это не работает: у кого есть только озвучка, оригинал ещё
 * впереди, поэтому равки остаются на виду.
 */
function buildSections(episode: EpisodeItem, types: TranslationType[]): Section[] {
  const available = episode.translations.filter((item) =>
    isTypeVisible(item.kind, item.lang, types),
  )

  const grouped = new Map<string, Section>()
  for (const item of available) {
    const kind = item.kind as Kind
    const key = `${kind}|${item.lang}`
    const section = grouped.get(key)
    if (section) {
      section.items.push(item)
    } else {
      grouped.set(key, { key, kind, lang: item.lang, items: [item], covered: null })
    }
  }

  const hasVideo = episode.have.includes('video')
  const audioLangs = episode.audio_langs
  // Пустой список при наличии видео означает «не смогли определить». Прятать
  // разделы по догадке нельзя: цена ошибки — спрятанная нужная загрузка.
  const audioKnown = audioLangs.length > 0
  const hasOriginal = audioLangs.includes('ja')

  for (const section of grouped.values()) {
    section.covered = coverageOf(section, {
      have: episode.have,
      hasVideo,
      audioKnown,
      audioLangs,
      hasOriginal,
    })
    // Внутри раздела — сначала то, что выше по разрешению: при прочих равных
    // берут лучшее качество, а автора всё равно выбирают глазами.
    section.items.sort((a, b) => (b.height ?? 0) - (a.height ?? 0))
  }

  // Закрытые разделы уходят в конец независимо от приоритета типа: «уже есть»
  // перевешивает любую расстановку, потому что скачивать там нечего.
  return [...grouped.values()].sort(
    (a, b) =>
      Number(a.covered !== null) - Number(b.covered !== null) ||
      typeRank(translationType(a.kind, a.lang)) - typeRank(translationType(b.kind, b.lang)),
  )
}

interface LocalState {
  have: string[]
  hasVideo: boolean
  audioKnown: boolean
  audioLangs: string[]
  hasOriginal: boolean
}

function coverageOf(section: Section, local: LocalState): Covered {
  if (section.kind === 'sub') {
    return local.have.includes(`sub_${section.lang}`) ? 'haveSub' : null
  }

  // Видео нет вовсе или звук не определён — не прячем ничего.
  if (!local.hasVideo || !local.audioKnown) return null

  if (local.audioLangs.includes(section.lang)) return 'haveAudio'
  if (section.kind === 'voice' && local.hasOriginal) return 'hasOriginal'
  return null
}
