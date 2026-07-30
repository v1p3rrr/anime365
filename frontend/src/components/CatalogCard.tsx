import { Badge, Skeleton, Text } from '@mantine/core'
import { IconStar } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'

import type { CatalogCard as CardData } from '../api/client'
import classes from './CatalogCard.module.css'

interface Props {
  card?: CardData
  /** Какие значки показывать — настройка пользователя, хранится в браузере. */
  visibleBadges?: BadgeKey[]
}

export type BadgeKey = 'video' | 'sub_ru' | 'sub_ja' | 'sub_en'

export const BADGE_LABELS: Record<BadgeKey, string> = {
  video: 'Видео',
  sub_ru: 'RU',
  sub_ja: 'JA',
  sub_en: 'EN',
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

  const primary = card.title_ru || card.title_romaji || `#${card.id}`
  const secondary = card.title_ru ? card.title_romaji : null

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
        {card.poster_url_small ? (
          <img
            className={classes.poster}
            src={card.poster_url_small}
            alt=""
            loading="lazy"
            decoding="async"
          />
        ) : null}

        {card.is_airing && (
          <Badge className={classes.cornerBadge} size="xs" color="red" variant="filled">
            онгоинг
          </Badge>
        )}

        {(local || card.mapping_problem) && (
          <div className={classes.badgeStrip}>
            {card.mapping_problem && (
              <Badge size="xs" color="orange" variant="filled">
                проверить привязку
              </Badge>
            )}
            {visibleBadges.map((key) => {
              if (!local) return null
              const have = local[key] ?? 0
              // Знаменатель приходит с сервера уже по правилу
              // «максимум из плана, эпизодов в API и локального номера».
              return (
                <Badge
                  key={key}
                  size="xs"
                  variant="filled"
                  color={coverageColor(have, local.total)}
                >
                  {BADGE_LABELS[key]} {have}/{local.total}
                </Badge>
              )
            })}
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
          <span className={classes.metaType}>{card.type_title}</span>
          {card.number_of_episodes > 0 && <span>{card.number_of_episodes} эп.</span>}
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
