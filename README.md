# game-ui-mcp

Claude'un (ya da MCP destekleyen başka bir AI'ın) **oyun UI'ı çizmesini** sağlayan, tamamen yerel çalışan bir MCP server.

İçinde görsel üreten bir AI modeli yok ve internete bağlanmıyor. AI, "dikdörtgen çiz, içine şunu yaz, glow ekle" gibi tool'ları çağırır; server bunları temiz, kenarı yumuşatılmış PNG'ye çevirir, AI kendi çizdiğine bakıp düzeltir ve Unity / Unreal Engine / Roblox Studio için dışa aktarır.

![Örnek kit](examples/kit_sheet.png)

![Örnek HUD](examples/hud_mockup.png)

Yukarıdaki görsellerin tamamı [`examples/demo.py`](examples/demo.py) içindeki tool çağrılarıyla çizildi.

## Ne yapabiliyor

- **Şekiller:** dikdörtgen (yuvarlak ya da kesik köşe), elips, çokgen, yıldız, çizgi, SVG path
- **Stil:** düz renk, linear / radial gradient, çizgili / noktalı / ızgara doku, kenar çizgisi (iç, orta, dış), gölge, glow, iç gölge, bevel, opaklık, döndürme, başka bir şekle kırpma (`clip`)
- **Yazı:** 6 gömülü font (Inter, Rajdhani, Orbitron, Cinzel, Fredoka, Bangers) + bilgisayardaki fontlar, satır kaydırma, genişliğe sığdırma, kontur, Türkçe büyük harf (`uppercase: "tr"`)
- **İkon:** 72 hazır oyun ikonu, çizgi ve dolu hali ([hepsi](examples/icons.png))
- **Tema:** renk token'ları (`$primary`) ve isimli stiller; bütün kit tutarlı kalır
- **Önizleme:** AI çizdiğini resim olarak geri görür (yakınlaştırma, eleman kutuları, oyun sahnesi arka planı)
- **Dışa aktarma:** PNG (1x, 2x), otomatik 9-slice, atlas, motor başına hazır değerler ve yardımcı scriptler

## Kurulum (Windows)

Python 3.10 ya da üstü gerekli.

```powershell
git clone https://github.com/TENETTE/game-ui-mcp
cd game-ui-mcp
python -m venv .venv
.venv\Scripts\pip install -e .
```

Deneme (örnek görselleri yeniden üretir):

```powershell
.venv\Scripts\python examples\demo.py
```

### Claude Desktop'a bağlama

`claude_desktop_config.json` dosyasına ekle (Windows'ta `%APPDATA%\Claude\` klasöründe; uygulamada Ayarlar → Developer → Edit Config ile de açılır, menü adı sürüme göre değişebilir):

```json
{
  "mcpServers": {
    "game-ui": {
      "command": "C:\\Users\\SEN\\game-ui-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "game_ui_mcp"]
    }
  }
}
```

Yolu kendi klasörüne göre değiştir, Claude Desktop'ı tamamen kapatıp aç.

### Claude Code'a bağlama

```powershell
claude mcp add game-ui -- C:\Users\SEN\game-ui-mcp\.venv\Scripts\python.exe -m game_ui_mcp
```

### Skill

`skill/game-ui-mcp/SKILL.md`, Claude'a bu tool'ları iyi kullanmayı öğretir (tema kurma, katman sırası, 9-slice kuralları, kontrol turu, motor tarafı). Claude'da skill olarak ekle; Claude Code kullanıyorsan klasörü `.claude/skills/` altına kopyala.

## Dosyalar nereye gidiyor

Varsayılan çalışma klasörü `C:\Users\SEN\GameUI`:

| Klasör | İçerik |
| --- | --- |
| `projects/` | Canvas'lar (JSON). Oturum kapansa da kalır. |
| `exports/` | Yol verilmeden yapılan dışa aktarmalar |
| `fonts/` | Buraya attığın `.ttf` / `.otf` dosyaları font listesine girer |
| `theme.json` | Renk token'ları ve stiller |

Başka bir klasör için `GAME_UI_MCP_DIR` ortam değişkenini ayarla (config'te `"env": {"GAME_UI_MCP_DIR": "D:\\UI"}`).

## Tool'lar

| Grup | Tool'lar |
| --- | --- |
| Canvas | `create_canvas`, `list_canvases`, `get_canvas`, `update_canvas`, `duplicate_canvas`, `delete_canvas` |
| Çizim | `add_rect`, `add_ellipse`, `add_line`, `add_polygon`, `add_path`, `add_text`, `add_icon`, `add_image` |
| Düzenleme | `update_element`, `delete_elements`, `duplicate_element`, `move_elements`, `reorder_element`, `align_elements`, `batch`, `undo` |
| Tema ve bilgi | `set_theme`, `get_theme`, `list_fonts`, `list_icons`, `measure_text` |
| Önizleme | `preview`, `contact_sheet` |
| Dışa aktarma | `export_png`, `export_asset`, `export_atlas`, `write_engine_helpers` |

Stil nesnesi kısaca:

```json
{
  "fill": {"type": "linear", "angle": 90, "stops": [[0, "#a78bfa"], [1, "#5b21b6"]]},
  "stroke": {"color": "$primary@0.6", "width": 2, "align": "inside"},
  "shadow": {"color": "#000000@0.5", "blur": 12, "dy": 6},
  "glow": {"color": "$accent", "blur": 16},
  "inner_shadow": {"color": "#000@0.6", "blur": 8, "dy": 2},
  "bevel": {"size": 2},
  "opacity": 1.0
}
```

Renkler: `#rrggbb`, `#rrggbbaa`, `rgba(...)`, `$token`; sonuna `@0.5` eklenince alfa olur.

## Motorlara aktarma

`export_asset` PNG'nin yanına `<ad>.gameui.json` yazar ve klasördeki `game_ui_manifest.json` dosyasını günceller. `nine_slice: "auto"` esneyecek orta bölgeyi kendisi bulur ve dokuyu küçültür.

| Motor | 9-slice değeri | Yardımcı (`write_engine_helpers`) |
| --- | --- | --- |
| Unity | Sprite border (L, B, R, T) | `GameUiImporter.cs`: yanında `.gameui.json` olan her PNG'yi UI sprite olarak ayarlar |
| Unreal | Brush `Draw As: Box`, Margin (L, T, R, B, 0-1) | `import_game_ui.py`: texture'ları UI ayarlarıyla içe aktarır, margin'leri log'a yazar |
| Roblox | `SliceCenter`, `SliceScale` | `GameUiAssets.luau`: `GameUi.apply(imageLabel, "ad")` |

Roblox'ta görselleri Studio'ya kendin yüklemen gerekir (server internete çıkmaz); asset id'lerini sonra `write_engine_helpers(roblox_ids=...)` ile işlersin.

## Sınırlar

- Karakter portresi, illüstrasyon, fotoğraf gibi şeyleri üretmez. Bunlar için hazır bir görseli `add_image` ile yerleştirebilirsin.
- Stroke birleşimleri çokgen ve path'lerde yuvarlaktır (dikdörtgenlerde keskin).
- Yazıda harf aralığı verilince kerning uygulanmaz.
- Orbitron fontunda Ğ / ğ yok; tool böyle durumlarda uyarı döndürür.
- Motor tarafındaki yardımcı dosyalar (C#, Unreal Python, Luau) bu repoda yalnızca üretiliyor; Unity, Unreal ve Roblox Studio içinde henüz denenmedi.

## Geliştirme

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

Çizim motoru Pillow + numpy üzerine kurulu: şekiller 4 kat çözünürlükte piksel merkezine göre taranır, sonra küçültülür; efektler çıkış çözünürlüğünde uygulanır. MCP Python SDK'nın 1.x ve 2.x sürümleriyle çalışır.

## Fontlar

`src/game_ui_mcp/assets/fonts/` altındaki fontlar SIL Open Font License ile dağıtılır; her klasörde `OFL.txt` bulunur.
