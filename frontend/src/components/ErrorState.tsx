import { Alert, Button, Code, Group, Text } from '@mantine/core'
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react'

import { ApiError } from '../api/client'
import { useT, type TranslationKey } from '../i18n'

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
  const { t } = useT()
  const { heading, hint } = describe(error, t, title)
  const detail = error instanceof Error ? error.message : String(error)

  if (compact) {
    return (
      <Text size="xs" c="red" style={{ wordBreak: 'break-word' }}>
        {heading}
        {hint && ` — ${hint}`}
        {onRetry && (
          <Button size="compact-xs" variant="subtle" ml={6} onClick={onRetry}>
            {t('errorCodes.retryShort')}
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
            {t('common.retry')}
          </Button>
        </Group>
      )}
    </Alert>
  )
}

/** Превращает код ответа в понятную причину и совет. */
function describe(
  error: unknown,
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string,
  fallbackTitle?: string,
): { heading: string; hint?: string } {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return { heading: t('errorCodes.e401'), hint: t('errorCodes.e401hint') }
      case 403:
        return { heading: t('errorCodes.e403'), hint: t('errorCodes.e403hint') }
      case 404:
        return { heading: t('errorCodes.e404'), hint: t('errorCodes.e404hint') }
      case 409:
        return { heading: t('errorCodes.e409'), hint: t('errorCodes.e409hint') }
      case 429:
        return { heading: t('errorCodes.e429'), hint: t('errorCodes.e429hint') }
      case 502:
      case 503:
        return { heading: t('errorCodes.e502'), hint: t('errorCodes.e502hint') }
      default:
        return { heading: fallbackTitle ?? t('errorCodes.generic', { status: error.status }) }
    }
  }

  if (error instanceof TypeError) {
    return { heading: t('errorCodes.offline'), hint: t('errorCodes.offlineHint') }
  }

  return { heading: fallbackTitle ?? t('errors.title') }
}
