#!/usr/bin/env python3
"""Frame raw screenshots for the README: rounded corners, hairline border,
soft drop shadow, padded canvas, light and dark variants.

One-off tooling, run with:
    uv run --with pillow python3 docs/images/frame_screenshots.py

Not wired into CI; re-run by hand whenever a screenshot is recaptured. Takes
the light/dark screenshot pairs already captured per
docs/social-card/README.md (device pixel ratio 2, downscaled by half with
Lanczos) and composites each onto its own light or dark canvas: a light
capture never gets a dark canvas and vice versa, since the capture's own
background already matches one scheme.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

IMAGES = Path(__file__).parent

CANVAS_LIGHT = (246, 248, 250)
CANVAS_DARK = (22, 27, 34)
BORDER_LIGHT = (208, 215, 222)
BORDER_DARK = (48, 54, 61)
SHADOW_COLOUR = (0, 0, 0)

CORNER_RADIUS = 18
PAD_SIDE = 48
PAD_TOP = 48
PAD_BOTTOM = 72
SHADOW_BLUR = 28
SHADOW_OFFSET_Y = 14
SHADOW_OPACITY = 90  # 0-255


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        [(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=255
    )
    return mask


def frame(src_path: Path, dest_path: Path, canvas_colour, border_colour) -> None:
    shot = Image.open(src_path).convert("RGB")
    sw, sh = shot.size

    canvas_w = sw + PAD_SIDE * 2
    canvas_h = sh + PAD_TOP + PAD_BOTTOM
    canvas = Image.new("RGBA", (canvas_w, canvas_h), canvas_colour + (255,))

    # Soft shadow: a blurred rounded rectangle the same size as the shot,
    # offset down slightly, sitting under the card.
    shadow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    shadow_shape = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_shape)
    shadow_draw.rounded_rectangle(
        [(0, 0), (sw - 1, sh - 1)],
        radius=CORNER_RADIUS,
        fill=SHADOW_COLOUR + (SHADOW_OPACITY,),
    )
    shadow_layer.paste(
        shadow_shape, (PAD_SIDE, PAD_TOP + SHADOW_OFFSET_Y), shadow_shape
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    canvas = Image.alpha_composite(canvas, shadow_layer)

    # Rounded-corner card with the real screenshot inside.
    card = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    card.paste(shot, (0, 0))
    mask = rounded_mask((sw, sh), CORNER_RADIUS)
    canvas.paste(card, (PAD_SIDE, PAD_TOP), mask)

    # Hairline border on top, same rounded rect.
    border_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_layer)
    border_draw.rounded_rectangle(
        [(PAD_SIDE, PAD_TOP), (PAD_SIDE + sw - 1, PAD_TOP + sh - 1)],
        radius=CORNER_RADIUS,
        outline=border_colour + (255,),
        width=1,
    )
    canvas = Image.alpha_composite(canvas, border_layer)

    canvas.convert("RGB").save(dest_path, "PNG", optimize=True)
    print(f"wrote {dest_path} ({canvas.width}x{canvas.height})")


# Each capture already matches one colour scheme (it is a screenshot of the
# real page in that scheme), so it gets framed onto the matching canvas only.
SHOTS = [
    ("config-page-light.png", CANVAS_LIGHT, BORDER_LIGHT),
    ("config-page-dark.png", CANVAS_DARK, BORDER_DARK),
    ("report-light.png", CANVAS_LIGHT, BORDER_LIGHT),
    ("report-dark.png", CANVAS_DARK, BORDER_DARK),
    ("issues-feedback-light.png", CANVAS_LIGHT, BORDER_LIGHT),
    ("issues-feedback-dark.png", CANVAS_DARK, BORDER_DARK),
    ("domain-verdicts-light.png", CANVAS_LIGHT, BORDER_LIGHT),
    ("domain-verdicts-dark.png", CANVAS_DARK, BORDER_DARK),
]

# Recapturing a subset is the normal case: a change that only affects the
# report body leaves the config-page and domain-verdicts captures untouched.
# A missing unframed- input therefore means "not recaptured this round", not
# an error, and the existing framed file is left exactly as it was. It is
# announced rather than passed over in silence, because a skipped step that
# looks identical to a completed one is the failure mode this project keeps
# writing rules about; and if nothing at all was framed, that is a mistake
# (wrong directory, inputs deleted early) and exits non-zero rather than
# printing a clean-looking nothing.
framed = 0
skipped = []
for name, canvas_colour, border_colour in SHOTS:
    src = IMAGES / f"unframed-{name}"
    if not src.is_file():
        skipped.append(name)
        continue
    frame(src, IMAGES / name, canvas_colour, border_colour)
    framed += 1

for name in skipped:
    print(f"SKIPPED {name}: no unframed-{name} found; left as committed")

if framed == 0:
    raise SystemExit(
        f"framed nothing: none of the {len(SHOTS)} unframed- inputs were found in "
        f"{IMAGES}. Capture first, or check the working directory."
    )
print(f"framed {framed} of {len(SHOTS)}, skipped {len(skipped)}")
