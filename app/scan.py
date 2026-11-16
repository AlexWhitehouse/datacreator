"""Rasterise pages and optionally make them look scanned or faxed."""

from __future__ import annotations

import io

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

PRESETS = {
    "none": None,
    "light": dict(angle=0.6, blur=0.3, noise=4, speckle=0.0, contrast=(0.97, 1.0), bright=(0.99, 1.02), shift=4, gradient=0.0),
    "medium": dict(angle=1.2, blur=0.6, noise=8, speckle=0.0003, contrast=(0.9, 0.98), bright=(0.97, 1.03), shift=8, gradient=0.05),
    "heavy": dict(angle=2.5, blur=1.0, noise=14, speckle=0.001, contrast=(0.8, 0.92), bright=(0.92, 1.04), shift=14, gradient=0.12),
    "fax": dict(angle=1.0, blur=0.4, noise=6, speckle=0.0008, contrast=(1.0, 1.0), bright=(1.0, 1.0), shift=6, gradient=0.0, fax=True),
}


def page_image(page: pymupdf.Page, dpi: int, preset: str, colour: str, seed: int) -> Image.Image:
    grey = colour in ("greyscale", "bw") or preset == "fax"
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY if grey else pymupdf.csRGB, alpha=False)
    img = Image.frombytes("L" if grey else "RGB", (pix.width, pix.height), pix.samples)
    p = PRESETS.get(preset)
    rng = np.random.default_rng(seed)
    if p:
        fill = 255 if grey else (255, 255, 255)
        scale = dpi / 200
        dx, dy = (rng.uniform(-p["shift"], p["shift"], 2) * scale).round()
        img = img.rotate(rng.uniform(-p["angle"], p["angle"]), resample=Image.BILINEAR, fillcolor=fill,
                         translate=(int(dx), int(dy)))
        if p["blur"]:
            img = img.filter(ImageFilter.GaussianBlur(p["blur"] * scale))
        a = np.asarray(img, dtype=np.float32)
        a = (a - 128) * rng.uniform(*p["contrast"]) + 128
        a *= rng.uniform(*p["bright"])
        if p["gradient"]:
            g = np.linspace(1 - p["gradient"] * rng.uniform(0, 1), 1, a.shape[1], dtype=np.float32)
            if rng.random() < 0.5:
                g = g[::-1]
            a *= g[None, :, None] if a.ndim == 3 else g[None, :]
        if p["noise"]:
            noise = rng.standard_normal(a.shape[:2], dtype=np.float32) * np.float32(p["noise"])
            a += noise[..., None] if a.ndim == 3 else noise
        if p["speckle"]:
            mask = rng.random(a.shape[:2]) < p["speckle"]
            a[mask] = rng.uniform(0, 90)
        a = np.clip(a, 0, 255).astype(np.uint8)
        img = Image.fromarray(a)
        if p.get("fax"):
            w, h = img.size
            img = img.resize((w, max(1, h // 2)), Image.BILINEAR).resize((w, h), Image.NEAREST)
            return img.point(lambda v: 255 if v > 175 else 0, mode="1")
    if colour == "bw":
        return img.point(lambda v: 255 if v > 160 else 0, mode="1")
    return img


def encode_for_pdf(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    if img.mode == "1":
        img.save(buf, format="PNG")
    else:
        img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
