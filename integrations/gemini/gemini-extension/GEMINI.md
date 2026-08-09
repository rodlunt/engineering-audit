# Engineering Audit Triggers (merge into GEMINI.md)

This file ships as a placeholder. The engineering-audit repository contains no
rule content of its own (rules packs are distributed separately, see the
repository root README), so there is nothing real to bake into this context file
at packaging time.

Before installing this extension, generate the real trigger content from your own
rules pack and replace everything below this line with the generator's output:

```
uv run engineering-audit-fragments --rules-dir <path-to-rules-clone> --out-dir <tmp-dir>
```

Then copy `<tmp-dir>/GEMINI-fragment.md` over the section below (it already starts
with a "merge into GEMINI.md" header matching this file).

For the standalone audit flow, see `AUDIT.md` in the engineering-audit repository:
it is the source of truth this extension's `/audit` command points at.
