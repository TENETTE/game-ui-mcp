---
name: game-ui-mcp
description: Oyun UI'ı (menü, HUD, buton, panel, bar, gösterge, ikon) çizerken ve canlandırırken game-ui-mcp server'ının temel gereçlerini kullanır: stilize ve gerçekçi (yarış, simülasyon, AAA) tarz, keyframe animasyon; Unity, Unreal, Roblox ve Assetto Corsa için PNG, 9-slice, katman ve animasyon çıktısı.
---

# game-ui-mcp ile oyun UI'ı çizmek

Bu skill, `game-ui-mcp` MCP server'ının çizim tool'larını (create_canvas, add_rect, add_arc, add_ticks, add_text, preview, export_asset...) iyi kullanmanın yolunu anlatır. Server'da AI görsel üretimi yok: her piksel senin çağırdığın şekil, yazı, ikon ve efektlerden gelir. Hazır bileşen de yok: "buton", "panel", "gösterge" ya da "pop animasyonu" diye bir tool bulunmaz, yalnızca temel gereçler (şekil, yazı, stil, keyframe) vardır. Ürünü sen tasarlarsın; aşağıdaki tarifler kopyalanacak şablon değil, başlangıç noktasıdır. Kalite tamamen senin tasarım kararlarına ve kendi çıktına bakıp düzeltmene bağlı.

Tool'lar listede yoksa (`create_canvas`, `preview` görünmüyorsa) server bağlı değildir. Kullanıcıya server'ı Claude'a bağlaması gerektiğini söyle (repo README'sinde kurulum var) ve çizmiş gibi davranma.

## Çalışma sırası

1. **Brief.** Motor (Unity / Unreal / Roblox / Assetto Corsa app'i), platform, tür, hangi parçalar lazım ve **stil ailesi**: gerçekçi mi (yarış, simülasyon, AAA) yoksa stilize mi (cartoon, arcade, sci-fi, fantastik). İkisinin kuralları farklı; "Stil ailesi" bölümüne göre ilerle. Eksikse en fazla 2-3 soru sor; kullanıcı "sen karar ver" dediyse karar ver ve ne seçtiğini yaz.
2. **Tema.** İlk iş `set_theme`. Renkleri ve tekrar eden stilleri orada tanımla, sonra her yerde `$token` ve stil adı kullan. Tek tek hex yazma: kit tutarsızlaşır.
3. **Canvas.** Her asset ayrı canvas, asset'in gerçek boyutunda. Ekran tasarımı ise 1920x1080 tek canvas (mockup).
4. **Çiz.** `batch` ile, elemanlara açık `id` vererek. Tek tek `add_*` çağrısı yavaş.
5. **Bak.** `preview` çağır ve resme gerçekten bak. Sorun bul, `update_element` ile düzelt, tekrar bak. En az bir tur düzeltme yapmadan export etme.
6. **Durumlar.** Buton gibi parçalarda `duplicate_canvas` ile hover / pressed / disabled üret.
7. **Hareket.** Gerekiyorsa `create_animation` + `set_keyframes` ile canlandır, `preview_animation` film şeridine bak ("Animasyon" bölümü).
8. **Tutarlılık.** `contact_sheet` ile bütün kiti yan yana gör.
9. **Export.** `export_asset` (motor + 9-slice), ikon setleri için `export_atlas`, hareketli parçalar için `export_layers`, animasyon için `export_animation`. Sonra motor tarafındaki kodu yaz.

## Tema: önce bunu kur

```json
{
  "colors": {"bg": "#0b0a12", "surface": "#1b1830", "primary": "#8b5cf6", "accent": "#22d3ee",
             "text": "#f4f1ff", "muted": "#9a93b8", "danger": "#f43f5e", "gold": "#fbbf24"},
  "styles": {
    "panel": {"fill": "$surface@0.93", "stroke": {"color": "$primary@0.55", "width": 2},
              "shadow": {"color": "#000000@0.55", "blur": 24, "dy": 10},
              "inner_glow": {"color": "$primary@0.18", "blur": 18}},
    "title": {"font": "Rajdhani", "weight": 700, "size": 30, "fill": "$text", "uppercase": "tr", "letter_spacing": 2},
    "body":  {"font": "Inter", "weight": 500, "size": 15, "fill": "$muted"}
  }
}
```

Palet kuralı: 1 zemin, 1-2 yüzey tonu, 1 ana renk, 1 vurgu, yazı + soluk yazı, durum renkleri (tehlike, başarı, altın). Daha fazlası dağınık görünür. Türkçe metinde `uppercase: "tr"` kullan (i → İ doğru çıkar).

Font seçimi (`list_fonts`): Inter (temiz, modern AAA menü), Barlow Condensed (motorsport, gerçekçi HUD; gerçek italiği var), Bebas Neue (uzun başlık harfleri), Michroma (geniş gösterge paneli yazısı), JetBrains Mono (sabit genişlikli rakam: süre, telemetri), Rajdhani / Orbitron (sci-fi, teknoloji), Cinzel (fantastik, ortaçağ), Fredoka (yuvarlak, casual), Bangers (çizgi roman, arcade). Bir kitte en fazla 2 aile (gerçekçi tarzda tipik ikili: Barlow Condensed + JetBrains Mono). Orbitron'da Ğ/ğ yok; tool uyarı verirse fontu değiştir.

## Canvas boyutu ve kenar payı

- Gölge ve glow canvas kenarında kesilir. Şeklin etrafında en az `blur × 1.5 + |dx,dy|` boşluk bırak. Örnek: blur 24, dy 10 gölgeli panel için her yanda ~40 px, altta ~46 px.
- Koordinatları tam sayı ver. Yarım pikseller kenarı bulanıklaştırır.
- Tipik boyutlar (1x): ikon 64 veya 128, envanter slotu 96, buton 280-360 × 80-110, 9-slice panel 192-256 kare, bar 360×64.
- Keskinlik için `scales: [1, 2]` export et. Roblox'ta görsel 1024 px'i geçmesin; tool geçerse uyarır.

## Stil ailesi: stilize mi, gerçekçi mi

| | Stilize (Roblox, cartoon, arcade) | Gerçekçi (yarış, simülasyon, AAA) |
| --- | --- | --- |
| Renk | canlı, çok renkli | nötr koyu zemin + tek vurgu rengi; renk yalnızca anlam taşır (kırmızı = limit, yeşil = iyi, mor = en iyi tur) |
| Kenar | kalın stroke (3-5 px), büyük radius | kıl inceliğinde çizgi (1-1.5 px), küçük radius (2-8) ya da keskin köşe |
| Efekt | belirgin glow, kalın gölge, parlama şeridi | çok yumuşak geniş gölge; glow sadece gerçekten ışık yayan şeyde (LED, ibre, aktif değer) |
| Yazı | iri, konturlu, eğlenceli font | dar grotesk; küçük açık aralıklı BÜYÜK HARF etiket + iri rakam; kontur yok |
| Yüzey | düz renk + gradient | koyu cam (yarı saydam), karbon, fırçalı metal |
| Yoğunluk | dolu, sıkı | ferah; az eleman, net hiyerarşi |

Kullanıcı bir oyunu örnek verdiyse (Forza, Assetto Corsa, bir AAA oyun) o oyunun arayüzünü birebir kopyalama: logo, isim ve aynı yerleşim olmaz. Tasarım dilini al (oranlar, tipografi disiplini, renk kullanımı) ve özgün bir düzen kur.

## Gerçekçi tarz kuralları

1. **Palet:** zemin `#07080a`-`#14171c` arası, yazı `#f2f4f7`, soluk yazı `#8b929c`, çizgi `#ffffff@0.14`, tek vurgu (ör. kırmızı `#ff3b30`). Durum renkleri (amber, yeşil, mavi, mor) yalnızca veri için.
2. **Cam panel:** `fill` koyu ve %85 opak dikey gradient (`#171a20d9` → `#0b0d10d9`), `stroke` 1 px ve kendisi gradient (üstte `#ffffff@0.26`, altta `#ffffff@0.05`: üst kenar ışık alır), `shadow` blur 24-28, dy 12, alfa 0.5. Radius 6-8. Vurgu için panelin sol kenarına 3-4 px renkli şerit (`clip` ile).
3. **Tipografi:** etiket = Barlow Condensed 600, 14-17 px, `uppercase`, `letter_spacing` 2-3, soluk renk. Değer = Barlow Condensed 700 `italic`, etiketin 2.5-8 katı boyut. Süre ve telemetri = JetBrains Mono (rakamlar zıplamaz). Etiketi sola, değeri sağa hizala (`middle-left` / `middle-right`), satırları 1 px çizgiyle ayır.
4. **Eğim:** hız hissi için `skew` 10-14 derece; bütün eğik parçalarda aynı açı. Eğik rect'in içindeki yazı italik olsun.
5. **Malzeme:** karbon `{"type":"carbon","color":"#3b4048","color2":"#111317","size":4}`; fırçalı metal `{"type":"brushed","color":"#b9c0c8","color2":"#6d747d","amount":0.7}`; torna metal (göbek, düğme) `{"type":"conic","stops":["#eef1f4","#646b75","#f7f9fa","#4c525a","#dfe4e9","#5c636d","#eef1f4"]}`. Dokuyu küçük alanlarda kullan (başlık şeridi, düğme, çerçeve); bütün paneli karbonla kaplama.
6. **Işık:** LED, ibre ve dolu yay kendi renginde `glow` alır (alfa 0.5-0.75, blur 10-18). Panel ve yazıya glow verme. Sis ya da ışık hüzmesi için `style.blur` verilmiş yarı saydam elips.
7. **Geçişler:** gradient'ler otomatik dither'lanır, koyu yumuşak geçişlerde bantlanma olmaz. Çok büyük blur'lu gölgede bant görürsen blur'u küçült ya da alfayı düşür.

### Gösterge (devir, hız, turbo, yakıt)

Açılar: 0 = saat 3 yönü, saat yönünde artar. Klasik kadran 135 → 405 (270 derecelik süpürme). Değer açısı = `135 + 270 × oran`.

```json
[{"op":"add_ellipse","group":"dial","cx":220,"cy":220,"rx":206,
  "fill":{"type":"radial","stops":[[0,"#1c2026"],[0.72,"#0c0e11"],[1,"#050607"]]},
  "stroke":{"color":{"type":"linear","angle":70,"stops":["#7b838d","#1b1e23","#565d66"]},"width":6},
  "style":{"shadow":{"color":"#000000@0.65","blur":40,"dy":16},"inner_shadow":{"color":"#000000@0.9","blur":26,"dy":6}}},
 {"op":"add_arc","group":"dial","cx":220,"cy":220,"radius":186,"start_angle":135,"end_angle":405,"thickness":12,"color":"#ffffff@0.07"},
 {"op":"add_arc","group":"dial","cx":220,"cy":220,"radius":166,"start_angle":345,"end_angle":405,"thickness":5,"color":"#ff3b30"},
 {"op":"add_ticks","id":"rpm","group":"dial","cx":220,"cy":220,"radius":176,"start_angle":135,"end_angle":405,
  "count":37,"major_every":4,"length":8,"width":1.5,"major_length":17,"major_width":3,
  "color":"#ffffff@0.42","major_color":"#f2f4f7","labels":["0","1","2","3","4","5","6","7","8","9"],
  "label_size":26,"label_font":"Barlow Condensed","label_weight":600,"label_italic":true,"label_color":"#d5dae1"},
 {"op":"add_arc","group":"value","cx":220,"cy":220,"radius":186,"start_angle":135,"end_angle":335,"thickness":12,
  "color":{"type":"conic","from":135,"to":405,"stops":[[0,"#dfe6ee"],[0.62,"#f2f4f7"],[0.76,"#ffb020"],[0.9,"#ff3b30"]]},
  "style":{"glow":{"color":"#ffb020@0.55","blur":18}}},
 {"op":"add_polygon","group":"needle","origin":[220,220],"rotation":335,
  "points":[[186,215.5],[388,218.8],[388,221.2],[186,224.5]],
  "fill":{"type":"linear","angle":90,"stops":["#ff6a60","#ff3b30","#b3170f"]},
  "style":{"glow":{"color":"#ff3b30@0.75","blur":14},"shadow":{"color":"#000000@0.7","blur":8,"dy":5}}},
 {"op":"add_ellipse","group":"hub","cx":220,"cy":220,"rx":22,
  "fill":{"type":"conic","stops":["#e6eaef","#5a616b","#f4f6f8","#474d55","#cfd5dc","#5a616b","#e6eaef"]},
  "stroke":{"color":"#000000@0.7","width":1.5}}]
```
Canvas 440×460. İbre sağa bakan ince bir çokgen olarak çizilir, `origin` göbektir, `rotation` değer açısıdır. `count` = (büyük aralık sayısı × ara bölme) + 1; yukarıda 9 × 4 + 1 = 37. Alttaki açık dilime vites ya da birim yazısı koy. Parçalara `group` ver: motor tarafında ibre ve yay ayrı oynar.

**Segmentli yay / LED sırası:** `add_arc` ya da `add_line` + `dash: [dolu, boş]` + `cap: "butt"`. Önce sönük tam sıra (`#ffffff@0.09`), üstüne aynı başlangıçtan daha kısa parlak sıra (renk gradient'i + glow). Vites ışıkları: yeşil → amber → kırmızı.

**Eğik sıra sekmesi:** kırmızı gradient'li `skew: 14` rect + `bevel` 1.5 + italik "P3"; yanına aynı eğimde cam rect içinde etiket ve değer.

**Karbon buton:** `skew: 12` rect, karbon dolgu, 1.5 px gradient stroke, üst yarıda `#ffffff@0.14 → 0` parlama (clip), altta 6 px vurgu şeridi (clip + glow), italik büyük harf yazı ve yumuşak gölge.

## Derinlik veren katman sırası

Düz tek dikdörtgen "programcı işi" görünür. Bir yüzey şu katmanlardan oluşur (alttan üste):

1. **Gövde:** hafif dikey gradient (üst açık, alt koyu) ya da düz renk; `shadow` ile zeminden ayrılır.
2. **Kenar:** `stroke` (varsayılan `inside`, boyutu değiştirmez). Ana rengin yarı saydam hali ya da açık tonu.
3. **İç ışık:** `bevel` (üstte parlak, altta koyu ince çizgi) veya `inner_glow`.
4. **Parlama:** üst yarıda beyazdan saydama giden gradient'li ikinci bir rect, `clip` ile gövdeye kırpılmış.
5. **Doku (isteğe bağlı):** `stripes` / `dots` / `grid` pattern, çok düşük alfa (0.04-0.08), yine `clip` ile.
6. **İçerik:** ikon ve yazı, hafif `shadow` ile.
7. **Vurgu:** ince çizgi, köşe işareti, küçük elmas; `glow` ile.

Efektleri az kullan: bir asset'te glow ya da kalın gölgeden biri baskın olsun. Her şeye glow verince hiçbir şey öne çıkmaz.

## Tarifler

**Buton (sci-fi, kesik köşe):**
```json
[{"op":"add_rect","id":"body","x":16,"y":14,"width":288,"height":60,"radius":14,"corner":"cut",
  "fill":{"type":"linear","angle":90,"stops":[[0,"#a78bfa"],[0.5,"#7c3aed"],[1,"#5b21b6"]]},
  "stroke":{"color":"#ddd6fe@0.9","width":2},
  "style":{"glow":{"color":"$primary@0.7","blur":22},"shadow":{"color":"#000@0.5","blur":10,"dy":6},"bevel":{"size":2}}},
 {"op":"add_rect","id":"shine","x":22,"y":18,"width":276,"height":24,"clip":"body",
  "fill":{"type":"linear","angle":90,"stops":[[0,"#ffffff@0.28"],[1,"#ffffff@0"]]}},
 {"op":"add_text","id":"label","text":"oyuna başla","x":160,"y":44,"anchor":"center","size":28,"color":"#ffffff",
  "style":{"use":"title","shadow":{"color":"#2e1065@0.9","blur":4,"dy":2}}}]
```
Canvas 320×96. Yazı `anchor: "center"` ile gövdenin tam ortasına: dikey ortalama büyük harf yüksekliğine göre yapılır, optik olarak ortada durur.

**Buton durumları** (`duplicate_canvas` → sadece farkı değiştir):
- hover: gradient'i bir ton aç, glow `blur` ve alfayı artır.
- pressed: gradient'i koyulaştır, gölgeyi azalt, gövde + içerik 2 px aşağı (`move_elements`), bevel'i ters çevir (highlight koyu, shadow açık).
- disabled: gri-mor soluk dolgu, glow yok, içerik `opacity` 0.45.

**Cartoon / Roblox buton:** altta koyu "derinlik" rect'i, 10 px yukarıda açık gradient'li yüz, ikisinde de koyu ve kalın `outside` stroke (4 px), üstte yarı saydam beyaz parlama şeridi, Bangers ya da Fredoka yazı + kalın koyu stroke + `shadow` blur 0, dy 4.

**Can / XP barı:** `track` (koyu, radius = yükseklik/2, `inner_shadow`) + `fill` (track ile aynı x, y, yükseklik; istenen genişlik; `clip: "track"`; yatay gradient; `bevel`). Dolgunun yuvarlak ucu clip'ten gelir; fill'e radius verme. Motor tarafında dolgu ayrı parça olacaksa track ve fill'i ayrı canvas'lara çiz.

**Envanter slotu:** radial gradient'li koyu kare, ince altın ya da ana renk stroke, `inner_shadow`, ortada `solid` ikon, sağ altta `anchor: "bottom-right"` adet yazısı + siyah stroke. Nadirlik rengi için sadece stroke ve glow rengini değiştir.

**9-slice panel:** aşağıdaki kurallara göre; merkez düz kalmalı.

**HUD mockup:** 1920×1080 canvas, arka plan olarak koyu gradient (gerçekçi tarzda üstüne `blur` verilmiş sis elipsi ve radial vinyet). Parçaları güvenli alan içinde tut (kenarlardan ≥ 32 px). `preview(background="game")` ile oyun sahnesi üstünde okunuyor mu bak.

## Animasyon

Hazır animasyon yok: hangi özelliğin ne zaman, hangi değere, hangi ivmeyle gideceğini `create_animation` + `set_keyframes` ile sen yazarsın. Neredeyse her şey keyframe'lenir: konum (`dx`, `dy`), boyut (`scale`, `scale_x`, `scale_y`, `width`, `height`), `rotation`, `opacity`, renk ve gradient (`style.fill`), efekt (`style.glow.blur`, `style.shadow.dy`), yay (`end_angle`), kesik çizgi akışı (`dash_offset`), yazı ve görünürlük (`text`, `visible`: adım adım değişir).

**Sıra:** statik hali bitir → `create_animation(ad, süre)` → `set_keyframes` (batch içinde) → `preview_animation` → düzelt → `export_animation`. Bir canvas'ta birden çok animasyon olabilir: `appear`, `hover`, `press`, `idle`.

**Süre:** hover / press 0.10-0.18 sn. Küçük parça girişi 0.2-0.3. Panel, menü 0.3-0.45. Ekran geçişi en fazla 0.6. Boşta döngü (nefes alan glow, dönen halka) 1.5-3 sn ve `loop: true`. Oyuncunun beklediği her animasyon kısa olur.

**Ease** (keyframe'deki `ease`, o keyframe'e varışı şekillendirir):
- Giren şey `-out` ile gelir (hızlı başlar, yumuşak durur): `cubic-out`, `expo-out`. Çıkan şey `-in`. Ekranda yer değiştiren `-in-out`.
- Taşma: `back-out`. Stilize tarzda belirgin, gerçekçi tarzda yok ya da çok kısa.
- `elastic-out`, `bounce-out` yalnızca cartoon / arcade.
- Döngüler `sine-in-out`. Sabit hızla dönen ya da akan şeyler `linear`.
- Fiziksel göstergeler gerçek davranışı taklit eder: ibre `quad-in` ile hızlanır, vites değişiminde `quad-out` ile aniden düşer.

**Kurallar:**
- Bir animasyonda 1 ana hareket ve en fazla 1-2 destek (opaklık, glow). Her şey aynı anda oynamaz.
- Sıralı giriş: aynı keyframe'leri her öğeye `offset: i × 0.05-0.08` ile ver.
- Bir grubu tek parça gibi büyütmek ya da döndürmek için `target: "group:ad"` ve ortak `pivot`.
- Ölçek 0'dan başlamaz (0.5-0.85'ten başlat) ve opaklıkla birlikte gelir.
- Döngüde ilk ve son keyframe değeri aynıdır.
- Hareket canvas dışına taşıyorsa (kayarak giriş, taşma, büyüyen glow) canvas'ta pay bırak.
- Tarza uy. Stilize: squash / stretch (`scale_x` ve `scale_y` ters yönde), taşma, zıplama. Gerçekçi: kısa, taşmasız, opaklık + 8-20 px kayma, ışık değişimi.

**Film şeridini okumak** (`preview_animation`): kareler arası fark hep aynıysa hareket doğrusaldır ve cansız durur; sona doğru farklar küçülüyorsa ease-out çalışıyordur. Bir karede öğe aniden belirip kayboluyorsa keyframe zamanı ya da başlangıç değeri yanlıştır. Hızlı kısmı `times=[0.0, 0.05, 0.1, ...]` ile yakından örnekle. Döngüde son kare ilk kareye bağlanıyor mu bak. Tek bir anı tam boy görmek için `preview(canvas, animation=..., time=...)`.

**Örnek: buton girişi + parlama geçişi.** Butonun bütün elemanları `group: "btn"`; `shine`, gövdeye `clip`'li, eğik, ortası yarı saydam beyaz gradient'li dar bir rect ve başlangıçta gövdenin solunda, dışarıda durur.
```json
[{"op":"create_animation","name":"appear","duration":1.0},
 {"op":"set_keyframes","animation":"appear","target":"group:btn","property":"scale","pivot":[180,64],
  "keyframes":[[0,0.55],[0.28,1,"back-out"]]},
 {"op":"set_keyframes","animation":"appear","target":"group:btn","property":"opacity",
  "keyframes":[[0,0],[0.14,1,"quad-out"]]},
 {"op":"set_keyframes","animation":"appear","target":"shine","property":"dx",
  "keyframes":[[0.35,0],[0.85,400,"quad-in-out"]]}]
```

**Dışa aktarma seçimi:**
- Motorun kendisinin canlandırabildiği her şey (konum, ölçek, dönme, opaklık, renk): `export_animation(format="json")` ve parçalar için `export_layers`. JSON her keyframe için süreyi, değeri ve ease'in Roblox (`EasingStyle`, `EasingDirection`) ile DOTween adını verir; motor kodunu bu veriden sen yazarsın. Roblox: `TweenService:Create(nesne, TweenInfo.new(süre, stil, yön), {özellik = değer})`, ardışık keyframe'ler için tween'leri sırayla oynat. Unity: DOTween ya da coroutine / Animator. Unreal: UMG Animation ya da tick'te `FMath::InterpEaseOut` benzeri fonksiyonlar. `dx`/`dy` konuma, `scale` ölçeğe, `pivot` AnchorPoint ya da pivot'a karşılık gelir.
- Motorun canlandıramadığı efektler (kırpılmış parlama, şekil değiştiren yay, dash akışı): `format="spritesheet"` (Unity sprite animasyonu, Unreal flipbook / SubUV, Roblox `ImageRectOffset`). Kare sayısını düşük tut: fps 12-24, kısa süre; Roblox'ta sheet 1024 px'i geçmesin.
- Kullanıcıya göstermek için `format="gif"`; kalite ve saydamlık gerekiyorsa `apng` ya da `webp`.

## 9-slice kuralları (panel, buton, tooltip, bar)

Motorun köşeleri bozmadan esnetebilmesi için merkezin tek düze olması gerekir.

- Esneyecek yönde gradient olmaz. Dikey gradient'li gövde sadece yatay esner (sabit yükseklikli buton için sorun değil). İki yöne esneyecek panelde düz dolgu kullan; derinliği `inner_glow`, `bevel` ve stroke ile ver.
- Merkezde yazı, ikon, pattern ya da `noise` olmaz. Başlık ve ikon motor tarafında ayrı eleman olarak eklenir.
- Süsleri (köşe işareti, vurgu çizgisi) köşe bölgesinde tut.
- `export_asset(nine_slice="auto")` kenarları kendisi bulur ve merkezi birkaç piksele indirir. Sonuçtaki `notes` alanını oku: "only stretches horizontally" ya da "no stretchable area" diyorsa tasarımı düzelt ya da kenarları elle ver (`{"left":..,"top":..,"right":..,"bottom":..}`).
- Büyük yumuşak gölge kenar payını büyütür (gölge köşede kıvrıldığı için). Bu normal.

## Kontrol turu (her asset için)

`preview` resmine bakarken sırayla:

1. Kesilen gölge ya da glow var mı? (kenara yapışmış koyu bant) → canvas'ı büyüt ya da efekti küçült.
2. Yazı kutuya sığıyor mu, ortada mı? `add_text` sonucundaki `bbox_xywh` ile gövde boyutunu karşılaştır; taşarsa `fit_width` ya da küçük `size`.
3. Kontrast: yazı zemin üstünde net mi? HUD için `background="game"` ile dene; gerekirse yazıya koyu `stroke` (2-3 px) ya da `shadow` ekle.
4. Hizalama: aynı sıradaki parçalar aynı çizgide mi, boşluklar eşit mi? `align_elements` ile düzelt, elle tahmin etme.
5. Detay: `preview(region=[x,y,w,h], zoom=4)` ile köşelere ve ince çizgilere yakından bak.
6. Nerede ne olduğu karıştıysa `preview(show_ids=true)`.
7. Kit bitince `contact_sheet`: köşe yarıçapları, stroke kalınlıkları, ışık yönü (hep üstten) ve renkler tutarlı mı?

Bir şey beklediğin gibi çıkmadıysa `get_canvas(details=true)` ile gerçek değerlere bak, `undo` ile geri al.

## İkonlar

- `list_icons` ile hazır seti gör (yaklaşık 70 ikon: kontrol, durum, eşya, ekonomi). `variant: "solid"` oyun UI'ında daha okunaklı; `line` ince menüler için.
- Renk gradient olabilir: `"color": {"type":"linear","angle":135,"stops":["#fde68a","#d97706"]}`.
- Sette yoksa `add_path` ile çiz: 24×24 ızgarada SVG path yaz, `scale` ile büyüt. Basit geometrik formlar kullan; çizdikten sonra mutlaka yakından önizle.
- Motor içinde renklendirilecek ikonları beyaz export et (Unity `Image.color`, Roblox `ImageColor3`, Unreal `Tint`).
- İkon setini tek dosyada vermek için `export_atlas`.

## Export ve motor tarafı

`export_asset` her PNG'nin yanına `<ad>.gameui.json` ve klasöre `game_ui_manifest.json` yazar; sonuçta motorun istediği sayılar hazır gelir. `write_engine_helpers` proje başına bir kez çalıştırılır.

**Roblox:** `export_asset(engine="roblox", scales=[2])` → kullanıcı PNG'leri Studio'da yükler (Asset Manager) → `write_engine_helpers("roblox", out_dir, roblox_ids={...})` → `GameUiAssets.luau`. UI'ı Luau ile kur: `ScreenGui` → `ImageLabel` / `ImageButton`, `GameUi.apply(label, "panel_main")` görseli ve `SliceCenter`'ı ayarlar. Boyutlarda `Scale` + `UIAspectRatioConstraint`, yazı için `TextLabel` (yazıyı görsele gömme: çeviri ve ölçekleme için motor yazısı daha iyi), hover/press için `TweenService`. 2x export'ta `SliceScale` 0.5 gelir.

**Unity:** `write_engine_helpers("unity", "<Proje>/Assets/Editor")` → `export_asset(engine="unity", out_dir="<Proje>/Assets/UI/Generated")`. Importer her PNG'yi Sprite yapar ve 9-slice border'ı (L, B, R, T) ayarlar. uGUI'de `Image.type = Sliced`; UI Toolkit'te USS `-unity-slice-left/top/right/bottom` aynı sayılarla. Yazı TextMeshPro ile.

**Unreal:** `export_asset(engine="unreal", out_dir=<klasör>)` → `write_engine_helpers("unreal", <klasör>)` → editörde Python ile `import_game_ui.py` çalıştırılır. Texture'lar UI ayarlarıyla gelir; her brush için `Draw As: Box` ve `Margin` (L, T, R, B, 0-1 arası) log'a yazılır. Widget tarafında `UImage` / `UButton` brush'larına bu değerleri gir.

**Hareketli parçalar (gösterge, ibre, dolan yay):** tek PNG yetmez. Elemanlara `group` ver ve `export_layers` kullan: her grup ayrı PNG olur, `<ad>.layout.json` her parçanın x, y, boyut ve `origin` (pivot) değerini verir. İbreyi export etmeden önce `rotation: 0` yap (sağa bakar halde), motor döndürsün.
- Unreal: ibre `UImage`, `RenderTransformPivot` = pivot / boyut (0-1), `SetRenderTransformAngle(açı)`. Dolan yay için UI material'da radial maske + skaler parametre. Cam panel arkası için `BackgroundBlur` widget'ı.
- Unity: ibre `RectTransform.pivot` + `localEulerAngles.z = -açı` (Unity saat yönünün tersini pozitif sayar). Dolan yay: `Image.type = Filled`, `fillMethod = Radial360`, `fillAmount`.
- Roblox: ibre `AnchorPoint` + `Rotation`. Dolan yay için segmentli (dash'li) tasarım seç ve segmentleri ayrı görsel olarak aç/kapat; arka plan bulanıklığı yoktur, camı daha opak yap.
- Assetto Corsa (CSP Lua app): PNG'leri app klasörüne koy, `ui.transparentWindow` içinde `ui.drawImage` ile çiz. Döndürme ve kırpma fonksiyonlarının adını CSP Lua SDK'sından doğrula, ezberden yazma.

Motor helper dosyaları bu server'da derlenip denenmedi; kullanıcı ilk kullanımda hata görürse hata mesajına göre düzelt.

## Sık yapılan hatalar

- Tema kurmadan çizmeye başlamak → renkler ve köşeler tutmaz.
- Önizlemeye bakmadan export etmek.
- 9-slice asset'in ortasına yazı ya da gradient koymak.
- Gölge için kenar payı bırakmamak.
- Her parçaya glow vermek; çok ince (1 px) açık renk stroke'u büyük boyutta kullanmak.
- Yazıyı zemine çok yakın tonda bırakmak.
- Gerçekçi tarzda stilize alışkanlıkları sürdürmek: kalın kontur, her yerde glow, büyük radius, 4-5 renk. Sonuç oyuncak gibi durur.
- Gösterge ibresini kadranla aynı gruba koymak (motor döndüremez) ya da `origin` vermeden döndürmek (kendi ortasından döner).
- Eğik parçalarda farklı `skew` açıları kullanmak.
- Animasyonu film şeridine bakmadan export etmek; her şeye bounce / elastic vermek; motorun tween'leyebileceği basit hareketi sprite sheet'e gömmek (gereksiz doku).
- Aynı şeyi 6 kere elle çizmek: `duplicate_element` ve `align_elements(distribute=...)` kullan.
- Karakter portresi, illüstrasyon gibi şeyleri şekillerle çizmeye çalışmak. Onlar bu aracın işi değil: yer tutucu bir çerçeve çiz, kullanıcı görseli verirse `add_image` ile yerleştir.
