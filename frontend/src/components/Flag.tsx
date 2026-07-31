import { Tooltip } from '@mantine/core'
import { useId } from 'react'

import { useT } from '../i18n'
import { languageName } from '../i18n/languages'

/**
 * Флаг языка, нарисованный вручную.
 *
 * Эмодзи-флаги здесь не годятся, и это не вопрос вкуса: эмодзи флага — пара
 * «региональных индикаторов», а в Windows системного шрифта для них нет.
 * Chrome и Edge показывают вместо флага две буквы (`RU`, `JP`, `GB`), и
 * выглядит это как поломка. Firefox рисует, потому что тащит свой набор, —
 * то есть картинка зависит от браузера, чего в интерфейсе быть не должно.
 *
 * Отсюда SVG прямо в коде: рисуется одинаково везде, ничего не грузится по
 * сети (а CSP внешние картинки и не пропустит) и не зависит от темы.
 *
 * Флагов всего три, поэтому инлайн дешевле любой библиотеки иконок. Для
 * остальных языков флага нет — остаётся код, и этого достаточно: языки, кроме
 * ru/ja/en, в дорожках попадаются, но редко.
 */

interface Props {
  lang: string
  /** Высота в пикселях; ширина считается по пропорции 3:2. */
  size?: number
}

export function Flag({ lang, size = 11 }: Props) {
  // Идентификатор обрезки должен быть уникальным: флагов на странице десятки,
  // а одинаковые id в документе — это невалидная разметка.
  const clipId = useId()

  const common = {
    height: size,
    width: size * 1.5,
    style: {
      display: 'inline-block',
      // По середине строки, а не по базовой линии: у SVG базовой линии нет,
      // и по умолчанию он «висит» на ней, оказываясь ниже соседнего текста
      // и галочки внутри значка.
      verticalAlign: 'middle',
      borderRadius: 1,
      // Белые полосы на светлой теме сливаются с фоном — обводим тонкой рамкой.
      boxShadow: '0 0 0 0.5px rgba(128, 128, 128, 0.55)',
      flexShrink: 0,
    },
    'aria-hidden': true,
  } as const

  if (lang === 'ru') {
    return (
      <svg {...common} viewBox="0 0 9 6">
        <rect width="9" height="2" fill="#ffffff" />
        <rect y="2" width="9" height="2" fill="#0039a6" />
        <rect y="4" width="9" height="2" fill="#d52b1e" />
      </svg>
    )
  }

  if (lang === 'ja') {
    return (
      <svg {...common} viewBox="0 0 9 6">
        <rect width="9" height="6" fill="#ffffff" />
        <circle cx="4.5" cy="3" r="1.8" fill="#bc002d" />
      </svg>
    )
  }

  if (lang === 'en') {
    // Union Jack. Диагонали рисуются дважды: белые во всю ширину, поверх них
    // красные тоньше и с обрезкой по половинкам — иначе получится симметричный
    // крест, а у настоящего флага диагонали смещены.
    return (
      <svg {...common} viewBox="0 0 60 30">
        <clipPath id={clipId}>
          <path d="M30,15 h30 v15 z v15 h-30 z h-30 v-15 z v-15 h30 z" />
        </clipPath>
        <rect width="60" height="30" fill="#00247d" />
        <path d="M0,0 L60,30 M60,0 L0,30" stroke="#ffffff" strokeWidth="6" />
        <path
          d="M0,0 L60,30 M60,0 L0,30"
          clipPath={`url(#${clipId})`}
          stroke="#cf142b"
          strokeWidth="4"
        />
        <path d="M30,0 v30 M0,15 h60" stroke="#ffffff" strokeWidth="10" />
        <path d="M30,0 v30 M0,15 h60" stroke="#cf142b" strokeWidth="6" />
      </svg>
    )
  }

  return null
}

/**
 * Флаг и код языка рядом.
 *
 * Код оставлен намеренно: флаг узнаётся быстрее, но именно код отвечает на
 * вопрос «а это точно японский, а не японские субтитры к чему-то другому» —
 * и он же остаётся, если флаг почему-то не нарисуется.
 */
export function LangTag({ lang, size }: Props) {
  const { locale } = useT()
  const name = languageName(lang, locale)

  const tag = (
    <span>
      <Flag lang={lang} size={size} /> {lang.toUpperCase()}
    </span>
  )

  // Без подсказки код вроде HAW или FR ни о чём не говорит. Раньше это было
  // неважно — языков было ровно три; теперь приведение идёт через ISO-справочник
  // и в дорожках может оказаться что угодно.
  return name ? <Tooltip label={name}>{tag}</Tooltip> : tag
}

