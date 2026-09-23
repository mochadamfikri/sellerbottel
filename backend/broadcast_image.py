from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os

BG = (10, 7, 18)
PANEL = (22, 14, 38)
PURPLE = (151, 74, 255)
WHITE = (248, 245, 255)
MUTED = (177, 164, 201)
GREEN = (92, 224, 154)

def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def _fit(draw, text, font, max_width):
    text = str(text)
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while len(text) > 4 and draw.textbbox((0, 0), text + "…", font=font)[2] > max_width:
        text = text[:-1]
    return text + "…"

def render_transaction_image(total_qty, total_amount, currency="IDR", title="PENJUALAN BERHASIL"):
    width, height = 1200, 700
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    for r in range(360, 20, -20):
        alpha = int(32 * (1 - r / 380))
        c = (PURPLE[0] // 3 + alpha, PURPLE[1] // 3 + alpha, PURPLE[2] // 3 + alpha)
        draw.ellipse((width - r, -r // 2, width + r, r // 2), outline=c, width=3)
    draw.rounded_rectangle((55, 55, width - 55, height - 55), 28, fill=PANEL, outline=(67, 43, 95), width=2)
    brand_font = _font(32, True)
    title_font = _font(66, True)
    big_font = _font(92, True)
    label_font = _font(25, True)
    value_font = _font(42, True)
    draw.text((95, 90), "IDSE NETWORK CONNECT HUB", font=brand_font, fill=PURPLE)
    draw.text((95, 155), title, font=title_font, fill=WHITE)
    cx, cy = 1060, 180
    draw.ellipse((cx - 45, cy - 45, cx + 45, cy + 45), fill=(32, 80, 62), outline=GREEN, width=3)
    draw.line((cx - 20, cy, cx - 5, cy + 16), fill=GREEN, width=8)
    draw.line((cx - 5, cy + 16, cx + 24, cy - 18), fill=GREEN, width=8)
    draw.text((95, 285), "TOTAL ORDER", font=label_font, fill=MUTED)
    qty = _fit(draw, f"{int(total_qty):,} AKUN", big_font, 480)
    draw.text((95, 320), qty, font=big_font, fill=WHITE)
    draw.text((650, 285), "TOTAL TRANSAKSI", font=label_font, fill=MUTED)
    if currency == "IDR":
        amount = f"Rp {float(total_amount):,.0f}".replace(",", ".")
    else:
        amount = f"${float(total_amount):,.2f}"
    amount = _fit(draw, amount, value_font, 440)
    draw.text((650, 330), amount, font=value_font, fill=WHITE)
    draw.rounded_rectangle((95, 515, width - 95, 605), 18, fill=(31, 63, 51), outline=GREEN, width=2)
    status_font = _font(28, True)
    status = "✓ TRANSACTION COMPLETED"
    bbox = draw.textbbox((0, 0), status, font=status_font)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, 543), status, font=status_font, fill=GREEN)
    out = BytesIO()
    image.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()

def render_product_image(product_name, price, stock=None, description=""):
    width, height = 1200, 700
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((55, 55, width - 55, height - 55), 28, fill=PANEL, outline=(67, 43, 95), width=2)
    brand_font = _font(30, True)
    title_font = _font(62, True)
    value_font = _font(46, True)
    label_font = _font(24, True)
    small_font = _font(24, False)
    draw.text((95, 90), "IDSE NETWORK CONNECT HUB", font=brand_font, fill=PURPLE)
    draw.text((95, 155), "PRODUCT UPDATE", font=title_font, fill=WHITE)
    name = _fit(draw, product_name, _font(50, True), 1000)
    draw.text((95, 255), name, font=_font(50, True), fill=WHITE)
    draw.text((95, 350), "HARGA", font=label_font, fill=MUTED)
    draw.text((95, 390), str(price), font=value_font, fill=WHITE)
    if stock is not None:
        draw.text((650, 350), "STOCK", font=label_font, fill=MUTED)
        draw.text((650, 390), f"{int(stock):,}", font=value_font, fill=WHITE)
    if description:
        desc = _fit(draw, " ".join(description.split()), small_font, 1000)
        draw.text((95, 500), desc, font=small_font, fill=MUTED)
    out = BytesIO()
    image.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()
