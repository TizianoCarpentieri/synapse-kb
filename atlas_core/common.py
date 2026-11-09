from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from . import __version__ as VERSION
SCHEMA = '1'
PROFILES = {
    'compatto': {'words': 160, 'fanout': 8},
    'bilanciato': {'words': 320, 'fanout': 12},
    'ampio': {'words': 640, 'fanout': 16},
}
DEFAULT_PROFILE = 'compatto'
DEFAULT_BUDGET = 1600
STOP = set('''a ad al alla alle allo ai agli anche ancora avere avendo che chi ci cio come con cosa da dal dalla dalle dei degli del della delle dello di do dove e ed era essere fa fra gli ha hai hanno ho i il in io la le lei lo loro lui ma mi mio ne nei nel nella nelle nello noi non o ogni per piu poco poi quale quando quanto queste questi questo qui se sei si sia siamo sono su sul sulla sulle tra tu un una uno vi voi the a an and are as at be been being by can could did do does each for from had has have how if in into is it its may more most no not of on or our out over should so some than that their them then there these they this those through to under up use used using was we were what when where which while who will with would you your also all about after before between only other such very much many been also che delle questo questa questi queste degli negli agli essere possono viene vengono modo esempio esempio esempio example examples return none true false def class function var const let public private void int new import from package end elif else print self value values data dati tipo type name nome tutti tutte tutto altra altro altri altre senza sullo dell nell all d l s t il le del della delle'''.split())
GENERIC = set('introduzione conclusione conclusioni sintesi riassunto overview introduction conclusion summary note notes testo text contenuto content indice index untitled'.split())


class AtlasError(Exception):
    pass


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def clean(text: str, limit: int = 110) -> str:
    text = re.sub(r'\s+', ' ', text).strip().replace('|', ' / ')
    return text if len(text) <= limit else text[:limit - 1] + '…'


def words(text: str) -> list[str]:
    return [w.casefold() for w in re.findall(r'[^\W_]+', text, re.UNICODE)]


def terms(text: str) -> list[str]:
    def normalized(w):
        if w.isascii():
            return w
        return ''.join(c for c in unicodedata.normalize('NFKD', w) if not unicodedata.combining(c))
    return [w for w in words(text) if 2 < len(w) < 45 and normalized(w) not in STOP and not w.isdecimal()]


def keywords(text: str, count: int = 3) -> str:
    """Literal lexical cues, not a summary. Input may be a declared sample."""
    # Repeated boilerplate lines cannot multiply their weight in a sample.
    tokens = terms('\n'.join(dict.fromkeys(text.splitlines())))
    counts = Counter(tokens)
    selected = []
    for term, _ in counts.most_common(50):
        if not any(term.startswith(old) or old.startswith(term) for old in selected):
            selected.append(term)
        if len(selected) == count:
            break
    return ' · '.join(selected)


def search_body(text: str) -> str:
    # Keep the original identifiers and make their components searchable as well.
    extra = []
    for token in re.findall(r'\b[A-Za-z][A-Za-z0-9_]{2,100}\b', text):
        split = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|_', ' ', token)
        if split != token:
            extra.append(split)
    extra.extend('atlasexact' + value.casefold().encode('utf-8').hex() for value in special_identifiers(text))
    return text + ('\n' + ' '.join(extra) if extra else '')


SPECIAL_IDENTIFIERS = re.compile(r'(?<!\w)(?:[A-Za-z]\w*(?:\+\+|\#)|[\w.+-]+@[\w.-]+)(?!\w)')


def special_identifiers(text):
    return [m.group(0) for m in SPECIAL_IDENTIFIERS.finditer(text) if len(m.group(0)) <= 120]


def query_words(text):
    exact = special_identifiers(text)
    for value in exact:
        text = text.replace(value, ' ')
    original = re.findall(r'[^\W_]+', text, re.UNICODE)
    natural = [w.casefold() for w in original if w.casefold() not in STOP or (w.isupper() and len(w) > 1)] or words(text)
    return list(dict.fromkeys(natural + ['atlasexact' + value.casefold().encode('utf-8').hex() for value in exact]))[:24]


def safe_child(root: Path, relative: str) -> Path:
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise AtlasError('Riferimento fuori dalla cartella indicizzata: ' + relative)
    return result


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def slug(value: str) -> str:
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    value = re.sub(r'[^a-zA-Z0-9._-]+', '-', value).strip('.-')
    return value[:90] or 'risorsa'
