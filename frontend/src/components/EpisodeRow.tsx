import { Badge, Button, Collapse, Group, Table, Text, Tooltip } from '@mantine/core'
import {
  IconCheck,
  IconChevronDown,
  IconDownload,
  IconExternalLink,
  IconFolderSymlink,
  IconLink,
  IconDeviceDesktop,
  IconPlayerPlay,
} from '@tabler/icons-react'
import { useState } from 'react'

import type { EpisodeTarget } from '../api/library'
import { type EpisodeItem } from '../api/title'
import { usePermissions } from '../hooks/usePermissions'
import { useT } from '../i18n'
import { languageList } from '../i18n/languages'
import { useFormat } from '../i18n/format'
import { Flag } from './Flag'
import { isTypeVisible, type TranslationType } from '../stores/preferences'
import classes from './EpisodeRow.module.css'
import { BindEpisodeModal } from './BindEpisodeModal'
import { EpisodeDownloadPanel } from './EpisodeDownloadPanel'
import { LocalPlayerModal } from './LocalPlayerModal'
import { PlayerModal } from './PlayerModal'

interface Props {
  episode: EpisodeItem
  /** Своя папка назначения у этой серии, если её назначили. */
  target?: EpisodeTarget
  /** Включённые типы переводов — общая настройка отображения. */
  types: TranslationType[]
  seriesUrl: string | null
  /** Открыть окно назначения папки для этой серии. */
  onAssignFolder?: (episode: number) => void
  seriesId: number
}

/** Что показываем как «есть локально». Порядок фиксирован для читаемости. */
/** Языковые коды не переводятся, «видео» — переводится. */
const ROLE_BADGES: Array<{ role: string; label: string; color: string }> = [
  { role: 'video', label: '', color: 'teal' },
  { role: 'sub_ru', label: 'ru', color: 'blue' },
  { role: 'sub_ja', label: 'ja', color: 'teal' },
  { role: 'sub_en', label: 'en', color: 'grape' },
]

/** Сколько колонок в таблице серий — на столько растягивается панель загрузки. */
const COLUMN_COUNT = 3

export function EpisodeRow({
  episode,
  types,
  target,
  seriesUrl,
  seriesId,
  onAssignFolder,
}: Props) {
  const { t, locale } = useT()
  const format = useFormat()
  // Три разных права сходятся в одной строке: скачать, посмотреть и разложить
  // по папкам. Значки наличия остаются всем — это чтение.
  const { can } = usePermissions()
  const canDownload = can('downloads')
  const canWatch = can('watch')
  const canBind = can('library')
  const [binding, setBinding] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [playingLocal, setPlayingLocal] = useState(false)
  const [expanded, setExpanded] = useState(false)
  // В подсказку идёт полная фраза, в сам значок — флаг. Один флаг не говорит,
  // о чём речь: у серии бывают и субтитры этого языка, и звуковая дорожка.
  const badgeLabel = (badge: { label: string }) =>
    badge.label
      ? `${t('kind.sub')} ${badge.label.toUpperCase()}`
      : t('catalog.badgeVideo')

  const available = episode.translations.filter((item) =>
    isTypeVisible(item.kind, item.lang, types),
  )

  // Значок субтитров показываем только там, где включён соответствующий тип:
  // иначе значок обещает то, чего в панели загрузки уже не найти.
  const visibleBadges = ROLE_BADGES.filter(
    (badge) => badge.role === 'video' || types.includes(badge.role as TranslationType),
  )

  return (
    <>
      <Table.Tr>
        <Table.Td>
          <Text size="sm">{episode.episode_full || episode.episode_int}</Text>
          {format.duration(episode.duration) && (
            <Text size="xs" c="dimmed">
              {format.duration(episode.duration)}
            </Text>
          )}
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

          {/* Эта серия уедет не туда, куда остальные. Значок стоит у номера, а
              не у кнопок: вопрос «куда попадёт файл» возникает раньше, чем
              вопрос «что скачать». */}
          {target && (
            <Tooltip
              label={t('episodeTarget.rowHint', { path: target.full_path })}
              multiline
              w={280}
            >
              <Badge
                size="xs"
                variant="light"
                color={target.writable ? 'yellow' : 'red'}
                leftSection={<IconFolderSymlink size={9} />}
              >
                {t('episodeTarget.rowBadge')}
              </Badge>
            </Tooltip>
          )}
        </Table.Td>

        <Table.Td>
          {/* align="center" обязателен: у значка с текстом и у голого флага
              разная высота, и по умолчанию они встают по базовой линии. */}
          <Group gap={6} align="center" wrap="nowrap">
            {visibleBadges.map((badge) => {
              const have = episode.have.includes(badge.role)
              const label = have
                ? t('episode.haveLocal', { label: badgeLabel(badge) })
                : t('episode.missing', { label: badgeLabel(badge) })

              // У значка видео к подсказке добавляем язык звуковой дорожки:
              // сам значок отвечает только «файл есть», а вопрос обычно другой —
              // «на каком языке». Дорожек в файле бывает несколько, поэтому
              // перечисляем все, а не первую попавшуюся.
              const videoLabel =
                badge.role === 'video' && have
                  ? episode.audio_langs.length > 0
                    ? `${label} · ${t('episode.audioTrack', {
                        langs: languageList(episode.audio_langs, locale),
                      })}`
                    : `${label} · ${t('episode.audioUnknown')}`
                  : label

              // Языки показываем голым флагом, без рамки: рамка вокруг картинки
              // ничего не добавляет, а выровнять её по строке с текстовым
              // значком всё равно не выходит. Отсутствие передаём обесцвечиванием
              // — форма флага остаётся узнаваемой, и ряд не скачет по ширине,
              // как скакал бы при появлении и пропаже галочки.
              if (badge.label) {
                return (
                  <Tooltip key={badge.role} label={label}>
                    <span
                      className={classes.flagMark}
                      data-missing={!have || undefined}
                      aria-label={label}
                    >
                      <Flag lang={badge.label} size={13} />
                    </span>
                  </Tooltip>
                )
              }

              return (
                <Tooltip key={badge.role} label={videoLabel}>
                  <Badge
                    size="xs"
                    variant={have ? 'filled' : 'outline'}
                    color={have ? badge.color : 'gray'}
                    leftSection={have ? <IconCheck size={9} /> : undefined}
                  >
                    {t('catalog.badgeVideo')}
                  </Badge>
                </Tooltip>
              )
            })}
          </Group>
        </Table.Td>

        <Table.Td>
          <Group gap={4} wrap="nowrap">
            {/* Разворачивается вниз, а не выпадает меню: выбор загрузки — это
                сравнение нескольких десятков вариантов, и в выпадашке поверх
                страницы такое сравнивать нечем. */}
            {canDownload && (
              <Button
                size="compact-xs"
                leftSection={<IconDownload size={12} />}
                rightSection={
                  <IconChevronDown
                    size={11}
                    style={{
                      transform: expanded ? 'rotate(180deg)' : undefined,
                      transition: 'transform 150ms ease',
                    }}
                  />
                }
                variant={expanded ? 'light' : 'filled'}
                disabled={available.length === 0}
                onClick={() => setExpanded((value) => !value)}
                aria-expanded={expanded}
              >
                {t('episode.download')}
              </Button>
            )}

            {/* Плеер идёт до ссылки на сайт: смотреть у себя — основной путь,
                а сайт остаётся запасным, если браузер не тянет кодек. */}
            {canWatch && available.length > 0 && (
              <Tooltip label={t('episode.play')}>
                <Button
                  size="compact-xs"
                  variant="light"
                  color="teal"
                  px={6}
                  onClick={() => setPlaying(true)}
                  aria-label={t('episode.play')}
                >
                  <IconPlayerPlay size={12} />
                </Button>
              </Tooltip>
            )}

            {/* Просмотр того, что уже скачано. Отдельной кнопкой, а не
                пунктом внутри плеера: у локального файла и у стрима разная
                природа — первый уже на диске и играет сразу, второй тянется с
                сайта. Смешивать их в одном списке значило бы прятать разницу,
                из-за которой человек и качал. */}
            {canWatch && episode.local_videos.length > 0 && (
              <Tooltip label={t('episode.playLocal')}>
                <Button
                  size="compact-xs"
                  variant="light"
                  color="grape"
                  px={6}
                  onClick={() => setPlayingLocal(true)}
                  aria-label={t('episode.playLocal')}
                >
                  <IconDeviceDesktop size={12} />
                </Button>
              </Tooltip>
            )}

            {/* Ссылка ведёт на страницу СЕРИИ, если её удалось вывести из
                адресов переводов, и только иначе — на страницу тайтла. Раньше
                здесь всегда был адрес тайтла, хотя подпись обещала серию. */}
            {(episode.url || seriesUrl) && (
              <Tooltip label={episode.url ? t('episode.openOnSite') : t('episode.openTitleOnSite')}>
                <Button
                  size="compact-xs"
                  variant="subtle"
                  px={6}
                  component="a"
                  href={episode.url || seriesUrl || undefined}
                  target="_blank"
                  rel="noreferrer noopener"
                  aria-label={t('episode.openOnSite')}
                >
                  <IconExternalLink size={12} />
                </Button>
              </Tooltip>
            )}

            {/* Куда положить то, чего ещё нет. Не путать с привязкой к файлу
                ниже: та связывает серию с уже лежащим на диске. */}
            {canBind && episode.episode_num != null && onAssignFolder && (
              <Tooltip label={t('episode.assignFolder')}>
                <Button
                  size="compact-xs"
                  variant="subtle"
                  px={6}
                  color={target ? 'yellow' : undefined}
                  onClick={() => onAssignFolder(episode.episode_num as number)}
                  aria-label={t('episode.assignFolder')}
                >
                  <IconFolderSymlink size={12} />
                </Button>
              </Tooltip>
            )}

            {/* Разбор имени ошибается на сдвинутой нумерации и на файлах без
                номера — тогда серию привязывают к файлу руками. */}
            {canBind && episode.episode_num != null && (
              <Tooltip label={t('episode.bind')}>
                <Button
                  size="compact-xs"
                  variant="subtle"
                  color="gray"
                  px={6}
                  onClick={() => setBinding(true)}
                  aria-label={t('episode.bind')}
                >
                  <IconLink size={12} />
                </Button>
              </Tooltip>
            )}
          </Group>

          <LocalPlayerModal
            opened={playingLocal}
            onClose={() => setPlayingLocal(false)}
            videos={episode.local_videos}
            episodeLabel={episode.episode_full || episode.episode_int}
          />

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

      {/* Отдельная строка на всю ширину, а не блок внутри ячейки: иначе панель
          жила бы в колонке кнопок и была бы шириной с неё. Строка есть всегда,
          но пустая и схлопнутая — так раскрытие анимируется, а не мигает. */}
      <Table.Tr>
        <Table.Td colSpan={COLUMN_COUNT} className={classes.panelCell}>
          <Collapse in={expanded}>
            <div className={classes.panelInner}>
              {/* Содержимое монтируется только раскрытым: у тайтла на 300 серий
                  панели всех строк сразу — это тысячи узлов DOM впустую. */}
              {expanded && <EpisodeDownloadPanel episode={episode} types={types} />}
            </div>
          </Collapse>
        </Table.Td>
      </Table.Tr>
    </>
  )
}
