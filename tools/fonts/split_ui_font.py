"""Split the interface font into a small first file and the rest, so a first visit downloads only what the screens use.

    python -B -I tools/fonts/split_ui_font.py            (from the repository root)
    python -B -I tools/fonts/split_ui_font.py --check    (exit 1 if the files or the CSS are out of date)

Input: static/vendor/ubts-sans/ubts-sans-var.woff2 (made by make_ui_font.py, kept as the source of the split).
Output, next to it:
- ubts-sans-ui.woff2: printable ASCII, and every other character that appears in the page, its scripts and styles
  (static/, without vendor/) or in the server's Python files (messages shown on screen): the Hangul syllables and the
  few symbols the screens use (degree, plus-minus, arrows, dashes ...);
- ubts-sans-ko.woff2: the other Hangul syllables of the font;
- ubts-sans-split.json: what went where, sizes and hashes.
It also rewrites the two @font-face rules between the markers in static/css/tokens.css: the second file covers the
whole Hangul syllable block and is written first, the small file lists its characters and is written last (where
ranges overlap the last rule is tried first). A browser downloads the second file only when the screen shows a
syllable that is not in the first one (for example a recording name or a document page). Both files are the same variable font (weights 400..700) and the
same family name, so the text looks the same as with the single file.
The other characters of the font (Hangul jamo, accented Latin letters, symbols no screen text uses) are in neither
file and show in the device font when a value from outside (a recording name, an SSID) has one: the first file has
to stay under 100 kB (tests/test_ui_design_system.js, docs/slow_link.md target 나-3) while the screens gain text.
Run it again whenever screen text changes; tests/test_ui_design_system.js fails while a syllable of the sources is
missing from the first file (which would make every first visit download the second file).
"""
import hashlib
import json
import re
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = ROOT / 'static' / 'vendor' / 'ubts-sans'
SOURCE = FONT_DIR / 'ubts-sans-var.woff2'
CSS = ROOT / 'static' / 'css' / 'tokens.css'
BEGIN, END = '/* ubts-sans faces: made by tools/fonts/split_ui_font.py */', '/* end ubts-sans faces */'
HANGUL = range(0xAC00, 0xD7A4)


ASCII = range(0x20, 0x7F)


def used_characters():
    """Every character above ASCII in the screen sources (Hangul syllables and symbols)."""
    files = [p for p in (ROOT / 'static').rglob('*') if p.suffix in ('.html', '.js', '.mjs', '.css') and 'vendor' not in p.parts]
    files += list((ROOT / 'dashboard_server').rglob('*.py'))
    found = set()
    for path in sorted(files):
        found |= {ord(c) for c in path.read_text(encoding='utf-8', errors='ignore') if ord(c) > 0x7E}
    return found



def ranges(codepoints):
    """unicode-range text for a set of code points, consecutive ones joined."""
    out, items = [], sorted(codepoints)
    start = prev = None
    for cp in items + [None]:
        if cp is not None and prev is not None and cp == prev + 1:
            prev = cp
            continue
        if start is not None:
            out.append('U+%X' % start if start == prev else 'U+%X-%X' % (start, prev))
        start = prev = cp
    return ', '.join(out)


def make(codepoints, target):
    font = TTFont(str(SOURCE))
    options = subset.Options()
    options.layout_features = ['*']
    options.name_IDs = ['*']
    options.name_languages = ['*']
    options.notdef_outline = True
    options.glyph_names = False
    options.hinting = False
    options.flavor = 'woff2'
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)
    font.flavor = 'woff2'
    font.save(str(target))
    return sorted(TTFont(str(target)).getBestCmap())


def faces(ui_range):
    """Two faces of one family. Where unicode-range values overlap, the face written last is tried first, so the
    small file (written last, explicit list) serves the characters it has and the second file (the whole Hangul
    syllable block, written first) is fetched only for a syllable the small file does not have."""
    rule = ('@font-face {{\n  font-family: "UBTS Sans";\n  src: url("/static/vendor/ubts-sans/{file}") format("woff2");\n'
            '  font-weight: 400 700;\n  font-style: normal;\n  font-display: swap;\n  unicode-range: {range};\n}}\n')
    return (BEGIN + '\n' + rule.format(file='ubts-sans-ko.woff2', range='U+AC00-D7A3')
            + rule.format(file='ubts-sans-ui.woff2', range=ui_range) + END)


def main(argv):
    check = '--check' in argv
    whole = set(TTFont(str(SOURCE)).getBestCmap())
    syllables = {cp for cp in whole if cp in HANGUL}
    characters = used_characters()
    used = characters & syllables
    ui_set = ((set(ASCII) | characters) & (whole - syllables)) | used
    ko_set = syllables - used
    css = CSS.read_text(encoding='utf-8')
    pattern = re.compile(re.escape(BEGIN) + '.*?' + re.escape(END), re.S)
    if not pattern.search(css):
        old = re.compile(r'@font-face \{\n  font-family: "UBTS Sans";.*?\n\}', re.S)
        if not old.search(css):
            raise SystemExit('tokens.css: no UBTS Sans @font-face rule found')
        css = old.sub(lambda m: BEGIN + '\n' + END, css, count=1)
    new_css = pattern.sub(lambda m: faces(ranges(ui_set)), css, count=1)
    if check:
        record = json.loads((FONT_DIR / 'ubts-sans-split.json').read_text(encoding='utf-8'))
        stale = (new_css != CSS.read_text(encoding='utf-8') or record['ui']['hangul_syllables'] != len(used)
                 or record['source_sha256'] != hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        print('font split is %s' % ('out of date: run tools/fonts/split_ui_font.py' if stale else 'current'))
        return 1 if stale else 0
    record = {'source': SOURCE.name, 'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'source_bytes': SOURCE.stat().st_size, 'tool': 'fontTools %s subset' % __import__('fontTools').version}
    for name, wanted in (('ui', ui_set), ('ko', ko_set)):
        target = FONT_DIR / f'ubts-sans-{name}.woff2'
        kept = make(wanted, target)
        data = target.read_bytes()
        record[name] = {'file': target.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                        'codepoints': len(kept), 'hangul_syllables': sum(1 for c in kept if c in HANGUL),
                        'missing': sorted(set(wanted) - set(kept))}
    CSS.write_bytes(new_css.encode('utf-8'))
    (FONT_DIR / 'ubts-sans-split.json').write_bytes((json.dumps(record, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
    print(json.dumps({k: (v if not isinstance(v, dict) else {x: v[x] for x in ('bytes', 'codepoints', 'hangul_syllables')})
                      for k, v in record.items()}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
