# Batch Video Converter — İyileştirme Planı

> Bu belge; backend (FastAPI), worker (FFmpeg) ve frontend (React/Vite) kodunun satır satır incelenmesi sonucunda çıkarılan, **öncelik sırasına göre** dizilmiş yapılacaklar listesidir. Hedef: kaliteli, modern, minimalist ve hatasız çalışan bir video converter.
>
> Öncelikler: **P0** = uygulamayı fiilen bozan hatalar → **P1** = güvenlik → **P2** = veri bütünlüğü / sağlamlık → **P3** = UI/UX ve görsel düzeltmeler → **P4** = mimari ve modernleşme → **P5** = yeni özellik önerileri.

---

## P0 — Kritik Hatalar (uygulama şu an bu noktalarda fiilen bozuk)

### Container / Süreç Yönetimi
- [x] **`entrypoint.sh` bozuk: `wait -n` bir bash komutu ama shebang `/bin/sh` (dash).** dash'te `wait -n` anında hata verip (`2>/dev/null || true` ile gizleniyor) script'in sonuna düşüyor ve API + worker başlar başlamaz `kill -TERM` ile öldürülüyor. Container ya restart döngüsüne giriyor ya da tanımsız durumda çalışıyor. Çözüm: `#!/bin/bash` + imaja bash kurulumu, ya da düz `wait` döngüsü / `tini` benzeri bir süpervizör. (`entrypoint.sh:1,27`)
- [x] **`cleanup()` trap'i API'yi hiç sonlandırmıyor** — sadece worker PID'i öldürülüp `exit 0` yapılıyor; uvicorn'a SIGTERM gitmiyor, devam eden istekler düşüyor. (`entrypoint.sh:13-18`)

### Worker / FFmpeg
- [x] **Gömülü altyazı her zaman `-c:s copy`:** MP4 çıktıda SRT/ASS kopyalamak FFmpeg'de hata verir (MP4 `mov_text` ister), WebM sadece WebVTT kabul eder → MP4/WebM + gömülü altyazı işleri garantili başarısız. Konteynıra göre altyazı codec'i seçilmeli (MP4→`mov_text`, MKV→`copy`, WebM→`webvtt`; PGS/VOBSUB gibi bitmap altyazılar için uygun davranış). (`worker/main.py:312-317`)
- [x] **Altyazı dili seçiliyken `-map 0` tüm stream'leri (font attachment, data) MP4'e taşımaya çalışıyor** → muxing hatası. Ayrıca dil seçiliyken tüm ses stream'leri, seçili değilken tek ses stream'i map'leniyor — tutarsız. Map mantığı yeniden yazılmalı. (`worker/main.py:313-316`)
- [x] **`prefer_stream_copy_video` girdi codec'ini hiç kontrol etmiyor:** HEVC/VP9 bir girdi `h264_mp4` profili istese de olduğu gibi kopyalanıyor → çıktı istenen profile uymuyor, HEVC-in-MP4 Apple cihazlarda oynamıyor. ffprobe ile girdi codec'i doğrulanıp yalnızca gerçekten h264 ise stream copy yapılmalı. (`worker/main.py:601`)
- [x] **İş kaybı:** worker `blpop` ile işi kuyruktan düşürdükten sonra, `running` durumuna geçemeden çökerse iş sonsuza dek `queued` görünür ama kuyrukta yoktur; kurtarma mekanizması sadece `running` işleri kapsıyor. `BLMOVE` + processing list (reliable queue) desenine geçilmeli. (`worker/main.py:753-756`)
- [x] **Çökme sonrası `running` işler kurtarılamıyor:** kurtarma yalnızca worker açılışında bir kez ve 1 saatlik bayatlık eşiğiyle çalışıyor; worker saniyeler içinde yeniden başladığında iş "bayat" sayılmıyor ve sonsuza dek `running` kalıyor. Periyodik kurtarma döngüsü + heartbeat eklenmeli. (`worker/main.py:822-824`, `job_repository.py:213-239`)
- [x] **Başarısız/iptal edilen işlerin yarım çıktı dosyaları silinmiyor** ve worker doğrudan `outputs/` içine yazıyor (`temp/` hiç kullanılmıyor) → yarım dosyalar çıktı listesinde görünüp indirilebiliyor. Temp dizinine yaz → tamamlanınca `outputs/`'a taşı (rename) deseni + hata/iptalde temizlik. (`worker/main.py:232-235`)

### Backend API
- [x] **SSE endpoint'i her istemci için bir threadpool thread'ini sonsuza dek kilitliyor** (sync generator + `time.sleep(5)`). Birkaç açık sekme tüm uygulamayı kilitleyebilir. `async` generator + `asyncio.sleep` + `request.is_disconnected()` kullanılmalı; `Cache-Control: no-cache` ve `X-Accel-Buffering: no` başlıkları eklenmeli. (`api/main.py:586-609`)
- [x] **`worker_health` Redis çöktüğünde 500 dönüyor:** `count_running_jobs()` try/except bloğunun dışında çağrılıyor; "degraded" cevap yolu fiilen çalışmıyor. (`api/main.py:636`)
- [x] **Kuyruktaki bir işi arşivlemek işi kalıcı olarak kilitliyor:** arşivleme işi kuyruktan çıkarıyor ama durumu `queued` bırakıyor; unarchive endpoint'i yok ve `start` "already queued" diye reddediyor. (`api/main.py:822-888`)

### Frontend
- [x] **Çıktı indirme linkleri her zaman 401 veriyor:** düz `<a href>` Authorization header göndermiyor; backend'in verdiği `download_url` alanı da kullanılmıyor, dosya adı URL-encode edilmiyor. Fetch + blob, imzalı kısa ömürlü indirme token'ı veya cookie tabanlı auth ile çözülmeli. (`components/ui.tsx:72`)
- [x] **`normalizeStatus` boş (identity) fonksiyon** → `done` kontrolü hiç tutmuyor: tamamlanan işin yeşil noktası hiç görünmüyor, `badge-completed`/`badge-cancelled` sınıflarının CSS'i yok (rozetler şeffaf/bozuk), tamamlanan işin progress bar dolgusu **görünmez**. Status → görsel eşlemesi tek yerden, eksiksiz yapılmalı. (`utils/helpers.ts:45-47`, `ui.tsx:19-23,259`, `styles.css:543-594`)
- [x] **Arama kutusuna her tuş vuruşunda SSE bağlantısı yıkılıp yeniden kuruluyor** (`filters` → `refreshJobs` → SSE effect bağımlılık zinciri) ve debounce yok. (`main.tsx:189,273-333`)
- [x] **Arama, tüm iş listesini ve dashboard metriklerini bozuyor:** sunucu tarafında filtrelenmiş alt küme tüm `jobs` state'inin yerine yazılıyor; metrik kartları arama alt kümesini sayıyor, işler "kayboluyor". (`main.tsx:158`, `helpers.ts:70-88`)
- [x] **SSE anlık görüntüsü listeyi 100 işe kırpıyor** (REST 250 çekiyor) → liste 100/250 arasında gidip geliyor. (`api/main.py:586-593`, `api.ts:92`)
- [x] **Geri/ileri (browser back) navigasyonu bozuk:** `hashchange` dinleyicisi yok; URL değişiyor ama sayfa değişmiyor. (`main.tsx:96-99,267-271`)

---

## P1 — Güvenlik (dışa açık bir deployment için acil)

- [x] **Varsayılan `admin` / `12345678` kimlik bilgileri kaldırılmalı** — env değişkeni yoksa uygulama açılışta hata verip durmalı ya da ilk açılışta zorunlu şifre belirleme akışı olmalı. (`core/config.py:33-34,108-109`)
- [x] **Parola hash'i güvensiz:** repoda sabit salt'lı tek geçiş SHA-256 (`sha256("bvc_static_salt_123!" + parola)`) — offline brute-force'a tamamen açık. **bcrypt veya argon2**'ye geçilmeli. (`api/auth.py:38-42`)
- [x] **`POST /auth/forgot-password` kimliksiz erişime açık:** herkes admin parolasını rastgele bir değere sıfırlayıp gerçek yöneticiyi kilitleyebiliyor (kalıcı DoS); geçici parola loglara yazılıyor. Endpoint kaldırılmalı ya da tamamen yeniden tasarlanmalı. (`api/auth.py:176-189`)
- [x] **`JWT_SECRET` yoksa her restart'ta yeniden üretiliyor** → tüm oturumlar düşüyor; çoklu worker/replica'da her süreç farklı secret kullanıyor. Secret `DATA_ROOT` altında kalıcılaştırılmalı ya da eksikse açılış hatası verilmeli. (`core/config.py:110-114`)
- [x] **`APP_PASSWORD=""` tüm kimlik doğrulamayı sessizce kapatıyor** — silme/parola değiştirme dahil her endpoint anonim oluyor. Boş parola bir hata olmalı, "auth kapalı" modu açıkça ayrı bir bayrak olmalı. (`api/auth.py:91-92,118-119`)
- [x] **Login'de rate limit / hesap kilidi yok**; sabit-zamanlı olmayan (`!=`) karşılaştırma kullanılıyor (`secrets.compare_digest` olmalı). (`api/auth.py:122-146`)
- [x] **30 günlük access token + parola değişince eski token'lar geçerli kalıyor.** Kısa ömürlü access token + refresh akışı; parola değişiminde token iptali (`iat` / rotation timestamp kontrolü) ve logout mekanizması. (`api/auth.py:65,104-113`)
- [x] **Token query string ile kabul ediliyor (`?token=...`)** — SSE için kullanılıyor; 30 günlük bearer token access/proxy loglarına ve tarayıcı geçmişine sızıyor. Kısa ömürlü SSE bileti ya da cookie ile değiştirilmeli. (`api/auth.py:87-89`, `api.ts` / `main.tsx:313`)
- [x] **`docker-compose.yml` Redis'i host'a `0.0.0.0:6380` şifresiz açıyor** — LAN'daki herkes kuyruğu silebilir. Port mapping kaldırılmalı ya da Redis parolası eklenmeli. (`docker-compose.yml`)
- [x] **ffprobe `timeout` olmadan çalıştırılıyor** — bozuk/kötü niyetli dosya veya ölü NFS mount threadpool'u tüketebilir (soft DoS). Tüm `subprocess.run` çağrılarına timeout. (`api/main.py:1046`, `worker/main.py:672-694`)
- [x] **Login sayfası implementasyon detayı sızdırıyor:** "Enter APP_USERNAME" placeholder'ları ve varsayılan `admin` kullanıcı adı UI'da görünüyor. (`LoginPage.tsx:6,67,79`)
- [x] Docker imajı **root olarak çalışıyor**; `USER` tanımı ve `HEALTHCHECK` yok, init süreci (tini) yok. (`Dockerfile`)
- [x] Bulk endpoint'lerde `job_ids` listesi sınırsız (`max_length` yok) → tek istekle binlerce sıralı Redis çağrısı. (`core/models.py:163-175`)

---

## P2 — Veri Bütünlüğü ve Sağlamlık

- [x] **API ↔ worker arasında lost-update yarışı:** `persist`/`update_status` ve `_cancel_record` korumasız oku-değiştir-yaz yapıyor; `cancel_requested` bayrağı veya durum güncellemeleri sessizce ezilebiliyor. Redis `WATCH`/CAS veya alan bazlı atomik güncelleme (HSET) gerekli. (`job_repository.py:44-51,121-199`, `api/main.py:758-782`)
- [x] **Filtreli sayfalamada kayıt atlanıyor:** `limit` sayfa ortasında dolduğunda `next_cursor` tüm sayfanın sonrasını gösteriyor; ayrıca offset tabanlı cursor'lar yeni iş eklendikçe kayıyor (tekrar/atlama). Stabil (ör. job-id tabanlı) cursor'a geçilmeli. (`api/main.py:487-529`)
- [x] **`list_batches` yanlış özet üretiyor** (kısmi tarama ile eksik sayım) ve her istekte tüm iş indeksini baştan tarıyor (O(tüm işler)). (`api/main.py:553-583`)
- [x] **Idempotency atomik değil ve payload'a bağlı değil:** aynı key ile eşzamanlı iki istek çift iş üretebiliyor; aynı key + farklı payload eski cevabı dönüyor (standart: 422 ile reddetmek). Key'ler kullanıcıya göre ayrılmalı, `SET NX` ile atomikleştirilmeli. (`api/main.py:285-385`)
- [x] **Ses `copy` uyumsuzluklarında fallback yok:** Vorbis/FLAC/TrueHD → MP4 kopyalama başarısız oluyor; mevcut fallback yalnızca video codec'ini değiştiriyor. Konteynıra göre ses codec doğrulaması/fallback eklenmeli. (`worker/main.py:301-302,630-639`)
- [x] **Graceful shutdown işleri `failed` olarak işaretliyor** — rutin bir redeploy devam eden işleri kalıcı başarısız yapıyor; requeue edilmeli. Ayrıca 600 sn drain süresi Docker'ın 10 sn'lik varsayılan stop grace period'uyla uyumsuz → compose dosyalarına `stop_grace_period` eklenmeli. (`worker/main.py:126-149,646-655,783`)
- [x] **Başarısız işler `progress_percent=100` gösteriyor** — UI'da "%100 failed" görünüyor. (`worker/main.py:705-713`)
- [x] **`_get_dynamic_concurrency` sınırsız/korumasız:** bozuk `system:settings` değeri (0 veya negatif) worker'ı sessizce sonsuza dek durduruyor; `WORKER_CONCURRENCY` env üst sınırsız. 1–8 aralığına clamp'lenmeli. (`worker/main.py:719-727,748`, `core/config.py:102-105`)
- [x] **Stale-recovery hem API'de hem worker'da çalışıyor ve `requeue_existing` kuyruk üyeliğini kontrol etmiyor** → aynı iş iki kez kuyruğa girip iki kez dönüştürülebiliyor. Tek sahip + idempotent requeue. (`api/main.py:67-82`, `job_repository.py:103-111`)
- [x] **`update_status` her çağrıda `error_message`'ı eziyor** (error=None gelen progress güncellemesi önceki hatayı siliyor). (`job_repository.py:148`)
- [x] **Local (SQLite) modda hata yakalama yanlış:** her yerde yalnızca `redis.RedisError` yakalanıyor; SQLite hataları 500'e dönüşüyor, lifespan'de ise sessizce yutuluyor. Storage soyutlamasına ortak hata tipi tanımlanmalı. (`api/main.py:74-78,130-146`, `storage.py`)
- [x] **TOCTOU hataları:** `list_outputs` sıralama sırasında silinen dosyada 500 atıyor; batch oluşturma, doğrulama geçtikten sonra dosya kaybolursa tüm batch'i 422 ile düşürüyor. (`api/main.py:345-347,678-689`)
- [x] **Root logger formatı `%(job_id)s` zorunlu kılıyor** — üçüncü parti kütüphane logları "Logging error" spam'i üretiyor; Filter ile varsayılan değer enjekte edilmeli. API tarafında ise hiç logging yapılandırması yok. (`worker/main.py:28-31`)
- [x] **Cancel-after-success yarışı:** ffmpeg başarıyla bittikten hemen sonra gelen iptal, tamamlanmış çıktıyı "cancelled" yapıyor. (`worker/main.py:527-528`)
- [x] **`cancel_jobs_bulk` atlanan işleri hem `updated` hem `skipped` listesine koyuyor** → UI toast'ı yanlış sayı gösteriyor. (`api/main.py:800-803`)
- [x] İş kayıtları ve indeks sonsuza dek büyüyor (TTL/retention yok) → performans zamanla lineer düşüyor. (`job_repository.py:63-65`)
- [x] **CI test çalıştırmıyor:** `ci.yml` sadece Ruff + Black; pytest, frontend build/typecheck ve Docker build doğrulaması CI'da yok. `release.yml` arm64'ü `setup-qemu-action` olmadan derlemeye çalışıyor, build cache yok. (`.github/workflows/`)

---

## P3 — UI/UX ve Görsel Düzeltmeler

### Görsel Bozukluklar
- [x] **Onlarca "hayalet" Tailwind sınıfı:** Tailwind kurulu değil, JSX'te kullanılan `max-h-[160px]`, `space-y-2`, `text-[10px]`, `pb-2`, `border-rose-900`, `hover:*`, `sm:inline` vb. sınıfların CSS karşılığı yok. Görünür sonuçları: Recent Outputs listesi sınırsız uzuyor, metrik kart alt yazıları 16px görünüyor, login hata kutusu kırmızı değil gri, footer ayırıcı her boyutta gizli, running rozetindeki nokta hiç pulse etmiyor. **Karar:** ya gerçek Tailwind kurulmalı ya da tüm sınıflar mevcut el yazımı CSS sistemine çevrilmeli (öneri: madem minimalist hedef var, Tailwind'i kurup el yazımı CSS'i sadeleştirmek daha sürdürülebilir).
- [x] **Native checkbox'lar dark temayı kırıyor:** `.form-checkbox` `appearance: none` veya `accent-color` olmadan stil vermeye çalışıyor → tarayıcı varsayılanı (açık mavi) görünüyor. (`styles.css:521-530`)
- [x] **Hiçbir yerde focus stili yok:** input'larda `outline: none`, butonlarda/sekmelerde `:focus-visible` yok → klavye kullanıcısı odağı göremiyor. (`styles.css:464-468`)
- [x] **Convert sayfası kaynak tarayıcı satırı mobilde taşıyor** (sabit `width: 200px` select + wrap olmayan flex). (`main.tsx:556-565`)
- [x] **Mobilde sağlık göstergeleri (API/Redis/Worker) tamamen gizleniyor**, yerine hiçbir şey konmuyor. (`styles.css:263-265`)
- [x] Mobil çekmece (drawer) menüde body scroll kilidi, focus trap ve Escape desteği yok. (`main.tsx:411-421`)
- [x] Dağınık inline stiller token sistemine taşınmalı; "Forgot Password?" butonundaki `btn-outline` + inline sıfırlama çakışması giderilmeli; toast kenarlığındaki hardcoded `#27272a` → `var(--zinc-800)`. Ölü/mükerrer CSS temizlenmeli (`.justify-center` iki kez tanımlı vb.).
- [x] **Google Fonts CDN'den yükleniyor** — self-host edilen bir Pi uygulaması offline'da font'suz kalıyor; fontlar pakete gömülmeli veya sistem font stack kullanılmalı. Favicon da yok. (`index.html:8-10`)
- [x] Settings'te **Dark/Light/System tema seçeneği var ama hiçbir şey yapmıyor** — ya light tema gerçekten uygulanmalı ya da kontrol kaldırılmalı (aynısı `density: compact` için de geçerli). (`SettingsPanel.tsx:159-160`)

### UX Eksikleri
- [x] **İş detay görünümü hiç yok:** `log_tail`, `timeline`, `error_message`, fps/speed/bitrate verileri her kayıtta mevcut ama hiçbir yerde gösterilmiyor — **başarısız bir işin hatası UI'da hiçbir yerde okunamıyor.** İş satırına tıklayınca açılan detay drawer/modal eklenmeli. (`main.tsx:94`, `ui.tsx:172`)
- [x] **Logout butonu yok** — oturum hiç kapatılamıyor.
- [x] **Silme işlemleri onaysız:** "Delete Selected" ve satır bazlı silme anında çalışıyor; yalnızca "Clear outputs" `window.confirm` kullanıyor. Stil ile uyumlu bir onay modal bileşeni yazılmalı.
- [x] **Presets sayfası geçersiz değerler yazıyor:** formdaki `copy`, `remove`, `embed`, `burn`, `auto` seçenekleri tip tanımlarında ve backend sözlüğünde yok (`as any` ile bypass ediliyor); ayrıca Presets ve Convert sayfası **aynı state'i paylaşıyor** — preset formunu düzenlemek Convert ayarlarını sessizce eziyor. Enum'lar tek kaynaktan tanımlanmalı, state'ler ayrılmalı. (`main.tsx:691-729`)
- [x] **Ölü/yarım özellikler tamamlanmalı veya kaldırılmalı:** altyazı probe (`probeSubtitles`) ve batch ön-doğrulama (`validateJobs`) import edilip hiç çağrılmıyor → "Language Preference" hep boş; `batches` her poll'da çekilip hiç render edilmiyor; `sort` ve `sourceType` filtrelerinin UI kontrolü yok; bulk toolbar'da Cancel/Archive butonu yok ("Start" kuyruktaki işlerde hep "0 jobs started" diyor, açıklama yok).
- [x] **Sayfalama yok:** işler 250, çıktılar 50 ile sınırlı; `nextCursor` dönüyor ama hiç kullanılmıyor — fazlası görünmez. *(Bilinçli olarak P4'e ertelendi: SSE snapshot + istemci tarafı filtreleme ile çakışıyor; React Query + veri katmanı refactor'üyle birlikte yapılmalı.)*
- [x] 401 yönetimi yalnızca `listJobs`'ta; ortak `request()` yardımcısına 401 → login yönlendirmesi eklenmeli; hata gövdesi JSON değilse kullanıcıya `SyntaxError` metni gösteriliyor. (`api.ts:34-63,107-113,192-201`)
- [x] "3 jobs **deleteed**" / "archiveed" yazım hatası düzeltilmeli; satır aksiyonlarındaki `.then()` zincirlerine `.catch` eklenmeli (sessiz hata). (`main.tsx:359`, `ui.tsx:271-275`)
- [x] Select-all checkbox'ı filtre/veri değişince tutarsız kalıyor; `selectedJobIds` refresh sonrası temizlenmiyor. (`ui.tsx:209`)
- [x] Dosya tarayıcısında "bir üst dizin" / breadcrumb yok; her gezinmede seçim sıfırlanıyor.
- [x] Klavye erişilebilirliği: tıklanabilir `div`'ler buton olmalı, ikon butonlarına `aria-label`, toast'lara `role="status"`/`aria-live` eklenmeli.
- [x] ETA saat desteği (`512m 10s` yerine `8h 32m`), `formatBytes`'a TB desteği; footer'daki "/api/v1/ endpoints are preserved" geliştirme kalıntısı kaldırılmalı; sürüm numarası JSX'e hardcoded yazılmamalı.
- [x] Settings paneli yüklenirken form kilitlenmiyor (gelen veri kullanıcının girdiğini ezebiliyor) ve yükleme hatası UI'da gösterilmiyor. (`SettingsPanel.tsx:35-49`)

---

## P4 — Mimari ve Modernleşme

### Backend
- [x] **1104 satırlık `api/main.py` router'lara bölünmeli** (jobs, batches, outputs, media, settings, auth, health) + ince bir app factory; import anındaki yan etkiler (settings, dizin oluşturma, storage client) lifespan/DI'ya taşınmalı. (`api/main.py:59-64`)
- [x] **Sync redis → `redis.asyncio`** ve endpoint'lerin gerçek `async` yapılması (şu an her istek threadpool slotu yakıyor).
- [x] **`pydantic-settings` (BaseSettings)** ile env yönetimi — el yazımı `os.getenv` + try/except ve dataclass/env çifte varsayılan tanımları kalksın. (`core/config.py`)
- [x] Hata mesajı string'ini grep'leyerek hata kodu üretme (`_error_code_from_message`) kaldırılmalı — `path_validation.py`'daki tipli istisnalar zaten var, uçtan uca taşınmalı. (`api/main.py:99-109,186-187`)
- [x] `auth.py`'nin kendi storage client'ı oluşturması kaldırılıp tek paylaşımlı client'a geçilmeli. (`api/auth.py:25-32`)
- [x] Running-jobs takibi Redis list yerine SET olmalı (`SADD/SREM/SCARD`); `JobRepository` tip imzası `StorageClient` union'ını kullanmalı. (`job_repository.py:13,21,202-211`)
- [x] `profile`/`video_export`/`audio_export`/`subtitle_export` serbest string yerine **enum** olmalı — geçersiz değer submit anında 422 alsın, worker'da patlamasın. (`core/models.py:22-25`)
- [x] SSE, 5 saniyede bir top-100 snapshot diff'i yerine Redis pub/sub ile gerçek delta yayınlamalı.
- [x] Ölü kod temizliği: `_run_single` (worker), `AutoCleanupSettings` (hiç okunmuyor — ya P5'teki cleanup işine bağlanmalı ya kaldırılmalı), erişilemeyen dallar; job create endpoint'leri 200 yerine 201 dönmeli; OpenAPI'de gerçek hata zarfı (`StructuredErrorResponse`) belgelenmeli.
- [x] Testlerin kapsamadığı kritik alanlara test eklenmeli: SSE, filtre+sayfalama etkileşimi, archive→start, forgot-password, local-storage hata yolları.

### Frontend
- [x] **807 satırlık `main.tsx` bölünmeli:** sayfa bileşenleri (Dashboard/Convert/Presets/Settings), veri katmanı ve context/store ayrıştırılmalı; ~25 `useState` tek bileşende — her tuş vuruşu tüm uygulamayı re-render ediyor.
- [x] **Server-state için React Query (TanStack Query) veya SWR** — el yazımı `refreshInFlight` ref'leri, `JSON.stringify` ile deep-equality, manuel merge ve görünürlük yönetimi silinir; P0'daki `filters`→SSE bağımlılık hataları kökten çözülür.
- [x] **Gerçek router** (react-router veya minimalist wouter): `hashchange` sorunu, route param'ları (`/jobs/:id` detay sayfası) ve lazy loading için.
- [x] **`as any` cast'leri kaldırılmalı**; backend OpenAPI şemasından tip üretimi (openapi-typescript) ile frontend/backend tip kayması bitirilmeli.
- [x] **ESLint + Prettier + `noUnusedLocals`/`noUnusedParameters`** eklenmeli — mevcut ölü import/state yığını (`submitSummary`, `EmptyState`, `DashboardView` vb.) bu yüzden fark edilmiyor.
- [x] Worker-health çift yerden poll'lanıyor (dashboard'da ~2 saniyede bir istek — Pi için gereksiz yük); tek kaynağa indirilmeli.
- [x] ErrorBoundary eklenmeli (şu an herhangi bir render hatası beyaz ekran).
- [x] Toast sistemi: temizlenmeyen timeout'lar, dismiss butonu, dedupe; ya küçük bir kütüphane (sonner vb.) ya da düzeltilmiş el yazımı sistem.

### Altyapı
- [x] Dockerfile: non-root `USER`, `HEALTHCHECK`, tini; compose dosyalarına `stop_grace_period` ve app healthcheck; raspi compose'da `:latest` yerine sürüm pin'i + bellek limiti.
- [x] `docker-compose.yml`'deki `./data:${DATA_ROOT:-/data}` host-env foot-gun'ı düzeltilmeli; Redis verisi app `/data` ağacının dışına alınmalı.
- [x] ffmpeg'e `-threads` kontrolü eklenmeli — 2 CPU limitli container'da 8 paralel çok-thread'li ffmpeg ciddi oversubscription.
- [x] `run_local.py`: `--worker-only` çalıştırmada bile her seferinde `npm ci && npm run build` yapılmasın; varsayılan bind `0.0.0.0` yerine `127.0.0.1` olmalı.
- [x] CI: pytest + frontend `tsc`/build + `docker build` PR'larda çalışmalı; release'e QEMU + build cache eklenmeli.

---

## P5 — Yeni Özellik Önerileri (kaliteli bir converter için)

### Yüksek Değerli
- [x] **İş detay paneli** (P3'teki maddenin genişletilmişi): canlı log tail, timeline, fps/speed/bitrate grafiği — veriler zaten modelde var.
- [x] **Otomatik retry + backoff:** `attempt_count` alanı mevcut ama hiçbir şey tüketmiyor; geçici ffmpeg hataları insan müdahalesi olmadan (sınırlı deneme ile) yeniden denenmeli.
- [x] **Kalite/çözünürlük kontrolleri:** CRF/preset/ses bitrate'i şu an hardcoded; iş modeline kalite (CRF veya hedef bitrate), çözünürlük (1080p/720p/orijinal) ve encoder preset alanları eklenmeli. VP9 için `-row-mt 1` gibi hız bayrakları.
- [x] **Donanım hızlandırma (Raspberry Pi):** Pi 4'te `h264_v4l2m2m` gerçek zamanlı ile saatler arası fark demek; açılışta capability probe yapılmalı (Pi 5'te H.264 HW encoder yok), compose'a `/dev/video*` device mapping eklenmeli.
- [x] **Disk alanı kontrolü:** dönüştürme öncesi/sırasında `shutil.disk_usage` kontrolü + UI'da disk kullanımı göstergesi (Pi'de kritik).
- [x] **Otomatik temizlik (retention) worker'ı:** modeli zaten var olan `AutoCleanupSettings`'i gerçekten uygulayan periyodik görev — eski çıktılar ve terminal iş kayıtları budansın.
- [x] **Batch UI:** backend `/api/v1/batches` özetleri hazır ve frontend çekiyor ama render etmiyor; batch bazlı ilerleme kartı + batch bazlı cancel/retry/archive/delete endpoint'leri.

### Orta Değerli
- [x] **Çıktı önizleme:** thumbnail üretimi (tek ffmpeg karesi) + tarayıcıda `Accept-Ranges` destekli stream önizleme endpoint'i.
- [x] **Tekil çıktı silme** (`DELETE /outputs/{filename}`) — şu an ya hepsi ya hiçbiri; iş kaydından çıktıya doğrudan link.
- [x] **Kuyruk pozisyonu / ETA:** kuyruktaki her işin sırası (`LPOS`) ve tahmini başlama süresi UI'da gösterilsin.
- [x] **Worker heartbeat:** Redis'te heartbeat anahtarı — takılmış worker "sağlıklı" görünmesin; ffmpeg stall detection (ilerleme durursa timeout).
- [x] **Kalıcı iş logları:** 50 satırlık log_tail yerine tam ffmpeg stderr'i `logs/` altına dosya olarak yazılsın (dizin zaten oluşturuluyor, hiç kullanılmıyor).
- [x] **Light tema + `prefers-color-scheme`** desteği (Settings'teki ölü kontrol gerçek olsun).
- [x] **Debounce'lu arama + URL'de kalıcı filtreler**, sort/sourceType kontrolleri.

### Düşük Öncelikli / Vitrin
- [x] PWA manifest + favicon + self-host fontlar (ev sunucusu uygulaması için ideal).
- [x] İş önceliği (tek FIFO yerine öncelik kuyruğu).
- [x] Sürükle-bırak dosya yükleme (şu an yalnızca sunucu tarafı tarama var).
- [x] Operatör denetim kaydı (kim neyi iptal etti/sildi/parola değiştirdi).
- [x] i18n altyapısı (TR/EN dil seçeneği).

---

## Önerilen Uygulama Sırası (özet yol haritası)

1. **Hafta 1 — "Çalışır hale getir":** P0'ın tamamı (entrypoint, altyazı/codec mantığı, iş kaybı, yarım dosyalar, indirme linkleri, status/badge, SSE zincirleri).
2. **Hafta 2 — "Güvenli hale getir":** P1'in tamamı + CI'ya pytest.
3. **Hafta 3 — "Sağlamlaştır":** P2 (yarışlar, sayfalama, idempotency, fallback'ler, logging).
4. **Hafta 4 — "Modernleştir":** P4 mimari refactor (router'lar, React Query + router, tip üretimi, lint) — bu refactor P3'teki birçok UI düzeltmesini doğal olarak kapsar.
5. **Sonrası:** P3 kalanları + P5 özellikleri değer sırasına göre.

> Not: P4 refactor'ünü P3'ün tamamından önce yapmak bilinçli bir öneri — monolitik `main.tsx` bölünmeden yapılacak UI düzeltmeleri iki kez elden geçmek zorunda kalır.
