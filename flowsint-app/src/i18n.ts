import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import ru from './locales/ru.json'

// Natural keys: the English source string is the key, so a missing translation falls back to English.
void i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru } },
  lng: 'ru',
  fallbackLng: false,
  keySeparator: false,
  nsSeparator: false,
  interpolation: { escapeValue: false },
  returnEmptyString: false,
  initImmediate: false
})

export const t = i18n.t.bind(i18n)
export default i18n

const LOCALIZED_KEYS = new Set(['label', 'description', 'placeholder'])

// Backend schemas (entity types, fields, options) arrive in English: translate their display strings.
export function localizeSchema<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map(localizeSchema) as T
  }
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [
        k,
        LOCALIZED_KEYS.has(k) && typeof v === 'string' ? t(v) : localizeSchema(v)
      ])
    ) as T
  }
  return value
}
