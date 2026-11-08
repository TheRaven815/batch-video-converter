import { useEffect, useState } from 'react';
import { Toaster } from 'sonner';

export function ThemedToaster() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() =>
    document.documentElement.dataset.theme === 'light' ? 'light' : 'dark',
  );

  useEffect(() => {
    const element = document.documentElement;
    const sync = () => setTheme(element.dataset.theme === 'light' ? 'light' : 'dark');
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(element, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  return <Toaster theme={theme} richColors closeButton position="bottom-right" />;
}
