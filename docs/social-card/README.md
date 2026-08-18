# Social preview card

`card.html` is the source for the GitHub social preview card (Settings, Social preview),
1280x640 per GitHub's spec. It renders both this repository's card and the
engineering-framework one, so the two cannot drift apart.

## Why this directory exists

The first card was composed without committing its source. Nothing then tied the picture
to the product, so when the report was redesigned the card kept advertising a layout that
no longer existed, including a summed coverage figure that issue #87 had already removed
from the report for having no honest reading. A picture of the product is a claim about
the product, and a claim nobody can regenerate is one nobody can keep true.

## Regenerating

The card embeds `docs/images/report-light.png`, so refresh that first if the report has
changed (see the note at the end).

```sh
cp ../images/report-light.png report-light.png
python3 -m http.server 8940 --bind 127.0.0.1
```

Then capture at exactly 1280x640, device pixel ratio 1:

- this repository: `http://127.0.0.1:8940/card.html`
- engineering-framework: same URL with
  `?title=Engineering%20Framework&sub=...&tag=...` (the text that repository's card
  carries; see its README for the current wording)

Save to `docs/images/social-card.png` here, and `assets/social-card.png` there. Delete the
copied `report-light.png` afterwards: it is a build input, not a tracked asset.

Uploading the result is a UI-only step, GitHub does not read the file from the repository:
Settings, Social preview, on each repository.

## Refreshing the embedded report capture

The report screenshots in `docs/images/` are captured from the committed demo report:

```sh
uv run python scripts/generate-demo-report.py
python3 -m http.server 8931 --bind 127.0.0.1   # from docs/demo/
```

Then, for each of the four report images, in an **isolated browser context** (a draft
cookie otherwise renders a returning-visitor view as the first-run experience):

| image | viewport | scrolled to |
|---|---|---|
| `report-light.png`, `report-dark.png` | 1157x1672 | top |
| `issues-feedback-light.png`, `issues-feedback-dark.png` | 1157x1222 | the Issues heading, minus 40px |

Emulate the colour scheme explicitly for each, capture at **device pixel ratio 2**, then
downscale by half with Lanczos. The extra pixels are for text crispness; the committed
files are device pixel ratio 1 at the sizes above.

**Suppress the page scrollbar before capturing**, or it appears down the right edge and
steals 15px of content width:

```js
document.head.insertAdjacentHTML('beforeend',
  '<style>html{scrollbar-width:none!important}html::-webkit-scrollbar{display:none!important}</style>')
```

The 1157 figure is the content width, not the window width. An earlier round captured at a
1172 window where a real scrollbar took the other 15px, which is why the committed files
have always been 1157 wide; setting the viewport to 1157 with the scrollbar suppressed
reaches the same layout in one step.

## Refreshing the config-page capture

`config-page-light.png` and `config-page-dark.png` are the interactive configuration page,
served from a `ConfigServer` running against the taster rules pack (the same three domains
a first-time user sees, not the full pack), with `output_dir=None` so the page shows the
generic "this run's output directory" placeholder rather than a real machine's filesystem
path:

```python
from pathlib import Path
from engineering_audit.config_page import ConfigServer
from engineering_audit.rules import load_pack

pack = load_pack(Path("examples/taster-rules"))
server = ConfigServer(pack.domains, output_dir=None)
print(server.start())  # prints the localhost URL to capture
```

Run with `uv run python`, capture the page at 1157x1483 following the same isolated
context, colour scheme, device pixel ratio and scrollbar-suppression steps as the report
images above, then shut the server down (`server.shutdown()`, or just kill the process).

## Framing the raw captures

All six `docs/images/{config-page,report,issues-feedback}-{light,dark}.png` files are
raw captures composited onto a padded canvas with rounded corners, a hairline border and
a soft drop shadow: `docs/images/frame_screenshots.py`, run with
`uv run --with pillow python3 docs/images/frame_screenshots.py`. It reads
`unframed-<name>.png` for each of the six and writes the framed, committed version over
the same path; put the raw capture at that `unframed-` name first, run the script, then
delete the `unframed-` intermediate (it is a build input, not a tracked asset, same as
`report-light.png` copied into this directory below).

## Keeping the social card current

The card embeds the now-framed `docs/images/report-light.png`, so **regenerate the social
card whenever the report capture changes appearance**, framing included: a change to the
padding, shadow or corner radius shows up in the card's cropped 560px-wide thumbnail too,
and `tests/test_social_card_currency.py` fails the suite if the card's last commit is
older than the report capture it embeds, so this cannot be skipped and left for later.

`tests/test_social_card_currency.py` fails the suite if any of these files falls behind
what it is a picture of, so none of this has to be remembered.
