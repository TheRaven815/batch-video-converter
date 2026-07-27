import { SettingsPanel } from '../components/SettingsPanel';
import { useAppContext } from '../context/AppContext';
import { useI18n } from '../i18n';

export default function SettingsPage() {
  const { showToast } = useAppContext();
  const { t } = useI18n();
  return (
    <div className="form-container settings-page">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-50">{t('settings.title')}</h1>
        <p className="text-xs text-zinc-400 mt-1">{t('settings.subtitle')}</p>
      </div>
      <div className="form-panel">
        <SettingsPanel showToast={showToast} />
      </div>
    </div>
  );
}
