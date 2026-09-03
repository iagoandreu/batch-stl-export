# What the Fusion STL export API actually does

Everything here was measured in **Fusion 2704.1.23** by exporting known geometry
and reading the files back, because the API reference is wrong in places that
matter. Re-check it with `tools/bse.py export_raw` if a Fusion update moves
something.

## The units, which the API documents wrongly

`STLExportOptions` has four refinement properties. The Fusion API reference gives
the wrong unit for two of them. Everything below was measured in Fusion 2704.1.23
by exporting a 30 mm ball and reading the file back.

| property | documented | **actually** |
|---|---|---|
| `surfaceDeviation` | centimetres | **millimetres** |
| `normalDeviation` | radians | **degrees** |
| `maximumEdgeLength` | centimetres | **millimetres** |
| `aspectRatio` | - | unitless |

They carry the same numbers the 3D Print dialog shows, not Fusion's internal
centimetres.

The evidence:

- `maximumEdgeLength = 1.0` produced a mesh whose median edge is 0.98 mm and
  longest is 1.39 mm. Centimetres would have given ten times that.
- `normalDeviation = 5` held every facet normal within 1.19 deg of the true surface
  normal and forced 5040 triangles. Five radians is 286 deg and would have
  constrained nothing at all.
- The defaults Fusion hands back are exactly the bounding-box diagonal **in
  millimetres**: 51.96 for a 30 mm ball, 30.00 for a 20x20x10 block, 138.56 for an
  80 mm cylinder.
- Default `surfaceDeviation` is that diagonal / 6310 on every body tested, so it
  shares the unit.

The `TriangleMeshCalculator`, by contrast, really does use internal units —
centimetres and radians, as documented. The two are not interchangeable.

**On what `surfaceDeviation` delivers.** It tracks the number you set, linearly,
across three decades. On the 40 mm-radius cylinder the chord deviation came out at
the value asked for almost exactly — 0.003045 mm for 0.003, 0.006981 for 0.006945.
On the 30 mm ball the worst facet-plane deviation came out at twice the value
asked for, consistently:

| set | worst facet deviation | ratio |
|---|---|---|
| 0.001 mm | 0.002001 mm | 2.00 |
| 0.003 mm | 0.005968 mm | 2.00 |
| 0.010 mm | 0.020008 mm | 2.00 |
| 0.050 mm | 0.094434 mm | 1.89 |

(Measured against a least-squares sphere fitted to the exported vertices. Using the
vertex mean as the centre instead gives numbers up to 7x wrong, because a lat-lon
tessellation puts far more vertices near the poles than the equator.)

So: predictable and linear, but treat it as a dial rather than a guaranteed bound,
and halve it if you need a hard limit on a doubly-curved surface.

## What the presets actually are

Measured across four bodies of different size, the presets are pure functions of
the bounding-box diagonal:

| preset | surface deviation | normal deviation | maximum edge | aspect |
|---|---|---|---|---|
| High | diagonal / 19954 | 10 deg | diagonal | 21.5 |
| Medium | diagonal / 6310 | 15 deg | diagonal | 21.5 |
| Low | diagonal / 2512 | 30 deg | diagonal | 21.5 |

For the 30 mm ball (diagonal 51.96 mm) that is 0.002604, 0.008235 and
0.020686 mm.

Maximum edge length is always the diagonal itself, which no triangle can exceed -
so no preset limits edge length, and 0 (no limit) is the faithful default for a
batch rather than pinning one body's diagonal onto all of them.

## A hidden body exports nothing

`exportManager.execute()` returns `true` and no file appears. Nothing in the API
tells you. The add-in switches the light bulb on for the duration of that body's
export and switches it back afterwards, which is enough - no `doEvents()` needed -
and marks those rows `was hidden` in the report. Visibility is not a timeline
feature, so this leaves nothing behind in the design.

Confirmed three ways with `tools/bse.py export_diag`: as it stands, forced, and
forced with a `doEvents()`. Only the first fails.

## There is no triangle-count preview, deliberately

`TriangleMeshCalculator` looked like the way to get one. Fed the same tolerance as
the exporter it returned **128 880** triangles where the export produced **7 224**,
and at a 0.01 mm tolerance with `maxSideLength` and `maxAspectRatio` left at 0 it
**took Fusion down entirely**.

Its own units, unlike the export options, really are the documented internal ones
(centimetres and radians). The two are not interchangeable.

A preview that is both wrong and able to crash the application is worse than none,
so counts are reported after the fact, read out of the written files: the little
endian `uint32` at byte 80 of a binary STL, or a scan for `facet normal` in an
ASCII one.

## Re-checking any of this

```
py -3.10 tests/test_export.py        # names, collisions, counts, settings, report
```

With the dev bridge on, against a live Fusion:

```
py -3.10 tools/bse.py export_make_test          # scratch doc: a ball, blocks,
                                                # a component and a hidden body
py -3.10 tools/bse.py export_probe              # enums and per-body defaults
py -3.10 tools/bse.py preset_values             # what High/Medium/Low resolve to
py -3.10 tools/bse.py export_targets '{"source": "All bodies"}'
py -3.10 tools/bse.py export_run '{"source": "All bodies", "folder": "C:/out"}'
py -3.10 tools/bse.py export_raw '{"body": "Ball 30mm", "path": "C:/out/b.stl",
                                   "surfaceDeviation": 0.01}'
py -3.10 tools/bse.py export_diag '{"folder": "C:/out"}'   # why a body will not export
py -3.10 tools/bse.py all_panels '{"like": "make"}'

py -3.10 tools/bse.py export_install            # register without a Stop/Run
py -3.10 tools/bse.py export_dialog             # then export_set_inputs,
                                                # then export_press_ok
py -3.10 tools/bse.py export_read_dialog        # NB: this terminates the command,
                                                # so read last, never mid-sequence
```

`export_raw` sets the option properties to raw values with no conversion in
between; it is how the unit table above was established, and how to re-check it if
a Fusion update changes something.
