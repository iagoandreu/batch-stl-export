# Batch STL Export

A Fusion 360 add-in that exports **many bodies or components to STL in one pass**,
with refinement settings that are **remembered between runs**.

Fusion's own **3D Print** dialog does neither. It exports one object per
invocation and forgets every setting the moment it closes, so producing a tray of
parts at a fixed tolerance means opening it once per body and retyping the same
four numbers each time.

This reports the triangle count and file size **read back out of the files it
wrote**, not predicted — and writes an optional manifest next to them recording
exactly what was used.

## Install

1. Clone or copy this folder anywhere.
2. Run `powershell -File install.ps1`. It copies the add-in into
   `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\BatchSTLExport`.
3. In Fusion: **Utilities → ADD-INS → Scripts and Add-Ins → Add-Ins** →
   `Batch STL Export` → tick **Run on Startup** → **Run**.
4. The button appears in **Utilities → Make**, next to 3D Print, and in
   **Mesh → Export**.

No dependencies. It runs entirely inside Fusion's bundled Python, stdlib only.

## What it exports

| Export | Meaning |
|---|---|
| **Selected bodies and components** | one file per selected solid body, mesh body or component |
| **All visible bodies** | one file per visible body anywhere in the design |
| **All bodies** | the same, hidden ones included |
| **Top-level components** | one file per component directly under the root, each holding everything inside it, plus any body sitting loose in the root |

Bodies inside components are taken as **proxies**, so each file is written in
assembly coordinates and the parts still fit together when reopened.

## File names

The **File name** field is a template:

| token | is |
|---|---|
| `{body}` | body name, or the component name for a component |
| `{comp}` | the component the body belongs to |
| `{occ}` | the occurrence, instance number included (`Guide:2`) |
| `{doc}` | the document name |
| `{n}` `{nn}` `{nnn}` | position in the run, zero padded to 1/2/3 digits |
| `{date}` `{time}` | when the run started, `20260903` and `162233` |
| `{kind}` | `solid`, `mesh` or `component` |

`<>:"/\|?*` and control characters become `_`, a trailing dot is dropped, and
`.stl` is appended unless the template already ends in it.

**If a file exists** chooses between numbering the new one, overwriting and
skipping. Whatever that is set to, two objects that resolve to the *same* name
inside one run are always numbered apart — silently collapsing them into one file
would lose a part without saying so.

## Refinement

The four numbers are **always visible**. Hiding the surface deviation behind a
preset is the thing that makes the 3D Print dialog awkward to tune, and the
deviation is the number worth tuning.

Picking `High`, `Medium` or `Low` fills them in with what that preset resolves to
for the first object, so you can see what it means. Typing over any of them flips
the dropdown to `Custom`, exactly as Fusion's own dialog does. What the panel shows
is always what gets applied.

The two modes differ in a way that matters for a batch:

- a **preset** is applied as a preset, so Fusion scales the deviation to each
  body's own size — every file gets a different absolute tolerance;
- **Custom** applies the numbers verbatim, so every file gets the *same* absolute
  tolerance. For a print tray that is usually what you want.

Measured: `Custom 0.003 mm` gave a 30 mm ball 24 648 triangles and an 80 mm
cylinder 2 566; `High` gave the same ball 28 560 and the same cylinder 1 348 —
finer on the small part, coarser on the large one.

`Maximum edge length` and `Aspect ratio` take **0 for no limit**, which is the
default. That loses nothing: the presets set maximum edge length to the body's own
bounding-box diagonal, and no triangle can be longer than that, so a preset does
not limit it either.

**Reset all settings** puts the panel back to its defaults. The output folder is
kept, because re-picking that is the one thing a reset should not cost you.

Everything, the folder included, is written to `last_export.json` beside the
add-in and comes back next time.

## What gets written

A report, and — unless you turn it off — a `stl_export_<timestamp>.txt` manifest in
the output folder holding the same text: every setting used, and for each file its
name, triangle count, size and source object. The counts are parsed out of the
written files (the binary header, or a scan for `facet normal`), so the manifest
records what actually happened rather than what was intended. That matters when
the export is part of a controlled record.

## Things Fusion does quietly

Two behaviours worth knowing about, both handled here and both documented with the
measurements in [docs/FUSION-STL-API.md](docs/FUSION-STL-API.md):

- **The API reference gives the wrong unit for half the refinement properties.**
  `surfaceDeviation` and `maximumEdgeLength` are millimetres, not centimetres, and
  `normalDeviation` is degrees, not radians. They carry the same numbers the 3D
  Print dialog shows.
- **A hidden body exports nothing.** `exportManager.execute()` returns `true` and
  no file appears. This add-in switches the light bulb on for the duration and
  puts it back, and marks those rows `was hidden` in the report.

## Repository layout

```
BatchSTLExport.py        add-in shell: lifecycle and the dev bridge, nothing else
bse_core.py              all the working code, plus the command, its handlers
                         and its button; re-imported on every event
install.ps1              push into Fusion's AddIns folder
resources/               toolbar icons
docs/                    FUSION-STL-API.md, index.html
tools/                   dev-bridge client, icon generator
tests/                   offline tests, no Fusion required
```

`bse_core.py` **hot-reloads** — it is re-imported on every command event, so
pushing it takes effect on the next click; only `BatchSTLExport.py` needs a
Stop/Run. Because of that reload, nothing may keep state in a module global
between events: dropdown choices are resolved from the item **label**, never a
remembered index.

## Tests

```
py -3.10 tests/test_export.py
```

45 checks covering names, collision policy, reading triangle counts back out of
both STL flavours, settings persistence and migration, and the report.
`tests/stub` fakes `adsk`, so it runs without Fusion.

## The dev bridge

Fusion has no headless mode and no CLI, so the only way to test an add-in
automatically is from inside a running Fusion. Creating a `devbridge.on` file next
to the add-in starts a folder watcher that runs jobs on Fusion's main thread:

```
py -3.10 tools/bse.py export_make_test      # scratch doc with the awkward cases
py -3.10 tools/bse.py export_probe          # enums and per-body defaults
py -3.10 tools/bse.py preset_values         # what High/Medium/Low resolve to
py -3.10 tools/bse.py export_run '{"source": "All bodies", "folder": "C:/out"}'
py -3.10 tools/bse.py export_dialog         # then export_set_inputs, export_press_ok
```

Off by default. Nothing in normal operation depends on it.

## Licence

MIT — see [LICENSE](LICENSE).
