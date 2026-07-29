import { Badge, Skeleton, Text } from '@mantine/core'
import { IconStar } from '@tabler/icons-react'
import { useNavigate } from 'react-router-dom'

import type { CatalogCard as CardData } from '../api/client'
import classes from './CatalogCard.module.css'

/**
 * Значки наличия локальных файлов. Появятся на этапе 4, когда заработает маппинг;
 * структура заложена сейчас, чтобы вёрстка сразу проверялась на максимуме элементов.
 */
export interface LocalBadges {
  video?: { have: number; total: number }
  subRu?: { have: number; total: number }
  subJa?: { have: number; total: number }
  subEn?: { have: number; total: number }
  voice?: { have: number; total: number }
  mappingProblem?: boolean
}

interface Props {
  card?: CardData
  badges?: LocalBadges
}

function coverageColor(have: number, total: number): string {
  if (total <= 0) return 'gray'
  if (have >= total) return 'teal'
  if (have === 0) return 'gray'
  return 'yellow'
}

export function CatalogCard({ card, badges }: Props) {
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

  const coverage: Array<[string, { have: number; total: number } | undefined]> = [
    ['Видео', badges?.video],
    ['RU', badges?.subRu],
    ['JA', badges?.subJa],
    ['EN', badges?.subEn],
    ['Озв', badges?.voice],
  ]

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

        <div className={classes.badgeStrip}>
          {badges?.mappingProblem && (
            <Badge size="xs" color="orange" variant="filled">
              маппинг
            </Badge>
          )}
          {coverage.map(([label, value]) =>
            value ? (
              <Badge
                key={label}
                size="xs"
                variant="filled"
                color={coverageColor(value.have, value.total)}
              >
                {label} {value.have}/{value.total}
              </Badge>
            ) : null,
          )}
        </div>
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
