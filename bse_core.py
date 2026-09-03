"""Batch STL Export - all the working code. Re-imported fresh on every use.

BatchSTLExport.py is the shell: lifecycle and an optional dev bridge, nothing else.
This module owns the command itself - its definition, its handlers and its toolbar
button - so the whole thing hot-reloads and editing it takes effect on the next
click, with no Stop/Run and no Fusion restart.

Why it exists: Fusion's own 3D Print dialog exports one object per invocation and
forgets every setting the moment it closes, so producing a tray of parts at a fixed
tolerance means retyping the same four numbers once per body.

UNITS - MEASURED IN THIS FUSION, NOT TAKEN FROM THE DOCS, WHICH ARE WRONG.
Fusion's internal length unit is centimetres and every ValueCommandInput here
returns centimetres, so dialog values are converted once on the way out. But the
four STLExportOptions refinement properties are NOT internal units: they carry the
same numbers the 3D Print dialog shows.

    surfaceDeviation    MILLIMETRES  (docs say centimetres)
    normalDeviation     DEGREES      (docs say radians)
    maximumEdgeLength   MILLIMETRES  (docs say centimetres)
    aspectRatio         unitless

Evidence: maximumEdgeLength = 1.0 on a 30 mm ball produced a mesh whose median
edge is 0.98 mm; normalDeviation = 5 held facet normals inside 1.19 deg, whereas
5 radians is 286 deg and would constrain nothing; and the defaults Fusion hands
back are exactly the bounding-box diagonal in mm (30 mm ball -> 51.96).

Fusion's bundled Python has no numpy - stdlib only in this file.

NO CROSS-EVENT MODULE STATE: this module is re-imported for every command event,
so nothing may be stashed in a global between them. Dropdown choices are resolved
by item LABEL, never by remembered index.
"""

import adsk.core
import adsk.fusion

import json
import math
import os
import struct
import sys
import time
import traceback
import types

VERSION = '1.0.0'

ADDIN_DIR = os.path.dirname(os.path.realpath(__file__))
SETTINGS_PATH = os.path.join(ADDIN_DIR, 'last_export.json')
LOG_PATH = os.path.join(ADDIN_DIR, 'BatchSTLExport.log')
REPORT_DIR = os.path.join(ADDIN_DIR, 'reports')

CM = 10.0          # cm -> mm
MM = 0.1           # mm -> cm

CMD_ID = 'BatchSTLExport_Cmd'
CMD_NAME = 'Batch STL Export'
CMD_TOOLTIP = (
    'Export many bodies or components to STL in one pass.\n\n'
    'One file per body, a filename template, and refinement settings that are '
    'remembered between runs. Reports the triangle count and size actually '
    'written for every file.'
)
RES_DIR = os.path.join(ADDIN_DIR, 'resources', 'BatchSTLExport')


def log(msg):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as fh:
            fh.write('%s  [export] %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


def short_error(text):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line and not line.startswith(('File "', 'Traceback')):
            return line
    return text.strip()[-200:]


# --------------------------------------------------------------------------- #
# choices - resolved by label everywhere, never by index
# --------------------------------------------------------------------------- #

SRC_SELECTED = 'Selected bodies and components'
SRC_VISIBLE = 'All visible bodies'
SRC_ALL = 'All bodies'
SRC_COMPONENTS = 'Top-level components'
SOURCES = [SRC_SELECTED, SRC_VISIBLE, SRC_ALL, SRC_COMPONENTS]

REF_CUSTOM = 'Custom'
REFINEMENTS = ['High', 'Medium', 'Low', REF_CUSTOM]

EXISTING = ['Number the new one', 'Overwrite', 'Skip']
FORMATS = ['Binary', 'ASCII']
UNITS = ['Millimeter', 'Centimeter', 'Meter', 'Inch', 'Foot']


def ref_enum(label):
    e = adsk.fusion.MeshRefinementSettings
    return {'High': e.MeshRefinementHigh,
            'Medium': e.MeshRefinementMedium,
            'Low': e.MeshRefinementLow,
            REF_CUSTOM: e.MeshRefinementCustom}[label]


def unit_enum(label):
    e = adsk.fusion.DistanceUnits
    return {'Millimeter': e.MillimeterDistanceUnits,
            'Centimeter': e.CentimeterDistanceUnits,
            'Meter': e.MeterDistanceUnits,
            'Inch': e.InchDistanceUnits,
            'Foot': e.FootDistanceUnits}[label]


def chosen(inputs, cid, fallback):
    """The LABEL of the selected dropdown item. Index is meaningless across a reload."""
    try:
        item = inputs.itemById(cid).selectedItem
        return item.name if item else fallback
    except Exception:
        return fallback


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #

DEFAULTS = {
    'source': SRC_SELECTED,
    'folder': '',
    'template': '{body}',
    'existing': 'Number the new one',
    'format': 'Binary',
    'units': 'Millimeter',
    'refinement': REF_CUSTOM,
    'surfaceDeviation': 0.01,      # mm
    'normalDeviation': 15.0,       # deg
    'maxEdge': 0.0,                # mm, 0 = no limit
    'aspectRatio': 21.5,           # 0 = no limit
    'openFolder': True,
    'manifest': True,
    'showReport': True,
}


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as fh:
            s.update(json.load(fh))
    except Exception:
        pass
    # a remembered label that no longer exists would silently select item 0
    for key, allowed in (('source', SOURCES), ('refinement', REFINEMENTS),
                         ('existing', EXISTING), ('format', FORMATS),
                         ('units', UNITS)):
        if s.get(key) not in allowed:
            s[key] = DEFAULTS[key]
    # settings written before the two tick boxes became "0 means no limit": an
    # unticked box left a stale number behind that would now be applied for real
    if s.pop('limitEdge', True) is False:
        s['maxEdge'] = 0.0
    if s.pop('setAspect', True) is False:
        s['aspectRatio'] = 0.0
    return s


def save_settings(d):
    try:
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as fh:
            json.dump(d, fh, indent=2)
    except Exception:
        log(traceback.format_exc())


# --------------------------------------------------------------------------- #
# what to export
# --------------------------------------------------------------------------- #

def _target(geom, body, comp, occ, kind):
    return {'geom': geom, 'body': body, 'comp': comp, 'occ': occ, 'kind': kind}


def _visible(entity):
    for attr in ('isVisible', 'isLightBulbOn'):
        try:
            return bool(getattr(entity, attr))
        except Exception:
            continue
    return True


def _bodies_of(comp_or_occ, comp_name, occ_name, out, visible_only):
    for coll, kind in ((comp_or_occ.bRepBodies, 'solid'),
                       (comp_or_occ.meshBodies, 'mesh')):
        for b in coll:
            if visible_only and not _visible(b):
                continue
            out.append(_target(b, b.name, comp_name, occ_name, kind))


def gather(design, source, sel_input):
    """Every object that will become one STL file, in browser order."""
    root = design.rootComponent
    out = []
    if source == SRC_SELECTED:
        if sel_input is None:
            return out
        for i in range(sel_input.selectionCount):
            ent = sel_input.selection(i).entity
            occ = adsk.fusion.Occurrence.cast(ent)
            if occ:
                out.append(_target(occ, occ.component.name, occ.component.name,
                                   occ.name, 'component'))
                continue
            mesh = adsk.fusion.MeshBody.cast(ent)
            body = mesh or adsk.fusion.BRepBody.cast(ent)
            if body:
                a = body.assemblyContext
                out.append(_target(body, body.name, body.parentComponent.name,
                                   a.name if a else root.name,
                                   'mesh' if mesh else 'solid'))
        return out

    if source == SRC_COMPONENTS:
        for occ in root.occurrences:
            out.append(_target(occ, occ.component.name, occ.component.name,
                               occ.name, 'component'))
        # bodies sitting loose in the root are inside no component, and exporting
        # the root component would pull in every occurrence again, so take them
        # one by one instead
        _bodies_of(root, root.name, root.name, out, False)
        return out

    visible_only = source == SRC_VISIBLE
    _bodies_of(root, root.name, root.name, out, visible_only)
    for occ in root.allOccurrences:
        if visible_only and not _visible(occ):
            continue
        # occ.bRepBodies are proxies - assembly coordinates, and only the bodies
        # directly in this occurrence, so nothing is counted twice
        _bodies_of(occ, occ.component.name, occ.name, out, visible_only)
    return out


# --------------------------------------------------------------------------- #
# file names
# --------------------------------------------------------------------------- #

BAD_CHARS = '<>:"/\\|?*'
TOKENS = ('{doc}', '{comp}', '{occ}', '{body}', '{n}', '{nn}', '{nnn}',
          '{date}', '{time}', '{kind}')


def safe_name(text, fallback='body'):
    s = ''.join('_' if (c in BAD_CHARS or ord(c) < 32) else c for c in str(text))
    s = s.strip().rstrip('. ')
    return s[:120] or fallback


def render_name(template, t, n, doc_name, stamp):
    values = {
        'doc': doc_name, 'comp': t['comp'], 'occ': t['occ'], 'body': t['body'],
        'n': str(n), 'nn': '%02d' % n, 'nnn': '%03d' % n,
        'date': stamp[:8], 'time': stamp[9:], 'kind': t['kind'],
    }
    name = template
    for key, val in values.items():
        name = name.replace('{%s}' % key, safe_name(val, key))
    name = safe_name(name)
    return name if name.lower().endswith('.stl') else name + '.stl'


def resolve_path(folder, name, policy, used):
    """Apply the collision policy. Returns (path, note); path is None when skipped."""
    path = os.path.join(folder, name)
    taken = path.lower() in used
    if not taken and not os.path.exists(path):
        used.add(path.lower())
        return path, ''
    if not taken:
        if policy == 'Overwrite':
            used.add(path.lower())
            return path, 'overwritten'
        if policy == 'Skip':
            return None, 'skipped, already there'
    # two objects with the same name inside one run must never collapse into one
    # file, so numbering applies whatever the policy says
    stem, ext = os.path.splitext(name)
    for k in range(2, 1000):
        cand = os.path.join(folder, '%s_%d%s' % (stem, k, ext))
        if cand.lower() not in used and not os.path.exists(cand):
            used.add(cand.lower())
            return cand, 'renamed to avoid a clash'
    raise RuntimeError('no free filename for %s' % name)


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #

def apply_refinement(opts, o):
    """Setting any of the four values flips meshRefinement to Custom by itself, and
    setting meshRefinement resets all four - so it is one or the other, never both.

    A preset is applied AS a preset, so Fusion scales the deviation to each body's
    own size. Custom applies the four numbers verbatim to every body. Zero means
    no limit for the two that have one."""
    if o['refinement'] != REF_CUSTOM:
        opts.meshRefinement = ref_enum(o['refinement'])
        return
    # MEASURED, not assumed - see UNITS above. No cm or radian conversion here.
    opts.surfaceDeviation = o['surface_mm']
    opts.normalDeviation = o['normal_deg']
    if o['max_edge_mm'] > 0:
        opts.maximumEdgeLength = o['max_edge_mm']
    if o['aspect'] > 0:
        opts.aspectRatio = o['aspect']


def make_options(exp_mgr, geom, path, o):
    opts = exp_mgr.createSTLExportOptions(geom, path)
    opts.isBinaryFormat = o['format'] == 'Binary'
    opts.isOneFilePerBody = False
    try:
        opts.sendToPrintUtility = False      # otherwise it can hand off to a slicer
    except Exception:
        pass
    try:
        opts.unitType = unit_enum(o['units'])
    except Exception:
        log('unitType %r rejected' % o['units'])
    apply_refinement(opts, o)
    return opts


def stl_triangles(path):
    """Read the count back out of the written file - the only honest source for it."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as fh:
            head = fh.read(84)
        if len(head) == 84:
            n = struct.unpack('<I', head[80:84])[0]
            if 84 + 50 * n == size:
                return n
        needle = b'facet normal'
        count, tail = 0, b''
        with open(path, 'rb') as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                buf = tail + chunk
                count += buf.count(needle)
                tail = buf[-(len(needle) - 1):]
        return count
    except Exception:
        return None


def _force_visible(entity):
    """A hidden body exports NOTHING - execute() still returns true and no file
    appears. Switch the light bulbs on for the duration and hand back what to undo.
    Visibility is not a timeline feature, so this leaves no trace in the design."""
    switched = []
    node = entity
    while node is not None:
        try:
            if node.isLightBulbOn is False:
                node.isLightBulbOn = True
                switched.append(node)
        except Exception:
            pass
        node = getattr(node, 'assemblyContext', None)
    return switched


def _restore(switched):
    for node in reversed(switched):
        try:
            node.isLightBulbOn = False
        except Exception:
            pass


def export_one(exp_mgr, t, path, o):
    switched = _force_visible(t['geom'])
    try:
        opts = make_options(exp_mgr, t['geom'], path, o)
        if not exp_mgr.execute(opts):
            raise RuntimeError('exportManager.execute returned false')
    finally:
        _restore(switched)
    if not os.path.exists(path):
        raise RuntimeError('no file was written')
    return {'triangles': stl_triangles(path), 'bytes': os.path.getsize(path),
            'unhidden': bool(switched)}


def run_export(app, o, targets, progress=None):
    design = adsk.fusion.Design.cast(app.activeProduct)
    exp_mgr = design.exportManager
    doc_name = safe_name(app.activeDocument.name, 'design')
    stamp = time.strftime('%Y%m%d_%H%M%S')
    os.makedirs(o['folder'], exist_ok=True)

    used, rows = set(), []
    for i, t in enumerate(targets):
        if progress is not None:
            if progress.wasCancelled:
                rows.append({'name': '', 'target': t['body'], 'kind': t['kind'],
                             'status': 'cancelled'})
                break
            progress.progressValue = i
            progress.message = 'Exporting ' + t['body'] + '  (%v of %m)'
        name = render_name(o['template'], t, i + 1, doc_name, stamp)
        try:
            path, note = resolve_path(o['folder'], name, o['existing'], used)
            if path is None:
                rows.append({'name': name, 'target': t['body'], 'kind': t['kind'],
                             'status': note})
                continue
            res = export_one(exp_mgr, t, path, o)
            status = note or 'written'
            if res['unhidden']:
                status = (status + ', was hidden') if note else 'written, was hidden'
            rows.append({'name': os.path.basename(path), 'target': t['body'],
                         'kind': t['kind'], 'status': status,
                         'triangles': res['triangles'], 'bytes': res['bytes']})
        except Exception:
            tb = traceback.format_exc()
            log('%s\n%s' % (t['body'], tb))
            rows.append({'name': name, 'target': t['body'], 'kind': t['kind'],
                         'status': 'FAILED: ' + short_error(tb)})
    return rows


# --------------------------------------------------------------------------- #
# probing a body for the numbers Fusion itself would use
#
# There is deliberately NO triangle-count preview. TriangleMeshCalculator looked
# like the way to get one, but it disagreed with the exporter (128 880 against
# 7 224 triangles on the same ball at the same tolerance) and, with no aspect or
# edge limit set, it locked Fusion's main thread for minutes. The counts in the
# report are read back out of the written files instead.
# --------------------------------------------------------------------------- #

PROBE_PATH = os.path.join(ADDIN_DIR, '_probe.stl')


def bodies_under(geom):
    occ = adsk.fusion.Occurrence.cast(geom)
    if not occ:
        return [geom]
    out = list(occ.bRepBodies) + list(occ.meshBodies)
    for child in occ.childOccurrences:
        out.extend(bodies_under(child))
    return out


def preset_fields(exp_mgr, geom, label):
    """The four numbers a refinement resolves to for this object.

    Measured across four bodies: surface deviation is the bounding-box diagonal
    over 19954 (High), 6310 (Medium) or 2512 (Low); normal deviation is a flat
    10 / 15 / 30 deg; aspect ratio is always 21.5. Maximum edge length always comes
    back as the diagonal itself, which no triangle can exceed - so it is not a
    limit at all, and it is reported as 0 rather than pinning one body's diagonal
    onto a whole batch."""
    for b in bodies_under(geom):
        if adsk.fusion.MeshBody.cast(b):
            continue
        opts = exp_mgr.createSTLExportOptions(b, PROBE_PATH)
        if label != REF_CUSTOM:
            opts.meshRefinement = ref_enum(label)
        return {'surface_mm': opts.surfaceDeviation,
                'normal_deg': opts.normalDeviation,
                'max_edge_mm': 0.0,
                'aspect': opts.aspectRatio}
    return None


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

WRITTEN = ('written', 'written, was hidden', 'overwritten', 'overwritten, was hidden',
           'renamed to avoid a clash', 'renamed to avoid a clash, was hidden')


def human_bytes(n):
    if n is None:
        return '-'
    if n < 1024:
        return '%d B' % n
    for unit in ('kB', 'MB', 'GB'):
        n /= 1024.0
        if n < 1024 or unit == 'GB':
            return '%.1f %s' % (n, unit)


def thousands(n):
    return '{:,}'.format(int(n)).replace(',', ' ')


def settings_lines(o):
    lines = ['Folder           : %s' % o['folder'],
             'Source           : %s' % o['source'],
             'File name        : %s' % o['template'],
             'Format           : STL %s, %s' % (o['format'].lower(),
                                                o['units'].lower()),
             'Refinement       : %s' % o['refinement']]
    if o['refinement'] == REF_CUSTOM:
        lines.append('  surface deviation : %.4f mm  (same for every file)'
                     % o['surface_mm'])
        lines.append('  normal deviation  : %.2f deg' % o['normal_deg'])
        lines.append('  maximum edge      : %s'
                     % ('%.4f mm' % o['max_edge_mm'] if o['max_edge_mm'] > 0
                        else 'no limit'))
        lines.append('  aspect ratio      : %s'
                     % ('%.2f' % o['aspect'] if o['aspect'] > 0 else 'no limit'))
    else:
        lines.append('                     Fusion scales the deviation to each '
                     'body, so it differs per file')
    lines.append('If a file exists : %s' % o['existing'])
    return lines


def format_report(rows, o, secs):
    ok = [r for r in rows if r['status'] in WRITTEN]
    bad = [r for r in rows if r['status'].startswith('FAILED')]
    other = [r for r in rows if r not in ok and r not in bad]

    w = max([len(r['name']) for r in rows] + [12]) if rows else 12
    lines = ['Batch STL export - %d of %d written in %.1f s'
             % (len(ok), len(rows), secs), '']
    lines.extend(settings_lines(o))
    lines.append('')
    lines.append('%-*s  %11s  %9s  %s' % (w, 'file', 'triangles', 'size', 'source'))
    lines.append('%-*s  %11s  %9s  %s' % (w, '-' * w, '-' * 11, '-' * 9, '-' * 20))
    total_tri = 0
    for r in rows:
        tri = r.get('triangles')
        if tri:
            total_tri += tri
        note = '' if r['status'] == 'written' else '   [%s]' % r['status']
        lines.append('%-*s  %11s  %9s  %s%s'
                     % (w, r['name'] or '-', thousands(tri) if tri else '-',
                        human_bytes(r.get('bytes')), r['target'], note))
    lines.append('')
    lines.append('Total: %s triangles in %d files' % (thousands(total_tri), len(ok)))
    if other:
        lines.append('Not written: %d' % len(other))
    if bad:
        lines.append('Failed: %d - see %s' % (len(bad), LOG_PATH))
    return '\n'.join(lines)


def write_manifest(folder, text):
    path = os.path.join(folder, time.strftime('stl_export_%Y%m%d_%H%M%S.txt'))
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')
    return path


def write_report(text):
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, time.strftime('export_%Y%m%d_%H%M%S.txt'))
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return path
    except Exception:
        return LOG_PATH


# --------------------------------------------------------------------------- #
# dialog
# --------------------------------------------------------------------------- #

INFO_CSS = ('<style>table.m{border-collapse:collapse;font-size:11px}'
            'table.m td{padding:1px 10px 1px 0;vertical-align:top}'
            'table.m td.k{color:#666}</style>')


def rows_html(pairs):
    body = ''.join('<tr><td class="k">%s</td><td>%s</td></tr>' % p for p in pairs)
    return INFO_CSS + '<table class="m">' + body + '</table>'


def build_inputs(inputs, s):
    src = inputs.addDropDownCommandInput(
        'source', 'Export', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label in SOURCES:
        src.listItems.add(label, label == s['source'])

    sel = inputs.addSelectionInput('sel', 'Objects',
                                   'Select bodies or components to export')
    for f in ('SolidBodies', 'MeshBodies', 'Occurrences'):
        sel.addSelectionFilter(f)
    sel.setSelectionLimits(0, 0)

    info = inputs.addTextBoxCommandInput('info', 'Plan', '', 6, True)
    info.isFullWidth = True

    go = inputs.addGroupCommandInput('grpOut', 'Output')
    go.isExpanded = True
    g = go.children
    g.addStringValueInput('folder', 'Folder', s['folder'])
    g.addBoolValueInput('btnFolder', 'Choose folder', False, '', False)
    tpl = g.addStringValueInput('template', 'File name', s['template'])
    tpl.tooltip = 'Tokens: ' + ' '.join(TOKENS)
    dd = g.addDropDownCommandInput(
        'existing', 'If a file exists', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label in EXISTING:
        dd.listItems.add(label, label == s['existing'])
    df = g.addDropDownCommandInput(
        'format', 'Format', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label in FORMATS:
        df.listItems.add(label, label == s['format'])
    du = g.addDropDownCommandInput(
        'units', 'Units', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label in UNITS:
        du.listItems.add(label, label == s['units'])

    gr = inputs.addGroupCommandInput('grpRef', 'Refinement settings')
    gr.isExpanded = True
    r = gr.children
    dr = r.addDropDownCommandInput(
        'refinement', 'Refinement', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label in REFINEMENTS:
        dr.listItems.add(label, label == s['refinement'])
    # all four are always visible: the deviation is the number worth tuning and
    # hiding it behind a preset is exactly what makes the 3D Print dialog awkward
    r.addValueInput('surfaceDeviation', 'Surface deviation', 'mm',
                    adsk.core.ValueInput.createByReal(s['surfaceDeviation'] * MM))
    r.addValueInput('normalDeviation', 'Normal deviation', 'deg',
                    adsk.core.ValueInput.createByReal(math.radians(s['normalDeviation'])))
    me = r.addValueInput('maxEdge', 'Maximum edge length', 'mm',
                         adsk.core.ValueInput.createByReal(s['maxEdge'] * MM))
    me.tooltip = ('0 leaves it to Fusion, which is no limit in practice - the '
                  'presets set it to the bounding-box diagonal, which no triangle '
                  'can exceed.')
    ar = r.addFloatSpinnerCommandInput('aspectRatio', 'Aspect ratio', '', 0.0, 100.0,
                                       0.5, float(s['aspectRatio']))
    ar.tooltip = '0 leaves it to Fusion. The presets all use 21.5.'
    rst = r.addBoolValueInput('btnReset', 'Reset all settings', False, '', False)
    rst.tooltip = ('Puts every setting on this panel back to its default. '
                   'The output folder is kept.')

    ga = inputs.addGroupCommandInput('grpAfter', 'After export')
    ga.isExpanded = True
    a = ga.children
    a.addBoolValueInput('manifest', 'Write a manifest next to the files', True, '',
                        s['manifest'])
    a.addBoolValueInput('openFolder', 'Open the folder when finished', True, '',
                        s['openFolder'])
    a.addBoolValueInput('showReport', 'Show the report when finished', True, '',
                        s['showReport'])


def resolve_options(inputs):
    return {
        'source': chosen(inputs, 'source', SRC_SELECTED),
        'folder': inputs.itemById('folder').value.strip().strip('"'),
        'template': inputs.itemById('template').value.strip() or '{body}',
        'existing': chosen(inputs, 'existing', EXISTING[0]),
        'format': chosen(inputs, 'format', 'Binary'),
        'units': chosen(inputs, 'units', 'Millimeter'),
        'refinement': chosen(inputs, 'refinement', REF_CUSTOM),
        'surface_mm': inputs.itemById('surfaceDeviation').value * CM,
        'normal_deg': math.degrees(inputs.itemById('normalDeviation').value),
        'max_edge_mm': inputs.itemById('maxEdge').value * CM,
        'aspect': inputs.itemById('aspectRatio').value,
        'manifest': inputs.itemById('manifest').value,
        'open_folder': inputs.itemById('openFolder').value,
        'show_report': inputs.itemById('showReport').value,
    }


def settings_from(inputs):
    o = resolve_options(inputs)
    # the mm -> cm -> mm and deg -> rad -> deg round trips leave float dust
    # (15 deg comes back as 14.999999999999998); rounding keeps the saved file
    # readable and stops it drifting further every time it is reopened
    return {
        'source': o['source'], 'folder': o['folder'], 'template': o['template'],
        'existing': o['existing'], 'format': o['format'], 'units': o['units'],
        'refinement': o['refinement'],
        'surfaceDeviation': round(o['surface_mm'], 8),
        'normalDeviation': round(o['normal_deg'], 6),
        'maxEdge': round(o['max_edge_mm'], 8),
        'aspectRatio': round(o['aspect'], 6),
        'manifest': o['manifest'], 'openFolder': o['open_folder'],
        'showReport': o['show_report'],
    }


def refresh_visibility(inputs):
    inputs.itemById('sel').isVisible = (chosen(inputs, 'source', SRC_SELECTED)
                                        == SRC_SELECTED)
    # nothing in the refinement group is ever hidden any more; a preset simply
    # shows what it resolves to for the first object


def plan(app, inputs):
    """What OK would do, said out loud, so nothing is a surprise."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        return rows_html([('State', 'no design is open')])
    o = resolve_options(inputs)
    targets = gather(design, o['source'], inputs.itemById('sel'))
    doc = safe_name(app.activeDocument.name, 'design')
    stamp = time.strftime('%Y%m%d_%H%M%S')

    pairs = [('Files', '%d' % len(targets))]
    if targets:
        preview = [render_name(o['template'], t, i + 1, doc, stamp)
                   for i, t in enumerate(targets[:3])]
        pairs.append(('First', '<br>'.join(preview)
                      + ('<br>...' if len(targets) > 3 else '')))
    if not o['folder']:
        pairs.append(('Folder', '<b>not set</b> - choose one'))
    elif not os.path.isdir(o['folder']):
        pairs.append(('Folder', '%s (will be created)' % o['folder']))
    if not targets:
        pairs.append(('Nothing to do',
                      'select something' if o['source'] == SRC_SELECTED
                      else 'this design has no bodies'))
    pairs.append(('Refinement',
                  ('%.4f mm deviation, the same for every file' % o['surface_mm'])
                  if o['refinement'] == REF_CUSTOM
                  else '%s - Fusion scales the deviation to each body' % o['refinement']))
    return rows_html(pairs)


def do_browse(app, inputs):
    dlg = app.userInterface.createFolderDialog()
    dlg.title = 'Where should the STL files go'
    current = inputs.itemById('folder').value.strip()
    if current and os.path.isdir(current):
        dlg.initialDirectory = current
    if dlg.showDialog() == adsk.core.DialogResults.DialogOK:
        inputs.itemById('folder').value = dlg.folder


FIELDS = ('surfaceDeviation', 'normalDeviation', 'maxEdge', 'aspectRatio')


def _write_fields(inputs, d):
    inputs.itemById('surfaceDeviation').value = d['surface_mm'] * MM
    inputs.itemById('normalDeviation').value = math.radians(d['normal_deg'])
    inputs.itemById('maxEdge').value = d['max_edge_mm'] * MM
    inputs.itemById('aspectRatio').value = d['aspect']


def _first_geom(app, inputs):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        return None, None
    targets = gather(design, chosen(inputs, 'source', SRC_SELECTED),
                     inputs.itemById('sel'))
    return (design, targets[0]) if targets else (design, None)


def fill_from_refinement(app, inputs):
    """Show what the chosen refinement resolves to for the first object. Custom has
    nothing of its own to resolve, so it borrows Medium as a starting point."""
    design, first = _first_geom(app, inputs)
    if first is None:
        return None
    label = chosen(inputs, 'refinement', REF_CUSTOM)
    d = preset_fields(design.exportManager, first['geom'],
                      'Medium' if label == REF_CUSTOM else label)
    if d is None:
        return None
    _write_fields(inputs, d)
    return (first, label, d)


def do_reset(app, inputs):
    """Everything back to the defaults, except the output folder - re-picking that
    is the one thing a reset should not cost you."""
    d = dict(DEFAULTS)
    d['folder'] = inputs.itemById('folder').value
    for cid in ('source', 'existing', 'format', 'units', 'refinement'):
        dd = inputs.itemById(cid)
        for i in range(dd.listItems.count):
            item = dd.listItems.item(i)
            item.isSelected = item.name == d[cid]
    inputs.itemById('folder').value = d['folder']
    inputs.itemById('template').value = d['template']
    _write_fields(inputs, {'surface_mm': d['surfaceDeviation'],
                           'normal_deg': d['normalDeviation'],
                           'max_edge_mm': d['maxEdge'],
                           'aspect': d['aspectRatio']})
    for cid in ('manifest', 'openFolder', 'showReport'):
        inputs.itemById(cid).value = d[cid]


FIELD_TOL = {'surfaceDeviation': 1e-9, 'normalDeviation': 1e-6,
             'maxEdge': 1e-9, 'aspectRatio': 1e-9}
FIELD_KEY = {'surfaceDeviation': 'surface_mm', 'normalDeviation': 'normal_deg',
             'maxEdge': 'max_edge_mm', 'aspectRatio': 'aspect'}


def field_matches_preset(app, inputs, cid):
    """Does this one field still hold what the chosen preset gives?

    ONE field, not all four. Writing the four fields when a preset is picked makes
    Fusion fire inputChanged for each write in turn, so a check across all of them
    sees a half-written set, calls it an edit, and flips the dropdown to Custom
    before the fill has finished. Checking only the field that changed is right
    during the fill and right when the user really types."""
    design, first = _first_geom(app, inputs)
    label = chosen(inputs, 'refinement', REF_CUSTOM)
    if first is None or label == REF_CUSTOM:
        return False
    d = preset_fields(design.exportManager, first['geom'], label)
    if d is None:
        return False
    return abs(resolve_options(inputs)[FIELD_KEY[cid]]
               - d[FIELD_KEY[cid]]) < FIELD_TOL[cid]


def select_custom(inputs):
    dd = inputs.itemById('refinement')
    for i in range(dd.listItems.count):
        if dd.listItems.item(i).name == REF_CUSTOM:
            dd.listItems.item(i).isSelected = True
            return


def on_created(app, cmd):
    cmd.isExecutedWhenPreEmpted = False
    cmd.okButtonText = 'Export'
    cmd.setDialogInitialSize(470, 700)
    s = load_settings()
    build_inputs(cmd.commandInputs, s)
    refresh_visibility(cmd.commandInputs)
    cmd.commandInputs.itemById('info').formattedText = plan(app, cmd.commandInputs)
    _STATE.live_export_cmd = cmd      # dev bridge only


def on_input_changed(app, inputs, changed_id):
    if changed_id == 'btnFolder':
        do_browse(app, inputs)
    elif changed_id == 'btnReset':
        do_reset(app, inputs)
    elif changed_id == 'refinement':
        # picking a preset shows what it means, the way the 3D Print dialog does
        fill_from_refinement(app, inputs)
    elif changed_id in FIELDS and not field_matches_preset(app, inputs, changed_id):
        # a number the preset did not produce IS a custom refinement; say so rather
        # than showing High while exporting something else
        select_custom(inputs)
    refresh_visibility(inputs)
    inputs.itemById('info').formattedText = plan(app, inputs)


def on_validate(inputs):
    try:
        if not inputs.itemById('folder').value.strip().strip('"'):
            return False
        if chosen(inputs, 'source', SRC_SELECTED) == SRC_SELECTED:
            return inputs.itemById('sel').selectionCount > 0
        return True
    except Exception:
        return False


def on_execute(app, ui, inputs):
    o = resolve_options(inputs)
    save_settings(settings_from(inputs))
    design = adsk.fusion.Design.cast(app.activeProduct)
    targets = gather(design, o['source'], inputs.itemById('sel'))
    if not targets:
        ui.messageBox('Nothing to export.', CMD_NAME)
        return

    progress = ui.createProgressDialog()
    progress.isCancelButtonShown = True
    progress.show(CMD_NAME, 'Starting', 0, len(targets), 0)
    t0 = time.time()
    try:
        rows = run_export(app, o, targets, progress)
    finally:
        progress.hide()
    text = format_report(rows, o, time.time() - t0)

    manifest = ''
    if o['manifest']:
        try:
            manifest = write_manifest(o['folder'], text)
        except Exception:
            log(traceback.format_exc())
    path = write_report(text)
    log(text)
    if o['open_folder']:
        try:
            os.startfile(o['folder'])
        except Exception:
            log(traceback.format_exc())
    if o['show_report']:
        extra = '\n\nManifest: ' + manifest if manifest else ''
        ui.messageBox(text + extra + '\n\nSaved to ' + path, CMD_NAME + ' - report')


# This module is re-imported for every event, so nothing may live in a module
# global between them. _STATE is the one exception: a module object parked in
# sys.modules, which reload does not touch. It holds only the handler objects
# Fusion needs kept alive, and the live command for the dev bridge.
_STATE = sys.modules.setdefault('_bse_state', types.ModuleType('_bse_state'))


# --------------------------------------------------------------------------- #
# toolbar
# --------------------------------------------------------------------------- #

PANEL_GROUPS = [
    ['ParaMeshExportPanel', 'ParaMeshModifyPanel'],
    ['MakePanel', 'SolidMakePanel', 'UtilityMakePanel', 'SolidCreatePanel'],
]


def place_button(ui, cmd_def):
    """Put the button in the first panel of each group that exists. Returns the ids."""
    placed = []
    for group in PANEL_GROUPS:
        for pid in group:
            panel = ui.allToolbarPanels.itemById(pid)
            if not panel:
                continue
            existing = panel.controls.itemById(cmd_def.id)
            if existing:
                existing.deleteMe()
            ctrl = panel.controls.addCommand(cmd_def)
            ctrl.isPromoted = True
            ctrl.isPromotedByDefault = True
            placed.append(pid)
            break
    return placed


# --------------------------------------------------------------------------- #
# handlers and installation
#
# These live here rather than in the shell so the whole command hot-reloads. The
# handler INSTANCES survive across reloads - Fusion holds them - but every notify
# re-imports this module first, so the body of the work is always the current file.
# --------------------------------------------------------------------------- #

def _fresh():
    if ADDIN_DIR not in sys.path:
        sys.path.insert(0, ADDIN_DIR)
    import importlib
    return importlib.reload(sys.modules['bse_core'])


def _oops(where):
    tb = traceback.format_exc()
    log('%s\n%s' % (where, tb))
    try:
        adsk.core.Application.get().userInterface.messageBox(
            '%s\n\n%s\n\nFull traceback in\n%s' % (where, short_error(tb), LOG_PATH),
            CMD_NAME)
    except Exception:
        pass


class _Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            m = _fresh()
            app = adsk.core.Application.get()
            cmd = args.command
            m.on_created(app, cmd)
            for h, ev in ((_Execute(), cmd.execute),
                          (_Changed(), cmd.inputChanged),
                          (_Validate(), cmd.validateInputs)):
                ev.add(h)
                _STATE.export_handlers.append(h)
        except Exception:
            _oops('Batch STL Export could not open its dialog.')


class _Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            _fresh().on_execute(app, app.userInterface, args.command.commandInputs)
        except Exception:
            _oops('Batch STL Export failed.')


class _Changed(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            # args.inputs is the collection the changed input lives in, which for a
            # grouped input is the group's children - always go via the command
            _fresh().on_input_changed(adsk.core.Application.get(),
                                      args.input.parentCommand.commandInputs,
                                      args.input.id)
        except Exception:
            log(traceback.format_exc())


class _Validate(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        try:
            args.areInputsValid = _fresh().on_validate(args.inputs)
        except Exception:
            args.areInputsValid = False


def install(app, ui):
    """Create the command definition, wire it up, place the button. Idempotent."""
    if not hasattr(_STATE, 'export_handlers'):
        _STATE.export_handlers = []
    old = ui.commandDefinitions.itemById(CMD_ID)
    if old:
        old.deleteMe()
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_TOOLTIP, RES_DIR)
    h = _Created()
    cmd_def.commandCreated.add(h)
    _STATE.export_handlers.append(h)
    return place_button(ui, cmd_def)


# --------------------------------------------------------------------------- #
# dev bridge commands
# --------------------------------------------------------------------------- #

def dev_all_panels(app, job):
    ui = app.userInterface
    ids = [ui.allToolbarPanels.item(i).id for i in range(ui.allToolbarPanels.count)]
    needle = job.get('like', '')
    if needle:
        ids = [i for i in ids if needle.lower() in i.lower()]
    return {'count': len(ids), 'panels': sorted(ids)}


def dev_export_probe(app, job):
    """Confirm the enums and the option defaults exist before relying on them."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    out = {'refinement_enum': {}, 'unit_enum': {}, 'bodies': []}
    for label in REFINEMENTS:
        out['refinement_enum'][label] = ref_enum(label)
    for label in UNITS:
        out['unit_enum'][label] = unit_enum(label)
    if not des:
        return out
    exp = des.exportManager
    for b in list(des.rootComponent.bRepBodies)[:3]:
        opts = exp.createSTLExportOptions(b, PROBE_PATH)
        out['bodies'].append({
            'name': b.name, 'refinement': opts.meshRefinement,
            'surface_mm': opts.surfaceDeviation,
            'normal_deg': opts.normalDeviation,
            'max_edge_mm': opts.maximumEdgeLength,
            'aspect': opts.aspectRatio, 'unit': opts.unitType,
            'binary': opts.isBinaryFormat})
    for m in list(des.rootComponent.meshBodies)[:2]:
        try:
            opts = exp.createSTLExportOptions(m, PROBE_PATH)
            out['bodies'].append({'name': m.name, 'mesh_body_accepted': True,
                                  'surface_mm': opts.surfaceDeviation})
        except Exception:
            out['bodies'].append({'name': m.name, 'mesh_body_accepted': False,
                                  'error': short_error(traceback.format_exc())})
    return out


def dev_export_raw(app, job):
    """Export one body with the option properties set to RAW values, bypassing every
    conversion in this module. Used to establish what unit each property is in."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    exp = des.exportManager
    body = None
    for b in des.rootComponent.bRepBodies:
        if b.name == job['body']:
            body = b
    if body is None:
        return {'error': 'no body named %r' % job['body']}
    opts = exp.createSTLExportOptions(body, job['path'])
    opts.isBinaryFormat = True
    opts.sendToPrintUtility = False
    for prop in ('surfaceDeviation', 'normalDeviation', 'maximumEdgeLength',
                 'aspectRatio'):
        if prop in job:
            setattr(opts, prop, job[prop])
    ok = exp.execute(opts)
    return {'ok': ok, 'path': job['path'],
            'set': {p: getattr(opts, p) for p in
                    ('surfaceDeviation', 'normalDeviation', 'maximumEdgeLength',
                     'aspectRatio')},
            'triangles': stl_triangles(job['path'])}


def dev_export_diag(app, job):
    """Why does a body refuse to export? Report its visibility flags and try the
    export three ways: as it stands, with the light bulb forced, and with a
    doEvents in between."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    exp = des.exportManager
    out = []
    for b in des.rootComponent.bRepBodies:
        row = {'name': b.name}
        for attr in ('isVisible', 'isLightBulbOn', 'isSolid'):
            try:
                row[attr] = getattr(b, attr)
            except Exception:
                row[attr] = 'n/a'
        for tag, force, events in (('as_is', False, False),
                                   ('forced', True, False),
                                   ('forced_doevents', True, True)):
            path = os.path.join(job['folder'], '%s_%s.stl' % (tag, safe_name(b.name)))
            try:
                os.remove(path)
            except Exception:
                pass
            switched = _force_visible(b) if force else []
            if events:
                adsk.doEvents()
            try:
                opts = exp.createSTLExportOptions(b, path)
                opts.isBinaryFormat = True
                ok = exp.execute(opts)
                row[tag] = 'file' if os.path.exists(path) else 'execute=%s, NO FILE' % ok
            except Exception:
                row[tag] = short_error(traceback.format_exc())
            finally:
                _restore(switched)
        out.append(row)
    return {'bodies': out}


def dev_preset_values(app, job):
    """What each refinement preset actually sets, per body. This is what the dialog
    fills the four fields with when a preset is chosen."""
    des = adsk.fusion.Design.cast(app.activeProduct)
    exp = des.exportManager
    out = []
    for b in list(des.rootComponent.bRepBodies)[:4]:
        bb = b.boundingBox
        diag = math.sqrt(sum((bb.maxPoint.asArray()[i] - bb.minPoint.asArray()[i]) ** 2
                             for i in range(3))) * CM
        row = {'body': b.name, 'bbox_diagonal_mm': round(diag, 4)}
        for label in ('High', 'Medium', 'Low'):
            opts = exp.createSTLExportOptions(b, PROBE_PATH)
            opts.meshRefinement = ref_enum(label)
            row[label] = {'surface': opts.surfaceDeviation,
                          'normal': opts.normalDeviation,
                          'maxEdge': opts.maximumEdgeLength,
                          'aspect': opts.aspectRatio}
        out.append(row)
    return {'presets': out}


def dev_export_targets(app, job):
    des = adsk.fusion.Design.cast(app.activeProduct)
    targets = gather(des, job.get('source', SRC_ALL), None)
    doc = safe_name(app.activeDocument.name, 'design')
    stamp = time.strftime('%Y%m%d_%H%M%S')
    tpl = job.get('template', '{body}')
    return {'count': len(targets),
            'targets': [{'body': t['body'], 'comp': t['comp'], 'occ': t['occ'],
                         'kind': t['kind'],
                         'file': render_name(tpl, t, i + 1, doc, stamp)}
                        for i, t in enumerate(targets)]}


def _job_options(job):
    s = load_settings()
    s.update(job.get('settings', {}))
    return {'source': job.get('source', s['source']), 'folder': job.get('folder', ''),
            'template': s['template'], 'existing': s['existing'],
            'format': s['format'], 'units': s['units'],
            'refinement': s['refinement'], 'surface_mm': s['surfaceDeviation'],
            'normal_deg': s['normalDeviation'], 'max_edge_mm': s['maxEdge'],
            'aspect': s['aspectRatio'], 'manifest': s['manifest'],
            'open_folder': False, 'show_report': False}


def dev_export_run(app, job):
    """Headless equivalent of pressing OK, for testing."""
    o = _job_options(job)
    des = adsk.fusion.Design.cast(app.activeProduct)
    targets = gather(des, o['source'], None)
    t0 = time.time()
    rows = run_export(app, o, targets)
    text = format_report(rows, o, time.time() - t0)
    if o['manifest']:
        write_manifest(o['folder'], text)
    return {'rows': rows, 'text': text}


def _box(comp, x0_mm, y0_mm, w_mm, h_mm, name):
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(x0_mm * MM, y0_mm * MM, 0),
        adsk.core.Point3D.create((x0_mm + w_mm) * MM, (y0_mm + w_mm) * MM, 0))
    ext = comp.features.extrudeFeatures.addSimple(
        sk.profiles.item(0), adsk.core.ValueInput.createByReal(h_mm * MM),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    b = ext.bodies.item(0)
    b.name = name
    return b


def _cylinder(comp, x0_mm, y0_mm, r_mm, h_mm, name):
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.sketchCurves.sketchCircles.addByCenterRadius(
        adsk.core.Point3D.create(x0_mm * MM, y0_mm * MM, 0), r_mm * MM)
    ext = comp.features.extrudeFeatures.addSimple(
        sk.profiles.item(0), adsk.core.ValueInput.createByReal(h_mm * MM),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    b = ext.bodies.item(0)
    b.name = name
    return b


def _sphere(comp, r_mm, name):
    """A curved body is the only honest test of surface deviation."""
    sk = comp.sketches.add(comp.xZConstructionPlane)
    r = r_mm * MM
    arc = sk.sketchCurves.sketchArcs.addByCenterStartSweep(
        adsk.core.Point3D.create(0, 0, 0),
        adsk.core.Point3D.create(0, -r, 0), math.pi)
    sk.sketchCurves.sketchLines.addByTwoPoints(arc.startSketchPoint,
                                               arc.endSketchPoint)
    rin = comp.features.revolveFeatures.createInput(
        sk.profiles.item(0), comp.zConstructionAxis,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    rin.setAngleExtent(False, adsk.core.ValueInput.createByReal(2.0 * math.pi))
    b = comp.features.revolveFeatures.add(rin).bodies.item(0)
    b.name = name
    return b


def dev_export_make_test(app, job):
    """A scratch document with the awkward cases: bodies of very different size, a
    body inside a component, and a hidden body."""
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    made = []
    made.append(_sphere(root, 15.0, 'Ball 30mm').name)
    made.append(_box(root, 40, 0, 20, 10, 'Small block').name)
    made.append(_cylinder(root, 0, 60, 40, 80, 'Big pin').name)

    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = 'Bracket'
    made.append(_box(occ.component, -80, 0, 25, 12, 'Bracket plate').name)

    hidden = _box(root, 90, 0, 15, 15, 'Hidden block')
    hidden.isLightBulbOn = False
    made.append(hidden.name + ' (hidden)')
    return {'document': doc.name, 'bodies': made,
            'root_breps': root.bRepBodies.count}


def dev_export_install(app, job):
    """Register the command without restarting the add-in. The shell does this at
    startup; this is how it gets tested before the shell has been reloaded."""
    return {'placed': install(app, app.userInterface)}


def dev_export_dialog(app, job):
    """Open the real dialog, so the UI path itself gets exercised."""
    cd = app.userInterface.commandDefinitions.itemById(CMD_ID)
    if not cd:
        return {'error': 'not installed - run export_install first'}
    cd.execute()
    return {'opened': CMD_ID}


def dev_export_read_dialog(app, job):
    """Dump the live dialog. Reading it TERMINATES the command, so the order is
    always open -> set -> press, never open -> read -> press."""
    cmd = getattr(_STATE, 'live_export_cmd', None)
    if cmd is None:
        return {'error': 'no dialog has been opened'}
    out, seen = [], set()
    def walk(coll, depth):
        # commandInputs lists a group's children at the top level as well as under
        # the group, so without this the dump shows everything twice
        for i in range(coll.count):
            ci = coll.item(i)
            if ci.id in seen:
                continue
            seen.add(ci.id)
            row = {'id': ci.id, 'type': type(ci).__name__, 'visible': ci.isVisible,
                   'depth': depth}
            for attr in ('value', 'expression', 'selectionCount'):
                try:
                    row[attr] = getattr(ci, attr)
                except Exception:
                    pass
            try:
                row['selected'] = ci.selectedItem.name
                row['items'] = [ci.listItems.item(k).name
                                for k in range(ci.listItems.count)]
            except Exception:
                pass
            out.append(row)
            try:
                walk(ci.children, depth + 1)
            except Exception:
                pass
    walk(cmd.commandInputs, 0)
    return {'inputs': out}


def dev_export_set_inputs(app, job):
    """Write values into the live dialog and fire the changed handler, exactly as
    a click would. Strings with units go through .expression, not .value."""
    cmd = getattr(_STATE, 'live_export_cmd', None)
    if cmd is None:
        return {'error': 'no dialog has been opened'}
    inputs = cmd.commandInputs
    done = {}
    for cid, val in job.get('values', {}).items():
        ci = inputs.itemById(cid)
        if ci is None:
            done[cid] = 'no such input'
            continue
        try:
            if hasattr(ci, 'listItems') and isinstance(val, str):
                hit = False
                for k in range(ci.listItems.count):
                    item = ci.listItems.item(k)
                    if item.name == val:
                        item.isSelected = True
                        hit = True
                done[cid] = 'selected' if hit else 'no item named %r' % val
            elif isinstance(val, str) and hasattr(ci, 'expression'):
                ci.expression = val          # "0.05 mm" - never a bare cm float
                done[cid] = ci.value
            else:
                ci.value = val
                done[cid] = ci.value
        except Exception:
            done[cid] = short_error(traceback.format_exc())
        try:
            on_input_changed(app, inputs, cid)
        except Exception:
            log(traceback.format_exc())
    return {'set': done, 'valid': on_validate(inputs)}


def dev_export_press_ok(app, job):
    cmd = getattr(_STATE, 'live_export_cmd', None)
    if cmd is None:
        return {'error': 'no dialog has been opened'}
    cmd.doExecute(False)
    return {'executed': True}


DEV_COMMANDS = {
    'all_panels': dev_all_panels,
    'export_install': dev_export_install,
    'export_make_test': dev_export_make_test,
    'export_read_dialog': dev_export_read_dialog,
    'export_set_inputs': dev_export_set_inputs,
    'export_press_ok': dev_export_press_ok,
    'export_probe': dev_export_probe,
    'export_diag': dev_export_diag,
    'preset_values': dev_preset_values,
    'export_targets': dev_export_targets,
    'export_raw': dev_export_raw,
    'export_run': dev_export_run,
    'export_dialog': dev_export_dialog,
}
