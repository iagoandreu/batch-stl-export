"""Offline tests for Batch STL Export.

    py -3.10 tests/test_export.py

adsk is stubbed (see tests/stub), so bse_core imports outside Fusion. Only the
parts that need no live document are exercised here - naming, collisions, settings
persistence, reading a triangle count back out of a file, and the report. The parts
that need Fusion are covered by the dev-bridge commands at the bottom of
bse_core.py.
"""

import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'stub'))
sys.path.insert(0, os.path.dirname(HERE))

import bse_core as X                                       # noqa: E402

FAILED = []


def check(name, cond, detail=''):
    print('  %s %s%s' % ('ok  ' if cond else 'FAIL', name,
                         '' if cond else '   %s' % (detail,)))
    if not cond:
        FAILED.append(name)


def target(body='Body1', comp='Comp', occ='Comp:1', kind='solid'):
    return {'geom': None, 'body': body, 'comp': comp, 'occ': occ, 'kind': kind}


# --------------------------------------------------------------------------- #
print('file names')
# --------------------------------------------------------------------------- #
check('illegal characters go',
      X.safe_name('a/b:c*d?e"f<g>h|i') == 'a_b_c_d_e_f_g_h_i',
      X.safe_name('a/b:c*d?e"f<g>h|i'))
check('a trailing dot would make an unopenable file',
      X.safe_name('part.') == 'part')
check('slashes become underscores rather than path separators',
      X.safe_name('///') == '___', X.safe_name('///'))
check('a name that sanitises to nothing falls back',
      X.safe_name('  .. ') == 'body', X.safe_name('  .. '))
check('long names are capped', len(X.safe_name('x' * 400)) == 120)

t = target('Left plate', 'Guide', 'Guide:2')
check('plain token',
      X.render_name('{body}', t, 1, 'CaseA', '20260903_101500') == 'Left plate.stl')
check('every token resolves',
      X.render_name('{doc}_{comp}_{occ}_{body}_{n}_{nn}_{nnn}_{kind}', t, 7,
                    'CaseA', '20260903_101500')
      == 'CaseA_Guide_Guide_2_Left plate_7_07_007_solid.stl')
check('date and time split correctly',
      X.render_name('{date}-{time}', t, 1, 'D', '20260903_101500')
      == '20260903-101500.stl')
check('an explicit .stl is not doubled',
      X.render_name('{body}.stl', t, 1, 'D', '20260903_101500') == 'Left plate.stl')
check('the occurrence colon is sanitised, not left in the path',
      ':' not in X.render_name('{occ}', t, 1, 'D', '20260903_101500'))


# --------------------------------------------------------------------------- #
print('collision policy')
# --------------------------------------------------------------------------- #
tmp = tempfile.mkdtemp(prefix='mtp_export_test_')
open(os.path.join(tmp, 'taken.stl'), 'w').close()

used = set()
p, note = X.resolve_path(tmp, 'fresh.stl', 'Skip', used)
check('a free name is used as is', p == os.path.join(tmp, 'fresh.stl') and note == '')

used = set()
p, note = X.resolve_path(tmp, 'taken.stl', 'Skip', used)
check('Skip refuses an existing file', p is None and 'skipped' in note)

used = set()
p, note = X.resolve_path(tmp, 'taken.stl', 'Overwrite', used)
check('Overwrite takes the existing path',
      p == os.path.join(tmp, 'taken.stl') and note == 'overwritten')

used = set()
p, note = X.resolve_path(tmp, 'taken.stl', 'Number the new one', used)
check('numbering steps past an existing file',
      p == os.path.join(tmp, 'taken_2.stl') and 'renamed' in note, p)

# the case that silently loses a file if it is got wrong: two bodies, one name
used = set()
a, _ = X.resolve_path(tmp, 'same.stl', 'Overwrite', used)
b, _ = X.resolve_path(tmp, 'same.stl', 'Overwrite', used)
check('two bodies named alike never collapse into one file', a != b, '%s %s' % (a, b))
c, _ = X.resolve_path(tmp, 'same.stl', 'Skip', set())
d, note = X.resolve_path(tmp, 'same.stl', 'Skip', {c.lower()})
check('Skip still separates a within-run clash', d is not None and d != c, note)


# --------------------------------------------------------------------------- #
print('reading the triangle count back out of a file')
# --------------------------------------------------------------------------- #
def write_binary(path, n):
    with open(path, 'wb') as fh:
        fh.write(b'\0' * 80)
        fh.write(struct.pack('<I', n))
        fh.write(b'\0' * (50 * n))


bpath = os.path.join(tmp, 'bin.stl')
write_binary(bpath, 137)
check('binary count from the header', X.stl_triangles(bpath) == 137,
      X.stl_triangles(bpath))

# an ASCII file whose first bytes happen to parse as a plausible header
apath = os.path.join(tmp, 'ascii.stl')
facet = ('facet normal 0 0 1\n outer loop\n  vertex 0 0 0\n  vertex 1 0 0\n'
         '  vertex 0 1 0\n endloop\nendfacet\n')
with open(apath, 'w') as fh:
    fh.write('solid part\n' + facet * 40 + 'endsolid part\n')
check('ascii count by scanning', X.stl_triangles(apath) == 40, X.stl_triangles(apath))

# the chunk boundary is the only interesting failure mode of the ascii scan
big = os.path.join(tmp, 'big.stl')
with open(big, 'w') as fh:
    fh.write('solid p\n' + facet * 20000 + 'endsolid p\n')
check('ascii count survives the 1 MB chunk boundary',
      X.stl_triangles(big) == 20000, X.stl_triangles(big))
check('a missing file gives None, not a crash',
      X.stl_triangles(os.path.join(tmp, 'nope.stl')) is None)


# --------------------------------------------------------------------------- #
print('settings')
# --------------------------------------------------------------------------- #
saved = X.SETTINGS_PATH
X.SETTINGS_PATH = os.path.join(tmp, 'last_export.json')
try:
    check('defaults load when there is no file',
          X.load_settings()['template'] == '{body}')
    X.save_settings(dict(X.DEFAULTS, template='{comp}_{body}',
                         surfaceDeviation=0.003, folder=tmp))
    s = X.load_settings()
    check('a saved template comes back', s['template'] == '{comp}_{body}')
    check('a saved deviation comes back exactly', s['surfaceDeviation'] == 0.003)
    check('a saved folder comes back', s['folder'] == tmp)
    X.save_settings(dict(X.DEFAULTS, refinement='Organic'))
    check('a label that no longer exists falls back instead of picking item 0',
          X.load_settings()['refinement'] == X.DEFAULTS['refinement'])
    # a file written before "0 means no limit" carried a stale number behind an
    # unticked box; applying it now would silently cap a whole batch
    X.save_settings({'limitEdge': False, 'maxEdge': 5.0,
                     'setAspect': False, 'aspectRatio': 21.5})
    old = X.load_settings()
    check('an old unticked edge limit migrates to 0, not to 5 mm',
          old['maxEdge'] == 0.0, old['maxEdge'])
    check('an old unticked aspect ratio migrates to 0', old['aspectRatio'] == 0.0)
    X.save_settings({'limitEdge': True, 'maxEdge': 2.5})
    check('an old ticked edge limit keeps its number',
          X.load_settings()['maxEdge'] == 2.5)
    check('the retired keys do not survive the migration',
          'limitEdge' not in X.load_settings())
finally:
    X.SETTINGS_PATH = saved


# --------------------------------------------------------------------------- #
print('the report')
# --------------------------------------------------------------------------- #
OPTS = {'folder': tmp, 'source': X.SRC_ALL, 'template': '{body}',
        'format': 'Binary', 'units': 'Millimeter', 'refinement': 'Custom',
        'surface_mm': 0.01, 'normal_deg': 15.0,
        'max_edge_mm': 0.0, 'aspect': 21.5,
        'existing': 'Overwrite'}
ROWS = [
    {'name': 'a.stl', 'target': 'A', 'kind': 'solid', 'status': 'written',
     'triangles': 7224, 'bytes': 361284},
    {'name': 'b.stl', 'target': 'B', 'kind': 'solid', 'status': 'written, was hidden',
     'triangles': 12, 'bytes': 684},
    {'name': 'c.stl', 'target': 'C', 'kind': 'solid', 'status': 'skipped, already there'},
    {'name': 'd.stl', 'target': 'D', 'kind': 'solid', 'status': 'FAILED: boom'},
]
rep = X.format_report(ROWS, OPTS, 1.5)
check('the headline counts only what was written', '2 of 4 written' in rep,
      rep.splitlines()[0])
check('the total adds up', 'Total: 7 236 triangles in 2 files' in rep)
check('a failure is called out', 'Failed: 1' in rep)
check('a skip is called out', 'Not written: 1' in rep)
check('the settings are in the report, so a manifest is self-describing',
      'surface deviation : 0.0100 mm' in rep and 'normal deviation  : 15.00 deg' in rep)
check('a zero edge limit reads as no limit, not as a stale number',
      'maximum edge      : no limit' in rep, [l for l in rep.splitlines()
                                              if 'maximum edge' in l])
check('Custom says the number is the same for every file',
      'same for every file' in rep)
preset_rep = X.format_report(ROWS, dict(OPTS, refinement='High'), 1.0)
check('a preset says it scales per body instead of printing four numbers',
      'scales the deviation to each body' in preset_rep
      and 'surface deviation' not in preset_rep)
check('a hidden body is disclosed', 'was hidden' in rep)
check('no unicode in the report - the console is cp1252',
      all(ord(ch) < 128 for ch in rep))

check('bytes read as humans do', X.human_bytes(684) == '684 B'
      and X.human_bytes(361284) == '352.8 kB', X.human_bytes(361284))
check('thousands are spaced, not comma-ed', X.thousands(7224) == '7 224')


# --------------------------------------------------------------------------- #
print('defaults, which the reset button restores')
# --------------------------------------------------------------------------- #
check('every default is a label the dropdown actually offers',
      X.DEFAULTS['source'] in X.SOURCES
      and X.DEFAULTS['refinement'] in X.REFINEMENTS
      and X.DEFAULTS['existing'] in X.EXISTING
      and X.DEFAULTS['format'] in X.FORMATS
      and X.DEFAULTS['units'] in X.UNITS)
check('the two optional limits default to off, not to a stale number',
      X.DEFAULTS['maxEdge'] == 0.0 and X.DEFAULTS['aspectRatio'] == 21.5)
check('the default template resolves to a usable name',
      X.render_name(X.DEFAULTS['template'], target(), 1, 'D', '20260903_101500')
      == 'Body1.stl')
check('reset covers every key the dialog writes back',
      set(X.DEFAULTS) == {'source', 'folder', 'template', 'existing', 'format',
                          'units', 'refinement', 'surfaceDeviation',
                          'normalDeviation', 'maxEdge', 'aspectRatio',
                          'openFolder', 'manifest', 'showReport'},
      sorted(X.DEFAULTS))


print()
if FAILED:
    print('%d FAILED: %s' % (len(FAILED), ', '.join(FAILED)))
    sys.exit(1)
print('all export tests passed')
