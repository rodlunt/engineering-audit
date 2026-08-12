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

Capture at 1172px wide, device pixel ratio 1, in both colour schemes.
