import { Alert, Badge, Group, Modal, Select, SimpleGrid, Stack } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { LANG_COLORS, titleApi, type EpisodeTranslation } from '../api/title'
import { useT } from '../i18n'
import { useKindLabel } from '../i18n/kind'
// Подписи вариантов в Select — строки, флаг-компонент туда не вставить.
import { langCode } from '../i18n/flags'

// Стабильная ссылка на пустой список: см. heightsKey ниже.
const EMPTY_HEIGHTS: number[] = []

interface Props {
  opened: boolean
  onClose: () => void
  /** Переводы этой серии — из них выбирается дорожка для просмотра. */
  translations: EpisodeTranslation[]
  episodeLabel: string
  seriesUrl: string | null
}

/**
 * Встроенный плеер на Artplayer.
 *
 * Видео идёт через наш прокси: прямые ссылки CDN содержат бессрочный
 * премиум-токен, и отдавать их в браузер нельзя.
 *
 * ## Почему не браузерный `<video>` и не JASSUB
 *
 * Раньше здесь был обычный `<video>`, а ASS рисовал JASSUB. Субтитры не
 * появлялись, и разбор занял много времени: ассеты отдавались с кодом 200,
 * воркер запускался без ошибок, CSP не мешала — но `renderer.ready` не
 * разрешался никогда, а сам JASSUB молчал об этом даже с `debug: true`. Ни
 * обновление до 2.5.14, ни откат на 1.7.17 ничего не изменили.
 *
 * Artplayer выбран по фактам, а не по известности: у него официальный плагин
 * `artplayer-plugin-libass`, а внутри плагина — **`libass-wasm`**, то есть
 * ДРУГАЯ реализация libass, не JASSUB. Сломанное место обходится стороной, а
 * не чинится наугад. Заодно приходит то, чего у браузерного плеера нет вовсе:
 * жесты на телефоне, горячие клавиши, скорость воспроизведения, картинка в
 * картинке и кнопка показа-скрытия субтитров.
 *
 * Рассматривался и Vidstack, но он отпал по проверке: его движок субтитров
 * `media-captions` ASS только разбирает как текст (libass внутри нет), то есть
 * ни позиционирования, ни оформления — для аниме это половина смысла.
 *
 * ⚠️ Пути к воркеру ведут в `/libass/`, а не в бандл. SubtitlesOctopus грузит
 * `.wasm` **относительно воркера**, а Vite переименовывает ассеты в
 * хешированные имена — воркер искал бы файл, которого нет. Раскладку в
 * `public/` делает `scripts/copy-libass.mjs`.
 */

/** Стабильные пути: файлы кладёт `scripts/copy-libass.mjs`. */
const LIBASS_WORKER = '/libass/subtitles-octopus-worker.js'
const LIBASS_LEGACY_WORKER = '/libass/subtitles-octopus-worker-legacy.js'

export function PlayerModal({ opened, onClose, translations, episodeLabel }: Props) {
  const { t } = useT()
  const kindLabel = useKindLabel()
  const containerRef = useRef<HTMLDivElement>(null)
  const artRef = useRef<{ destroy: (removeHtml?: boolean) => void } | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Перевод с субтитрами — это полноценное видео с японской дорожкой, а не
  // отдельный файл субтитров. Исключать его из источников видео было ошибкой:
  // у того, кто оставил в настройках только субтитры, плеер сообщал, что
  // смотреть нечего.
  const playable = translations
  const subtitleOptions = translations.filter((item) => item.kind === 'sub')

  const [videoId, setVideoId] = useState<string | null>(null)
  const [subId, setSubId] = useState<string | null>(null)
  const [subTouched, setSubTouched] = useState(false)

  // Ступени качества для выбранной дорожки. Спрашиваем embed: в списке
  // переводов есть только `height` исходника, а это оценка — набор реально
  // доступных ступеней из неё не выводится (526 даёт [478], 528 даёт [720, 360]).
  const quality = useQuery({
    queryKey: ['embed-stream-heights', videoId],
    queryFn: () => titleApi.embed(Number(videoId)),
    enabled: opened && videoId !== null,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  const streamHeights = quality.data?.stream_heights ?? EMPTY_HEIGHTS
  // Ключ вместо массива в зависимостях эффекта. `?? []` возвращал бы новый
  // массив на каждый рендер, эффект видел бы изменение и пересоздавал плеер
  // бесконечно — с обрывом воспроизведения на каждом круге.
  const heightsKey = streamHeights.join(',')

  useEffect(() => {
    if (!opened) {
      setSubTouched(false)
      return
    }
    if (videoId === null && playable.length > 0) setVideoId(String(playable[0].id))
  }, [opened, playable, videoId])

  // Выбрали как видео перевод с субтитрами — субтитры к нему подставляем сами.
  // Иначе выходит нелепо: человек выбирает «Субтитры · RU», получает японскую
  // дорожку без единой надписи и должен догадаться выбрать тот же перевод ещё
  // раз, уже во втором списке.
  useEffect(() => {
    if (!opened || subTouched || videoId === null) return
    const source = translations.find((item) => String(item.id) === videoId)
    if (source?.kind === 'sub') setSubId(videoId)
  }, [opened, videoId, subTouched, translations])

  // Плеер живёт ровно столько, сколько открыт диалог: он держит воркер libass
  // и слушателей на документе, и брошенный экземпляр продолжал бы работать.
  useEffect(() => {
    let cancelled = false

    async function mount() {
      artRef.current?.destroy(true)
      artRef.current = null
      setError(null)

      if (!opened || !videoId || !containerRef.current) return

      try {
        const [{ default: Artplayer }, { default: libassPlugin }] = await Promise.all([
          import('artplayer'),
          import('artplayer-plugin-libass'),
        ])
        if (cancelled || !containerRef.current) return

        // ⚠️ Адрес субтитров передаётся Artplayer'у, а НЕ плагину: плагин
        // читает его из `art.option.subtitle.url` на событии `ready`.
        //
        // И расширение `.ass` обязательно — формат определяется по нему
        // (`getExt(url) === 'ass'`). На пути без расширения плагин молча ничего
        // не грузит, что мы и наблюдали.
        const plugins = subId
          ? [libassPlugin({ workerUrl: LIBASS_WORKER, legacyWorkerUrl: LIBASS_LEGACY_WORKER })]
          : []

        const art = new Artplayer({
          container: containerRef.current,
          url: `/api/stream/${videoId}/video`,
          // Выбор качества — пунктом в меню под шестерёнкой, рядом со
          // скоростью и субтитрами, а не отдельной кнопкой на панели: штатная
          // опция `quality` у Artplayer ставит собственный контрол справа
          // (`position: "right"`), и настройки воспроизведения оказывались бы
          // в двух разных местах.
          //
          // Ступени — только те, что отдал сам API для ПРОСМОТРА. Числа среди
          // них попадаются непривычные (478p, 396p), но это настоящие ступени,
          // а не мусор: набор не выводится из разрешения исходника, и оценка в
          // сводке тайтла с ним намеренно не совпадает.
          //
          // Одна ступень — меню не показываем: выбирать не из чего.
          ...(streamHeights.length > 1
            ? {
                settings: [
                  {
                    html: t('player.quality'),
                    width: 160,
                    tooltip: `${streamHeights[0]}p`,
                    selector: streamHeights.map((height, index) => ({
                      html: `${height}p`,
                      default: index === 0,
                    })),
                    // Адрес выводим из подписи, а не храним отдельным полем:
                    // тип Setting у Artplayer посторонних свойств не допускает,
                    // а обходить это приведением значило бы отключить проверку
                    // там, где она как раз полезна.
                    onSelect(item) {
                      const height = parseInt(item.html, 10)
                      // switchQuality сохраняет текущее время и состояние
                      // воспроизведения, в отличие от switchUrl.
                      art.switchQuality(`/api/stream/${videoId}/video?height=${height}`)
                      return item.html
                    },
                  },
                ],
              }
            : {}),
          // ⚠️ Ключ `subtitle` либо есть объектом, либо его нет вовсе.
          // `subtitle: undefined` Artplayer не принимает и падает с
          // «'option.subtitle' require 'object' type, but got 'undefined'» —
          // то есть при просмотре без субтитров плеер вообще не запускался.
          ...(subId
            ? { subtitle: { url: `/api/stream/${subId}/subtitles.ass`, type: 'ass' } }
            : {}),
          volume: 1,
          autoplay: true,
          // Ничего из этого браузерный плеер не даёт, а на телефоне без жестов
          // и полноэкранного режима смотреть попросту неудобно.
          setting: true,
          playbackRate: true,
          fullscreen: true,
          fullscreenWeb: true,
          pip: true,
          hotkey: true,
          gesture: true,
          miniProgressBar: true,
          autoOrientation: true,
          plugins,
        })

        artRef.current = art as unknown as { destroy: (removeHtml?: boolean) => void }

        // Кнопка показа и скрытия субтитров.
        //
        // ⚠️ Переключаем состояние САМОГО Artplayer (`art.subtitle.show`), а не
        // зовём `plugin.show()/hide()`. Плагин синхронизирует свою видимость с
        // плеером — в его коде прямо стоит `this.visible = this.art.subtitle.show`,
        // — и прямые вызовы плагина плеер обратно перетирал. Именно поэтому
        // кнопка «нажимается, а субтитры не прячутся».
        if (subId) {
          art.controls.add({
            name: 'subs-toggle',
            position: 'right',
            html: t('player.subsOn'),
            tooltip: t('player.subsToggle'),
            click(component) {
              art.subtitle.show = !art.subtitle.show
              const control = (component as unknown as { $control?: HTMLElement }).$control
              if (control) {
                control.innerHTML = art.subtitle.show
                  ? t('player.subsOn')
                  : t('player.subsOff')
              }
            },
          })
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message)
      }
    }

    void mount()
    return () => {
      cancelled = true
      artRef.current?.destroy(true)
      artRef.current = null
    }
    // streamHeights в зависимостях: ступени приходят отдельным запросом и
    // обычно позже создания плеера. Без пересоздания меню качества не
    // появилось бы до переключения дорожки.
  }, [opened, videoId, subId, heightsKey, t])

  // Параметр намеренно не `t`: так называется функция перевода, и затенение
  // её внутри JSX читается как ошибка, даже когда работает.
  const describe = (item: EpisodeTranslation) =>
    `${langCode(item.lang)} · ${item.authors}` +
    (item.height ? ` · ${Math.min(item.height, 1080)}p` : '')

  /**
   * Раскладывает переводы по группам «вид · язык».
   *
   * Плоский список из двух десятков строк читать невозможно: озвучки, равки и
   * субтитры идут вперемешку. Mantine рисует между группами заголовок, и он же
   * служит той самой чертой.
   *
   * Внутри группы — по убыванию качества. Оно оценочное (высота исходника, а
   * не реальная ступень), но для сравнения дорожек между собой годится: выбрать
   * из двух озвучек ту, что лучше, — ровно та задача, где нужен порядок.
   */
  const grouped = (items: EpisodeTranslation[]) => {
    const groups = new Map<string, EpisodeTranslation[]>()
    for (const item of items) {
      const key = `${kindLabel(item.kind)} · ${langCode(item.lang)}`
      groups.set(key, [...(groups.get(key) ?? []), item])
    }
    return [...groups.entries()].map(([group, list]) => ({
      group,
      items: list
        .slice()
        .sort((a, b) => (b.height ?? 0) - (a.height ?? 0))
        .map((item) => ({ value: String(item.id), label: describe(item) })),
    }))
  }

  return (
    <Modal opened={opened} onClose={onClose} title={t('player.title', { episode: episodeLabel })} size="xl">
      <Stack gap="sm">
        {playable.length === 0 ? (
          <Alert color="yellow" icon={<IconAlertTriangle size={18} />}>
            {t('player.noPlayable')}
          </Alert>
        ) : (
          <>
            {/* Artplayer монтируется внутрь и держит своё соотношение сторон.
                Высоту задаём здесь: без неё пустой контейнер схлопывается,
                и плееру некуда встать. */}
            <div
              ref={containerRef}
              style={{
                width: '100%',
                aspectRatio: '16 / 9',
                maxHeight: '65vh',
                background: '#000',
                borderRadius: 6,
                overflow: 'hidden',
              }}
            />

            {error && <Alert color="red">{error}</Alert>}

            {/* На 375px два селектора рядом сжимаются до «RU · StudioBand · 10»,
                и выбранное не прочитать. Раскладываем их в столбик. */}
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
              <Select
                label={t('player.track')}
                data={grouped(playable)}
                value={videoId}
                onChange={setVideoId}
                allowDeselect={false}
              />
              <Select
                label={t('player.subtitles')}
                placeholder={t('player.noSubtitles')}
                data={grouped(subtitleOptions)}
                value={subId}
                onChange={(value) => {
                  setSubTouched(true)
                  setSubId(value)
                  // Выбрали субтитры — переключаем и видео на тот же перевод,
                  // если он есть среди дорожек. Перевод с субтитрами это и
                  // есть видео с японской дорожкой, так что чаще всего он там
                  // и лежит; без этого человек выбирал субтитры, а смотрел
                  // чужую озвучку, поверх которой они не совпадают по таймингу.
                  if (value && playable.some((item) => String(item.id) === value)) {
                    setVideoId(value)
                  }
                }}
                clearable
              />
            </SimpleGrid>

            <Group gap={6}>
              {playable.slice(0, 6).map((item) => (
                <Badge key={item.id} size="xs" variant="light" color={LANG_COLORS[item.lang]}>
                  {langCode(item.lang)}
                </Badge>
              ))}
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  )
}
