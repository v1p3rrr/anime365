import { Badge, Skeleton, Text } from '@mantine/core'
import { useState } from 'react'
import { IconStar } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'

import type { CatalogCard as CardData } from '../api/client'
import classes from './CatalogCard.module.css'
import { useSeriesTypeLabel } from '../i18n/seriesType'
import { useT } from '../i18n'
import { Flag } from './Flag'
import { PosterPlaceholder } from './PosterPlaceholder'
import { useFormat } from '../i18n/format'
import { useTitleName } from '../i18n/titles'

interface Props {
  card?: CardData
  /** Какие значки показывать — настройка пользователя, хранится в браузере. */
  visibleBadges?: BadgeKey[]
}

export type BadgeKey = 'video' | 'sub_ru' | 'sub_ja' | 'sub_en'

/** Подписи значков. Языковые коды не переводятся — они одинаковы везде. */
export const BADGE_LABELS: Record<BadgeKey, string> = {
  video: '',
  sub_ru: 'ru',
  sub_ja: 'ja',
  sub_en: 'en',
}

/** По умолчанию только то, что просил пользователь: видео, RU- и JA-сабы. */
export const DEFAULT_BADGES: BadgeKey[] = ['video', 'sub_ru', 'sub_ja']

function coverageColor(have: number, total: number): string {
  if (total <= 0) return 'gray'
  if (have >= total) return 'teal'
  if (have === 0) return 'gray'
  return 'yellow'
}

export function CatalogCard({ card, visibleBadges = DEFAULT_BADGES }: Props) {
  const navigate = useNavigate()
  const { t } = useT()
  const seriesTypeLabel = useSeriesTypeLabel()
  const format = useFormat()
  const titleName = useTitleName()
  const [posterFailed, setPosterFailed] = useState(false)

  if (!card) {
    // Заглушка ровно тех же размеров: иначе при подгрузке окна сетка дёргается.
    return (
      <div className={classes.card}>
        <div className={classes.posterWrap}>
          <Skeleton height="100%" radius={0} />
        </div>
        <div className={classes.body}>
          <Skeleton height={12} mt={4} />
          <Skeleton height={10} mt={6} width="60%" />
        </div>
      </div>
    )
  }

  const primary = titleName.primary(card, `#${card.id}`)
  const secondary = titleName.secondary(card)

  // Значков нет вовсе, если тайтла нет в библиотеке: пустое «0/12» на каждой
  // из 29 тысяч карточек — шум, а не информация.
  const local = card.local

  return (
    <div
      className={classes.card}
      onClick={() => navigate(`/series/${card.id}`)}
      role="link"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter') navigate(`/series/${card.id}`)
      }}
    >
      <div className={classes.posterWrap}>
        {/* Заглушка и при отсутствии ссылки, и при неудачной загрузке:
            пустой прямоугольник на месте картинки читается как поломка. */}
        {card.poster_url_small && !posterFailed ? (
          <img
            className={classes.poster}
            src={card.poster_url_small}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setPosterFailed(true)}
          />
        ) : (
          <PosterPlaceholder />
        )}

        {/* «Идёт» и «анонс» взаимоисключающи по построению признака, поэтому
            занимают один угол и никогда не наезжают друг на друга. */}
        {card.is_airing ? (
          <Badge className={classes.cornerBadge} size="xs" color="red" variant="filled">
            {t('catalog.cardOngoing')}
          </Badge>
        ) : card.announced ? (
          <Badge className={classes.cornerBadge} size="xs" color="violet" variant="filled">
            {t('catalog.cardAnnounced')}
          </Badge>
        ) : null}

        {/* Тайтл заведён в библиотеке. Это не то же самое, что «скачан»:
            папка может быть привязана и пустой, и счётчики ниже покажут нули. */}
        {local && (
          <Badge className={classes.localBadge} size="xs" color="teal" variant="filled">
            {t('catalog.badgeInLibrary')}
          </Badge>
        )}

        {(local || card.mapping_problem) && (
          <div className={classes.badgeStrip}>
            {card.mapping_problem && (
              <Badge size="xs" color="orange" variant="filled">
                {t('catalog.badgeCheckMapping')}
              </Badge>
            )}
            {visibleBadges.map((key) => {
              if (!local) return null
              const have = local[key] ?? 0
              // Знаменатель — вышедшие серии: больше скачать неоткуда, и
              // «10/11» у онгоинга честнее, чем «10/18» красным. План
              // дописывается в скобках, когда он больше вышедшего, —
              // так видно, сколько всего ждать.
              const aired = local.aired || local.total
              return (
                <Badge key={key} size="xs" variant="filled" color={coverageColor(have, aired)}>
                  {BADGE_LABELS[key] ? (
                    <Flag lang={BADGE_LABELS[key]} />
                  ) : (
                    t('catalog.badgeVideo')
                  )}{' '}
                  {have}/{aired}
                  {local.total > aired && `(${local.total})`}
                </Badge>
              )
            })}
            {/* Размер — в ту же полосу, а не в строку метаданных: там nowrap,
                и четвёртый элемент вытеснял бы оценку на узкой карточке. */}
            {local && local.size_bytes > 0 && (
              <Badge size="xs" variant="filled" color="dark">
                {format.size(local.size_bytes)}
              </Badge>
            )}
          </div>
        )}
      </div>

      <div className={classes.body}>
        <div className={classes.titleMain} title={primary}>
          {primary}
        </div>
        {secondary && (
          <div className={classes.titleSub} title={secondary}>
            {secondary}
          </div>
        )}

        <div className={classes.meta}>
          <span>{card.year ?? '—'}</span>
          <span className={classes.metaType}>{seriesTypeLabel(card.type, card.type_title)}</span>
          {card.number_of_episodes > 0 && (
            <span>
              {card.number_of_episodes} {t('common.episodes')}
            </span>
          )}
          {card.mal_score != null && (
            <Text component="span" size="xs" c="yellow.6" style={{ display: 'inline-flex', gap: 2 }}>
              <IconStar size={12} style={{ alignSelf: 'center' }} />
              {card.mal_score.toFixed(1)}
            </Text>
          )}
        </div>
      </div>
    </div>
  )
}
