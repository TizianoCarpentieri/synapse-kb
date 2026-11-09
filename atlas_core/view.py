from __future__ import annotations

import codecs
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import quote

from .common import AtlasError, GENERIC, STOP, clean, digest, keywords, safe_child, sha_file, slug, terms, words, query_words
from .index import connect, get_root, meta, reference


def resolve(db, selector='root'):
    if selector in {'root', '.', ''}:
        return db.execute('SELECT n.* FROM nodes n JOIN dirs d ON d.node=n.id WHERE d.path="."').fetchone()
    match = re.fullmatch(r'([nd])(\d+)-([0-9a-f]{8})', selector)
    if match:
        if selector != reference(db, int(match[2]), match[1]):
            raise AtlasError('Riferimento superato, alterato o appartenente a un altro indice/versione: ' + selector)
        selector = match[1] + match[2]
    elif re.fullmatch(r'[nd]\d+(?:-\w*)?', selector):
        raise AtlasError('Riferimento incompleto o malformato. Copiare anche il suffisso di controllo.')
    if re.fullmatch(r'n\d+', selector):
        row = db.execute('SELECT * FROM nodes WHERE id=?', (int(selector[1:]),)).fetchone()
        if not row:
            raise AtlasError('Riferimento assente o superato dopo un aggiornamento: ' + selector)
        return row
    if re.fullmatch(r'd\d+', selector):
        row = db.execute('SELECT n.* FROM nodes n JOIN sources s ON s.root_node=n.id WHERE s.id=?', (int(selector[1:]),)).fetchone()
        if row:
            return row
        raise AtlasError('Documento rimosso o riferimento superato: ' + selector)
    target, number = selector.replace('\\', '/'), None
    if target.startswith('./'):
        target = target[2:]
    match = re.match(r'^(.*):(\d+)$', target)
    if match:
        target, number = match[1], int(match[2])
    folder = db.execute('SELECT n.* FROM nodes n JOIN dirs d ON d.node=n.id WHERE d.path=?', (target,)).fetchone()
    if folder is not None and number is None:
        return folder
    sources = db.execute('SELECT * FROM sources WHERE path=?', (target,)).fetchall()
    if not sources:
        sources = [s for s in db.execute('SELECT * FROM sources WHERE path LIKE ?', ('%' + target,)) if Path(s['path']).name == target]
    if len(sources) != 1:
        raise AtlasError('Selettore assente o ambiguo: ' + selector + '. Usare percorso relativo completo oppure nNUMERO.')
    source = sources[0]
    if number is not None:
        candidates = db.execute('SELECT * FROM nodes WHERE sid=? AND kind="passage" AND line<=? AND end_line>=? ORDER BY start',
                                (source['id'], number, number)).fetchall()
        if len(candidates) != 1:
            raise AtlasError('Riga assente o condivisa da più blocchi (testo su una sola riga). Usare i riferimenti nNUMERO.')
        return candidates[0]
    return db.execute('SELECT * FROM nodes WHERE id=?', (source['root_node'],)).fetchone()


def children(db, node):
    return db.execute('SELECT * FROM nodes WHERE parent=? ORDER BY ord,id', (node['id'],)).fetchall()


def check_source(db, index, sid, verify=False):
    source = db.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()
    if not source:
        raise AtlasError('Fonte non registrata.')
    if source['status'] not in {'ok', 'vuoto'}:
        raise AtlasError('Fonte non estratta: ' + source['path'] + '\n' + '\n'.join(json.loads(source['warnings'])))
    path = safe_child(get_root(db, index), source['path'])
    if not path.is_file():
        raise AtlasError('Fonte spostata o rimossa: ' + source['path'] + '. Eseguire prepara.')
    stat = path.stat()
    if stat.st_size != source['size'] or stat.st_mtime_ns != source['mtime']:
        # File copy/restore may preserve bytes but change timestamps.
        if sha_file(path) != source['sha']:
            raise AtlasError('Fonte cambiata dopo la preparazione: ' + source['path'] + '. Eseguire prepara.')
    elif verify and sha_file(path) != source['sha']:
        raise AtlasError('Impronta della fonte cambiata: ' + source['path'] + '. Eseguire prepara --verifica.')
    return source


def read_bytes(db, index, source, start, end):
    cache = safe_child(index, source['cache'])
    floor = db.execute('SELECT start FROM nodes WHERE sid=? AND kind="passage" AND start<=? ORDER BY start DESC LIMIT 1',
                       (source['id'], start)).fetchone()
    floor = floor[0] if floor else start
    rows = db.execute('SELECT start,end,checksum FROM nodes WHERE sid=? AND kind="passage" AND start>=? AND start<? ORDER BY start',
                      (source['id'], floor, end))
    pieces = []
    position = start
    with cache.open('rb') as stream:
        for row in rows:
            stream.seek(row['start'])
            data = stream.read(row['end'] - row['start'])
            if digest(data) != row['checksum']:
                raise AtlasError('Testo derivato alterato o troncato; eseguire prepara --ricostruisci.')
            a, b = max(start, row['start']), min(end, row['end'])
            if a != position:
                raise AtlasError('Lacuna nella copertura del testo normalizzato.')
            pieces.append(data[a - row['start']:b - row['start']])
            position = b
    if position != end:
        raise AtlasError('Intervallo di lettura non coperto dall’indice.')
    return b''.join(pieces)


def label(node):
    result = node['title'] or node['label'] or 'Testo'
    if (words(result) and all(w in GENERIC for w in words(result))) or re.fullmatch(r'(?:articolo|art\.?|allegato|slide|pagina|sezione|capitolo)\s+[\w. -]+|[\d.]+', result, re.I):
        cues = keywords(node['sample'], 3)
        if cues:
            result += ' — ' + cues
    return clean(result, 100)


def row_text(node, db):
    suffix = ''
    if node['kind'] == 'group' and node['sid'] is not None:
        suffix = (f' [byte {node["start"]}–{node["end"]}]' if node['line'] == node['end_line']
                  else f' [righe {node["line"]}–{node["end_line"]}]')
    return f'- {label(node)}{suffix} → {reference(db, node["id"])}'


def frontier(db, node, budget=1600):
    current = node
    entries = children(db, current)
    while len(entries) == 1 and entries[0]['kind'] not in {'unavailable', 'passage'}:
        current = entries[0]
        entries = children(db, current)
    if not entries:
        entries = [current] if current['kind'] == 'passage' else []
    max_rows = min(40, max(4, budget // 140))
    # Expand presentation groups and folders, preserving complete coverage.
    # Documents/headings are useful choices and are not automatically flooded with their bodies.
    while True:
        changed = False
        for i, entry in enumerate(entries):
            if entry['kind'] not in {'group', 'directory'}:
                continue
            sub = children(db, entry)
            if not sub:
                continue
            candidate = entries[:i] + sub + entries[i + 1:]
            if len(candidate) <= max_rows and sum(len(row_text(x, db)) + 1 for x in candidate) <= budget - 420:
                entries = candidate
                changed = True
                break
        if not changed:
            break
    return entries


def explore(index: Path, selector='root', budget=1600, offset=0, as_json=False):
    if not 600 <= budget <= 100000:
        raise AtlasError('Budget mappa ammesso: 600–100000 caratteri.')
    with connect(index) as db:
        node = resolve(db, selector)
        if node['sid'] is not None and node['kind'] != 'unavailable':
            check_source(db, index, node['sid'])
        entries = frontier(db, node, budget)
        if as_json:
            return {'ambito': reference(db, node['id']), 'voci': [dict(r) | {'ref': reference(db, r['id'])} for r in entries], 'budget_caratteri': budget}
        header = f'# {label(node)} · {reference(db, node["id"])}\n'
        if node['kind'] == 'passage':
            return header + 'Passaggio di testo: leggere con leggi ' + reference(db, node['id']) + '.\n'
        if not entries:
            header += 'Nessuna voce testuale in questo ambito; consultare copertura.md.\n'
        footer = '\nApri un riferimento con esplora oppure leggi. Ricerca libera sempre disponibile.\n'
        missing = db.execute('SELECT count(*) FROM sources WHERE status NOT IN ("ok","vuoto")').fetchone()[0]
        if missing:
            footer += f'{missing} fonti non estratte: consultare copertura.md.\n'
        partial = db.execute("SELECT count(*) FROM sources WHERE status='ok' AND (warnings LIKE '%senza testo%' OR warnings LIKE '%non estratt%' OR warnings LIKE '%revisioni%')").fetchone()[0]
        if partial:
            footer += f'{partial} fonti con lacune segnalate: consultare copertura.md.\n'
        if any(e['kind'] in {'group', 'passage'} for e in entries):
            footer += 'Etichette a campione, non riassunti né elenco dei temi. Per dettagli rari usare cerca.\n'
        if selector in {'root', '.', ''}:
            footer += 'Esclusioni attive (ambienti, segreti, generati): copertura.md.\n'
        selected = []
        used = len(header) + len(footer) + 100
        for entry in entries[offset:]:
            row = row_text(entry, db)
            if not selected and used + len(row) + 1 > budget:
                available = budget - used - len(reference(db, entry['id'])) - 8
                if available >= 8:
                    row = f'- {clean(label(entry), available)} → {reference(db, entry["id"])}'
            if used + len(row) + 1 > budget:
                break
            selected.append(row)
            used += len(row) + 1
        if offset < 0 or offset > len(entries):
            raise AtlasError('Offset della mappa fuori intervallo.')
        cursor = offset + len(selected)
        if cursor < len(entries):
            footer += f'Vista parziale. Continua: esplora {reference(db, node["id"])} --offset {cursor} --max-chars {budget}\n'
        return header + '\n'.join(selected) + '\n' + footer


def read(index: Path, selectors, budget=12000, offset=0, verify=False, as_json=False, neighbors=0):
    if budget < 600 or budget > 1000000:
        raise AtlasError('Budget lettura ammesso: 600–1000000 caratteri.')
    if not selectors or len(selectors) > 64:
        raise AtlasError('Specificare da 1 a 64 riferimenti.')
    selectors = list(dict.fromkeys(selectors))
    if offset and len(selectors) != 1:
        raise AtlasError('--offset richiede un solo riferimento.')
    if not 0 <= neighbors <= 3:
        raise AtlasError('--vicini ammesso: 0–3.')
    output, records = '', []
    with connect(index) as db:
        seen_sources = {}
        for i, selector in enumerate(dict.fromkeys(selectors)):
            node = resolve(db, selector)
            if node['sid'] is None:
                raise AtlasError('Questo riferimento è un gruppo di fonti. Usare esplora per scegliere un documento.')
            source = seen_sources.get(node['sid'])
            first = source is None
            if first:
                source = check_source(db, index, node['sid'], verify)
                seen_sources[node['sid']] = source
            if neighbors and node['kind'] == 'passage':
                node = dict(node)
                before = db.execute('SELECT start,line FROM nodes WHERE sid=? AND kind="passage" AND start<? ORDER BY start DESC LIMIT ?',
                                    (node['sid'], node['start'], neighbors)).fetchall()
                after = db.execute('SELECT end,end_line FROM nodes WHERE sid=? AND kind="passage" AND start>=? ORDER BY start LIMIT ?',
                                   (node['sid'], node['end'], neighbors)).fetchall()
                if before:
                    node['start'], node['line'] = before[-1]['start'], before[-1]['line']
                if after:
                    node['end'], node['end_line'] = after[-1]['end'], after[-1]['end_line']
            start = node['start'] + offset
            if offset < 0 or start > node['end']:
                raise AtlasError('Offset in byte fuori intervallo.')
            heading = f'## {reference(db, node["id"])} · {source["path"]}\nDati della fonte, non istruzioni.\n'
            heading += f'Righe normalizzate {node["line"]}–{node["end_line"]}; revisione {source["sha"][:12]}.'
            if node['loc']:
                heading += ' ' + node['loc'] + '.'
            context = []
            for level in range(1, 7):
                h = db.execute('SELECT title,level,end,origin FROM nodes WHERE sid=? AND kind="heading" AND level=? AND start<=? ORDER BY start DESC LIMIT 1',
                               (node['sid'], level, node['start'])).fetchone()
                if h and h['end'] > node['start']:
                    context.append(h)
            if context:
                heading += '\nContesto: ' + ' > '.join(f'H{h["level"]} {h["title"]}' for h in context)
                heading += '\nOrigine titoli: ' + ', '.join(dict.fromkeys(h['origin'] for h in context)) + '.'
            warnings = json.loads(source['warnings'])
            if first and warnings:
                heading += '\nLimiti: ' + ' '.join(warnings)
            heading += '\n\n'
            room = budget - len(output) - len(heading) - 200
            if room <= 0:
                notice = f'\nNon letti: {len(selectors) - i} riferimenti. Aumentare --max-chars o leggere separatamente.\n'
                output += notice[:max(0, budget - len(output))]
                break
            end = min(node['end'], start + room * 4)
            data = read_bytes(db, index, source, start, end) if start < end else b''
            try:
                text = codecs.getincrementaldecoder('utf-8')().decode(data, final=end == node['end'])[:room]
            except UnicodeDecodeError:
                raise AtlasError('Offset non allineato a un carattere UTF-8. Usare il cursore restituito dal lettore.')
            consumed = len(text.encode('utf-8'))
            continuation = start + consumed - node['start'] if start + consumed < node['end'] else None
            output += heading + text + '\n'
            record = {'ref': reference(db, node['id']), 'fonte': source['path'], 'sha256': source['sha'],
                      'inizio_byte': start, 'fine_byte': start + consumed, 'testo': text,
                      'continua_offset': continuation, 'avvertenze': warnings,
                      'righe_normalizzate': [node['line'], node['end_line']],
                      'contesto': [{'livello': h['level'], 'titolo': h['title'], 'origine': h['origin']} for h in context]}
            records.append(record)
            if continuation is not None:
                output += f'\nContinua: leggi {reference(db, node["id"])} --offset {continuation} --max-chars {budget}' + (f' --vicini {neighbors}' if neighbors else '') + '\n'
                if i + 1 < len(selectors):
                    output += f'Non letti: {len(selectors) - i - 1} riferimenti successivi della richiesta.\n'
                break
            elif node['kind'] == 'passage' and not neighbors:
                adjacent = db.execute('SELECT id FROM nodes WHERE sid=? AND kind="passage" AND (end=? OR start=?) ORDER BY start',
                                      (node['sid'], node['start'], node['end'])).fetchall()
                if adjacent:
                    output += '\nContesto adiacente: ' + ', '.join(reference(db, r[0]) for r in adjacent) + ' (oppure --vicini 1).\n'
    return records if as_json else output


def find(index: Path, query: str, scope='root', limit=6, budget=1600, as_json=False,
         all_matches=False, offset=0, edition=None):
    if not 600 <= budget <= 100000 or not 1 <= limit <= 50:
        raise AtlasError('Budget 600–100000 caratteri; limite 1–50 risultati.')
    if offset < 0 or (offset and not all_matches):
        raise AtlasError('--offset richiede --tutti e un valore non negativo.')
    tokens = query_words(query)
    if not tokens:
        raise AtlasError('Query senza termini ricercabili.')
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    with connect(index) as db:
        current_edition = meta(db, 'edition')
        if edition is not None and str(edition) != current_edition:
            raise AtlasError('Indice aggiornato durante la paginazione: ricominciare la ricerca.')
        node = resolve(db, scope)
        extra, params = '', []
        if node['sid'] is not None:
            extra = ' AND n.sid=? AND n.start<? AND n.end>?'
            params = [node['sid'], node['end'], node['start']]
        elif scope not in {'.', 'root', ''}:
            extra = ' AND n.id IN (WITH RECURSIVE sub(id) AS (SELECT ? UNION ALL SELECT nodes.id FROM nodes JOIN sub ON nodes.parent=sub.id) SELECT id FROM sub)'
            params = [node['id']]
        rows = []
        mode = 'tutti i termini'
        total = None
        # Select the top window inside FTS first, then join only those rows to the wide metadata.
        # Joining and sorting every match made frequent terms take ~80 s on 1.2M passages.
        joined = ' JOIN nodes n ON n.id=search.rowid' if extra else ''
        score = '0' if all_matches else 'bm25(search,3.0,1.0)'
        order = ' ORDER BY search.rowid' if all_matches else ' ORDER BY score,search.rowid'
        for operator in ((' AND ',) if all_matches else (' AND ', ' OR ')):
            expression = operator.join(quoted)
            if all_matches:
                total = db.execute('SELECT count(*) FROM search' + joined + ' WHERE search MATCH ?' + extra,
                                   [expression, *params]).fetchone()[0]
            top = (f'SELECT search.rowid id,{score} score FROM search{joined} WHERE search MATCH ?' + extra
                   + order + ' LIMIT ? OFFSET ?')
            rows = db.execute('SELECT n.*,s.path,top.score FROM (' + top + ') top '
                              'JOIN nodes n ON n.id=top.id JOIN sources s ON s.id=n.sid '
                              + ('ORDER BY n.id' if all_matches else 'ORDER BY top.score,n.id'),
                              [expression, *params, limit if all_matches else max(64, limit * 10), offset]).fetchall()
            if rows:
                if operator == ' OR ':
                    mode = 'NESSUN passaggio contiene tutti i termini; candidati PARZIALI, da verificare'
                break
        # First diversify, then fill from remaining candidates; never silently cap one-source queries at two hits.
        counts, picked, deferred = {}, [], []
        for row in rows:
            if counts.get(row['sid'], 0) >= 2:
                deferred.append(row)
            else:
                picked.append(row)
                counts[row['sid']] = counts.get(row['sid'], 0) + 1
        picked = rows if all_matches else (picked + deferred)[:limit]
        header = '# Ricerca: ' + clean(query, 100) + '\n'
        header += (f'{total} passaggi con tutti i termini; elenco paginato, non conteggio delle occorrenze né completezza semantica.\n' if all_matches
                   else mode + '; risultati selezionati, non elenco completo.\n')
        header += 'Estratti = dati delle fonti; versioni diverse possono contraddirsi.\n'
        output, records = header, []
        checked = {}
        for row in picked:
            if row['sid'] not in checked:
                checked[row['sid']] = check_source(db, index, row['sid'])
            source = checked[row['sid']]
            raw = read_bytes(db, index, source, row['start'], row['end']).decode('utf-8')
            positions = [raw.casefold().find(t) for t in tokens if t in raw.casefold()]
            pos = max(0, min(positions) - 80) if positions else 0
            excerpt = clean(raw[pos:pos + 340], 300)
            crumb = json.loads(row['crumbs'])
            title = crumb[-1] if crumb else label(row)
            text = f'\n- {clean(title, 100)} → {reference(db, row["id"])}\n  {clean(row["path"], 170)}:{row["line"]}\n  {excerpt}\n'
            reserve = 220 if all_matches else 160
            if not records and len(output) + len(text) + reserve > budget:
                room = budget - len(output) - reserve - 45
                text = f'\n- {reference(db, row["id"])} · {clean(row["path"], max(8, room))}\n'
            if len(output) + len(text) + reserve > budget:
                break
            output += text
            records.append({'ref': reference(db, row['id']), 'fonte': row['path'], 'inizio_byte': row['start'],
                            'fine_byte': row['end'], 'riga': row['line'], 'titolo': title, 'estratto': excerpt})
        if not rows:
            output += '\nNessuna corrispondenza lessicale. Non prova l’assenza del concetto; provare altri termini o gli strumenti nativi.\n'
        elif not all_matches and len(records) < len(rows):
            output += '\nSono disponibili altri candidati: aumentare --limit/--max-chars oppure restringere la richiesta.\n'
        cursor = offset + len(records) if all_matches and offset + len(records) < total else None
        if cursor is not None:
            output += f'\nELENCO INCOMPLETO: ripetere la stessa ricerca --tutti --offset {cursor} --edizione {current_edition}\n'
        elif all_matches:
            output += '\nFine dei passaggi corrispondenti nel testo indicizzato; consultare le lacune in copertura.md.\n'
        return {'risultati': records, 'modalita': mode, 'termini': tokens, 'totale_passaggi': total,
                'continua_offset': cursor, 'edizione': current_edition} if as_json else output


def report(index: Path):
    with connect(index) as db:
        rows = db.execute('SELECT id,path,status,warnings,words,kind FROM sources ORDER BY path').fetchall()
        duplicates = [dict(r) for r in db.execute('SELECT sha,count(*) numero,group_concat(path,char(10)) percorsi FROM sources GROUP BY sha HAVING count(*)>1')]
        return {'versione': meta(db, 'version'), 'configurazione': json.loads(meta(db, 'config')),
                'preparato': meta(db, 'built'), 'esclusioni': json.loads(meta(db, 'exclusions', '[]')),
                'duplicati_identici': duplicates,
                'fonti': [dict(r) | {'warnings': json.loads(r['warnings'])} for r in rows]}


def export(index: Path, limit=200):
    """Bounded Obsidian preview; the SQLite index remains complete."""
    data = report(index)
    notes = index / 'mappa'
    notes.mkdir(exist_ok=True)
    generated = set()
    with connect(index) as db:
        root = get_root(db, index)
        top = explore(index, budget=1600)
        catalog = '# Fonti — anteprima visuale\n\n'
        for source in data['fonti'][:limit]:
            name = slug(Path(source['path']).name) + '--d' + str(source['id']) + '.md'
            generated.add(name)
            catalog += f'- [{source["path"]}](mappa/{quote(name)})\n'
            try:
                target = os.path.relpath(root / source['path'], notes).replace('\\', '/')
            except ValueError:
                target = (root / source['path']).as_uri()
            text = f'# {Path(source["path"]).name}\n\n[Apri originale]({quote(target, safe="/.:_-")})\n\n'
            text += 'Vista generata; usare gli originali per le modifiche.\n\n'
            if source['status'] in {'ok', 'vuoto'}:
                text += explore(index, reference(db, source['id'], 'd'), budget=1600)
            text += '\n## Limiti di lettura\n\n' + ('\n'.join('- ' + w for w in source['warnings']) or 'Testo indicizzato.') + '\n'
            (notes / name).write_text(text, encoding='utf-8')
        if len(data['fonti']) > limit:
            catalog += f'\nAnteprima limitata a {limit} fonti; tutte le {len(data["fonti"])} fonti sono nel catalogo e nella navigazione CLI.\n'
        top += '\n[Catalogo visuale opzionale](catalogo.md) · [Copertura](copertura.md). I riferimenti nNUMERO si aprono con Atlas.\n'
        (index / 'index.md').write_text(top, encoding='utf-8')
        (index / 'catalogo.md').write_text(catalog, encoding='utf-8')
        coverage = '# Copertura della preparazione\n\n'
        coverage += 'Nessuna inferenza LLM. Le fonti non estratte restano elencate.\n\n'
        coverage += '## Esclusioni attive\n\n' + ', '.join(data['esclusioni']) + '\n\nLe esclusioni non sono fonti lette.\n\n'
        coverage += '## Duplicati identici\n\n'
        for group in data['duplicati_identici']:
            coverage += '\n'.join('- ' + p for p in group['percorsi'].splitlines()) + '\n\n'
        coverage += 'Rilevamento per SHA-256 dei file. Quasi-duplicati, conversioni fra formati e precedenza fra versioni non risolti automaticamente.\n\n'
        for source in data['fonti']:
            coverage += f'## {source["path"]}\n\nStato: {source["status"]}; parole estratte: {source["words"]}.\n\n'
            coverage += '\n'.join('- ' + w for w in source['warnings']) + '\n\n'
        (index / 'copertura.md').write_text(coverage, encoding='utf-8')
        (index / 'copertura.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        # Only recognizable generated artifacts inside this index; never source files.
        for old in notes.glob('*--d*.md'):
            if old.name not in generated and not old.is_symlink() and re.fullmatch(r'[A-Za-z0-9._-]+--d\d+\.md', old.name):
                safe_child(index, 'mappa/' + old.name).unlink()
        live = {r[0] for r in db.execute('SELECT cache FROM sources WHERE cache IS NOT NULL')}
        for old in (index / 'testi').glob('d*.txt'):
            if ('testi/' + old.name) not in live and not old.is_symlink() and re.fullmatch(r'd\d+-[0-9a-f]{16}-[0-9a-f]+\.txt', old.name):
                safe_child(index, 'testi/' + old.name).unlink()
    return str(index / 'index.md')
