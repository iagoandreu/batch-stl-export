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

## How to use

### Where the button is

**Design** workspace: **Utilities** tab, **Make** panel (next to 3D Print), and
**Mesh** tab, **Export** panel. The button is **Batch STL Export**. It is promoted,
so it shows on the toolbar. If one of these panels does not exist in your Fusion
build, the add-in uses the next panel on its short list.

### Before you start

- **Bodies or components to export** in the open design: solid bodies, mesh
  bodies, or components.
- **An output folder.** The dialog will not run without one. It is created if it
  does not exist.
- **No external Python.** Everything runs inside Fusion's own Python.

### Step by step

1. Click **Batch STL Export**. The OK button is labelled **Export**.
2. Choose what to export in **Export**.
3. If you chose *Selected bodies and components*, select them in **Objects**.
4. Read the **Plan** box: number of files, the first three file names, the folder
   state, and the refinement that will be used.
5. Set the output, refinement and after-export options below.
6. Click **Export**. A progress bar runs; **Cancel** stops after the current file.

The settings, the folder included, are saved (`last_export.json` beside the
installed add-in) and come back next time. The defaults below are the first-run
values.

| Input | Default | What it does |
|---|---|---|
| Export | Selected bodies and components | *Selected bodies and components*, *All visible bodies*, *All bodies* (hidden ones too), or *Top-level components* (one file per component under the root, plus one per body loose in the root). |
| Objects | (empty) | Shown for *Selected bodies and components*. Solid bodies, mesh bodies and components; any number. One file each. |

**Output**

| Input | Default | What it does |
|---|---|---|
| Folder | (empty) | Where the files go. Required. |
| Choose folder | | Opens a folder picker and writes the result into **Folder**. |
| File name | `{body}` | Name template. Tokens: `{body}` `{comp}` `{occ}` `{doc}` `{n}` `{nn}` `{nnn}` `{date}` `{time}` `{kind}` (see *File names* below). `.stl` is added. |
| If a file exists | Number the new one | *Number the new one* (adds `_2`, `_3`...), *Overwrite*, or *Skip*. |
| Format | Binary | *Binary* or *ASCII*. |
| Units | Millimeter | Unit written into the file: *Millimeter*, *Centimeter*, *Meter*, *Inch*, *Foot*. |

**Refinement settings** (how finely solids are triangulated)

| Input | Default | What it does |
|---|---|---|
| Refinement | Custom | *High*, *Medium*, *Low* or *Custom*. |
| Surface deviation | 0.01 mm | Maximum distance between the triangles and the true surface. |
| Normal deviation | 15 deg | Maximum angle between neighbouring triangle normals. |
| Maximum edge length | 0 mm | Longest triangle edge allowed. 0 leaves it to Fusion (no limit in practice). |
| Aspect ratio | 21.5 (0 to 100) | Maximum triangle aspect ratio. 0 leaves it to Fusion. The presets all use 21.5. |
| Reset all settings | | Puts every setting back to its default, except the folder. |

How the dropdown and the four numbers interact:

- Picking **High**, **Medium** or **Low** fills the four numbers with what that
  preset gives for the **first** object in the list. On export the preset is
  applied as a preset, so Fusion scales the deviation to each body's size and every
  file gets a different absolute tolerance.
- Typing any of the four numbers switches the dropdown to **Custom**. Custom
  applies the four numbers as typed, so every file gets the **same** absolute
  tolerance.
- Picking **Custom** from the dropdown overwrites the four numbers with the
  *Medium* values for the first object, as a starting point. Type your own values
  after picking it.

**After export**

| Input | Default | What it does |
|---|---|---|
| Write a manifest next to the files | on | Writes `stl_export_YYYYMMDD_HHMMSS.txt` into the output folder with the full report. |
| Open the folder when finished | on | Opens the output folder in Explorer. |
| Show the report when finished | on | Shows the report in a message box. |

### What you get

- One STL file per object in the output folder, named from the template.
- Solids and components are written by Fusion's STL exporter with your refinement
  settings, in assembly coordinates, so parts still fit together when reopened.
- Mesh bodies are written directly from their own triangles. The refinement
  settings do not apply to them.
- A hidden solid or component is made visible for the export and hidden again.
  The report marks it `was hidden`.
- The manifest (if ticked) and a copy of the report in `reports\export_YYYYMMDD_HHMMSS.txt`
  beside the installed add-in. Errors go to `BatchSTLExport.log` in the same folder.
- Nothing is added to the design or its timeline.

### Checking the result

The report starts with *Batch STL export - N of M written*, then lists every
setting used, then one line per file: file name, triangle count, size, source
object, and a status note when it is not a plain write (`overwritten`,
`renamed to avoid a clash`, `skipped, already there`, `was hidden`, `cancelled`,
`FAILED: ...`).

1. Check that **N of M written** matches the number of objects you expected.
2. Look for `FAILED` and `Not written` lines. Details of a failure are in
   `BatchSTLExport.log`.
3. Check the triangle counts. They are read back from the written files, not
   predicted. A very low count on a curved part means the refinement was too
   coarse; a very high one means a large file for the printer.
4. Open one or two files in your slicer or a mesh viewer and check the scale
   (**Units**) and the surface quality before printing.

### Where it fits in a patient-specific workflow

It is the last step: once the guide, plate or model is designed, export every
part at one known tolerance in one pass. Keep the manifest with the case files; it
records the settings and the triangle count of every file as written.

### Known limits

- There is no triangle-count preview before export; the counts come from the
  written files.
- With a preset, the absolute deviation differs per body. Use **Custom** when
  every part must have the same tolerance.
- Two objects that resolve to the same file name in one run are always numbered
  apart, whatever **If a file exists** says.
- Characters `<>:"/\|?*` become `_`, and names are cut to 120 characters.

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

`Maximum edge length` and `Aspect ratio` take **0 for no limit**. Maximum edge
length defaults to 0; aspect ratio defaults to 21.5, the value all three presets
use. A 0 edge length loses nothing: the presets set maximum edge length to the
body's own bounding-box diagonal, and no triangle can be longer than that, so a
preset does not limit it either.

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
