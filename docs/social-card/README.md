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

`tests/test_social_card_currency.py` fails the suite if either of these images falls behind
what it is a picture of, so none of this has to be remembered.
