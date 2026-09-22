"""Script to generate a sleek, modern, futuristic ICO icon for QuickSlot Deck."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter


def create_quickslot_deck_icon(size: int = 512) -> Image.Image:
    # Canvas
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    
    # 1. Outer Glow Layer
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    inset = int(size * 0.06)
    radius = int(size * 0.22)
    glow_draw.rounded_rectangle(
        (inset - 8, inset - 8, size - inset + 8, size - inset + 8),
        radius=radius + 6,
        fill=(160, 0, 255, 60),
    )
    glow_draw.rounded_rectangle(
        (inset - 4, inset - 4, size - inset + 4, size - inset + 4),
        radius=radius + 3,
        fill=(0, 200, 255, 90),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=12))
    img.alpha_composite(glow)

    # 2. Main Card Background (Futuristic Dark Squircle)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=radius,
        fill="#0D1019",
        outline="#1E293B",
        width=int(size * 0.015),
    )

    # 3. Inner Neon Frame (Purple/Cyan Dual Border)
    inner_inset = inset + int(size * 0.02)
    inner_radius = radius - int(size * 0.02)
    
    draw.rounded_rectangle(
        (inner_inset, inner_inset, size - inner_inset, size - inner_inset),
        radius=inner_radius,
        fill="#111625",
        outline="#A000FF",
        width=int(size * 0.018),
    )

    # 4. StreamDeck 3x2 Grid Buttons Rendering
    grid_margin_x = int(size * 0.16)
    grid_margin_y = int(size * 0.18)
    grid_w = size - (grid_margin_x * 2)
    grid_h = size - (grid_margin_y * 2)
    
    cols = 3
    rows = 2
    tile_gap = int(size * 0.035)
    
    tile_w = (grid_w - (tile_gap * (cols - 1))) // cols
    tile_h = (grid_h - (tile_gap * (rows - 1))) // rows
    
    colors = [
        ("#00A6FF", "#0F1A30"),  # Blue
        ("#C026D3", "#220F30"),  # Purple
        ("#35C89A", "#0F2B20"),  # Green
        ("#F59E0B", "#2B220F"),  # Gold
        ("#EF4444", "#2B0F14"),  # Red
        ("#06B6D4", "#0F262B"),  # Cyan
    ]

    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            tx1 = grid_margin_x + c * (tile_w + tile_gap)
            ty1 = grid_margin_y + r * (tile_h + tile_gap)
            tx2 = tx1 + tile_w
            ty2 = ty1 + tile_h
            
            border_color, bg_color = colors[idx % len(colors)]
            
            draw.rounded_rectangle(
                (tx1, ty1, tx2, ty2),
                radius=int(tile_w * 0.25),
                fill=bg_color,
                outline=border_color,
                width=int(size * 0.01),
            )
            
            # Subtle inner highlight
            draw.rounded_rectangle(
                (tx1 + 3, ty1 + 3, tx2 - 3, ty1 + 10),
                radius=int(tile_w * 0.15),
                fill=(255, 255, 255, 25),
            )

    # 5. Center Lightning Bolt (⚡ Overlay with Glow)
    bolt_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bolt_draw = ImageDraw.Draw(bolt_layer)
    
    cx, cy = size // 2, size // 2
    s = size * 0.28
    
    bolt_poly = [
        (cx + s * 0.1, cy - s * 0.9),
        (cx - s * 0.55, cy + s * 0.05),
        (cx - s * 0.05, cy + s * 0.05),
        (cx - s * 0.25, cy + s * 0.85),
        (cx + s * 0.55, cy - s * 0.1),
        (cx + s * 0.05, cy - s * 0.1),
    ]
    
    bolt_draw.polygon(bolt_poly, fill="#00F0FF")
    bolt_glow = bolt_layer.filter(ImageFilter.GaussianBlur(radius=10))
    img.alpha_composite(bolt_glow)
    
    draw.polygon(bolt_poly, fill="#FFDF2B")

    # 6. Top Glass Reflection Arc
    glass = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glass_draw = ImageDraw.Draw(glass)
    glass_draw.ellipse(
        (-size * 0.2, -size * 0.4, size * 1.2, size * 0.55),
        fill=(255, 255, 255, 22),
    )
    
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=radius,
        fill=255,
    )
    
    img.paste(glass, (0, 0), mask=mask)

    return img


def main() -> None:
    master_icon = create_quickslot_deck_icon(512)
    
    app_dir = Path(r"C:\Users\shin\app\macro_tool")
    app_dir.mkdir(parents=True, exist_ok=True)
    
    png_path = app_dir / "quickslot-deck.png"
    ico_path = app_dir / "quickslot-deck.ico"
    
    master_icon.save(png_path)
    
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master_icon.save(ico_path, format="ICO", sizes=sizes)
    print(f"Saved PNG to {png_path}")
    print(f"Saved ICO to {ico_path}")


if __name__ == "__main__":
    main()
