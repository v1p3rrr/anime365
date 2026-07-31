import { Alert, Anchor, Group, Text, Tooltip } from '@mantine/core'
import { IconFolderSymlink } from '@tabler/icons-react'

import type { EpisodeTarget } from '../api/library'
import { useT } from '../i18n'
import pathClasses from './PathLine.module.css'

interface Props {
  targets: EpisodeTarget[]
  onManage: () => void
}

/**
 * Предупреждение о сериях с отдельной папкой.
 *
 * Нужно именно на странице тайтла, а не в отдельном экране: батч «все серии»
 * разложит такие серии не туда, куда остальные, и без этой плашки понять это
 * можно будет только по факту — когда файлы уже лежат.
 *
 * Серии перечислены до трёх, дальше счётчиком. Полный список из двадцати
 * номеров в плашке нечитаем, а первые три отвечают на главный вопрос: «это то,
 * что я сам недавно настроил, или неожиданность?».
 */
const SHOWN_EPISODES = 3

export function EpisodeTargetsAlert({ targets, onManage }: Props) {
  const { t } = useT()

  if (targets.length === 0) return null

  const sorted = [...targets].sort((a, b) => a.episode - b.episode)
  const shown = sorted.slice(0, SHOWN_EPISODES)
  const rest = sorted.length - shown.length

  const episodes = shown.map((target) => target.episode).join(', ')
  const summary =
    rest > 0
      ? t('episodeTarget.summaryMore', { episodes, count: rest })
      : t('episodeTarget.summary', { episodes })

  // Том только для чтения — отдельная беда: назначение есть, а загрузка в него
  // упадёт. Сказать об этом надо здесь, а не в момент падения задачи.
  const readOnly = sorted.filter((target) => !target.writable)

  return (
    <Alert
      color={readOnly.length > 0 ? 'red' : 'yellow'}
      icon={<IconFolderSymlink size={18} />}
      style={{ flexShrink: 0 }}
    >
      <Text size="sm">{summary}</Text>

      <Group gap={4} mt={4}>
        {shown.map((target) => (
          <Tooltip key={target.id} label={target.full_path} multiline w={280}>
            <Text size="xs" c="dimmed" className={pathClasses.pathLine}>
              {t('episodeTarget.episodeLine', { episode: target.episode })} → {target.rel_path}
            </Text>
          </Tooltip>
        ))}
      </Group>

      {readOnly.length > 0 && (
        <Text size="xs" c="red" mt={4}>
          {t('episodeTarget.readOnly', { count: readOnly.length })}
        </Text>
      )}

      <Anchor component="button" type="button" size="xs" mt={6} onClick={onManage}>
        {t('episodeTarget.manage')}
      </Anchor>
    </Alert>
  )
}
