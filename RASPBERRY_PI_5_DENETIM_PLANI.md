# Raspberry Pi 5 - Video Donusturme Denetimi ve Iyilestirme Plani

Denetim tarihi: 2026-10-06.

Bu belge, video donusturme dogrulugu, Raspberry Pi 5 dagitimi, CPU sinirlandirma, optimizasyon ve ayar sadelestirme bulgularini onem sirasina gore listeler. Mevcut iyilestirme planindan bagimsizdir; eski tamamlanmis maddeleri yeniden acmaz.

`- [x]` uygulanan ve yerelde dogrulanan maddeleri, `- [ ]` acik isleri gosterir. Denetim bulgulari tarihsel kanittir; asagidaki P0 bolumu duzeltme sonrasi sonucu kaydeder. Fiziksel Pi/Docker runtime kontrolleri ayrica P4 kapsaminda acik kalir.

Kanita gore ayrim:
- **Calisma zamaninda dogrulandi:** gercek FFmpeg/worker senaryosu calistirildi ve sonuc gozlemlendi.
- **Kaynak koduyla dogrulandi:** ilgili kod yolu incelendi; hedef ortamda davranis olculmedi.
- **Hedef ortamda dogrulanacak:** fiziksel Pi, ARM64 image veya Docker calistirmasi gerekiyor.
- **Onay gerekiyor:** kaldirma veya urun davranisi degisikligi uygulanmadan once kullanici karari gerekiyor.

Oncelikler:
- **P0:** guvenlik, kaynak dosya butunlugu veya sessizce yanlis sonuc ureten hatalar; dagitimdan once ele alinmali.
- **P1:** donusumu bozan, is durumunu kilitleyen veya Pi kaynak korumasini etkileyen hatalar.
- **P2:** performans, depolama ve isletim iyilestirmeleri.
- **P3:** kullanici onayina sunulan ayar kaldirma ve sadelestirme.
- **P4:** hedef cihazda kabul ve uzun sureli dogrulama.

Kaynak satir numaralari denetim anina aittir; sonraki degisikliklerde kayabilir.

## P0 - Dagitim Oncesi Kritik Duzeltmeler

- [x] **P0.1 - Compose kimlik bilgisi degiskenlerini aktar; ilk kurulum hesabini koru.**
  - **Duzeltme:** Compose `APP_USERNAME`, `APP_PASSWORD`, `JWT_SECRET` degerlerini app environment'a aktarir. Bos parola ilk kurulumu, bos secret mevcut otomatik kaliciligi korur. `.env.example` ve README public erisimden once ozel agda ilk kurulumu tamamlama uyarisi icerir.
  - **Kanıt:** resmi Compose v2.39.4 `config --format json` ile dolu/bos degerlerin gercek render sonucu kontrol edildi. Izole API ortaminda tanimli hesapla giris calisti; ayni ortamda ilk hesap olusturma girisimi 409 ile reddedildi.
  - **Sinir:** Docker daemon ve Coolify calistirilmadi; gercek konteyner/host kabul P4'te acik. Depoda kayitli UI kimlik bilgileri env'den once gelir.

- [x] **P0.2 - MP4 onarimi kaynak medyayi degistirmeden yeni cikti uretsin.**
  - **Duzeltme:** UUID isimli kopya `DATA_ROOT/temp` icinde hazirlanip `outputs` altina yayimlanir. Kaynak/destination esitligi, root disina cikis ve mevcut cikti reddedilir. Cross-device yayimlama gizli staging dizininden yapilir; yarim kopya listeye girmez.
  - **Koruma:** bos disk alani ve kaynak boyutu payi kontrol edilir. Toplam onarim deadline'i mevcut `FFMPEG_STALL_TIMEOUT_SECONDS` degeridir. Timeout/iptalde terminate, gerekirse kill ve child wait uygulanir; temp/staging temizlenir. Request disconnect ve UI AbortController iptali desteklenir.
  - **API/UI:** yanit yeni filename ve authenticated download_url icerir. UI kaynagin korunacagini, missing-moov kisitini anlatir; kopyayi indirme ve iptal sunar, cikti cache'ini gecersiz kilar.
  - **Kanıt:** salt okunur Windows dosya bayragiyla gercek MP4 onarildi; kaynak hash'i ayni, yeni cikti decode edildi, authenticated download baytlari ciktiyla ayni, temp temiz. Tarayicida gercek onarim/indirme/iptal kontrol edildi. Timeout, child kill, disk, traversal ve cross-device hata temizligi regression testleri gecti.
  - **Sinir:** Windows read-only bayragi Linux read-only mount kabulunun yerine gecmez. Tamamlanmis yayimlama iptalle geri alinmaz. Cross-device copy sirasinda iptal kontrolu yayimlamadan once yapilir; missing moov yeniden insa edilmez.

- [x] **P0.3 - H.264 kopyalama kalite/bitrate/cozunurluk istegini atlamasin.**
  - **Duzeltme:** JobCreateRequest ve JobRecord quality_crf nullable/default None. Kalite belirtilmemis/null, bitrate yok ve original cozunurluk ise uyumlu H.264 kopyalanabilir. Acik CRF, bitrate veya yeniden boyutlandirma encode gerektirir. Command builder da bu invariant'i korur.
  - **UI/preset:** UI kalite degerini acikca gonderir; normal UI donusumu artik gercek encode yapar. Eski yanlis kopyalama davranisindan daha fazla CPU kullanabilir. Yeni bir mod/flag veya uyumluluk alias'i eklenmedi.
  - **Kanıt:** gercek API/worker akisi 1280x720 kaynaktan 854x480 cikti uretti. Kalite atlanmis API isinde sikistirilmis video packet SHA256 degerleri kaynakla ayniydi. Bitrate senaryosu da tamamlandi; ciktilar decode edildi.

- [x] **P0.4 - CRF=0 ilk komut ve fallback'te korunsun.**
  - **Duzeltme:** sifir ile None ayrildi; iki command yolu ayni cozulmus kaliteyi kullanir. CRF desteklemeyen V4L2 bitrate encoder'i acik CRF isteginde secilmez; yazilim encoder'i kullanilir. Bitrate varsa CRF yerine bitrate uygulanir; UI CRF kontrolunu aciklamayla devre disi birakir.
  - **Kanıt:** gercek x264 worker logunda lossless qp=0 goruldu. V4L2 mevcutmus gibi ilan edilen worker'da auto + CRF0 yazilimla tamamlandi; cikti decoded-frame MD5'i kaynakla ayniydi. Tarayicida bitrate dolu/bos gecisinde CRF disabled/enabled davranisi kontrol edildi.
  - **Sinir:** CRF0 tum codec'lerde ayni kayipsiz semantik olarak ilan edilmez. Explicit CRF'li is copy denemesi yapmadigindan eski copy-fallback sifir kaybi normal akista olusmaz; fallback yine sifiri koruyan ortak degeri kullanir.

- [x] **P0.5 - Secilmis/tekli ses codec, bitrate ve stereo tercihini uygula.**
  - **Duzeltme:** acik AAC/MP3/Opus secimi secilmis izlerde de uygulanir; copy uyumluluk matrisi yalniz copy isteginde kullanilir. downmix2 her iki yolda encode + 2 kanal gerektirir; copy isteginde AAC, WebM'de Opus fallback. MP3 artik istenen bitrate'i kullanir.
  - **Kanıt:** gercek secilmis FLAC izleri AAC oldu; ters secim sirasi, eng/tur etiketleri ve preserve kanal sayilari korundu. Tekli/secili copy+downmix 2 kanal uretti; MP3 160 kbps uygulanip decode edildi. Gercek medya regression matrisi MP4/MKV/WebM downmix, audio olmayan kaynak, codec ve kalite senaryolarini kapsar.
  - **Olcum notu:** cok kisa MP4 sesindeki ffprobe ortalama bitrate padding nedeniyle nominal degerden biraz farkli olabilir; regression testi %2 toleransla 160k istegini kontrol eder, eski 192k davranisini reddeder.

### P0 Yerel Kabul Sonucu

- `venv/Scripts/python.exe -m pytest tests/api tests/core tests/worker`: **198 passed, 1 skipped**. Symlink destination testi Windows izin kisiti nedeniyle atlandi; diger traversal ve confinement testleri gecti.
- `npm run build`: TypeScript ve Vite build basarili. Frontend OpenAPI tipleri yeni kalite/onarim sozlesmesinden yeniden uretildi.
- Gercek API + worker ile 6 donusum senaryosu, kaynak hash kontrolu, ffprobe ve decode; ek auto-hardware CRF0 lossless smoke.
- Gercek API onarim ve authenticated download; masaustu/mobil tarayici goruntusu, indirme ve iptal, CRF/bitrate precedence. Mobilde yatay tasma yok (390px viewport/content).
- Python 3.14 ile mevcut FastAPI/Starlette/AnyIO deprecation uyarilari devam ediyor; bunlar bastirilmadi. Fiziksel Pi/ARM64/Docker runtime kaniti yok; P1-P4 maddeleri tamamlanmis sayilmadi.

## P1 - Donusum Basarisi ve CPU Koruma

- [x] **P1.1 - Sahte V4L2 cihaz algilamasini kaldir.**
  - Compose'tan `/dev/null:/dev/video11` kaldirildi. Varsayilan dagitim cihaz baglamaz; Pi 4 icin gercek cihaz/grup/uid inline override README'de belgeli ve Compose ile render edildi.
  - Worker encoder listesini veya cihaz varligini yeterli saymaz: her encoder icin 10 saniye deadline'li iki frame gercek encode probe kullanir.
  - `auto` donanim encode hatasinda yazilimla yeniden dener; iptal/shutdown fallback'i engeller. Acik `v4l2m2m` talebi cihaz yoksa veya CRF'yi karsilayamiyorsa acik hata verir. Kopyalamaya uygun kaynakta acik donanim talebi encode yapar; auto kopyalamada kullanilan donanim null'dir.
  - Yerel gercek non-H264 kaynak yazilimla tamamlandi; ilan edilmis ama kullanilamayan V4L2 encode hatasindan sonra auto yazilimla tamamlanip decode edildi. Fiziksel Pi 4/5 cihaz kabul testi yapilmadi.

- [x] **P1.2 - Altyazi dil secimini sayisal indekslerle uygula.**
  - ffprobe global indeksleri kullanilir; hatali dil optional-map ifadesi kaldirildi. Acik indeksler dile gore oncelikli; tekrarlar secim sirasiyla silinir. Dil tum eslesen izleri kaynak sirasiyla, secim yoksa ilk izi alir.
  - Gercek MP4/MKV/WebM gomulu ve ayri SRT matrisi gecti. Iki English izinin ikisi de korunur; `[4,2,4]` seciminde Turkish/English sirasi korunur. Bulunamayan dil deterministik uyari verir.
  - Tek dil/varsayilan tek iz eski `.srt` adini korur; acik indeks veya coklu eslesme `.eng.srt`, `.eng1.srt` gibi adlar kullanir. Dil etiketleri yan dosya adi icin sanitize edilir.
  - Windows FFmpeg 9.0.1 dogrulandi. Docker image FFmpeg/ARM64 surumler arasi kabul P4'te acik.

- [x] **P1.3 - Eksik altyazi sonucunu API ve dashboard'da bildir.**
  - `JobRecord.warnings` kalici liste; API/OpenAPI/TypeScript kontrati yenilendi. Dashboard uyari sayisini, is detaylari mesaji gosterir. Video basariliysa `completed` ve `Conversion completed with warnings`; eksik yan dosya basari gibi gizlenmez.
  - Bulunamayan dil/iz, probe hatasi, uyumsuz bitmap ve SRT hata/missing/timeout durumlari test edildi. MKV bitmap kopyalayabilir; MP4/WebM/text-SRT icin OCR gerektiren iz atlanir ve bildirilir.
  - Gercek olmayan jpn dil istegi videoyu korudu/decode etti; mobil ve masaustu UI uyari goruntusu dogrulandi. Yeni deneme eski uyarilari ve kullanilan donanim alanini temizler.
  - Altyazi yan dosyalari istege bagli sonuc politikasindadir; basarili video once yayimlanir. Mevcut SRT subprocess timeout'u 1800 saniye; extraction sirasinda canli iptal davranisi bu P1 degisikliginde genisletilmedi.

- [x] **P1.4 - retry:null kaynakli running kilidini duzelt.**
  - Worker null/eksik veya bozuk retry kaydini `RetrySettings` varsayilanlariyla cozer. Transient hata backoff ile queued/retry_wait; terminal hata failed olur.
  - Varsayilan/null ayar API'den gercekten kaydedildi; bozuk medya ve enqueue sonrasi silinen kaynak gercek worker'da failed/hata mesajiyla sonlandi. Null/eksik/transient regression matrisi gecti.

- [x] **P1.5 - Uyumsuz kapsayici/ses kombinasyonunu kuyruk oncesinde engelle.**
  - Merkezi backend validator JobCreateRequest, default export ve legacy worker kaydina uygulanir. WebM yalniz Opus/copy (Opus fallback); MP4/MKV copy/AAC/MP3/Opus kabul eder. Acik codec sessizce degistirilmez.
  - Tekli/batch/validate isteklerinde WebM AAC/MP3 422 structured actionable hata, kuyruk degismedi. Varsayilan ayar kaydinda da ayni kontrol var.
  - Convert/Preset/Settings ekranlari gecerli secenekleri sunar. Eski veya kapsayici degisimiyle uyumsuz tercih disabled olarak korunur, uyari ve submit engeli gorunur. Gercek tarayicida kontrol edildi. MP4/Opus tum oynaticilarda uyumluluk garantisi degil.

- [x] **P1.6 - Docker CPU/bellek kotasini belgele ve yerel render'i dogrula.**
  - `.env.example` ve README mevcut APP/REDIS CPU/bellek limitlerini, app cocuklarinin ortak kotasini, Redis'in ayri kotasini ve inspect/cgroup/stats kabul komutlarini icerir. Varsayilan 2.0/0.5 CPU ve 2g/256m korunur.
  - Pi 5 baslangici: `APP_CPU_LIMIT=1.5`, `REDIS_CPU_LIMIT=0.25`, `WORKER_CONCURRENCY=1`, `FFMPEG_THREADS=1`. 4 cekirdekte teorik toplam app+Redis CPU-zaman kotasi %43.75; tum host limiti degil. Docker disinda CPU env'i etkisiz.
  - Resmi Compose v2.39.4 config JSON: 1.5/0.25 ve 2147483648/268435456 bytes; devices yok. Varsayilan kotalar ve opsiyonel gercek Pi4 cihaz override render'i da dogrulandi.
  - **Hedef kabul acik:** Docker daemon/Pi erisimi yok. NanoCpus/cgroup enforcement, uzun is docker stats, termal ve Coolify kabul P4 kapsaminda yapilmadi; render enforcement kaniti degil.

- [x] **P1.7 - Decoder, filtre ve x265 thread havuzlarini sinirla.**
  - Decoder `-threads` girdi `-i` oncesinde; filter_threads/filter_complex_threads ve output encoder threads ayni FFMPEG_THREADS politikasinda. x265 pools/frame-threads sinirli. SRT extraction da decoder/filter/output thread kapsamlarini kullanir.
  - Gercek H.264/H.265/VP9 scale+AAC/Opus ciktisi decode edildi. Windows kisa sentetik karsilastirma (CPU tek cekirdek olcegi, 50ms sampling; Pi benchmark'i degil):

    | Codec | Eski/yeni sure (s) | Eski/yeni peak RAM (MB) | Eski/yeni process thread | Eski/yeni peak CPU (%) |
    |---|---:|---:|---:|---:|
    | H.264 | 0.546 / 0.547 | 39.4 / 35.6 | 21 / 12 | 217.8 / 216.2 |
    | H.265 | 1.671 / 1.981 | 147.2 / 73.9 | 30 / 14 | 249.1 / 279.4 |
    | VP9 | 5.633 / 5.637 | 61.6 / 57.7 | 21 / 12 | 186.8 / 186.5 |

  - x265 gercek logu once 8, sonra 1 thread pool ve 1 frame thread bildirdi. Thread ayari tum processi tek thread yapmaz, CPU sert tavani degildir; CPU tepe degeri azalmasi garanti edilmez. Hedef Pi/uzun medya throughput/RAM kabul P4'te acik.

- [x] **P1.8 - WORKER_CONCURRENCY sert tavanini uygula.**
  - Env 1..8 sert tavan; kayitli UI ayari yalniz azaltabilir. Eski volume yuksek degeri worker/API'de clamp edilir; API yeni tavan ustu istegi 422 reddeder. Client'in gonderdigi limit yetkili degildir.
  - Havuz env tavaninda; dequeue sonrasi dusen ayar yeniden kontrol edilir. Dusurme calisan isleri kesmez, yeni dispatch'i bekletir. UI secenekleri tavanla sinirli ve sozlesme acik yazili.
  - Worker heartbeat ve health API limit/etkin degeri sunar. Gercek loop env=3/kayit=1 ile limit3/etkin1 bildirdi ve graceful durdu. Volume eski degeri, ilk kurulum, dusurme ve aktif isleri koruma regression testleri gecti.

### P1 Yerel Kabul Sonucu

- Tam `venv/Scripts/python.exe -m pytest`: **252 passed, 1 skipped** (Windows symlink izin testi). Mevcut dependency deprecation uyarilari bastirilmadi.
- `npm run build`: TypeScript/Vite basarili. Degisen yuzeyler icin UI detector sonucu `[]`; gercek masaustu/mobil Convert/Preset/Settings/dashboard kontrolleri yapildi.
- Gercek API, worker, FFmpeg/ffprobe ve decode; dil/indeks/SRT, eksik kaynak/bozuk medya/null retry, auto software/donanim hata fallback, limit heartbeat; resmi Compose render dogrulandi.
- Docker daemon, fiziksel Pi 4/5, ARM64 image, Docker FFmpeg surumu ve kota enforcement/termal kabul yapilmadi. Bunlar P4 acik maddeleridir; yukaridaki `[x]` kod ve yerel dogrulamayi belirtir, hedef cihaz sertifikasi degil.

## P2 - Performans, Depolama ve Isletim

- [ ] **P2.1 - Pi 5 icin dusuk maliyetli codec/preset varsayilanlarini koru.**
  - **Kanıt:** kaynak koduyla dogrulandi. Varsayilan MP4/H.264, veryfast, tek is genel olarak uygun baslangic. UI ve `helpers.ts:54-57` MKV'yi zorunlu H.265, WebM'yi VP9 profiline bagliyor.
  - **Oneri:** kapsayici ile codec'i karistirma; MKV'nin H.264 de tasiyabildigini modelle. H.265/VP9/slow seceneklerini varsayilan yapmak yerine gelismis bolumde tut. Kaldirma kullanici onayina bagli; bunlar gercek islevler.
  - **Kalite:** H.264 CRF=23 baslangic olabilir; H.265/VP9 ayni sayida ayni kaliteyi vermez. Builder'daki 23/28/33 varsayilanlari process_job tarafindan her zaman 23 gonderilmesiyle etkisiz kalabiliyor; codec bazli varsayilan politikasi netlestirilmeli.
  - **Kabul:** fiziksel Pi'de ayni kaynak/istenen kaliteyle sure, CPU, RAM ve cikti boyutu karsilastirilsin. Resmi Pi raw-video benchmark'i bu uygulamanin decode/ses/scale maliyeti icin performans garantisi sayilmasin.

- [ ] **P2.2 - WebM'de uyumlu sesi gereksiz yeniden kodlamayi onle.**
  - **Kanıt:** kaynak koduyla dogrulandi. `worker/main.py:227-232` WebM + copy secimini kosulsuz Opus'a ceviriyor. Mevcut Opus/Vorbis ses de bu nedenle yeniden kodlanabilir.
  - **Oneri:** copy talebinde once codec uyumlulugunu kontrol et; uygun Opus/Vorbis izi kopyala, uyumsuz ise acik fallback politikasi kullan. Ses codec'ini acik isteyen kullanicinin tercihini koru.
  - **Kabul:** WebM + Opus/Vorbis copy encoder acmadan gecsin; AAC/FLAC kaynak uygun fallback ile donussun. Secilmis coklu ses yollarinda da ayni politika olsun.

- [ ] **P2.3 - Ilerleme yazma ve ayar okuma maliyetini olcerek azalt.**
  - **Kanıt:** kaynak koduyla dogrulandi; optimizasyon kazanci olculmedi. `worker/main.py:914,921` watcher yaklasik 0.35 saniyede disk kontrolu yaparken `_minimum_free_disk_bytes` ayar deposunu okuyor. Progress guncellemeleri `job_repository.py:273-380` uzerinden kayit, telemetry ve yayin islerini tetikliyor.
  - **Oneri:** ayarlar icin kisa omurlu snapshot; ilerleme icin son guncellemeyi koruyan daha seyrek persistence degerlendir. Yeni genel cache katmani eklemek yerine mevcut dongude kucuk degisiklik tercih et.
  - **Kabul:** Redis/SQLite islem sayisi ve Pi CPU/IO once-sonra olculsun; iptal gecikmesi, disk guvenligi, UI ilerleme ve heartbeat bayatlamasin. Ayar degisiminin ne kadar gecikmeyle uygulanacagi acik olsun.

- [ ] **P2.4 - FFmpeg ve Docker loglari icin buyume siniri koy.**
  - **Kanıt:** kaynak koduyla dogrulandi. `worker/main.py:864-875` kalici FFmpeg loguna yazar; `_cleanup_outputs:1522-1564` log temizlemez. Compose'ta log rotation ayari yok.
  - **Risk:** cok sayida is veya tekrar eden hata, cikti temizligi acik olsa bile diski doldurabilir. SD kartta gereksiz yazma maliyeti vardir.
  - **Oneri:** bounded Docker logging ve FFmpeg log retention kullan; aktif isin logu silinmesin. Destek/inceleme icin yeterli hata izi korunsun.
  - **Kabul:** cok sayida tamamlanan/basarisiz is sonrasi log kullanimi belirlenen tavanda kalsin; aktif hata inceleme ve log indirme bozulmasin.

- [ ] **P2.5 - Disk guvenligini upload, yan dosyalar ve kaynak tiplerine gore tamamla.**
  - **Kanıt:** kaynak koduyla dogrulandi. Donusum basinda `max(disk esigi, kaynak boyutu)` kontrolu var; donusum sirasinda disk izleniyor. Upload yolu ayni bos alan esigini uygulamiyor; cikti boyutu onceden rezerve edilmiyor.
  - **Oneri:** upload ve onarim icin de disk tabani kullan. Buyuyen cikti, coklu ses/SRT ve eszamanli is icin yeterli pay koru; esigi sifirlamayi kolaylastirma.
  - **Pi onerisi:** medya ve uygulama verilerini SSD/NVMe uzerinde tut. `MIN_FREE_DISK_BYTES=4294967296` (4 GiB) olculecek baslangic olabilir; kayitli UI disk degeri env'i gecersiz kilabildigi icin etkin deger de kontrol edilmeli.
  - **Kabul:** dusuk bos alanla yeni upload/donusum guvenli reddedilsin; calisan is kontrollu dursun; kaynaga dokunulmasin ve yarim cikti yayimlanmasin.

- [ ] **P2.6 - Otomatik temizligi video ve SRT cikti gruplari uzerinden yap.**
  - **Kanıt:** kaynak koduyla dogrulandi. `_cleanup_outputs:1541-1556` dosyalari ayri siralar ve `keep_minimum_outputs` degerini dosya sayisi olarak uygular.
  - **Risk / cikarim:** video ve SRT ayri korunabilir veya silinebilir; en az N cikti ifadesi gercekte en az N dosyadir. Ciftlerin ayrilmasi fiziksel smoke ile dogrulanmadi.
  - **Oneri:** cikti grubunu video + yan dosyalar olarak tanimla; minimum koruma ve retention ayni gruba uygulansin. Varsayilan cleanup kapali kalsin.
  - **Kabul:** bir videonun coklu SRT'leri minimum sayiyi yanlis tuketmesin; aktif/yeni cikti grubu parcali silinmesin; is kaydi temizligi ayri ve anlasilir olsun.

- [ ] **P2.7 - Bellek, swap ve Redis buyumesi icin olculmus Pi profili belirle.**
  - **Kanıt:** kaynak koduyla dogrulandi; OOM fiziksel olarak uretilmedi. app varsayilan 2 GiB, Redis 256 MiB konteyner limiti var. Swap politikasi acik degil; Redis AOF acik, metadata buyumesi icin uygulama retention'i istege bagli.
  - **Risk:** 2 GB Pi'de mevcut bellek limitleri host/Coolify payini garanti etmez; 4K/H.265, AOF rewrite veya SD swap sistemi zorlayabilir. Limit rezervasyon degildir.
  - **Oneri:** cihaz RAM'i ve is yuku icin sinirlari olc. En az 4 GB Pi + SSD + tek is icin app=1536m, Redis=256m test baslangici olabilir; 4K ve tum medya tipleri icin garanti degil. Coolify ayni hostta ise kendi bellek ihtiyaci ayrica hesaba katilsin.
  - **Kabul:** uzun donusumde RSS, OOM, swap ve Redis rewrite izlenerek sinirlar secilsin; guvensiz eviction ile is/kuyruk verisi kaybedilmesin.

- [ ] **P2.8 - Volume yedekleme, izin ve tek-instance sozlesmesini belgeleyip dogrula.**
  - **Kanıt:** kaynak koduyla dogrulandi. app-data cikti/temp/log/JWT secret; redis-data kuyruk, ayar ve kimlik verisini tutar. `entrypoint.sh:16-22` ust dizinleri chown eder, tasinan dosyalara recursive izin duzeltmesi yapmaz. Worker acilis kurtarmasi `:1700-1704` baska worker'in aktif islerini sahiplenmeye uygun degil.
  - **Oneri:** iki volume birlikte yedeklensin; SSD/NAS izinlerinde UID 1000 kontrol edilsin. Mevcut mimaride tek app/worker instance kullan; replica arttirma bu denetimin disinda ayri tasarim gerektirir.
  - **Kabul:** yedekten geri donus sonrasi kimlik, kuyruk, ayarlar ve ciktinin korunmasi; veri dizinine yazma ve medyayi okuma yetkisinin dogrulanmasi.

- [ ] **P2.9 - Shutdown, yeniden baslatma ve health ifadelerini gercek davranisla uyumlu yap.**
  - **Kanıt:** kaynak koduyla dogrulandi. SIGTERM aktif FFmpeg'i durdurur ve isi yeniden kuyruga alir; donusum kaldigi yerden devam etmez. `stop_grace_period:10m` tum donusumun 10 dakika tamamlanmasini bekleme garantisi degil. `/health/ready` depoyu kontrol eder; gercek encode yetenegini kanitlamaz.
  - **Oneri:** belgede yarida kalan isin bastan baslayacagini belirt; worker heartbeat ile storage health'i ayir. Host CPU olcumu ile konteyner/FFmpeg CPU kotasi UI'da karistirilmasin.
  - **Kabul:** donusum sirasinda stop/redeploy, worker arizasi ve OOM sonrasi kuyruk/partial temp davranisi hedef ortamda gozlemlensin. Healthy konteynerin basarili donusum yaptigi ayri smoke ile dogrulansin.

- [ ] **P2.10 - ARM64 image ve build asamasinin kaynak kullanimi sozlesmesini netlestir.**
  - **Kanıt:** kaynak seviyesinde ARM64'e uygun gorunuyor: resmi Node/Python/Redis imajlari ve hedef mimaride apt ile FFmpeg kurulumu; sabit amd64 platform yok. ARM64 build veya bagimlilik kurulumu bu makinede calistirilmadi.
  - **Oneri:** 64-bit Raspberry Pi OS kullan; baska hostta uretilmis image'in ARM64 manifestini dogrula. Runtime `APP_CPU_LIMIT` npm/apt/pip image build islemlerini sinirlamaz; build sirasinda da CPU korunacaksa ayri ARM64 builder veya builder'a uygun kaynak siniri kullan.
  - **Tekrarlanabilirlik:** floating image etiketleri ve `pydantic>=2.11` surumu gelecekte ayni commit icin ayni runtime'i garanti etmez; surum/digest pinleme ayri iyilestirme karari olarak degerlendirilsin.
  - **Kabul:** image ARM64 olarak build/import edilsin; FFmpeg encoder'lari ve Python bagimliliklari gercek image icinde calissin; build kaynak etkisi runtime kotasiyla karistirilmasin.

## P3 - Kaldirma ve Sadelestirme: Kullanici Onayi Gerekiyor

Asagidaki maddeler mevcut haliyle onaylanmis degildir. Bir ayarin hatali veya etkisiz olmasi, kullanicinin o islevi istemedigi anlamina gelmez. Kaldirma, gizleme ve islevini duzeltme kararlari ayridir.

- [ ] **P3.1 - "Acil" oncelik secenegini kaldirma onayi al.**
  - **Kanıt:** `ConvertPage.tsx:448-459` Yüksek=5, Acil=10 sunuyor; `api/async_storage.py:169-173` ve repository yalniz pozitif/sifir/negatif ayrimi yapar. Iki secenek ayni high FIFO kuyruğuna gider.
  - **Oneri:** Normal/Yüksek/Düşük kalsin; davranis farki olmayan Acil kaldirilsin. Eski pozitif islerin kuyruk davranisi bozulmasin.
  - **Kabul:** UI/preset/API semantigi tek politika olsun; yeni farkli oncelik sistemi gereksiz yere eklenmesin.

- [ ] **P3.2 - Ayri "Raspberry Pi V4L2" secenegini kaldirma veya gercek davranisla ayirma onayi al.**
  - **Kanıt:** `worker/main.py:1137-1143`, disabled disindaki auto ve v4l2m2m degerlerine ayni davranir. Acik V4L2 secimi donanim yoksa da yazilima dusebilir.
  - **Oneri:** mevcut fark yaratmayan secenegi kaldir; donanim tercihi yalniz gercek yetenek varsa gelismis bolumde gosterilsin. Pi 4 desteğinin tamamen kaldirilmasi bu onerinin anlami degil.
  - **Kabul:** UI bir donanim kodlama garantisi vermesin; saklanmis tercihler icin acik gecis politikasi olsun.

- [ ] **P3.3 - Global ve is bazli maksimum deneme sayisini tek acik politikaya indir.**
  - **Kanıt:** `worker/main.py:1434-1435`, her zaman gecerli is degerini veya 3'u secer; global `retry.max_attempts` normal akista kullanilmaz.
  - **Oneri / onay:** etkisiz global alani kaldirip gercek is alanini gelismise tasimak veya global varsayilanin uygulanmasini duzeltmek. Iki etkili kaynak varmis gibi gosterme.
  - **Metin:** deger toplam girisim sayisidir; 3, ilk deneme + en fazla 2 tekrar demektir. "Yeniden deneme sayisi" ifadesi buna gore duzeltilmeli.
  - **Kabul:** global retry ac/kapat ve bounded backoff korunsun; retry:null hatasi bu sadelestirmeden bagimsiz P1 duzeltmesi olarak ele alinsin.

- [ ] **P3.4 - Uygulanmayan global "Varsayilan cikti ayarlari" blogu icin kaldirma veya baglama karari al.**
  - **Kanıt:** `SettingsPanel.tsx` global default_export degerlerini kaydeder; `App.tsx:111-112` sabit `defaultSettings` ile baslar, `:160-171` global ayarlardan yalniz UI gorunumunu uygular. `:397-414` is payload'u yerel donusum ayarlarindan uretilir.
  - **Etkisiz alanlar:** gorunur profil, video formatı, ses formatı, altyazi modu ve dili; API-only kalite, video bitrate, ses bitrate, cozunurluk, encoder preset ve donanim tercihi. Depolanmalari uygulanmalari anlamina gelmez.
  - **Oneri:** blogu kaldirip donusum ayarlari + preset'leri tek kaynak yapmak. Kullanici global varsayilan istiyorsa blok kaldirilmasin; uygulama baslangici ve API inheritance sozlesmesi gercekten baglansin.
  - **Kabul:** secilen karar UI, API modeli, client tipleri ve belgelerde tutarli olsun. Yalniz gizlemek etkisiz ayar sorununu cozmez.

- [ ] **P3.5 - Cift camelCase/snake_case ExportSettings alanlarini temiz gecisle kaldir.**
  - **Kanıt:** `frontend/src/models.ts` ve `utils/constants.ts` ses/altyazi indeksleri, kanal modu ve skip alanlarini iki bicimde tasiyor; `App.tsx:410-414` nullish precedence uyguluyor. Varsayilan false/preserve bazi camelCase degerlerini maskeleyebilir.
  - **Oneri:** API ile uyumlu tek snake_case bicimi kullan. Eski localStorage preset degerlerini yuklemede tası; kullanicinin kayitli secimini sessizce kaybetme.
  - **Kabul:** tum callers tek bicime gecsin; obsolete aliaslar/re-exportlar kalmasin; eski preset'ler yeni sozlesmede ayni secimleri korusun.

- [ ] **P3.6 - Ayrintili donusum kontrollerini silmeden Gelismis bolumune tasi.**
  - **Ana ekranda kalsin:** format, ses, altyazi modu ve cozunurluk. Anlasilir kalite secimi sunulabilir; bunun davranisi gercek encoder ayarina bagli olmali.
  - **Gelismise tasinacaklar:** encoder preset, ham CRF, video/ses bitrate, is bazli girisim sayisi, kuyruk onceligi ve gercek donanim tercihleri.
  - **Onay siniri:** H.265 ve VP9 gercek ozellikler; Pi 5 icin pahali olabilmeleri bunlari izinsiz silme gerekcesi degil.
  - **Kabul:** varsayilan akis az kontrolle kullanilabilsin; gelismis seceneklere erisim ve kayitli preset'ler korunsun.

- [ ] **P3.7 - Yalniz ilgili durumda etkili kontrolleri goster ve hatali metinleri duzelt.**
  - **Altyazi:** none iken dil kontrolunu gizle. Bos secimin gercek davranisi ilk iz; "otomatik algila" bunu yanlis anlatmamali. Batch dil listesinin bir dosyada olan dili digerlerine otomatik garanti etmedigi acik olsun.
  - **Ses bitrate:** copy'de gizle; MP3 mevcut kodda sabit 192k kullaniyor, girilen sayi etkisiz. MP3 davranisini duzelt veya gercek bitrate goster. Coklu ses yolunun farkli politikasi P0.5 ile giderilsin.
  - **Preset:** VP9, hardware ve copy yollarinda uygulanmayan preset kontrolu aktif gorunmesin. VP9 icin desteklenmeyen x264/x265 preset'lerini anlamliymis gibi sunma.
  - **Kalite/bitrate:** birbirine alternatif mod olduklari belli olsun; bitrate seciliyken etkisiz CRF alanı aktif gorunmesin.
  - **Sistem rozetleri:** duzenlenen worker_concurrency degerini "aktif is sayisi" gibi gosterme; gercek active_jobs heartbeat verisi ayri.
  - **Kabul:** gorunen her kontrolun sonuca etkisi vardir; kosullu gizleme kaynak dogrulugu hatalarini ortmek icin kullanilmaz.

- [ ] **P3.8 - Temizlik ve gorunum detaylarini gruplandir; guvenlik ayarlarini koru.**
  - **Gelismise tasinacaklar:** temizlik retention, minimum korunacak cikti, terminal is temizligi ve saklama suresi; gorunum density tercihi.
  - **Kosullu gorunum:** cleanup kapaliyken alt alanlari gizle; is temizligi kapaliyken job retention gizli olsun. Density gercekte tablo hucre padding'ini etkiliyorsa tum uygulama yogunlugu vaadi verilmesin.
  - **Silinmeyecekler:** CPU kotasi, eszamanli is siniri, disk tabani, retry ac/kapat ve bounded politika, iptal, kimlik dogrulama, temizlikte veri koruma esikleri.
  - **Kabul:** kullanici onayi olmadan alan veya silme korumasi kaldirilmasin; destructive islemler anlasilir onayla sunulsun.

## P4 - Fiziksel Raspberry Pi 5 Kabul Kontrolleri

- [ ] **P4.1 - Hedef mimari ve image dogrulamasi yap.**
  - `uname -m` sonucu aarch64 ve Docker/image mimarisi ARM64 olsun.
  - Gercek image icinde `ffmpeg -version`, `ffprobe -version` ve encoder listesi kontrol edilsin.
  - Python bagimliliklari ve frontend ARM64 build/import yolunda calissin; salt image build basarisi donusum basarisi sayilmasin.

- [ ] **P4.2 - Gercek Docker CPU ve bellek kotalarini kontrol et.**
  - Compose'ta etkin APP_CPU_LIMIT / REDIS_CPU_LIMIT degerlerini gor.
  - Docker inspect HostConfig.NanoCpus, Memory ve MemorySwap; cgroup v2 varsa cpu.max/memory.max kontrol edilsin.
  - Uzun is sirasinda app ve Redis icin docker stats izlensin; tek cekirdek olcegi ile 4 cekirdekli host yuzdesi karistirilmasin.
  - Kotanin UI concurrency artirilsa da toplam tavan olarak kaldigi dogrulansin. Runtime kotasi ile build asamasi ayri olculsun.

- [ ] **P4.3 - Gercek medya matrisi ve uzun donusum calistir.**
  - H.264/H.265/VP9 kaynaklar, MP4/MKV/WebM ciktılar; ses yok, tek ses, coklu ses, 5.1 ve stereo.
  - Orijinal/720p/480p, CRF=0, hedef bitrate ve preset etkisi; istenen boyut/codec/kanal sayisi ffprobe ile kontrol edilsin.
  - SRT/ASS, varsa PGS/VOBSUB; gomulu ve ayri SRT; dil var/yok ve birden fazla ayni dil.
  - Kaynak dosya hash'i korunmali; tamamlanan cikti decode edilebilmeli. Kisa sentetik test uzun film performansinin yerine gecmez.

- [ ] **P4.4 - Termal ve guc davranisini olc.**
  - Uzun donusumde `vcgencmd measure_temp` ve `vcgencmd get_throttled` ile sicaklik/throttling izle.
  - Uygun guc kaynagi ve aktif sogutma ile test et; CPU kotasinin tek basina termal/voltaj garantisi olmadigini koru.
  - CPU, RAM, IO, is suresi ve cikti boyutunu birlikte kaydet; acceptable throughput'u gercek kaynakla belirle.

- [ ] **P4.5 - Iptal, disk azligi ve yeniden baslatma kabulunu yap.**
  - Gercek donusum sirasinda iptal; temp temizligi ve kaynak butunlugu.
  - Kontrollu dusuk bos alan; upload/donusum durdurma ve acik hata.
  - Docker stop/redeploy ve worker arizasi; yeniden kuyruga alma, bastan donusum ve tamamlanmis ciktinin korunmasi.
  - Sert kapanis/OOM sonrasi partial temp ve tekrar sahiplenme davranisi kontrollu test verisiyle incelensin; gercek kaynak/veri riske atilmasin.

- [ ] **P4.6 - Ilk kurulum, ag erisimi ve yedekten geri donusu kontrol et.**
  - Admin hesabi kurulmadan public erisim kapali olsun; Compose auth aktarimi gercekte dogrulansin.
  - Coolify kullaniliyorsa domain app servisine ve ic port 8765'e baglansin; host portunun proxy disinda dogrudan erisim acip acmadigi kontrol edilsin.
  - app-data + redis-data birlikte yedekten geri yuklensin; ayarlar, kuyruk, JWT secret ve ciktının kaliciligi kontrol edilsin.
  - SSD mountlari ve UID 1000 izinleri kontrol edilsin; medya salt okunur, uygulama verileri yazilabilir olsun.

## Denetimde Calistirilan Kontroller

- `python -m pytest tests/worker tests/core`: **72 passed**.
- Windows FFmpeg **9.0.1** ile gercek worker isleri: H.264 kopyalama, H.265 encode, VP9 encode, AAC/Opus, secilmis ses, stereo istegi, altyazi gomulmesi ve ayri SRT senaryolari.
- Basarili video ciktıları ffprobe ile incelendi ve yeniden decode edildi.
- H.264 480p isteginin uygulanmamasi; AAC/stereo tercihlerinin atlanmasi; dil map hatasi; eksik SRT ile completed sonucu; CRF=0'in 23 olmasi; WebM+MP3 hatasi; retry:null ile running kalma gercek senaryolarla goruldu.
- Gercek calisan VP9 isi iptal edildi: cancelled, temp/published cikti yok, kaynak hash'i degismedi.
- Gecici test dosyasinda MP4 onariminin kaynak dosyayi degistirdigi goruldu.
- FFMPEG_THREADS=1 ile H.264/H.265/VP9 process CPU ve OS thread sayisi olculdu; x265'in ayri thread havuzu logdan dogrulandi.
- Gecici medya, kuyruk, log ve cikti dosyalari temizlendi.

### Dogrulama Sinirlari

Fiziksel Raspberry Pi 5, ARM64 image build, Docker kota enforcement, Coolify canli dagitimi, uzun film throughput'u, sicaklik ve RAM kapasitesi bu makinede dogrulanmadi. Docker komutu mevcut degildi. Kaynak seviyesinde makul gorunen ARM64 uyumlulugu, gercek image/donusum kabulunun yerine gecmez. Mevcut testlerin gecmesi, yukarida gercek donusumde bulunan hatalari yakaladiklari anlamina gelmez.

## Kaynaklar

- [Raspberry Pi: H.264 encoding performance on Raspberry Pi 5-series computers](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-010033-WP-1-H.264%20encoding%20performance%20on%20Raspberry%20Pi%205_series%20computers.pdf)
- [Docker: CPU ve bellek kaynak sinirlari](https://docs.docker.com/engine/containers/resource_constraints/)
- [FFmpeg: secenek kapsami, thread ve filtre ayarlari](https://ffmpeg.org/ffmpeg.html)
- [x265: threading ve worker pool davranisi](https://x265.readthedocs.io/en/stable/threading.html)
- [Coolify: Docker Compose ortam degiskenleri](https://coolify.io/docs/applications/builds/docker-compose#environment-variables)
