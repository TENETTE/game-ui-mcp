# game-ui-mcp

Bir AI'ın (Claude, Cursor, VS Code Copilot... MCP destekleyen her istemci) **oyun ve uygulama arayüzü çizmesini, canlandırmasını ve Unity / Unreal / Godot / Roblox'a teslim etmesini** sağlayan yerel MCP server.

- Görsel üreten bir model yok, internete çıkmıyor: AI şekil, yazı, stil ve keyframe tool'larını çağırır, server bunu temiz PNG'ye çevirir.
- AI çizdiğine bakar, `review_canvas` ile hatalarını bulur (taşan yazı, düşük kontrast, eksik harf), düzeltir.
- Çıktı doğrudan motora girer: 9-slice ayarlı sprite, katmanlar, PSD, animasyon, hatta hazır sahne (Godot `.tscn`, Unity UXML, Roblox script).

![Örnek kit](examples/kit_sheet.png)

![Gerçekçi yarış HUD'u](examples/racing_hud.png)

| Buton girişi + parlama | Devir saati | Menü girişi ve seçim |
| --- | --- | --- |
| ![Buton animasyonu](examples/anim_button.gif) | ![Gösterge animasyonu](examples/anim_gauge.gif) | ![Menü animasyonu](examples/anim_menu.gif) |

## Kurulum

[uv](https://docs.astral.sh/uv/) kurulu olsun (`winget install astral-sh.uv` ya da `pip install uv`). Clone, venv ya da Python yolu gerekmez; ilk açılışta kendini indirir (~30 sn).

**Claude Code**

```bash
claude mcp add game-ui -s user -- uvx --from git+https://github.com/TENETTE/game-ui-mcp game-ui-mcp
```

**Claude Desktop, Cursor, Windsurf** (istemcinin MCP config dosyasına; VS Code'da `.vscode/mcp.json` içinde anahtar `mcpServers` yerine `servers`):

```json
{
  "mcpServers": {
    "game-ui": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/TENETTE/game-ui-mcp", "game-ui-mcp"]
    }
  }
}
```

Claude Desktop'ta dosya: Ayarlar → Developer → Edit Config. Kaydettikten sonra uygulamayı tamamen kapatıp aç.

**İsteğe bağlı skill:** [`skill/game-ui-mcp/SKILL.md`](skill/game-ui-mcp/SKILL.md) AI'a bu tool'ları iyi kullanmayı öğretir (tema, katman sırası, 9-slice, kontrol turu). Claude Code'da `~/.claude/skills/` altına kopyala.

## Şunu iste

- "Fantastik bir RPG için altın kenarlı envanter paneli ve üç durumlu buton çiz, Unity'ye 9-slice olarak aktar."
- "Yarış oyunu için devir saati çiz, ibresi dönen bir animasyon yap, parçaları ayrı katman olarak `D:\Oyun\UI` klasörüne ver."
- "Bu ana menüyü Arapça ve Japonca'ya çevir, taşan yazı var mı kontrol et."
- "Bu ekran görüntüsündeki HUD'un renk paletini al ve aynı tarzda bir skor tablosu yap."

## Ne yapabiliyor

- **Çizim:** dikdörtgen, elips, çokgen, yay, kadran çizgisi, SVG path ve SVG içe aktarma, başka canvas'ı bileşen olarak kullanma
- **Stil:** gradient, doku, gölge, glow, bevel, yönlü ışık, 13 karışım modlu katmanlar, AI'ın kendi yazdığı piksel formülleri (shader dolgusu)
- **Yazı:** 10 gömülü font + sistem fontları; Arapça, İbranice, Hintçe, Tayca, Çince, Japonca, Korece doğru şekillenir
- **Ekranlar:** hazır cihaz boyutları ve güvenli alanlar, kenara sabitleme, çok çözünürlük önizleme, renk körlüğü simülasyonu
- **Durum ve veri:** hover / pressed / disabled, tek şablondan onlarca kart, dil tabloları ve sahte (pseudo) dillerle taşma testi
- **Animasyon:** her özellik keyframe + ease ile; GIF, sprite sheet, kare dizisi ya da motorun tween'i için JSON
- **Teslim:** PNG / WebP / JPG, katman katman (PNG, JPG, WebP ya da katmanlı SVG), PSD, atlas, bitmap font, uygulama ikonu, tıklanabilir HTML prototip, motor sahneleri

Ayrıntılı özellik listesi, stil nesnesi, katmanlar, animasyon ve motor tabloları: **[docs/GUIDE.md](docs/GUIDE.md)**

## Tool'lar (71)

| Grup | Tool'lar |
| --- | --- |
| Canvas | `create_canvas`, `list_canvases`, `get_canvas`, `update_canvas`, `duplicate_canvas`, `delete_canvas`, `fit_canvas`, `resize_canvas` |
| Katman | `create_layer`, `update_layer`, `delete_layer`, `set_layer` |
| Çizim | `add_rect`, `add_ellipse`, `add_line`, `add_polygon`, `add_path`, `add_arc`, `add_ticks`, `add_text`, `add_image`, `add_instance`, `add_svg` |
| Düzenleme | `update_element`, `delete_elements`, `duplicate_element`, `move_elements`, `reorder_element`, `align_elements`, `arrange_elements`, `scatter`, `copy_elements`, `set_pin`, `batch`, `undo`, `redo` |
| Durum, veri, dil | `set_state`, `delete_state`, `generate_from_template`, `set_strings`, `get_strings` |
| Denetim | `review_canvas`, `inspect_pixels`, `view_image`, `preview_nine_slice` |
| Animasyon | `create_animation`, `set_keyframes`, `delete_keyframes`, `get_animations`, `delete_animation`, `preview_animation`, `export_animation` |
| Tema ve bilgi | `set_theme`, `get_theme`, `list_fonts`, `measure_text` |
| Önizleme | `preview`, `preview_resolutions`, `contact_sheet` |
| Dışa aktarma | `export_png`, `export_asset`, `export_layers`, `export_psd`, `export_atlas`, `export_bitmap_font`, `write_engine_helpers` |
| Teslim | `export_spec`, `export_theme`, `set_link`, `export_prototype`, `export_screen` |

Bakmaya yarayan tool'lar (`preview`, `get_*`, `review_canvas`...) salt okunur, `delete_*` ise yıkıcı olarak işaretli; istemciler buna göre onay sorar. Hatalar AI'a neyin yanlış olduğunu ve nasıl düzelteceğini söyler.

## Dosyalar

Canvas'lar, tema ve dışa aktarmalar `~/GameUI` klasöründe durur (oturum kapansa da kalır). Başka klasör için config'e `"env": {"GAME_UI_MCP_DIR": "D:\\UI"}` ekle. `~/GameUI/fonts/` içine attığın `.ttf` / `.otf` fontlar listeye girer.

## Güvenlik

- İnternete bağlanmaz; ürettiği motor script'lerini kendisi çalıştırmaz. Shader formülleri yalnızca aritmetik ve izinli fonksiyonlardan oluşabilir, dosyaya ya da sisteme erişemez.
- Motor script'lerine yazılan değerler (yollar, asset id'leri) izin listesiyle denetlenir; SVG'de DOCTYPE / ENTITY reddedilir; aşırı büyük render ve ağır formüller başlamadan reddedilir.
- Dışa aktarma araçları verdiğin klasöre yazar ve aynı adlı dosyanın üstüne yazar.

## Sınırlar

- Karakter, illüstrasyon, fotoğraf üretmez; hazır görseli `add_image` ile yerleştirebilirsin.
- Motor tarafı dosyalar üretiliyor ve biçimleri test ediliyor, ama Unity / Unreal / Godot / Roblox içinde henüz denenmedi.
- 71 tool'un tanımı yaklaşık 24 bin token; tool'ları gerektikçe yükleyen istemcilerde (Claude Code) bu maliyet düşer.

Tam liste: [docs/GUIDE.md](docs/GUIDE.md#sınırlar).

## Geliştirme

```bash
git clone https://github.com/TENETTE/game-ui-mcp
cd game-ui-mcp
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

Örnek görseller `examples/demo*.py` ile yeniden üretilir.

## Lisans

[MIT](LICENSE): ticari dahil özgürce kullan. Gömülü fontlar SIL Open Font License ile gelir. Bu araçla çizdiğin görseller senindir.
