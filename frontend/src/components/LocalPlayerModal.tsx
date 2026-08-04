import { Alert, Modal, Select, SimpleGrid, Stack, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { api } from '../api/client'
import { type LocalVideo } from '../api/title'
import { useT } from '../i18n'
import { useFormat } from '../i18n/format'
import { langCode } from '../i18n/flags'

interface Props {
  opened: boolean
  onClose: () => void
  /** Скачанные файлы этой серии. Их бывает несколько: озвучка и оригинал. */
  videos: LocalVideo[]
  episodeLabel: string
}

interface SubtitleTrack {
  id: string
  /** `file` — соседний файл, `embedded` — дорожка внутри контейнера. */
  source: 'file' | 'embedded'
  lang: string | null
  label: string
  url: string
}

/** Стабильные пути к libass: файлы кладёт `scripts/copy-libass.mjs`. */
const LIBASS_WORKER = '/libass/subtitles-octopus-worker.js'
const LIBASS_LEGACY_WORKER = '/libass/subtitles-octopus-worker-legacy.js'

/**
 * Просмотр того, что уже скачано.
 *
 * ## Почему здесь Artplayer, а не браузерный `<video>`
 *
 * Раньше стоял обычный `<video controls>`, и обоснование было такое: «локальный
 * файл содержит всё внутри себя, браузер справится сам». Проверка на реальной
 * библиотеке это опровергла, причём дважды:
 *
 * * **соседние файлы субтитров браузер не подхватывает вообще.** У пользователя
 *   они лежат именно так — `… .ja.srt` рядом с `… .mkv`, так размечает
 *   библиотеку Jellyfin. Без `<track>` их не видно, а `<track>` понимает только
 *   VTT — то есть ASS с оформлением всё равно мимо;
 * * **дорожки внутри контейнера браузер не показывает.** Ни одна распространённая
 *   сборка не рисует субтитры из mkv: контейнер он разбирает, а субтитровый
 *   поток игнорирует.
 *
 * Значит выбор дорожек нужен здесь ровно так же, как в онлайн-плеере, а рисовать
 * ASS должен libass. Раз обвязка всё равно нужна, разница между двумя плеерами
 * исчезла — и остался один, тот же самый.
 *
 * ⚠️ Браузер по-прежнему играет не всякий файл: `.mkv` с H.264 и AAC обычно
 * идёт, с HEVC или FLAC — уже нет, и зависит это от браузера. Перекодировать на
 * лету не станем: на слабом домашнем сервере это съело бы процессор ради одной
 * вкладки. При отказе честно говорим, что дело в формате, а не в файле.
 */
export function LocalPlayerModal({ opened, onClose, videos, episodeLabel }: Props) {
  const { t } = useT()
  const format = useFormat()
  const containerRef = useRef<HTMLDivElement>(null)
  const artRef = useRef<{ destroy: (removeHtml?: boolean) => void } | null>(null)
  const [fileId, setFileId] = useState<string | null>(null)
  const [subId, setSubId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Сбрасываем выбор при открытии: серия могла смениться, а состояние остаться
  // от прошлой.
  useEffect(() => {
    if (opened) {
      setFileId(videos.length > 0 ? String(videos[0].file_id) : null)
      setSubId(null)
      setError(null)
    }
  }, [opened, videos])

  // Дорожки спрашиваем на открытии плеера, а не вместе со страницей тайтла:
  // за вшитыми ходит ffprobe, и обходить им все серии ради списка, нужного для
  // одной, — это часы работы диска впустую.
  const tracks = useQuery({
    queryKey: ['local-subtitle-tracks', fileId],
    queryFn: () =>
      api.get<{ tracks: SubtitleTrack[]; ffmpeg: boolean }>(
        `/stream/local/${fileId}/subtitle-tracks`,
      ),
    enabled: opened && fileId !== null,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const available = tracks.data?.tracks ?? []
  const selected = available.find((track) => track.id === subId) ?? null

  // Первую дорожку подставляем сами: человек нажал «смотреть скачанное» и
  // ожидает увидеть субтитры, если они есть, а не искать их во втором списке.
  // Соседние файлы идут раньше вшитых — они бесплатны и появляются сразу.
  useEffect(() => {
    if (!opened || subId !== null || available.length === 0) return
    const preferred = available.find((track) => track.source === 'file') ?? available[0]
    setSubId(preferred.id)
  }, [opened, available, subId])

  useEffect(() => {
    let cancelled = false

    async function mount() {
      artRef.current?.destroy(true)
      artRef.current = null
      setError(null)

      if (!opened || !fileId || !containerRef.current) return

      try {
        const [{ default: Artplayer }, { default: libassPlugin }] = await Promise.all([
          import('artplayer'),
          import('artplayer-plugin-libass'),
        ])
        if (cancelled || !containerRef.current) return

        // ASS рисует libass; SRT Artplayer переводит в VTT сам, и оформления
        // там всё равно нет — терять нечего.
        const isAss = Boolean(selected && selected.url.toLowerCase().endsWith('.ass'))
        const plugins = isAss
          ? [libassPlugin({ workerUrl: LIBASS_WORKER, legacyWorkerUrl: LIBASS_LEGACY_WORKER })]
          : []

        const art = new Artplayer({
          container: containerRef.current,
          url: `/api/stream/local/${fileId}`,
          // ⚠️ Ключ `subtitle` либо есть объектом, либо его нет вовсе:
          // `subtitle: undefined` Artplayer не принимает и падает целиком.
          ...(selected
            ? {
                subtitle: {
                  url: selected.url,
                  type: selected.url.toLowerCase().endsWith('.ass') ? 'ass' : 'srt',
                },
              }
            : {}),
          volume: 1,
          autoplay: true,
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

        // Кодек внутри контейнера браузер может не потянуть — тогда плеер
        // сообщает об ошибке, и молчать об этом нельзя.
        art.on('error', () => {
          if (!cancelled) setError(t('episode.playLocalUnsupported'))
        })

        // Кнопка показа и скрытия субтитров. Переключаем состояние самого
        // Artplayer: плагин синхронизирует свою видимость с плеером, и прямые
        // вызовы плагина плеер перетирает обратно.
        if (selected) {
          art.controls.add({
            name: 'subs-toggle',
            position: 'right',
            html: t('player.subsOn'),
            tooltip: t('player.subsToggle'),
            click(component) {
              art.subtitle.show = !art.subtitle.show
              const control = (component as unknown as { $control?: HTMLElement }).$control
              if (control) {
                control.innerHTML = art.subtitle.show ? t('player.subsOn') : t('player.subsOff')
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
  }, [opened, fileId, selected, t])

  const videoOptions = videos.map((video) => ({
    value: String(video.file_id),
    label:
      (video.langs.length > 0 ? `${video.langs.map(langCode).join(', ')} · ` : '') +
      video.filename +
      (video.size_bytes ? ` · ${format.size(video.size_bytes)}` : ''),
  }))

  // Источник называем вслух: соседний файл открывается мгновенно, а вшитую
  // дорожку сначала вырезает ffmpeg, и первое ожидание надо объяснить заранее.
  const subtitleOptions = available.map((track) => ({
    value: track.id,
    label:
      (track.lang ? `${langCode(track.lang)} · ` : '') +
      track.label +
      (track.source === 'embedded' ? ` · ${t('episode.subsEmbedded')}` : ''),
  }))

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t('episode.playLocalTitle', { episode: episodeLabel })}
      size="xl"
      centered
    >
      <Stack gap="sm">
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

        {error && (
          <Alert color="yellow">
            <Text size="sm">{error}</Text>
          </Alert>
        )}

        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
          {/* Выбор файла показываем только когда есть из чего выбирать: серия
              лежит в двух копиях далеко не всегда. */}
          {videoOptions.length > 1 && (
            <Select
              data={videoOptions}
              value={fileId}
              onChange={(value) => {
                setFileId(value)
                // Дорожки принадлежат конкретному файлу: у соседней копии свои
                // соседние субтитры и свои вшитые.
                setSubId(null)
                setError(null)
              }}
              label={t('episode.playLocalFile')}
              allowDeselect={false}
            />
          )}

          <Select
            label={t('player.subtitles')}
            placeholder={
              tracks.isLoading ? t('common.loading') : t('episode.subsNoneLocal')
            }
            data={subtitleOptions}
            value={subId}
            onChange={setSubId}
            disabled={subtitleOptions.length === 0}
            clearable
          />
        </SimpleGrid>

        {/* Вшитые дорожки без ffmpeg не достать — сказать об этом надо там же,
            где их ждут, а не оставлять список загадочно коротким. */}
        {tracks.data && !tracks.data.ffmpeg && (
          <Text size="xs" c="dimmed">
            {t('episode.subsNoFfmpeg')}
          </Text>
        )}
      </Stack>
    </Modal>
  )
}
