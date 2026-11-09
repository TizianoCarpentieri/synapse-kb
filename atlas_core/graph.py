"""Local lexical dependency candidates, adapted from the Atlas prototype.
Scores rank candidates; they are not calibrated probabilities or verified rules.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import time
import unicodedata
from .common import STOP, GENERIC, clean
from .index import connect, reference, meta, set_meta
from .view import read_bytes, check_source
from .memory import DEP_SCHEMA, sync_memory

DOMAIN_GENERIC = STOP | GENERIC | set("document documents file files section sections page pages example examples note notes testo documento documenti sezione capitolo pagina pagine righe esempio informazione informazioni generale procedura procedure prerequisite prerequisites exception exceptions condition conditions conditional rule rules workflow workflows standard scope general prerequisito prerequisiti eccezione eccezioni condizione condizioni vincolo vincoli regola regole ambito".split())
PATTERNS = [
 ('eccezione_deroga', 1.35, re.compile(r"\b(?:except|unless|excluding|not applicable|does not apply|incompatible|eccezion\w+|in deroga|salvo|esclus[oi]|non si applica|incompatibil\w+|solo per)\b", re.I)),
 ('prerequisito_vincolo', 1.25, re.compile(r"\b(?:prerequisite\w*|only if|before proceeding|provided that|requires|must first|prerequisit\w+|prima di procedere|previa verifica|solo se|a condizione che|obbligatori\w+|richiede che|verificare prima|accertarsi che|vincol\w+)\b", re.I)),
 ('biforcazione_caso', 1.10, re.compile(r"\b(?:if|in case of|depending on|otherwise|nel caso in cui|qualora|se il|se la|in caso di|a seconda che|se invece|laddove|distinguere se)\b", re.I)),
]
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


def classify_passage_cues(text: str, is_volatile_file: bool) -> list[tuple[str, float, str, set[str]]]:
    """Individua frasi con marcatori di dipendenza/eccezione/prerequisito nel passaggio."""
    results = []
    for sent in split_sentences(text):
        sent_phrases = set(extract_phrases(sent))
        if not sent_phrases:
            continue
        matched = False
        if VOLATILE_SENT_RE.search(sent):
            results.append(('appunto_volatile', 1.45, sent, sent_phrases))
            matched = True
        for kind, weight, pattern in PATTERNS:
            if pattern.search(sent):
                results.append((kind, weight, sent, sent_phrases))
                matched = True
                break
        if not matched and is_volatile_file:
            results.append(('appunto_volatile', 1.30, sent, sent_phrases))
    return results


def build_dependencies(index_dir: Path, max_out_per_node: int = 5) -> dict:
    """Costruisce la tabella `dependencies` in `_atlas/atlas.sqlite` senza stampare testi."""
    started = time.perf_counter()
    with connect(index_dir, write=True) as db:
        db.executescript(DEP_SCHEMA)
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
                'crumbs': crumbs,
                'section_key': (r['sid'], tuple(crumbs[:2]) if crumbs else (r['label'],)),
                'head_phrases': head_phrases,
                'body_phrases': body_phrases,
                'all_phrases': head_phrases | body_phrases,
                'is_volatile': is_vol,
                'cues': classify_passage_cues(text, is_vol),
            })

        n_passages = len(passages)
        if not n_passages:
            sync_memory(db, index_dir)
            set_meta(db, 'graph_edition', meta(db, 'edition'))
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

        candidate_edges = defaultdict(list)  # src_idx -> list of (score, dst_idx, kind, anchor, cue)

        for dst_idx, dst in enumerate(passages):
            if not dst['cues'] or not dst['valid_all_anchors']:
                continue
            # Per ogni frase condizionale/eccezione/appunto in dst, cerchiamo le sezioni principali src
            for kind, kind_mult, cue_sent, sent_phrases in dst['cues']:
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
                        candidate_edges[src_idx].append((score, dst_idx, kind, best_anchor, cue_sent))

        # Selezione diversificata per ogni nodo sorgente
        inserted = []
        by_kind = Counter()
        cross_file_count = 0

        for src_idx, items in candidate_edges.items():
            items.sort(key=lambda x: (-x[0], x[1]))
            picked = []
            seen_dst = set()
            seen_sec = Counter()
            for score, dst_idx, kind, anchor, cue in items:
                if dst_idx in seen_dst:
                    continue
                sec = passages[dst_idx]['section_key']
                if seen_sec[sec] >= 2:
                    continue
                seen_dst.add(dst_idx)
                seen_sec[sec] += 1
                picked.append((passages[src_idx]['id'], passages[dst_idx]['id'], kind, anchor, cue, score))
                by_kind[kind] += 1
                if passages[src_idx]['sid'] != passages[dst_idx]['sid']:
                    cross_file_count += 1
                if len(picked) >= max_out_per_node:
                    break
            inserted.extend(picked)

        db.executemany(
            'INSERT OR REPLACE INTO dependencies(src, dst, kind, anchor, cue, score) VALUES(?,?,?,?,?,?)',
            inserted
        )
        sync_memory(db, index_dir)
        set_meta(db, 'graph_edition', meta(db, 'edition'))
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
        'nodes_with_outgoing_deps': nodes_with_out,
        'nodes_with_incoming_deps': nodes_with_in,
        'nodes_involved': nodes_involved,
        'seconds': elapsed,
    }
