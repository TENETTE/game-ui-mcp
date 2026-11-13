---
name: game-ui-mcp
description: Oyun UI'ı (menü, HUD, buton, panel, bar, ikon, envanter) çizmek için game-ui-mcp server'ını kullanır. Unity, Unreal Engine ve Roblox Studio için PNG + 9-slice üretir. "UI yap", "buton/panel/HUD çiz", "Roblox UI", "UMG", "uGUI" gibi isteklerde kullan.
---

# game-ui-mcp ile oyun UI'ı çizmek

Bu skill, `game-ui-mcp` MCP server'ının çizim tool'larını (create_canvas, add_rect, add_text, preview, export_asset...) iyi kullanmanın yolunu anlatır. Server'da AI görsel üretimi yok: her piksel senin çağırdığın şekil, yazı, ikon ve efektlerden gelir. Kalite tamamen senin tasarım kararlarına ve kendi çıktına bakıp düzeltmene bağlı.

Tool'lar listede yoksa (`create_canvas`, `preview` görünmüyorsa) server bağlı değildir. Kullanıcıya server'ı Claude'a bağlaması gerektiğini söyle (repo README'sinde kurulum var) ve çizmiş gibi davranma.

## Çalışma sırası

1. **Brief.** Motor (Unity / Unreal / Roblox), platform, tür ve hava, hangi parçalar lazım. Eksikse en fazla 2-3 soru sor; kullanıcı "sen karar ver" dediyse karar ver ve ne seçtiğini yaz.
2. **Tema.** İlk iş `set_theme`. Renkleri ve tekrar eden stilleri orada tanımla, sonra her yerde `$token` ve stil adı kullan. Tek tek hex yazma: kit tutarsızlaşır.
3. **Canvas.** Her asset ayrı canvas, asset'in gerçek boyutunda. Ekran tasarımı ise 1920x1080 tek canvas (mockup).
4. **Çiz.** `batch` ile, elemanlara açık `id` vererek. Tek tek `add_*` çağrısı yavaş.
5. **Bak.** `preview` çağır ve resme gerçekten bak. Sorun bul, `update_element` ile düzelt, tekrar bak. En az bir tur düzeltme yapmadan export etme.
6. **Durumlar.** Buton gibi parçalarda `duplicate_canvas` ile hover / pressed / disabled üret.
7. **Tutarlılık.** `contact_sheet` ile bütün kiti yan yana gör.
8. **Export.** `export_asset` (motor + 9-slice), ikon setleri için `export_atlas`. Sonra motor tarafındaki kodu yaz.

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

Font seçimi (`list_fonts`): Inter (temiz arayüz), Rajdhani / Orbitron (sci-fi, teknoloji), Cinzel (fantastik, ortaçağ), Fredoka (yuvarlak, casual), Bangers (çizgi roman, arcade). Bir kitte en fazla 2 aile. Orbitron'da Ğ/ğ yok; tool uyarı verirse fontu değiştir.

## Canvas boyutu ve kenar payı

- Gölge ve glow canvas kenarında kesilir. Şeklin etrafında en az `blur × 1.5 + |dx,dy|` boşluk bırak. Örnek: blur 24, dy 10 gölgeli panel için her yanda ~40 px, altta ~46 px.
- Koordinatları tam sayı ver. Yarım pikseller kenarı bulanıklaştırır.
- Tipik boyutlar (1x): ikon 64 veya 128, envanter slotu 96, buton 280-360 × 80-110, 9-slice panel 192-256 kare, bar 360×64.
- Keskinlik için `scales: [1, 2]` export et. Roblox'ta görsel 1024 px'i geçmesin; tool geçerse uyarır.

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

**HUD mockup:** 1920×1080 canvas, arka plan olarak koyu gradient. Parçaları güvenli alan içinde tut (kenarlardan ≥ 32 px). `preview(background="game")` ile oyun sahnesi üstünde okunuyor mu bak.

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

Motor helper dosyaları bu server'da derlenip denenmedi; kullanıcı ilk kullanımda hata görürse hata mesajına göre düzelt.

## Sık yapılan hatalar

- Tema kurmadan çizmeye başlamak → renkler ve köşeler tutmaz.
- Önizlemeye bakmadan export etmek.
- 9-slice asset'in ortasına yazı ya da gradient koymak.
- Gölge için kenar payı bırakmamak.
- Her parçaya glow vermek; çok ince (1 px) açık renk stroke'u büyük boyutta kullanmak.
- Yazıyı zemine çok yakın tonda bırakmak.
- Aynı şeyi 6 kere elle çizmek: `duplicate_element` ve `align_elements(distribute=...)` kullan.
- Karakter portresi, illüstrasyon gibi şeyleri şekillerle çizmeye çalışmak. Onlar bu aracın işi değil: yer tutucu bir çerçeve çiz, kullanıcı görseli verirse `add_image` ile yerleştir.
