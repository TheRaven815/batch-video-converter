import { SettingsPanel } from '../components/SettingsPanel';
import { useAppContext } from '../context/AppContext';

export default function SettingsPage() {
  const { showToast } = useAppContext();
  return (
    <div className="form-container">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-50">System Settings</h1>
        <p className="text-xs text-zinc-400 mt-1">Configuration and limits.</p>
      </div>
      <div className="form-panel">
        <SettingsPanel showToast={showToast} />
      </div>
    </div>
  );
}
