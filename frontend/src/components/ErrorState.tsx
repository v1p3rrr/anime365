import { Alert, Button, Code, Group, Text } from '@mantine/core'
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react'

import { ApiError } from '../api/client'

interface Props {
  error: unknown
  title?: string
  onRetry?: () => void
  compact?: boolean
}

/**
 * Единый показ ошибок.
 *
 * Молчаливый отказ — худший вариант: пользователь жмёт кнопку, ничего не происходит,
 * и непонятно, сломано оно или просто не готово. Поэтому у каждого сбоя есть текст
 * и, где это уместно, подсказка что делать.
 */
export function ErrorState({ error, title, onRetry, compact = false }: Props) {
  const { heading, hint } = describe(error, title)
  const detail = error instanceof Error ? error.message : String(error)

  if (compact) {
    return (
      <Text size="xs" c="red" style={{ wordBreak: 'break-word' }}>
        {heading}
        {hint && ` — ${hint}`}
        {onRetry && (
          <Button size="compact-xs" variant="subtle" ml={6} onClick={onRetry}>
            ещё раз
          </Button>
        )}
      </Text>
    )
  }

  return (
    <Alert color="red" title={heading} icon={<IconAlertTriangle size={18} />}>
      {hint && (
        <Text size="sm" mb={6}>
          {hint}
        </Text>
      )}
      <Code block style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {detail}
      </Code>
      {onRetry && (
        <Group mt="sm">
          <Button size="xs" leftSection={<IconRefresh size={14} />} onClick={onRetry}>
            Повторить
          </Button>
        </Group>
      )}
    </Alert>
  )
}

/** Превращает код ответа в понятную причину и совет. */
function describe(error: unknown, fallbackTitle?: string): { heading: string; hint?: string } {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return { heading: 'Требуется вход', hint: 'Сессия истекла — войдите заново.' }
      case 403:
        return {
          heading: 'Нет доступа к видео и субтитрам',
          hint:
            'Этот запрос требует access_token от аккаунта с активной премиум-подпиской. ' +
            'Задайте токен в настройках и проверьте, что подписка не истекла.',
        }
      case 404:
        return { heading: 'Не найдено', hint: 'Запись отсутствует в каталоге.' }
      case 409:
        return { heading: 'Операция уже выполняется', hint: 'Дождитесь завершения.' }
      case 429:
        return { heading: 'Слишком много запросов', hint: 'Подождите немного и повторите.' }
      case 502:
      case 503:
        return {
          heading: 'Anime365 недоступен',
          hint: 'Сайт не отвечает или зеркало заблокировано. Попробуйте сменить зеркало в настройках.',
        }
      default:
        return { heading: fallbackTitle ?? `Ошибка ${error.status}` }
    }
  }

  if (error instanceof TypeError) {
    return { heading: 'Нет связи с сервером', hint: 'Проверьте, запущено ли приложение.' }
  }

  return { heading: fallbackTitle ?? 'Что-то пошло не так' }
}
