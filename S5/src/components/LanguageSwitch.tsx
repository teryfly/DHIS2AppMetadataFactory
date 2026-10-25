import { useTranslation } from 'react-i18next';
import { useLanguageStore, type UiLanguage } from '@/stores/language';

/** UI-APP-002: user-switchable 简体中文 / English; drives i18next and the Accept-Language header. */
export function LanguageSwitch() {
  const { i18n, t } = useTranslation();
  const language = useLanguageStore((s) => s.language);
  const setLanguage = useLanguageStore((s) => s.setLanguage);

  const toggle = () => {
    const next: UiLanguage = language === 'zh-CN' ? 'en' : 'zh-CN';
    setLanguage(next);
    void i18n.changeLanguage(next);
  };

  return (
    <button type="button" className="topbar__action" onClick={toggle} aria-label={t('app.switchLanguage')} style={{ background: 'transparent', border: '1px solid rgba(255,255,255,.6)', color: '#fff', borderRadius: 4, padding: '4px 10px', cursor: 'pointer' }}>
      {language === 'zh-CN' ? 'EN' : '中'}
    </button>
  );
}
