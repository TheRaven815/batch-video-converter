# Batch Video Converter — Derin İnceleme ve Geliştirme Planı

> Tarih: 2026-08-20 — İnceleyen: AI kodlama aracı  \
> Kapsam: `src/video_converter/{api,core,worker}`, `frontend/src`, `tests/`, `scripts/legacy/*`, `Dockerfile`, `docker-compose.yml`, `run_local.py`, `AGENTS.md`, `IYILESTIRME_PLANI.md`, `README.md` satır-satır okundu.  
> Not: `IYILESTIRME_PLANI.md`’deki P0–P5 maddelerinin büyük kısmı fiilen kapatılmış (`[x]`). Bu plan **kalan artıkları, regresyon risklerini ve legacy scriptlerde var olup projede olmayan özellikleri** önceliklendirir.

---

## 1. Mevcut Durum Analizi

### 1.1 Mimari Özet
```
Browser (React 19 + Vite 8 + TS 6 + wouter + TanStack Query + sonner)
  → FastAPI (src/video_converter/api/main.py → api/routes.py + api/routers/* + auth.py)
    → JobRepository (core/job_repository.py) → StorageClient (core/storage.py: redis.Redis | LocalFileStore[SQLite])
    → Worker (worker/main.py) → FFmpeg/ffprobe → DATA_ROOT/{outputs,temp,logs,data}
```
- **API** modüler: `api/main.py` factory, `api/routes.py` tek kaynak, `routers/_select.py` predikate ile dilimler, `auth.py` JWT + bcrypt + ticket.
- **Storage**: `LocalFileStore` Redis API’sini (kv + lists + sets + pipeline + blmove + pubsub no-op) SQLite WAL ile taklit eder — çok prosesli `local` modda `busy_timeout=30s`, `BEGIN IMMEDIATE` ile doğru.
- **Worker**: ThreadPool + `BLMOVE` reliable-queue, heartbeat (`worker:heartbeat` 30s TTL), stale-recovery (periyodik 600s), stall detection, disk guard, temp→atomic rename, log Tail 50/120 limit.
- **Frontend**: `App.tsx` context + `useServerState.ts` (React Query infinite + SSE EventSource + ticket + fallback poll 5s). `ConvertPage.tsx` media browser + staging + probe, `DashboardPage.tsx` metrik + JobList + Outputs + BatchPanel.

### 1.2 Olgunluk Seviyesi
- P0’daki container/worker/FFmpeg/SSE/indirme rozet bugları çözülmüş. `entrypoint.sh` `#!/bin/bash` + `wait -n`, `tini`, non-root `app`, `HEALTHCHECK` mevcut.
- Auth sertleştirilmiş: default cred yok, setup akışı, bcrypt + legacy SHA fallback, `JWT_SECRET` kalıcılaştırma (`data/jwt_secret` 0600), `APP_PASSWORD=""` artık auth’u kapatmıyor, rate-limit (5/300s), 12h token + `credentials_updated_at` ile revoke, stream ticket 60s.
- P2’deki WATCH/CAS, idempotency `SET NX`, offset→job-id cursor stabilizasyonu uygulanmış.
- P3’te detay drawer, log tail, timeline, telemetry chart, ConfirmDialog, theme/density, focus stilleri düzeltilmiş.
- Buna rağmen **legacy scriptlerin çoklu akış seçimi ve çoklu SRT çıkarma gibi çekirdek özellikleri hiç taşınmamış**; bazı küçük runtime/UX açıkları ve test boşlukları sürüyor.

---

## 2. Tespit Edilen Hatalar (Önceliklendirilmiş)

### 2.1 KRİTİK (uygulamayı bozar / veri kaybı)

| # | Başlık | Dosya:Satır | Etki | Durum |
|---|--------|-------------|------|-------|
| K-1 | `ffprobe` süre/timeout eksik dallarda | `worker/main.py:472-511` `_probe_duration_seconds`, `worker/main.py:251-269` `_run_ffprobe_json` sadece bazı çağrılarda 30s timeout | Bozuk NFS / sonsuz ffmpeg mount thread’i kilitler, worker drain 600s boyunca asılı kalır | **Açık** |
| K-2 | `BLMOVE` per-queue timeout bölme hatası | `core/job_repository.py:199` `per_queue_timeout = max(1, timeout//3)` → 5s istekte her kuyruk 1s, toplam 3s; yüksek öncelik boşken düşük öncelik 3s gecikir, “adil” değil | Gecikmiş dequeue, test fake’inde farklı davranış | Orta-kritik |
| K-3 | `LocalFileStore.blmove` desteklenmeyen yönde `ValueError` | `core/storage.py:382` sadece LEFT→RIGHT destekler, Redis ise RIGHT→LEFT de kullanabilir (gelecek değişim kırar) | Gizli kırılma riski | Düşük-kritik |
| K-4 | `temp` → `outputs` atomic rename cross-device kopabilir | `worker/main.py:977` `os.replace(temp, output)` aynı filesystem varsayar; Docker bind + named volume ayrı cihazda `EXDEV` | Yazım hatası, yarım dosya kalır | Düşük |

### 2.2 YÜKSEK (güvenlik / tutarlılık)

| # | Başlık | Dosya:Satır | Açıklama |
|---|--------|-------------|----------|
| Y-1 | `preview` endpoint range parser çoklu range’i reddetmiyor doğru ama suffix-range 0-byte dosyada `start >= size` 416 fırlatır, `HEAD` desteği yok | `api/routes.py:945-988` |
| Y-2 | `upload` 10 GB limiti streaming’de `await file.read(1MB)` döngüsü; büyük dosyada bellek düşük ama süre aşımı yok, nginx yokken DoS | `api/routes.py:1058-1092` |
| Y-3 | `bulk` endpoint’lerde `job_ids` 500 limit var (`models.py:247` `max_length=500`) ama `BATCH` içinde `JobBatchCreateRequest.jobs` limitsiz — tek istek 1000+ ffprobe tetikler | `core/models.py:86` |
| Y-4 | `audit.jsonl` append `asyncio.to_thread` ile ama aynı dosyaya eşzamanlı yazımda interleaving (iki worker aynı anda yazarsa satır bölünür) | `api/routes.py:93-109` + `api/auth.py:37` |
| Y-5 | `media/browse` symlink traversal doğru engelleniyor ama `current_path` istemciye ham `rel_path` döndürüyor; `..` entry’si UI’da gösteriliyor, tıklandığında `openPath("..")` → backend 400 | `frontend/src/pages/ConvertPage.tsx:163` + `api/routes.py` browse |
| Y-6 | `download_output` `FileResponse` `Content-Disposition` filename’i ASCII dışı (Türkçe) için RFC6266 encode etmiyor | `api/routes.py:925-931` |

### 2.3 ORTA (fonksiyonel bug / UX kırılması)

| # | Açıklama | Dosya |
|---|----------|-------|
| O-1 | `subtitle_language` boş string `""` filtreleniyor ama probe `"und"` dillerini de dışlıyor; kullanıcı “und” altyazıyı seçemez | `ConvertPage.tsx:66-69` + `worker/main.py:313` |
| O-2 | `separate_srt` tek dosya üretiyor (`temp/<stem>.srt`), aynı batch’te iki iş aynı kaynak dosyadan çalışırsa temp çakışır (overwrite) | `worker/main.py:820,945-963` |
| O-3 | `audio_copy_fallback` sadece `mp4` için, `mkv` içinde `opus` → kopyalanamaz ama fallback yok; `webm`’de `aac` → fallback yok | `worker/main.py:365-377` |
| O-4 | `list_jobs` ikinci dal (numeric cursor) `queue_positions` hesaplıyor ama ilk dal (job-id cursor) da hesaplıyor — tutarlı ama `estimated_start_seconds = (pos-1)*60` sabit 60s varsayımı gerçek encode süresinden kopuk | `api/routes.py:581-622` |
| O-5 | `clearOutputs` tüm `outputs/*` siler, `.srt` sidecar’ları ve `thumbnails/*` bırakır — yetim dosya birikir | `api/routes.py:1118-1132` + `worker/main.py:947` |
| O-6 | `harware_encoder` sadece `h264_v4l2m2m` probe ediliyor, `hevc_v4l2m2m` listede ama hiç kullanılmıyor (ölü kod) | `worker/main.py:1142-1149` |
| O-7 | Frontend `loadMoreJobs` `hasNextJobs` varken çağrılıyor ama `listJobs` `status='all'` sabit; filtre aktifken “daha fazla yükle” filtre dışı kayıt getirir | `hooks/useServerState.ts:37-44` |
| O-8 | `formatBytes` TB üstünü göstermiyor (PB dosyası 1024 TB görünür), `formatEta` 24h üstünü `5h 30m` gibi gösterir, gün bilgisi yok | `utils/helpers.ts:20-41` |

### 2.4 DÜŞÜK (kod kalitesi / bakım)

- `api/main.py:113` `__getattr__` sync wrapper `asyncio.run()` test compat için; production’da çağrılırsa event-loop çakışması.
- `core/config.py:74` `_normalize_worker_concurrency` `max(1,min(8,int(...)))` sessizce kırpar, kullanıcı `16` yazınca uyarı yok.
- `worker/main.py:1029-1041` `_get_dynamic_concurrency` her döngüde `storage.get("system:settings")` json parse — 0.5s sleep ile 120 parse/dk, önbelleklenebilir.
- `frontend/src/api.ts:122-158` `listJobs` kendi `fetch`’ini kullanıyor (`request()` bypass), 401 → reload mantığı duplicate; `request()` zaten aynı işi yapıyor.
- `tests/worker/test_ffmpeg_command.py` sadece komut string’i test ediyor, gerçek ffprobe fallback’i test etmiyor.

---

## 3. Eksik Özellikler (Legacy’e Göre + Genel)

### 3.1 Legacy `mkv_to_mp4_converter.py` / `v1.2.py`’de Var, Projede Yok

| Legacy Özellik | Projedeki Durum | Boşluk |
|----------------|-----------------|--------|
| **Etkileşimli çoklu ses akışı seçimi** (`ses indexleri 1,2`) + her biri için ` -map 0:<idx>` ve ` -c:a:<n> copy/aac -b:a:<n> 96*channels -ac:<n>` | Sadece tek `audio_export` (copy/aac/mp3/opus) ve tek `0:a:0?` | Kullanıcı DTS → AAC isterken diğer sesleri kaybediyor; çok dilli filmde tek dil kalıyor |
| **Çoklu altyazı çıkarma** — seçilen her index için `.lang.srt` / `.lang1.srt` dil sayaçlı | Tek `separate_srt` çıktısı (`<stem>.srt`), tek `subtitle_language` | TR+EN altyazılı MKV’de ikinci dil çıkarılamıyor |
| **Kanal sayısına göre bitrate** (`96*channels`) ve kanal koruma (`-ac`) | Sabit `-b:a 128k/192k` | 5.1 filmde 128k yetersiz, stereo’da fazla |
| **Uyumlu ses codec listesiyle koşullu copy** (`aac,ac3,mp3` → copy, diğerleri AAC) | Sadece `mp4+copy→aac` fallback, diğer konteynerlerde yok | Vorbis/TrueHD → MP4/MKV’de gereksiz fail |
| **Video hep `-c:v copy` + `+faststart`** (hızlı remux) | `prefer_stream_copy_video` yalnızca h264→mp4/mkv’de | HEVC’yi remux etmek isteyen kullanıcı re-encode’a zorlanıyor |
| **Klasör tarama + skip-existing** (`get_video_files`, `output.mp4` varsa atla) | Browser tek tek seçme, aynı dosya yeniden kuyruğa alınabiliyor | 50 MKV’lik arşivde kullanıcı aynı işi tekrar ekleyip zaman kaybediyor |
| **Altyazı dil çakışması suffix’i** (`.tr.srt`, `.tr1.srt`) | Yok | Aynı dilde iki altyazı üstüne yazıyor |
| **`mp4_fix.py` → `+genpts +faststart -c copy` onarım aracı** | Hiç yok | Bozuk moov/faststart olmayan MP4’ler için “tek tık onar” ihtiyacı |

### 3.2 Genel Eksikler (P5’in kalanı ve yeni ihtiyaçlar)

- **Per-job akış seçimi override**: Convert sayfasında global export; legacy’de her dosya için ayrı ses/altyazı index’i. Toplu işte tek dosyanın dili farklıysa ayar tutmuyor.
- **Thumbnail önbellek temizliği**: `temp/thumbnails/*.jpg` hiç silinmiyor, 1000 çıktıda disk şişer.
- **İş → çıktı linki**: `output_filename` var ama `JobRecord` → `OutputFileDto` eşleşmesi UI’da zayıf; kullanıcı hangi iş hangi dosyayı üretti ayırt edemiyor.
- **Upload + server source karışık batch**: `source_root_key`’li + `input_filename`’li aynı batch’te karışık oluşturulabiliyor, worker’da `validate_source_path` sonrası input_dir fallback’i belirsiz.
- **i18n**: TR/EN var ama `mkv_to_mp4`’deki Türkçe log mesajları gibi kullanıcı dostu hata çevirisi yok; `error_code` kullanıcıya gösterilmiyor, sadece `message`.

---

## 4. Frontend / Backend Uyumsuzlukları

| Alan | Backend (Pydantic) | Frontend (TS) | Uyumsuzluk |
|------|--------------------|---------------|------------|
| `HardwareAcceleration` | `auto/disabled/v4l2m2m` | `auto/disabled/v4l2m2m` | **Uyumlu** — ama `ConvertPage` Pi4 etiketi `Raspberry Pi V4L2`, backend’de sadece `h264_v4l2m2m` |
| `VideoExport` | `mp4/mkv/webm` | `mp4/mkv/webm` | **Uyumlu** — ancak `deriveProfile` mkv→`h265_mp4`, frontend `videoOptions` etiketleri doğru değil (MKV H.265 varsayıyor ama kullanıcı H.264 MKV isteyebilir) |
| `AudioExport` `copy` | Worker WebM’de `opus`’a zorlar | UI `copy` gösterir | Kullanıcı WebM+copy seçer, backend sessizce opus yapar — UI’da uyarı yok |
| `SubtitleExport` | `none/embedded/separate_srt` | aynı | **Uyumlu** |
| `JobRecord.archived` | boolean | `filteredJobs` içinde `sourceType` filtresi archived’i gizliyor | `list_jobs` `include_archived=false` default; dashboard’da arşivli işler hiç görünmüyor, kullanıcı “kayboldu” sanıyor |
| `progress_percent` | `0..100` nullable | `getProgress` `Number(job.progress_percent ?? 0)` |  `failed` işte worker `None` gönderiyor, UI 0% gösteriyor — doğru ama IYILESTIRME’de “%100 failed” fix’i ile tutarlı |
| `timeline/log_tail` | `list[dict]/list[str]` max 40/50 | `JobDetailDrawer` reverse listeliyor | **Uyumlu** |
| `queue_position` / `estimated_start_seconds` | API hesaplıyor | UI `queued` satırında `#pos · ETA` gösteriyor | Hesap 60s/job sabit — backend’de süre ortalaması yok |
| `priority` | `-10..10` int | UI `0/5/10/-5` | Daraltılmış; negatif yüksek öncelik UI’da yok |
| `max_attempts` | 1..10 | ConvertPage 1..10 input | **Uyumlu** |

---

## 5. Önerilen Geliştirmeler (Madde Madde)

### 5.1 Güvenlik & Sağlamlık (önce)

| # | Ne Yapılacak | Hangi Dosya | Karmaşıklık |
|---|--------------|-------------|-------------|
| G-1 | `ffprobe` timeout’u evrenselleştir: `_probe_duration_seconds`’a 15s timeout ekle, `_run_ffprobe_json` tüm çağrılarda kullan | `worker/main.py:472,251` | **Kolay** (1 saat) |
| G-2 | `LocalFileStore` jobId collision testi: `os.replace` EXDEV fallback (`shutil.move`) ekle | `worker/main.py:977` | **Kolay** |
| G-3 | `audit.jsonl` dosya kilidi: `fcntl.flock` (POSIX) veya `portalocker` ile satır atomikliği | `api/routes.py:93`, `api/auth.py:37` | **Orta** (3h) |
| G-4 | `upload` hız limiti + mime sniff: `python-magic` ile header doğrulama, `max_upload_bytes` streaming’de 413 fırlatma korunuyor ama erken abort ekle | `api/routes.py:1058` | **Orta** |
| G-5 | `listJobs` filtreli paginasyon: `hasNextJobs` filtreyle birlikte çalışsın (backend’de filtre sonrası cursor, frontend’de `queryKey`’e `filters` ekle) | `hooks/useServerState.ts:37` + `api/routes.py:558` | **Orta** |
| G-6 | `clearOutputs` sidecar temizliği: `.srt` ve `thumbnails` de sil | `api/routes.py:1118` | **Kolay** |
| G-7 | `api.ts` `listJobs` → `request()`’e taşı, duplicate 401 mantığını tekilleştir | `frontend/src/api.ts:122` | **Kolay** |

### 5.2 Çekirdek Dönüşüm (FFmpeg)

| # | Ne Yapılacak | Dosya | Karmaşıklık |
|---|--------------|-------|-------------|
| G-8 | Çok kanallı ses fallback matrisi: `mp4: {aac,ac3,mp3,eac,alac}→copy else aac`, `mkv: hepsi copy`, `webm: opus/vorbis→copy else opus` | `worker/main.py:365` | **Orta** (4h) |
| G-9 | `target_video_bitrate` ile `crf` çakışmasında uyarı: UI’da biri doluyken diğerini disable et | `ConvertPage.tsx:387` | **Kolay** |
| G-10 | `resolution` scale filtresi `prefer_stream_copy`’de bile uygulanmamalı — zaten doğru, ama test ekle | `worker/main.py:430` | **Kolay** |
| G-11 | `hevc_v4l2m2m` probe’u aktif et veya ölü kodu sil | `worker/main.py:1142` | **Kolay** |

### 5.3 Yeni Preset / Format

| # | Ne Yapılacak | Dosya | Karmaşıklık |
|---|--------------|-------|-------------|
| G-12 | MKV için H.264 profili: `h264_mkv` ekle (şu an mkv hep h265) | `core/models.py:18`, `worker/main.py:412`, `utils/constants.ts:28` | **Orta** |
| G-13 | `av1`/`hevc` webm desteği (opsiyonel, ffmpeg `libaom-av1` varsa) | aynı | **Yüksek** |

---

## 6. Legacy Scriptleri Entegre Etme Planı

> Amaç: `scripts/legacy/mkv_to_mp4_converter*.py` ve `mp4_fix.py`’deki **kanıtlı akış**’ı modern API/UI’ya taşımak, CLI’yi bozmadan.

### 6.1 Veri Modeli Genişletmesi

```python
# core/models.py — JobCreateRequest / JobRecord’a ekle (backward-compat, Optional):
audio_stream_indexes: Optional[list[int]] = Field(default=None, max_length=8, description="Seçilen ses stream index’leri; boşsa 0:a:0?")
subtitle_stream_indexes: Optional[list[int]] = Field(default=None, max_length=8)
audio_channel_mode: Optional[str] = Field(default="preserve", pattern="^(preserve|downmix2)$")
skip_existing_output: bool = False
enable_faststart_fix_only: bool = False  # mp4_fix modu: -c copy + genpts + faststart
```
- Mevcut `audio_export`/`subtitle_export` + `subtitle_language` korunur; yeni alanlar **override** eder.
- Frontend `models.ts` → `ExportSettings`’e aynı alanlar eklenir; `api-schema.ts` openapi-typescript ile yeniden üretilir.

### 6.2 Backend — Probe & Validasyon

| Adım | Dosya | İş |
|------|-------|----|
| L-1 | `core/path_validation.py` | `validate_indexes()` helper: `0 <= idx < stream_count` |
| L-2 | `api/routes.py` → `GET /api/v1/media/streams?root_key=&path=` **yeni endpoint** | `ffprobe -show_entries stream=index,codec_type,codec_name,channels:stream_tags=language,title -of json` döner; mevcut `subtitles` endpoint’i korunur. Cache 60s. |
| L-3 | `worker/main.py` → `_probe_all_streams()` | Tek ffprobe ile video/audio/subtitle hepsi, `_probe_subtitle_streams` ile birleştir |
| L-4 | `worker/main.py` → `_build_ffmpeg_maps()` | Seçili `audio_indexes` için `-map 0:<idx>` döngüsü, `compatible_audio_codecs = {"aac","ac3","mp3"}` → `copy` else `aac -b:a:<n> 96*ch -ac:<n>` (legacy formül). `subtitle_indexes` için dışa çıkarma döngüsü. |

### 6.3 Worker — Çoklu SRT Çıkarma

```python
# worker/main.py — separate_srt dalı (mevcut tek dosya yerine):
for idx in subtitle_indexes or _indexes_for_language(subtitle_language):
    lang = lang_of(idx) or "und"
    suffix = f".{lang}.srt" if lang_counts[lang]==0 else f".{lang}{lang_counts[lang]}.srt"
    out = outputs_dir / f"{stem}{suffix}"   # outputs’a yaz, temp değil
    cmd = ["ffmpeg","-y","-i",input, "-map", f"0:{idx}", str(tmp_srt)]
    # bit eşlem: legacy’deki defaultdict(int) aynen
```
- Temp çakışmasını önlemek için `temp/<jobId>-<idx>.srt` kullan, sonra `outputs/`’a taşı.
- **Dosya**: `worker/main.py:945-963` → genişlet, `tests/worker/test_ffmpeg_command.py`’a `test_multi_audio_extract` ekle.

### 6.4 `mp4_fix` Endpoint’i

```
POST /api/v1/tools/mp4-fix   body: {source_root_key, source_path}
→ worker’da  `ffmpeg -y -fflags +genpts -i in -map 0 -c copy -movflags +faststart out.tmp && replace`
```
- **Dosya**: yeni `api/routers/tools.py`, `worker/main.py`’a `fix_mp4()` helper, `frontend/src/pages/ConvertPage.tsx`’a “Onar” sekmesi (tek dosya seç → Onar).
- Alternatif: `enable_faststart_fix_only=true` job’u normal queue’dan geçir (mevcut worker’ı bozmaz).

### 6.5 Frontend — ConvertPage Revizyonu

| UI Parçası | Değişiklik | Dosya |
|------------|-----------|-------|
| Stream tablosu | Browse → “Akışları Göster” butonu: `GET /media/streams` ile tablo (Index/Kodek/Dil/Kanal/Başlık), checkbox’larla çoklu seçim | `ConvertPage.tsx:49` `addSelected` sonrası değil, seçim ekranında |
| Batch aynı indeksleri uygulama | `v1.2`’deki gibi: ilk dosyadan örnek akış + “Bu seçimleri tüm seçili dosyalara uygula” checkbox | `ConvertPage.tsx` |
| Skip existing | `skip_existing_output` toggle, batch submit’te `422` yerine “atlandı” toast | `api.ts:167` `createJobsBatch` |
| Onarım | Ayrı kart: “MP4 Onar (faststart + genpts)” | `ConvertPage.tsx` |

### 6.6 Tahmini Karmaşıklık (Legacy Entegrasyonu)

| Faz | İş | Efor |
|-----|----|------|
| L-A (Model+Probe) | Pydantic + TS + yeni endpoint | **Orta (1 gün)** |
| L-B (Worker multi-audio/SRT) | FFmpeg map döngüsü + bitrate/channel | **Orta-Yüksek (2 gün)** |
| L-C (mp4_fix) | Tool endpoint + UI | **Kolay-Orta (0.5 gün)** |
| L-D (Test) | `test_ffmpeg_command` + `test_job_repository` + `test_outputs_media` | **Orta (1 gün)** |
| **Toplam** | | **~4.5 gün** |

### 6.7 Geriye Uyum & Risk

- Yeni alanlar `Optional` → eski işler bozulmaz.
- `audio_export=copy` + `audio_stream_indexes=[1,2]` çakışırsa indexler kazanır (dokümante et).
- `separate_srt` + `subtitle_stream_indexes` birlikteyse dil filtresi ignore edilir.

---

## 7. Uygulama Sırası / Fazlar (Önerilen Yol Haritası)

### Faz 0 — Hızlı Kazançlar (0.5 gün, risksiz)
- G-1 (timeout), G-2 (EXDEV), G-6 (sidecar temizlik), G-7 (api.ts tekilleştirme), O-6 (hevc probe sil), O-8 (formatHelpers gün desteği).
- Test: `pytest tests/worker` + `npm run build`.

### Faz 1 — Legacy Çekirdek (1. gün)
- L-A: Model genişlet + `GET /media/streams` endpoint + frontend stream tablosu skeleton.
- Validasyon: `tests/api/test_source_validation.py` genişlet.

### Faz 2 — Worker Çoklu Akış (2 gün)
- L-B: `_probe_all_streams`, `_build_ffmpeg_maps`, çoklu SRT döngüsü, kanal-bitrate.
- `tests/worker/test_ffmpeg_command.py`’a 4 yeni test (multi-audio copy, multi-audio transcode, multi-SRT lang suffix, skip-existing).

### Faz 3 — Onarım Aracı & UI Cilası (1 gün)
- L-C: `POST /tools/mp4-fix` + ConvertPage “Onar” kartı + Confirm.
- G-5 (filtreli pagination) + G-8 (ses fallback matrisi).

### Faz 4 — Sağlamlık & Gözlemlenebilirlik (1 gün)
- G-3 (audit lock), G-4 (upload mime), throttle `worker_health` poll 5s → 10s (Pi), `queue_position` ortalama süre (son 10 job ortalaması).
- `docker-compose.yml` `stop_grace_period` zaten 10m — dokümente et.

### Faz 5 — Genişletme (opsiyonel, 2 gün)
- G-12 (h264_mkv preset), thumbnail GC cron, job→output linki, i18n hata kodu gösterimi.
- CI’ya `pytest` + `npm run build` + `docker build` cache ( `IYILESTIRME_PLANI` P2 son madde zaten yapıldı — doğrula).

**Bağımlılık sırası**: Faz 0 → Faz 1 → Faz 2 (Faz 2 bitmeden Faz 3’ün worker kısmı başlanmamalı) → Faz 4 paralel.

---

## 8. Test & Doğrulama Matrisi

| Alan | Komut | Beklenen |
|------|-------|----------|
| Backend birim | `pytest tests/api tests/core -q` | Yeşil, yeni stream endpoint 200 |
| Worker | `pytest tests/worker -q` | multi-audio komutları doğru, `-map 0:<idx>` sayısı = seçili ses sayısı |
| Local storage | `pytest tests/core/test_local_storage.py -q` | blmove, pipeline rollback korunur |
| Frontend | `cd frontend && npm run build` | `tsc -b` hatasız, `api-schema.ts` güncel |
| Manuel | Browser: `/convert` → MKV seç → Akışları Göster → 2 ses + 2 altyazı seç → Batch 2 dosya → Dashboard’da timeline/log_tail görünüyor mu? |
| Onarım | `POST /tools/mp4-fix` → `ffprobe -v error -show_entries format=flags -of json` ile `movflags faststart` doğrula |
| Docker | `docker compose config && docker compose up --build -d && curl /health/ready` | 200, `worker:heartbeat` 30s içinde |

---

## 9. Riskler & Kaçınılacaklar

- **Monolitik `api/routes.py`’yi tekrar büyütme**: L-A endpoint’ini `api/routers/media.py`’a ekle, `_select` predikate’ini güncelle; routes.py tek kaynak kalsın.
- **Host path sızıntısı**: yeni `streams` endpoint’i sadece `rel_path` döndürsün, `Path` absolute’u loglama.
- **FFmpeg thread oversubscription**: `G-8` sonrası `WORKER_CONCURRENCY × FFMPEG_THREADS` 8’i geçerse uyarı logla.
- **Local vs Redis davranış farkı**: `publish` local’de no-op — SSE fallback zaten var, yeni tool endpoint’inde de aynı fallback’i kullan.

---

## 10. Ek Notlar (İnceleme Sırasında Görülen İyi Noktalar)

- `core/job_repository.py:382` `queue_positions()` `HIGH+QUEUE+LOW` sırasıyla doğru öncelik.
- `worker/main.py:142` `requeue_active_jobs` shutdown’ta requeue — redeploy’da iş kaybı yok.
- `api/auth.py:251` `credentials_updated_at` ile token revoke — P1’in en zor maddesi temiz çözülmüş.
- `frontend/src/hooks/useServerState.ts` `EventSource` + ticket + 30s retry + 300ms debounce — P0 SSE zinciri düzgün.

---

> **Sonraki adım**: Faz 0’ı PR’la, ardından L-A → L-B için ayrı PR’lar aç. Her PR’da `pytest` + `npm run build` + `docker compose config` çalıştır. Legacy entegrasyonu bittiğinde `scripts/legacy/*` README’ye “artık UI’da” notu ekle.
