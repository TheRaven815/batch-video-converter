# Raspberry Pi Docker Web Video Converter

Bu proje artık Raspberry Pi + Docker senaryosunda **sunucudaki medya klasörlerini gezip** seçtiğiniz videoları toplu kuyruğa atabileceğiniz modern bir web arayüzü içerir.

## Neler var?

- **API (FastAPI)**
  - [`GET /health/live`](src/video_converter/api/main.py:35)
  - [`GET /health/ready`](src/video_converter/api/main.py:45)
  - [`POST /api/v1/jobs`](src/video_converter/api/main.py:147)
  - [`POST /api/v1/jobs/batch`](src/video_converter/api/main.py:154)
  - [`GET /api/v1/jobs`](src/video_converter/api/main.py:164)
  - [`GET /api/v1/jobs/{job_id}`](src/video_converter/api/main.py:185)
  - [`GET /api/v1/media/roots`](src/video_converter/api/main.py:193)
  - [`GET /api/v1/media/browse`](src/video_converter/api/main.py:198)
- **Worker (Python)**
  - Redis kuyruğundan işleri çeker
  - lifecycle + progress alanlarını günceller (`queued -> running -> completed/failed`)
- **Modern Web UI (Vite + TypeScript + React)**
  - Minimal koyu temalı sunucu medya gezgini
  - Docker içindeki tanımlı medya köklerinde dosya seçimi
  - Seçilen öğeleri staging listesine alma, seç/sil/temizle
  - Toplu Export Ayarları: video formatı + ses formatı + altyazı export
  - Job dashboard + durum/progress yenileme, bulk aksiyonlar ve son çıktılar

## Raspberry Pi / Docker medya klasörü tanıtma

Yeni sade sözleşmede sadece iki ana fikir var:

1. Tek bir çalışma kökü: `DATA_ROOT`
2. Medya gezgini kökleri: `MEDIA_MOUNTS`

`.env` örneği:

```env
REDIS_URL=redis://redis:6379/0
DATA_ROOT=/data
MEDIA_MOUNTS=Filmler=/media/filmler;Diziler=/media/diziler
```

- `REDIS_URL`: Redis bağlantı adresi.
- `DATA_ROOT`: Uygulamanın çalışma kökü; altında `input`, `outputs`, `temp`, `logs`, `data` klasörleri otomatik türetilir/oluşturulur.
- `MEDIA_MOUNTS` formatı: `Etiket=/container/path;Etiket2=/container/path2`.
- `MEDIA_MOUNTS` içinde yazdığınız container path'leri, `docker-compose.yml` içinde volume mount olarak da bulunmalıdır.

Mevcut şablondaki mount örneği:

- [`./media/filmler:/media/filmler:ro`](docker-compose.yml:16)
- [`./media/diziler:/media/diziler:ro`](docker-compose.yml:17)

Ek medya kökü eklemek için:

- `MEDIA_MOUNTS` sonuna yeni çift ekleyin (örn. `Arsiv=/media/arsiv`).
- `api` ve `worker` servislerine aynı path için yeni volume satırı ekleyin (örn. `./media/arsiv:/media/arsiv:ro`).

## Çalıştırma

### 1) Ortam dosyasını hazırlayın

```bat
copy .env.example .env
```

### 2) (Opsiyonel) Host medya klasörlerini oluşturun

```bat
mkdir data
mkdir media\filmler
mkdir media\diziler
```

### 3) Servisleri ayağa kaldırın

```bat
docker compose up --build -d
```

Bu compose dosyası local geliştirme içindir. Redis portu `6379:6379` ile host'a açılır; production/public deployment'ta Redis'i internete açmayın, sadece Docker ağı veya private network içinde kullanın.

### 4) UI açın

- `http://localhost:8000/`

UI içinde:
1. **Server media** panelinden tanımlı medya köklerinde gezinin ve video dosyalarını seçin.
2. Seçilen öğeleri **Staging** listesinde kontrol edin (seç/kaldır/tümünü seç).
3. **Export settings** panelinden şu seçenekleri belirleyin:
   - `video_export`: `mp4 | mkv | webm`
   - `audio_export`: `copy | aac | mp3 | opus`
   - `subtitle_export`: `none | embedded | separate_srt`
   - `subtitle_language`: dosya(lar)dan dinamik gelen mevcut dil seçenekleri
4. **Create jobs** ile toplu job oluşturun.
5. Sağ panelden job durumunu, progress bilgisini, bulk aksiyonları ve tamamlanan çıktı linklerini izleyin.

## Docker olmadan yerelde çalıştırma

Python kaynak kodu artık [`src/video_converter/`](src/video_converter/) package layout'u altında bulunur. Yerel komutlarda `PYTHONPATH=src` kullanılmalı veya [`run_local.py`](run_local.py) tercih edilmelidir; testlerde bu ayar [`pytest.ini`](pytest.ini) üzerinden otomatik yapılır.

Docker kullanmadan yerel geliştirme için [`run_local.py`](run_local.py) `video_converter.api.main:app` API hedefini ve `python -m video_converter.worker.main` worker modülünü aynı terminalden başlatır. Varsayılan yerel mod Redis istemez; job kayıtları ve kuyruk [`data/data/local_queue.sqlite3`](data/data/local_queue.sqlite3) dosyasında saklanan SQLite tabanlı geliştirme deposunu kullanır. FFmpeg yine sisteminizde hazır olmalıdır.

Ön koşullar:

- Python 3.11+
- Python paketleri: [`requirements.txt`](requirements.txt)
- `ffmpeg` ve `ffprobe` komutları PATH üzerinde erişilebilir olmalı
- Redis varsayılan yerel çalıştırma için gerekli değildir.
- Web UI için production build gerekiyorsa [`frontend/dist`](frontend/dist) üretilmiş olmalı. Eksikse launcher uyarı verir; Vite geliştirme sunucusu ayrı çalıştırılabilir.

İlk kurulum örneği:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
cd frontend && npm install && npm run build
cd ..
```

Başlatma:

```bat
python run_local.py
```

[`run_local.py`](run_local.py) `PYTHONPATH` içine [`src`](src) dizinini ekler; ortam değişkenleri yoksa şu yerel varsayılanları atar ve gerekli klasörleri oluşturur:

```env
VIDEO_CONVERTER_STORAGE=local
REDIS_URL=redis://localhost:6379/0
DATA_ROOT=./data
MEDIA_MOUNTS=Filmler=./media/filmler;Diziler=./media/diziler
```

`VIDEO_CONVERTER_STORAGE=local` modunda `REDIS_URL` kullanılmaz; yalnızca Redis moduna geçmek isterseniz anlamlıdır.

Otomatik oluşturulan yerel klasörler:

```text
data
media\filmler
media\diziler
```

Faydalı seçenekler:

```bat
python run_local.py --api-only
python run_local.py --worker-only
python run_local.py --host 127.0.0.1 --port 8000
python run_local.py --no-browser
python run_local.py --storage redis
```

Çalışınca UI adresi: `http://localhost:8000/`. Ctrl+C ile API ve worker çocuk süreçleri temiz şekilde kapatılır. Redis ile çalışmak isterseniz `python run_local.py --storage redis` kullanın; bu modda `redis://localhost:6379/0` üzerinde yerel Redis/Memurai/WSL Redis gibi Redis uyumlu bir servis çalışıyor olmalıdır.

## Coolify Deployment

Git tabanlı Coolify deployment için mevcut [`Dockerfile`](Dockerfile) production image build eder: Node stage Vite frontend'i üretir, final Python image FastAPI runtime ve FFmpeg içerir. Coolify'da **[`docker-compose.coolify.yml`](docker-compose.coolify.yml)** dosyasını kullanın; bu varyant generic medya klasörü örneklerini ve public port ayarını doğrudan içerir.

Önerilen Coolify ayarları:

- Source: Git repository.
- Build/deploy type: Docker Compose.
- Compose file: [`docker-compose.coolify.yml`](docker-compose.coolify.yml).
- Public service: `api`.
- App/container port: `8000` (container içindeki FastAPI portu değişmedi).
- Public/external port: `7777` kullanın. [`docker-compose.coolify.yml`](docker-compose.coolify.yml) host port mapping olarak `7777:8000` tanımlar; domain/proxy kullanıyorsanız Coolify'da public entry yine `api` servisi ve container port `8000` olmalıdır. Domain/reverse proxy arkasında doğrudan host port açmak istemiyorsanız bu port mapping'i kaldırıp Coolify proxy ayarını kullanın.
- Healthcheck path: `/health/ready`.
- Environment variables: Medya klasörleri, `REDIS_URL`, `DATA_ROOT`, `MEDIA_MOUNTS` ve port mapping [`docker-compose.coolify.yml`](docker-compose.coolify.yml) içinde hardcoded durumdadır. Değerler sonradan değişirse [`.env.coolify.example`](.env.coolify.example) referans olarak kullanılabilir.

Kalıcı veri ve medya mount'ları:

- `/data`: uygulama çalışma alanı ve çıktılar. [`docker-compose.coolify.yml`](docker-compose.coolify.yml) içinde `app-data` named volume olarak tanımlıdır.
- `/data` Redis için kullanılmaz; Redis AOF verisi ayrı `redis-data` named volume'unda tutulur.
- Host path'leri public-safe örnek klasörlerdir ve Compose içinde quoted yazılmıştır:
  - `./data/media/movies` -> `/media/movies:ro`
  - `./data/media/tv-series` -> `/media/tv-series:ro`
  - `./data/media/downloads` -> `/media/downloads:ro`
- Medya mount'ları container içinde salt-okunurdur (`:ro`). Uygulama medya dosyalarını değiştirmez; çıktılar `/data/outputs` altına yazılır.
- `MEDIA_MOUNTS` container path'lerini kullanır, host path'lerini değil: `Movies=/media/movies;TV Series=/media/tv-series;Downloads=/media/downloads`.
- Kullanıcının Coolify environment variables alanından medya path düzenlemesi gerekmez; yalnızca sunucudaki medya klasörleri değişirse [`docker-compose.coolify.yml`](docker-compose.coolify.yml) içindeki bind mount hedeflerini ve `MEDIA_MOUNTS` container path eşleşmelerini güncelleyin.

Worker ölçekleme:

- Varsayılan olarak `worker` tek replica çalıştırılmalıdır; FFmpeg CPU/RAM yoğun olduğu için Raspberry Pi veya küçük VPS üzerinde güvenli başlangıç budur.
- Daha güçlü sunucuda birden fazla worker replica denenebilir, ancak aynı medya ve `/data` volume'larına eriştiklerinden emin olun ve kaynak limitlerini izleyin.

## Public repo temizliği

Aşağıdakileri commit etmeyin ve public repo'ya taşımayın:

- `.env`, `.env.coolify`, `.env.local`, `.env.production` gibi gerçek ortam dosyaları.
- `data/` içindeki SQLite/Redis verisi, loglar, input, temp, output ve dönüştürme sonuçları.
- `media/` içindeki kişisel medya arşivi veya gerçek sunucu klasör yapısı.
- Video çıktıları ve büyük medya dosyaları: `*.mp4`, `*.mkv`, `*.webm`, `*.avi`, `*.mov`, `*.m4v`, `*.mpg`, `*.mpeg`, `*.h264`.
- Build/cache klasörleri: `frontend/node_modules/`, `frontend/dist/`, `frontend/.vite/`, `.pytest_cache/`, `__pycache__/`, `htmlcov/`, `.coverage*`, `build/`, `dist/`, `.venv/`, `venv/`.

Sadece sanitize edilmiş [`.env.example`](.env.example) ve [`.env.coolify.example`](.env.coolify.example) dosyalarını paylaşın. Public'e çıkmadan önce `git status --short --untracked-files=all` ve `git ls-files` çıktısında yukarıdaki dosyaların olmadığını kontrol edin.

## Testler

Test paketi [`tests/`](tests/) altında API, core ve worker olarak gruplanmıştır. [`pytest.ini`](pytest.ini) `pythonpath = src` ayarıyla `src/` layout'undaki `video_converter` paketini test çalıştırırken otomatik erişilebilir yapar.

```bat
venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

## Frontend geliştirme ve build

Yeni arayüz [`frontend`](frontend) altında Vite + TypeScript + React uygulamasıdır. Docker image build sırasında frontend otomatik build edilir ve FastAPI [`frontend/dist`](frontend/dist) çıktısını [`/ui`](src/video_converter/api/main.py:702) altında, kök [`/`](src/video_converter/api/main.py:706) isteğinde ise SPA giriş dosyasını servis eder.

Lokal frontend geliştirme:

```bat
cd frontend && npm install
cd frontend && npm run dev
```

Vite dev server API çağrılarını [`vite.config.ts`](frontend/vite.config.ts) içindeki proxy ile `http://localhost:8000` adresine yönlendirir. Bu akış için API ayrıca çalışıyor olmalıdır.

Lokal production build:

```bat
cd frontend && npm run build
```

Build çıktısı [`frontend/dist`](frontend/dist) altına üretilir ve commit edilmez. Docker için ayrıca manuel build gerekmez; [`Dockerfile`](Dockerfile) içinde Node build stage'i bu çıktıyı üretir.

## Notlar

- Güvenlik için gezinme sadece tanımlı kök altında kalır (path traversal engellenir): [`browse_media_root()`](src/video_converter/api/main.py:210)
- `source_root_key + source_path` gönderildiğinde API gerçek dosya varlığını ve uzantısını kontrol eder: [`_validate_source_payload()`](src/video_converter/api/main.py:76)
- API job kayıtlarında yeni alanları tutar: `video_export`, `audio_export`, `subtitle_export`.
- Worker, yeni export alanları varsa FFmpeg komutunu öncelikle bu alanlara göre kurar; alanlar yoksa geriye dönük `profile` davranışına döner.
- Altyazı seçenekleri dosyaya göre dinamik gelir; batch seçiminde diller birleşim olarak listelenir ve bazı dosyalarda olmayabileceği notu gösterilir.
- Stream index farklılıkları (`29`, `30` vb.) dil bazlı eşleme (`language` tag) ile çözülür.
- `subtitle_export=separate_srt` seçeneğinde ayrı `.srt` çıkarımı denenir; seçilen dil yoksa veya altyazı akışı bulunamazsa job fail edilmeden video çıktısı tamamlanır.
