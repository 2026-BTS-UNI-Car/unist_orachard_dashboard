"""Build the dashboard interface font from Pretendard Variable (SIL OFL 1.1).

    python -B -I make_subset.py PretendardVariable.ttf out_dir

Keeps Basic Latin, Latin-1, the 2,350 Hangul syllables of KS X 1001, Hangul compatibility jamo and a small set of
punctuation and symbols; limits the weight axis to 400..700; renames the family (the licence reserves the original
font name for unmodified copies); writes one WOFF2 file and a JSON record of what went in.
"""
import hashlib
import json
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

FAMILY = 'UBTS Sans'
src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)

hangul = set()
for lead in range(0xB0, 0xC9):
    for trail in range(0xA1, 0xFF):
        try:
            hangul.add(ord(bytes([lead, trail]).decode('euc_kr')))
        except UnicodeDecodeError:
            pass
assert len(hangul) == 2350, len(hangul)
extra = '·…–—‘’“”•′″‰°±×÷≤≥≈≠→←↑↓↔⇒√∞µΩ℃㎜㎝㎞㎡㎏▲▼▶◀●○■□◆◇★☆✓✕※〈〉《》「」『』【】、。～ⓘ€£¥₩©®™§¶†‡⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉½¼¾⅓⅔'
codepoints = set(range(0x20, 0x7F)) | set(range(0xA0, 0x100)) | hangul | set(range(0x3131, 0x318F)) | {ord(c) for c in extra}

font = TTFont(str(src))
options = subset.Options()
options.layout_features = ['kern', 'mark', 'mkmk', 'ccmp', 'locl', 'calt', 'liga', 'clig', 'rlig', 'tnum', 'pnum', 'case', 'zero', 'frac', 'sups', 'subs']
options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14, 16, 17, 25]
options.name_languages = [0x409]
options.notdef_outline = True
options.glyph_names = False
options.hinting = False
options.desubroutinize = True
options.drop_tables += ['DSIG', 'meta']
subsetter = subset.Subsetter(options)
subsetter.populate(unicodes=sorted(codepoints))
subsetter.subset(font)
font = instancer.instantiateVariableFont(font, {'wght': (400, 700)})   # after subsetting: fewer glyphs to process
kept = sorted(font.getBestCmap())
missing = sorted(codepoints - set(kept))

name = font['name']
version = name.getDebugName(5)
for record in list(name.names):
    if record.nameID in (1, 16):
        record.string = FAMILY
    elif record.nameID == 4:
        record.string = FAMILY
    elif record.nameID == 6:
        record.string = FAMILY.replace(' ', '')
    elif record.nameID == 25:
        record.string = FAMILY.replace(' ', '')
    elif record.nameID == 3:
        record.string = FAMILY + ' subset of Pretendard Variable ' + (version or '')
    elif record.nameID in (2, 17):
        record.string = 'Regular'
font.flavor = 'woff2'
target = out_dir / 'ubts-sans-var.woff2'
font.save(str(target))
data = target.read_bytes()
check = TTFont(str(target))
record = {
    'file': target.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'family': FAMILY,
    'source': 'Pretendard Variable ' + (version or ''), 'source_sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
    'source_url': 'https://github.com/orioncactus/pretendard (v1.3.9, packages/pretendard/dist/public/variable/PretendardVariable.ttf)',
    'licence': 'SIL Open Font License 1.1 (PRETENDARD-OFL.txt). The original font name is reserved, so this modified copy is renamed.',
    'weight_axis': [(a.minValue, a.defaultValue, a.maxValue) for a in check['fvar'].axes if a.axisTag == 'wght'][0],
    'codepoints_kept': len(kept), 'hangul_syllables': sum(1 for c in kept if 0xAC00 <= c <= 0xD7A3),
    'requested_but_not_in_source': ''.join(chr(c) for c in missing),
    'layout_features': sorted({r.FeatureTag for r in check['GSUB'].table.FeatureList.FeatureRecord}) if 'GSUB' in check else [],
    'tool': 'fontTools %s (subset, then instancer limit wght 400..700)' % __import__('fontTools').version,
}
(out_dir / 'ubts-sans-var.json').write_bytes((json.dumps(record, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
print(json.dumps(record, ensure_ascii=True, indent=1))
