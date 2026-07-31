import { Alert, Badge, Group, Modal, Select, SimpleGrid, Stack } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'

import { LANG_COLORS, type EpisodeTranslation } from '../api/title'
import { useT } from '../i18n'
// Подписи вариантов в Select — строки, флаг-компонент туда не вставить.
import { langCode } from '../i18n/flags'

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
  }, [opened, videoId, subId, t])

  // Параметр намеренно не `t`: так называется функция перевода, и затенение
  // её внутри JSX читается как ошибка, даже когда работает.
  const describe = (item: EpisodeTranslation) =>
    `${langCode(item.lang)} · ${item.authors}` +
    (item.height ? ` · ${Math.min(item.height, 1080)}p` : '')

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
                data={playable.map((item) => ({ value: String(item.id), label: describe(item) }))}
                value={videoId}
                onChange={setVideoId}
                allowDeselect={false}
              />
              <Select
                label={t('player.subtitles')}
                placeholder={t('player.noSubtitles')}
                data={subtitleOptions.map((item) => ({
                  value: String(item.id),
                  label: describe(item),
                }))}
                value={subId}
                onChange={(value) => {
                  setSubTouched(true)
                  setSubId(value)
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
