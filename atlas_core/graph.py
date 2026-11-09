"""Local dependency candidates: lexical anchors plus resolved explicit references.
Scores rank candidates; they are not calibrated probabilities or verified rules.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import time
import hashlib
import posixpath
import unicodedata
from urllib.parse import unquote
from .common import STOP, GENERIC, clean
from .index import connect, reference, meta, set_meta
from .view import read_bytes, check_source
from .memory import ensure_schema, sync_memory
from .relations import CUES, MARKDOWN_LINK, REFERENCE_CUE, analyze, sentence_spans
from . import events

DOMAIN_GENERIC = STOP | GENERIC | set("document documents file files section sections page pages example examples note notes testo documento documenti sezione capitolo pagina pagine righe esempio informazione informazioni generale procedura procedure prerequisite prerequisites exception exceptions condition conditions conditional rule rules workflow workflows standard scope general prerequisito prerequisiti eccezione eccezioni condizione condizioni vincolo vincoli regola regole ambito".split())
PATTERNS = [(kind, weight, pattern) for kind, _, weight, pattern in CUES]  # 0.1 name, kept for importers
REFERENCE_SCORE = 60.0
VOLATILE_PATH_RE = re.compile(r'(?:volatile|updates?|appunt|aggiornament|avvis)', re.I)
VOLATILE_SENT_RE = re.compile(r'^\s*(?:note|warning|attention|update|nota bene|attenzione|avviso|aggiornamento)\b', re.I)

def norm_text(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c))


def extract_phrases(text: str) -> list[str]:
    """Estrae termini singoli specifici ed espressioni di 2-3 parole."""
    text = re.sub(r'https?://[^\s)\]<>]+', ' ', text)
    text = re.sub(r'\b[0-9a-fA-F]{10,}\b', ' ', text)
    out = []
    for line in norm_text(text).splitlines():
        for sent in re.split(r'[.!?;:|<>\[\]{}()]', line):
            tokens = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", sent)
            n = len(tokens)
            for i, tok in enumerate(tokens):
                # Numeric identifiers or specific words
                is_code = tok.isdigit() and 3 <= len(tok) <= 6
                if not is_code and (tok in DOMAIN_GENERIC or len(tok) < 4 or tok.isdigit()):
                    continue
                out.append(tok)
                for length in (2, 3):
                    if i + length <= n:
                        piece = tokens[i:i + length]
                        last = piece[-1]
                        if last not in DOMAIN_GENERIC and len(last) >= 3 and not last.isdigit():
                            # Almeno una parola non generica oltre l'ultima o stopword interna
                            phrase = ' '.join(piece)
                            if not all(w in DOMAIN_GENERIC for w in piece):
                                out.append(phrase)
    return out


def split_sentences(text: str) -> list[str]:
    raw = re.split(r'(?<=[.!?])\s+|\n+', text)
    return [clean(s, 220) for s in raw if len(s.strip()) >= 18]


def classify_passage_cues(text: str, is_volatile_file: bool) -> list[tuple[str, float, str, set[str], dict]]:
    """Sentences carrying a relation cue, with the sentence span inside the passage."""
    results = []
    for start, end in sentence_spans(text):
        raw = text[start:end]
        sent = clean(raw, 220)
        sent_phrases = set(extract_phrases(sent))
        if not sent_phrases:
            continue
        where = {'chars': (start, end)}
        matched = False
        if VOLATILE_SENT_RE.search(sent):
            results.append(('appunto_volatile', 1.45, sent, sent_phrases, where | {'relation': 'volatile_note'}))
            matched = True
        found = analyze(raw)
        if found:
            results.append((found['kind'], found['weight'], sent, sent_phrases,
                            where | {k: found[k] for k in ('relation', 'cue', 'condition', 'consequence', 'negated')}))
            matched = True
        if not matched and is_volatile_file:
            results.append(('appunto_volatile', 1.30, sent, sent_phrases, where | {'relation': 'volatile_note'}))
    return results


def _evidence(passage, text, meta_, resolver):
    """Where the cue sentence is, as bytes of the normalized text, plus its hash."""
    start, end = meta_['chars']
    a = passage['start'] + len(text[:start].encode('utf-8'))
    raw = text[start:end].encode('utf-8')
    data = {'resolver': resolver, 'cue_in': passage['ref'], 'sentence_bytes': [a, a + len(raw)],
            'sentence_sha256': hashlib.sha256(raw).hexdigest()}
    data.update({k: v for k, v in meta_.items() if k != 'chars'})
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def _title_key(title):
    return tuple(re.findall(r"[^\W_]+", norm_text(title)))


def explicit_references(passages, texts):
    """Edges for references a document states itself: a link, a file name, a cited heading.

    A heading is resolved only when its title is unique in the corpus and is
    more than a generic word. Yields (src_idx, dst_idx, kind, anchor, cue, score, evidence).
    """
    first_of_section, first_of_source, by_path, by_name = {}, {}, {}, defaultdict(set)
    for idx, p in enumerate(passages):
        first_of_source.setdefault(p['sid'], idx)
        by_path[p['path']] = p['sid']
        by_name[posixpath.basename(p['path']).casefold()].add(p['sid'])
        if p['crumbs']:
            first_of_section.setdefault((p['sid'], tuple(p['crumbs'])), idx)
    titles = defaultdict(set)
    for (sid, crumbs), idx in first_of_section.items():
        key = _title_key(crumbs[-1])
        if len(key) > 8 or not key:
            continue
        specific = [w for w in key if w not in DOMAIN_GENERIC and len(w) >= 4]
        if len(specific) >= 2 or (len(key) == 1 and len(key[0]) >= 6 and specific):
            titles[key].add(idx)
    unique_titles = {key: next(iter(v)) for key, v in titles.items() if len(v) == 1}

    def emit(src_idx, dst_idx, anchor, text, span, how):
        src, dst = passages[src_idx], passages[dst_idx]
        if src_idx == dst_idx or src['section_key'] == dst['section_key']:
            return
        raw = text[span[0]:span[1]]
        cue = clean(raw, 220)
        info = {'chars': span, 'relation': 'reference', 'resolved_by': how}
        yield (src_idx, dst_idx, 'riferimento_esplicito', anchor, cue, REFERENCE_SCORE,
               _evidence(src, text, info, 'explicit_reference'))
        found = analyze(raw)
        if found:
            # The citing sentence qualifies what it cites: a reader of the cited
            # rule should be sent to the passage that restricts or replaces it.
            info = {'chars': span} | {k: found[k] for k in ('relation', 'cue', 'condition', 'consequence', 'negated')}
            yield (dst_idx, src_idx, found['kind'], anchor, cue, REFERENCE_SCORE - 5,
                   _evidence(src, text, info, 'explicit_reference'))

    for src_idx, src in enumerate(passages):
        text = texts(src_idx)
        spans = list(sentence_spans(text))
        for link in MARKDOWN_LINK.finditer(text):
            target = unquote(link.group(2))
            if re.match(r'[a-z][a-z0-9+.-]*:', target, re.I):
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(src['path']), target))
            sid = by_path.get(resolved)
            if sid is not None and sid != src['sid']:
                span = next(((a, b) for a, b in spans if a <= link.start() < b), (link.start(), link.end()))
                yield from emit(src_idx, first_of_source[sid], resolved, text, span, 'markdown link')
        for start, end in spans:
            sentence = text[start:end]
            cue = REFERENCE_CUE.search(sentence)
            if not cue:
                continue
            tail = sentence[cue.end():]
            for name in re.findall(r"[\w./-]+\.[A-Za-z0-9]{2,5}\b", tail):
                sids = by_name.get(posixpath.basename(name).casefold(), ())
                if len(sids) == 1 and next(iter(sids)) != src['sid']:
                    yield from emit(src_idx, first_of_source[next(iter(sids))], name, text, (start, end), 'file name')
            words = _title_key(tail)[:24]
            position = 0
            while position < len(words):
                for length in range(min(8, len(words) - position), 0, -1):
                    target = unique_titles.get(words[position:position + length])
                    if target is not None:
                        yield from emit(src_idx, target, ' '.join(words[position:position + length]),
                                        text, (start, end), 'unique heading title')
                        position += length - 1
                        break
                position += 1


def build_dependencies(index_dir: Path, max_out_per_node: int = 5) -> dict:
    """Costruisce la tabella `dependencies` in `_atlas/atlas.sqlite` senza stampare testi."""
    started = time.perf_counter()
    with connect(index_dir, write=True) as db:
        ensure_schema(db)
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM dependencies')
        checked = set()
        sources = {s['id']: dict(s) for s in db.execute('SELECT * FROM sources ORDER BY id')}
        rows = db.execute("SELECT * FROM nodes WHERE kind='passage' ORDER BY sid, start").fetchall()
        passages = []
        for r in rows:
            src = sources[r['sid']]
            if r['sid'] not in checked:
                check_source(db, index_dir, r['sid'], verify=True)
                checked.add(r['sid'])
            text = read_bytes(db, index_dir, src, r['start'], r['end']).decode('utf-8', errors='replace')
            crumbs = json.loads(r['crumbs'])
            heading_text = ' '.join(crumbs[-2:]) if crumbs else r['label']
            body_phrases = set(extract_phrases(text))
            head_phrases = set(extract_phrases(heading_text))
            is_vol = bool(VOLATILE_PATH_RE.search(src['path']))
            passages.append({
                'id': r['id'],
                'ref': reference(db, r['id']),
                'sid': r['sid'],
                'path': src['path'],
                'line': r['line'],
                'end_line': r['end_line'],
                'start': r['start'],
                'end': r['end'],
                'crumbs': crumbs,
                'section_key': (r['sid'], tuple(crumbs[:2]) if crumbs else (r['label'],)),
                'head_phrases': head_phrases,
                'body_phrases': body_phrases,
                'all_phrases': head_phrases | body_phrases,
                'is_volatile': is_vol,
                'cues': classify_passage_cues(text, is_vol),
            })

        def passage_text(idx):
            p = passages[idx]
            return read_bytes(db, index_dir, sources[p['sid']], p['start'], p['end']).decode('utf-8', errors='replace')

        n_passages = len(passages)
        if not n_passages:
            sync_memory(db, index_dir)
            set_meta(db, 'graph_edition', meta(db, 'edition'))
            events.append(db, 'graph_built', {'edition': meta(db, 'edition'), 'edges': 0})
            db.commit()
            return {'passages': 0, 'edges': 0}

        df = Counter()
        head_df = Counter()
        for p in passages:
            for ph in p['all_phrases']:
                df[ph] += 1
            for ph in p['head_phrases']:
                head_df[ph] += 1

        max_df_multi = max(10, min(85, int(n_passages * 0.032)))
        max_df_single = max(8, min(40, int(n_passages * 0.015)))

        anchor_weight = {}
        for ph, count in df.items():
            if count < 2:
                continue
            words_count = ph.count(' ') + 1
            if words_count >= 2 and count <= max_df_multi:
                idf = math.log(1.0 + n_passages / count)
                anchor_weight[ph] = idf * (1.35 if words_count == 2 else 1.65)
            elif words_count == 1 and head_df[ph] >= 1 and count <= max_df_single:
                idf = math.log(1.0 + n_passages / count)
                anchor_weight[ph] = idf * 1.15

        # Indice inverso: ancora -> passaggi che la definiscono nel titolo (sezioni canoniche)
        # o nel corpo
        canonical_by_anchor = defaultdict(list)
        body_by_anchor = defaultdict(list)
        for idx, p in enumerate(passages):
            p['valid_head_anchors'] = {ph for ph in p['head_phrases'] if ph in anchor_weight}
            p['valid_all_anchors'] = {ph for ph in p['all_phrases'] if ph in anchor_weight}
            for ph in p['valid_head_anchors']:
                canonical_by_anchor[ph].append(idx)
            for ph in p['valid_all_anchors']:
                body_by_anchor[ph].append(idx)

        candidate_edges = defaultdict(list)  # src_idx -> list of (score, dst_idx, kind, anchor, cue, evidence, origin)

        for dst_idx, dst in enumerate(passages):
            if not dst['cues'] or not dst['valid_all_anchors']:
                continue
            # Per ogni frase condizionale/eccezione/appunto in dst, cerchiamo le sezioni principali src
            for kind, kind_mult, cue_sent, sent_phrases, cue_meta in dst['cues']:
                sent_anchors = {ph for ph in sent_phrases if ph in anchor_weight}
                active_anchors = sent_anchors or dst['valid_head_anchors']
                if not active_anchors:
                    continue

                # Trova candidati src che hanno queste ancore nel titolo della sezione
                src_matches = defaultdict(set)
                for ph in sorted(active_anchors):
                    for src_idx in canonical_by_anchor.get(ph, ()):
                        if src_idx != dst_idx:
                            src_matches[src_idx].add(ph)

                for src_idx, shared in src_matches.items():
                    src = passages[src_idx]
                    # Evita passaggi adiacenti nella stessa sezione dello stesso file (già coperti da --vicini 1)
                    if src['sid'] == dst['sid'] and abs(src['id'] - dst['id']) <= 2:
                        continue
                    if src['section_key'] == dst['section_key']:
                        continue

                    # Richiedi che l'ancora condivisa sia nel titolo di src e sia specifica:
                    # o multi-parola, o termine raro nel titolo (df <= 18), oppure accompagnata da una seconda ancora
                    all_shared = shared | (src['valid_all_anchors'] & dst['valid_all_anchors'])
                    has_strong_head = any(
                        ph in src['valid_head_anchors'] and (ph.count(' ') >= 1 or df[ph] <= 18 or len(all_shared) >= 2)
                        for ph in shared
                    )
                    if not has_strong_head:
                        continue

                    best_anchor = max(sorted(shared), key=lambda ph: anchor_weight[ph] * (1.35 if ph in sent_anchors else 1.0))
                    # Non sommare varianti sovrapposte della stessa frase
                    distinct_shared = []
                    for ph in sorted(all_shared, key=lambda x: (-anchor_weight[x], x)):
                        if not any(ph in old or old in ph for old in distinct_shared):
                            distinct_shared.append(ph)
                        if len(distinct_shared) == 2:
                            break
                    base_score = sum(anchor_weight[ph] for ph in distinct_shared)
                    cross_file_bonus = 1.45 if src['sid'] != dst['sid'] else 1.0
                    in_sent_bonus = 1.30 if best_anchor in sent_anchors else 1.0
                    head_to_head_bonus = 1.25 if (src['valid_head_anchors'] & dst['valid_head_anchors']) else 1.0
                    score = round(base_score * kind_mult * cross_file_bonus * in_sent_bonus * head_to_head_bonus, 3)

                    if score >= 7.5:
                        candidate_edges[src_idx].append((score, dst_idx, kind, best_anchor, cue_sent, (dst_idx, cue_meta), 'heuristic'))

        reference_count = 0
        for src_idx, dst_idx, kind, anchor, cue, score, evidence in explicit_references(passages, passage_text):
            candidate_edges[src_idx].append((score, dst_idx, kind, anchor, cue, evidence, 'reference'))
            reference_count += 1

        # Selezione diversificata per ogni nodo sorgente
        inserted = []
        by_kind = Counter()
        cross_file_count = 0

        for src_idx, items in candidate_edges.items():
            items.sort(key=lambda x: (-x[0], x[1]))
            picked = []
            seen_dst = set()
            seen_sec = Counter()
            for score, dst_idx, kind, anchor, cue, evidence, origin in items:
                if dst_idx in seen_dst:
                    continue
                sec = passages[dst_idx]['section_key']
                if seen_sec[sec] >= 2:
                    continue
                seen_dst.add(dst_idx)
                seen_sec[sec] += 1
                if not isinstance(evidence, str):
                    holder, cue_meta = evidence
                    evidence = _evidence(passages[holder], passage_text(holder), cue_meta, 'lexical_anchor')
                picked.append((passages[src_idx]['id'], passages[dst_idx]['id'], kind, anchor, cue, score,
                               json.loads(evidence).get('relation', ''), origin, evidence))
                by_kind[kind] += 1
                if passages[src_idx]['sid'] != passages[dst_idx]['sid']:
                    cross_file_count += 1
                if len(picked) >= max_out_per_node:
                    break
            inserted.extend(picked)

        db.executemany(
            'INSERT OR REPLACE INTO dependencies(src, dst, kind, anchor, cue, score, relation, origin, evidence) VALUES(?,?,?,?,?,?,?,?,?)',
            inserted
        )
        sync_memory(db, index_dir)
        set_meta(db, 'graph_edition', meta(db, 'edition'))
        events.append(db, 'graph_built', {'edition': meta(db, 'edition'), 'edges': len(inserted)})
        db.commit()

    elapsed = round(time.perf_counter() - started, 3)
    nodes_with_out = len({x[0] for x in inserted})
    nodes_with_in = len({x[1] for x in inserted})
    nodes_involved = len({x[0] for x in inserted} | {x[1] for x in inserted})
    return {
        'passages': n_passages,
        'valid_anchors': len(anchor_weight),
        'edges_total': len(inserted),
        'edges_cross_file': cross_file_count,
        'edges_by_kind': dict(by_kind),
        'edges_from_explicit_references': sum(x[7] == 'reference' for x in inserted),
        'nodes_with_outgoing_deps': nodes_with_out,
        'nodes_with_incoming_deps': nodes_with_in,
        'nodes_involved': nodes_involved,
        'seconds': elapsed,
    }
