"""
Генерация карточки-предупреждения о скамере.
Используется из bot.py и main.py для отправки фото вместо текста.
"""
import io
import base64
from PIL import Image, ImageDraw, ImageFont
import os

COLORS = {
    "high":  ("#1a0000", "#ff3333", "#ff6666"),
    "med":   ("#1a1000", "#ff9900", "#ffbb44"),
    "low":   ("#00001a", "#3399ff", "#66bbff"),
}

LABEL = {
    "high": "🔴 СКАМЕР",
    "med":  "🟠 ПОДОЗРИТЕЛЬНЫЙ",
    "low":  "🔵 ОСТОРОЖНО",
}

STATUS_LABEL = {
    "active":  "🔴 Активен",
    "blocked": "🔒 Заблокирован",
}

W, H = 900, 520


def _font(size: int):
    """Попытка загрузить шрифт, fallback на дефолтный."""
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_scam_card(scammer: dict) -> bytes:
    """
    Генерирует PNG-карточку предупреждения.
    Возвращает bytes изображения.
    """
    threat = scammer.get("threat", "high")
    bg_color, accent, light = COLORS.get(threat, COLORS["high"])

    img = Image.new("RGB", (W, H), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Фоновые полосы
    for i in range(0, W, 40):
        draw.rectangle([i, 0, i + 20, H], fill="#0d0d0d")

    # Верхняя полоса акцента
    draw.rectangle([0, 0, W, 8], fill=accent)

    # Заголовок
    f_title = _font(34)
    f_big   = _font(28)
    f_med   = _font(22)
    f_small = _font(18)
    f_tiny  = _font(15)

    title = "⚠  WTS SCAMLIST — ПРЕДУПРЕЖДЕНИЕ"
    draw.text((W // 2, 38), title, font=f_title, fill=accent, anchor="mm")

    # Разделитель
    draw.rectangle([40, 70, W - 40, 73], fill=accent)

    # Имя и юзернейм
    name = scammer.get("name") or "Неизвестно"
    username = scammer.get("username") or ""
    draw.text((60, 90), "👤", font=f_big, fill=light)
    draw.text((110, 90), f"{name}", font=f_big, fill="#ffffff")
    if username:
        draw.text((110, 128), username, font=f_med, fill=light)

    # Категория и статус
    cat = LABEL.get(threat, "?")
    status = STATUS_LABEL.get(scammer.get("status", "active"), "?")
    draw.text((60, 168), f"Категория: {cat}", font=f_med, fill=light)
    draw.text((60, 200), f"Статус: {status}", font=f_med, fill=light)

    # Разделитель
    draw.rectangle([40, 232, W - 40, 234], fill="#333333")

    # Причина
    reason = scammer.get("reason") or "—"
    draw.text((60, 244), "📋 Причина:", font=f_med, fill=accent)

    # Перенос длинного текста
    max_chars = 75
    y_r = 272
    words = reason.split()
    line = ""
    for w in words:
        if len(line) + len(w) + 1 > max_chars:
            draw.text((60, y_r), line.strip(), font=f_small, fill="#dddddd")
            y_r += 24
            line = w + " "
            if y_r > 370:
                draw.text((60, y_r), "...", font=f_small, fill="#dddddd")
                break
        else:
            line += w + " "
    if line and y_r <= 370:
        draw.text((60, y_r), line.strip(), font=f_small, fill="#dddddd")
        y_r += 24

    # Ущерб и жертвы
    amount   = scammer.get("amount") or ""
    currency = scammer.get("currency") or "USDT"
    victims  = scammer.get("victims") or 0

    y_bot = max(y_r + 8, 380)

    if amount:
        draw.text((60, y_bot), f"💸 Ущерб: {amount} {currency}", font=f_med, fill="#ff9966")
        y_bot += 30
    if victims:
        draw.text((60, y_bot), f"👥 Жертв: {victims} чел.", font=f_small, fill="#ffcc88")
        y_bot += 28

    # Нижняя полоса
    draw.rectangle([0, H - 48, W, H], fill="#111111")
    draw.rectangle([0, H - 48, W, H - 45], fill=accent)
    draw.text((W // 2, H - 24), "🛡  WTS ScamList — Проверяй перед сделкой", font=f_tiny, fill=light, anchor="mm")

    # Сохраняем в bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def generate_card_bytes(scammer: dict) -> bytes:
    return generate_scam_card(scammer)
