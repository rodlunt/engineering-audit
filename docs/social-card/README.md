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

Then, for each of the six report images, in an **isolated browser context** (a draft
cookie otherwise renders a returning-visitor view as the first-run experience):

| image | viewport | crop, in CSS px from the document top |
|---|---|---|
| `report-light.png`, `report-dark.png` | 900 wide | 0 to 904: the headline and the first high finding, ending in the gap before the second |
| `issues-feedback-light.png`, `issues-feedback-dark.png` | 900 wide | 5470 to 6212: the Issues heading and the first two issue blocks |
| `domain-verdicts-light.png`, `domain-verdicts-dark.png` | 900 wide | 8395 to 9048: "Every domain, side by side" and its table |

The crop offsets are the demo report's current geometry, not fixed constants: re-measure
them (`getBoundingClientRect().top + scrollY` on the relevant heading or card) after any
change to the demo content or the renderer, rather than trusting the numbers above.

Emulate the colour scheme explicitly for each, capture at **device pixel ratio 2**, then
downscale by half with Lanczos. The extra pixels are for text crispness; the committed
files are device pixel ratio 1 at the sizes above.

**900, not the full container width.** The report's container is 56rem (896px). Capturing
wider than the content and then rendering the result into GitHub's ~880px README column is
what made the previous round unreadable: a 1253px capture shown at `width="720"` put body
text on screen at about 7px, and the two images shown at `width="48%"` at about 4px. Every
capture here is now close to 1:1 with the width it is displayed at, and each README image
spans the column rather than sharing it.

**Do not use a full-page screenshot for the deeper crops.** The demo report is ~12,500 CSS
px tall, which at device pixel ratio 2 exceeds Chrome's ~16,384px texture limit, and the
region past that limit comes back showing the top of the page again rather than failing.
`domain-verdicts` sits past it. Scroll to the region and capture the viewport instead, which
is why that pair's viewport height is set to the crop height.

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
a first-time user sees, not the full pack), with an **invented** `output_dir` matching the
demo repository, so the page shows a concrete path a reader can understand rather than the
generic placeholder, and still never a real machine's filesystem path:

```python
from pathlib import Path
from engineering_audit.config_page import ConfigServer
from engineering_audit.rules import load_pack

pack = load_pack(Path("examples/taster-rules"))
server = ConfigServer(
    list(pack.domains),
    output_dir=Path("/home/you/code/orders-api/audit-output"),  # invented, matches the demo repo
)
print(server.start())  # prints the localhost URL to capture
```

Run with `uv run python`, capture at 760 wide (the config page's container is 44rem) and
crop 0 to 953, ending after "How should findings be delivered?", following the same
isolated context, colour scheme, device pixel ratio and scrollbar-suppression steps as the
report images above, then shut the server down (`server.shutdown()`, or just kill the
process).

**The server has to still be running when you capture.** Rendering the form to a static
HTML file and serving that instead puts a red "The audit process is no longer running"
banner across the top of the shot, because the page polls the server that opened it.

**A genuinely fresh isolated context per capture round.** Reusing one that has already
loaded the page carries the draft cookie forward, and the shot then shows the
returning-visitor "Your previous domain selection" view instead of the first-run one.

## Framing the raw captures

All eight `docs/images/{config-page,report,issues-feedback,domain-verdicts}-{light,dark}.png`
files are raw captures composited onto a padded canvas with rounded corners, a hairline
border and a soft drop shadow: `docs/images/frame_screenshots.py`, run with
`uv run --with pillow python3 docs/images/frame_screenshots.py`. It reads
`unframed-<name>.png` for each of the eight and writes the framed, committed version over
the same path; put the raw capture at that `unframed-` name first, run the script, then
delete the `unframed-` intermediate (it is a build input, not a tracked asset, same as
`report-light.png` copied into this directory below).

**Recapturing a subset is expected.** A change that only affects the report body leaves the
config-page and domain-verdicts captures untouched, so the script treats a missing
`unframed-` input as "not recaptured this round", leaves the committed file alone, and
prints a `SKIPPED` line naming it. It exits non-zero if it framed nothing at all, since
that means the inputs or the working directory are wrong rather than that there was
nothing to do.

## Keeping the social card current

The card embeds the now-framed `docs/images/report-light.png`, so **regenerate the social
card whenever the report capture changes appearance**, framing included: a change to the
padding, shadow or corner radius shows up in the card's cropped 560px-wide thumbnail too,
and `tests/test_social_card_currency.py` fails the suite if the card's last commit is
older than the report capture it embeds, so this cannot be skipped and left for later.

`tests/test_social_card_currency.py` fails the suite if any of these files falls behind
what it is a picture of, so none of this has to be remembered.
