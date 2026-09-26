#!/usr/bin/env python3
"""
Lyraz Brand Asset Pipeline - Step 1: Extract Transparent Logo
Extracts high-fidelity transparent contour orb from source image
and dynamically crops tightly to the emblem bounding box (zero wasted padding).
"""
import os
from PIL import Image
import numpy as np

def extract_transparent_logo(input_path, output_png, output_webp):
    img = Image.open(input_path).convert('RGBA')
    arr = np.array(img, dtype=np.float32)
    
    # Background color estimate from corners
    bg = np.array([36.0, 45.0, 48.0], dtype=np.float32)
    rgb = arr[:, :, :3]
    
    # Difference in green channel (strongest signal for lime contour lines)
    diff = rgb - bg
    g_signal = diff[:, :, 1]
    
    # Alpha calculation with smooth gamma
    alpha = (g_signal - 15.0) / (200.0 - 15.0)
    alpha = np.clip(alpha, 0.0, 1.0)
    alpha = np.power(alpha, 1.1) * 255.0
    
    # Reconstruct pristine foreground lime (#c3f532)
    out = np.zeros_like(arr)
    out[:, :, 0] = 195  # R
    out[:, :, 1] = 245  # G
    out[:, :, 2] = 50   # B
    out[:, :, 3] = alpha
    
    res = Image.fromarray(out.astype(np.uint8))
    
    # Dynamic bounding box of content
    bbox = res.getbbox()
    print("Detected content bbox:", bbox)
    
    # Crop tightly to the emblem boundaries
    cropped = res.crop(bbox)
    w, h = cropped.size
    print(f"Tight emblem size: {w}x{h}")
    
    # Ensure target directory exists
    os.makedirs(os.path.dirname(output_png), exist_ok=True)
    
    # Save high-res tight master PNG and WebP
    cropped.save(output_png, 'PNG', optimize=True)
    cropped.save(output_webp, 'WEBP', quality=100, lossless=True)
    print(f"Saved tight master assets: {output_png} and {output_webp}")
    return cropped

if __name__ == '__main__':
    src = '/Users/mahdi/.gemini/antigravity-ide/brain/9f6696c4-670a-4d4a-bee2-d116e6969273/.user_uploaded/media_1790368489844.png'
    extract_transparent_logo(src, 'core/static/images/brand/lyraz_contour_orb.png', 'core/static/images/brand/lyraz_contour_orb.webp')
