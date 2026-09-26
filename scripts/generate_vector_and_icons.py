#!/usr/bin/env python3
"""
Lyraz Brand Asset Pipeline - Step 2: Vectorization & Modern Web Icon Stack
Follows the 2026 Evil Martians / W3C Gold Standard for Web Icons:
1. Dynamic Vector Core:
   - lyraz_logo_lime.svg, lyraz_logo_dark.svg, lyraz_logo.svg (UI logos)
   - favicon.svg (Primary browser tab favicon with adaptive dark/light mode)
2. Master App Icon (1024x1024, obsidian #111215 with 84% bold emblem)
3. Essential Modern Fallbacks:
   - apple-touch-icon.png (180x180 for iOS)
   - icon-192x192.png & icon-512x512.png (PWA Web Manifest criteria)
   - favicon.ico (16, 32, 48 multi-res legacy fallback)
4. Cleans up legacy/deprecated redundant icon sizes.
"""
import os
import glob
import re
import subprocess
from PIL import Image
import numpy as np

def generate_svg_and_icons():
    # 1. Source image for vectorization
    src_path = '/Users/mahdi/.gemini/antigravity-ide/brain/9f6696c4-670a-4d4a-bee2-d116e6969273/.user_uploaded/media_1790368489844.png'
    img = Image.open(src_path).convert('RGB')
    arr = np.array(img, dtype=np.float32)
    bg = np.array([36.0, 45.0, 48.0])
    g_sig = arr[:, :, 1] - bg[1]
    
    # 2. Vectorization with potrace
    # Binary mask directly from source (intensity > 50 gives crisp, noise-free lines)
    mask = (g_sig > 50).astype(np.uint8) * 255
    inv = Image.fromarray(255 - mask)
    bmp_path = '/tmp/lyraz_perfect_mask.bmp'
    inv.save(bmp_path)
    
    raw_svg_path = '/tmp/lyraz_perfect_raw.svg'
    cmd = [
        '/opt/homebrew/bin/potrace',
        bmp_path,
        '-s',
        '--tight',
        '--flat',
        '--opttolerance', '0.2',
        '--alphamax', '1.0',
        '-o', raw_svg_path
    ]
    subprocess.run(cmd, check=True)
    
    with open(raw_svg_path, 'r') as f:
        raw_svg = f.read()
        
    # Dynamically extract potrace's exact tight viewBox and transform
    viewbox_match = re.search(r'viewBox="([^"]+)"', raw_svg)
    if not viewbox_match:
        raise ValueError("Could not extract viewBox from potrace output")
    viewbox = viewbox_match.group(1)
    
    transform_match = re.search(r'transform="([^"]+)"', raw_svg)
    transform_attr = transform_match.group(1) if transform_match else "scale(0.1, -0.1)"
    
    path_match = re.search(r'<path[^>]*d="([^"]+)"', raw_svg)
    if not path_match:
        raise ValueError("Could not extract path data from potrace output")
    path_d = path_match.group(1)
    
    print(f"Extracted dynamic vector properties: viewBox='{viewbox}'")
    
    # 3. Generate Clean SVGs
    os.makedirs('core/static/images/brand', exist_ok=True)
    os.makedirs('core/static', exist_ok=True)
    
    # A) Lime Neon (#84cc16)
    svg_lime = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" fill="#84cc16" class="lyraz-emblem">
  <g transform="{transform_attr}">
    <path d="{path_d}" />
  </g>
</svg>'''

    # B) Dark Charcoal (#111215)
    svg_dark = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" fill="#111215" class="lyraz-emblem">
  <g transform="{transform_attr}">
    <path d="{path_d}" />
  </g>
</svg>'''

    # C) Flexible CurrentColor
    svg_current = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" fill="currentColor" class="lyraz-emblem">
  <g transform="{transform_attr}">
    <path d="{path_d}" />
  </g>
</svg>'''

    # D) Primary Vector Favicon (with dynamic theme adaptation)
    svg_favicon = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">
  <style>
    path {{ fill: #84cc16; }}
    @media (prefers-color-scheme: light) {{
      path {{ fill: #65a30d; }}
    }}
  </style>
  <g transform="{transform_attr}">
    <path d="{path_d}" />
  </g>
</svg>'''

    with open('core/static/images/brand/lyraz_logo_lime.svg', 'w') as f:
        f.write(svg_lime)
    with open('core/static/images/brand/lyraz_logo_dark.svg', 'w') as f:
        f.write(svg_dark)
    with open('core/static/images/brand/lyraz_logo.svg', 'w') as f:
        f.write(svg_current)
    with open('core/static/favicon.svg', 'w') as f:
        f.write(svg_favicon)
    print("Generated dynamic SVGs: lyraz_logo_lime, lyraz_logo_dark, lyraz_logo, and favicon.svg")

    # 4. Master App Icon (1024x1024)
    tight_logo_path = 'core/static/images/brand/lyraz_contour_orb.png'
    logo_trans = Image.open(tight_logo_path).convert('RGBA')
    
    app_icon = Image.new('RGBA', (1024, 1024), (17, 18, 21, 255))  # #111215
    emblem_size = 860
    logo_scaled = logo_trans.resize((emblem_size, emblem_size), Image.Resampling.LANCZOS)
    offset = (1024 - emblem_size) // 2
    app_icon.paste(logo_scaled, (offset, offset), logo_scaled)
    
    app_icon.save('core/static/images/brand/lyraz_app_icon.png', 'PNG')
    app_icon.save('core/static/images/brand/lyraz_app_icon.webp', 'WEBP', quality=100)
    print("Saved master app icon: lyraz_app_icon.png and .webp")

    # 5. Modern Standard Icon Stack
    os.makedirs('core/static/icons', exist_ok=True)
    
    # PWA Standard Icons (192 and 512)
    icon_192 = app_icon.resize((192, 192), Image.Resampling.LANCZOS)
    icon_192.save('core/static/icons/icon-192x192.png', 'PNG', optimize=True)
    
    icon_512 = app_icon.resize((512, 512), Image.Resampling.LANCZOS)
    icon_512.save('core/static/icons/icon-512x512.png', 'PNG', optimize=True)
    print("Generated PWA standard icons: icon-192x192.png and icon-512x512.png")
    
    # Apple Touch Icon (180x180)
    apple_icon = app_icon.resize((180, 180), Image.Resampling.LANCZOS)
    apple_icon.save('core/static/apple-touch-icon.png', 'PNG', optimize=True)
    print("Generated apple-touch-icon.png (180x180)")
    
    # Favicon.ico fallback (contains 16, 32, 48)
    favicon_sizes = [(16, 16), (32, 32), (48, 48)]
    favicon_imgs = [app_icon.resize(s, Image.Resampling.LANCZOS) for s in favicon_sizes]
    favicon_imgs[0].save('core/static/favicon.ico', format='ICO', sizes=favicon_sizes, append_images=favicon_imgs[1:])
    print("Generated core/static/favicon.ico (16, 32, 48)")

    # 6. Clean up deprecated redundant icon sizes
    deprecated_icons = [
        'core/static/icons/icon-48x48.png',
        'core/static/icons/icon-72x72.png',
        'core/static/icons/icon-96x96.png',
        'core/static/icons/icon-128x128.png',
        'core/static/icons/icon-144x144.png',
        'core/static/icons/icon-152x152.png',
        'core/static/icons/icon-256x256.png',
        'core/static/icons/icon-384x384.png',
        'core/static/icons/apple-touch-icon.png'  # Root file core/static/apple-touch-icon.png is the standard
    ]
    for dep in deprecated_icons:
        if os.path.exists(dep):
            os.remove(dep)
            print(f"Removed deprecated icon: {dep}")

    # Remove temporary files
    for tmp in ['/tmp/lyraz_perfect_mask.bmp', '/tmp/lyraz_perfect_raw.svg']:
        if os.path.exists(tmp):
            os.remove(tmp)

    print("\n✅ Successfully implemented 2026 Modern Vector-First Asset Stack!")

if __name__ == '__main__':
    generate_svg_and_icons()
