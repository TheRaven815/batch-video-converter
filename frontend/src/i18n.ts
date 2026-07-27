export type AppLanguage = 'en' | 'tr';

const messages = {
  en: {
    dashboard: 'Dashboard',
    convert: 'Convert',
    presets: 'Presets',
    settings: 'Settings',
    refresh: 'Refresh',
    signOut: 'Sign out',
  },
  tr: {
    dashboard: 'Panel',
    convert: 'Dönüştür',
    presets: 'Profiller',
    settings: 'Ayarlar',
    refresh: 'Yenile',
    signOut: 'Çıkış',
  },
} as const;

export type MessageKey = keyof (typeof messages)['en'];

export function translate(language: AppLanguage, key: MessageKey): string {
  return messages[language][key];
}

export function loadLanguage(): AppLanguage {
  return localStorage.getItem('video-converter-language') === 'tr' ? 'tr' : 'en';
}
