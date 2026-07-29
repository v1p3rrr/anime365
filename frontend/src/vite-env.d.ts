/// <reference types="vite/client" />

// Без этой ссылки TypeScript не знает про импорты *.module.css
// и сборка падает на первом же `import classes from './X.module.css'`.
