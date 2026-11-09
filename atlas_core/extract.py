"""Read-only, offline extractors. Text and coordinates; no inferred prose."""
from __future__ import annotations

import ast
import codecs
import csv
import io
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import AtlasError, clean

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
ODT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
ODTABLE = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
CODE = set('.py .pyi .js .jsx .mjs .cjs .ts .tsx .java .c .h .cc .cpp .hpp .cs .go .rs .rb .php .sh .bash .ps1 .psm1 .r .swift .kt .scala .lua .sql .vue .svelte .dart .ex .exs .clj .jl'.split())
BINARY = set('.png .jpg .jpeg .gif .webp .tif .tiff .bmp .ico .mp3 .mp4 .wav .mov .avi .exe .dll .so .dylib .pyc .class .zip .7z .rar .gz .tar .db .sqlite .sqlite3 .parquet .doc .xls .ppt .odg .rtf .woff .woff2 .ttf .pdfx .pyz'.split())


@dataclass
class Piece:
    text: str
    level: int = 0
    title: str = ''
    origin: str = 'originale'
    location: str = ''


def encoding(path: Path) -> str:
    with path.open('rb') as f:
        sample = f.read(8192)
    if sample.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return 'utf-32'
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return 'utf-16'
    if sample.startswith(codecs.BOM_UTF8):
        return 'utf-8-sig'
    if b'\x00' in sample:
        raise AtlasError('File binario senza estrattore testuale.')
    try:
        # A sample may end inside a multi-byte character.
        codecs.getincrementaldecoder('utf-8')().decode(sample, final=False)
        return 'utf-8'
    except UnicodeDecodeError:
        return 'cp1252'


def lines(path: Path, enc: str):
    """Bound memory even when a file is a single enormous line."""
    with path.open('r', encoding=enc, errors='strict', newline=None) as f:
        start_of_line = True
        while True:
            part = f.readline(32768)
            if not part:
                return
            yield part, start_of_line
            start_of_line = part.endswith('\n')


def python_headings(path: Path, enc: str, warnings: list[str]) -> dict:
    if path.stat().st_size > 8 * 1024 * 1024:
        warnings.append('Python oltre 8 MiB: lettura testuale, AST non costruito.')
        return {}
    try:
        tree = ast.parse(path.read_text(encoding=enc))
    except (SyntaxError, RecursionError) as exc:
        warnings.append('AST Python non disponibile; testo indicizzato: ' + clean(str(exc), 120))
        return {}
    result, spans = {}, []

    def visit(node, depth=0):
        is_symbol = isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if is_symbol:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            result[start] = (min(depth + 1, 6), node.name, 'AST Python')
            spans.append((start, node.end_lineno, min(depth + 1, 6), node.name))
            depth += 1
        for child in ast.iter_child_nodes(node):
            visit(child, depth)
    visit(tree)
    length = len(path.read_text(encoding=enc).splitlines())
    for start, end, level, name in spans:
        following = end + 1
        if following <= length and following not in result:
            active = [s for s in spans if s[0] < following <= s[1]]
            parent = max(active, key=lambda s: s[2]) if active else None
            result[following] = (min(parent[2] + 1, 6), 'Corpo di ' + parent[3] + ' (continuazione)', 'confine AST') if parent else (1, 'Codice modulo', 'confine AST')
    return result


def plain(path: Path, warnings: list[str]):
    enc = encoding(path)
    if enc == 'cp1252':
        warnings.append('Decodifica fallback Windows-1252; verificare caratteri nell’originale.')
    suffix = path.suffix.lower()
    py = python_headings(path, enc, warnings) if suffix in {'.py', '.pyi'} else {}
    markdown = suffix in {'.md', '.markdown', '.mdx'}
    fence = None
    line_no = 1
    for text, beginning in lines(path, enc):
        level, title, origin = 0, '', 'originale'
        if beginning:
            if line_no in py:
                level, title, origin = py[line_no]
            elif markdown:
                mark = re.match(r'^ {0,3}(`{3,}|~{3,})', text)
                if mark:
                    char = mark[1][0]
                    if fence is None:
                        fence = (char, len(mark[1]))
                    elif char == fence[0] and len(mark[1]) >= fence[1]:
                        fence = None
                elif fence is None:
                    h = re.match(r'^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$', text)
                    if h and len(text) < 500:
                        level, title = len(h[1]), h[2]
        yield Piece(text, level, title, origin)
        line_no += text.count('\n')


def archive(path: Path):
    z = zipfile.ZipFile(path)
    if sum(i.file_size for i in z.infolist()) > 512 * 1024 * 1024:
        z.close()
        raise AtlasError('Archivio decompresso oltre 512 MiB: estrazione non eseguita.')
    return z


def xml(z, name):
    if z.getinfo(name).file_size > 128 * 1024 * 1024:
        raise AtlasError('Parte XML oltre 128 MiB: estrazione non eseguita.')
    return ET.fromstring(z.read(name))


def wtext(p):
    result = []
    def own_elements(node):
        yield node
        for child in node:
            if child.tag != W + 'p':
                yield from own_elements(child)
    for e in own_elements(p):
        if e.tag == W + 't':
            result.append(e.text or '')
        elif e.tag == W + 'tab':
            result.append('\t')
        elif e.tag in {W + 'br', W + 'cr'}:
            result.append('\n')
    return ''.join(result)


def docx(path, warnings):
    with archive(path) as z:
        styles = {}
        if 'word/styles.xml' in z.namelist():
            for style in xml(z, 'word/styles.xml').findall(W + 'style'):
                ident = style.get(W + 'styleId')
                base = style.find(W + 'basedOn')
                ol = style.find('.//' + W + 'outlineLvl')
                name = style.find(W + 'name')
                label = (name.get(W + 'val', '') if name is not None else '') + ' ' + (ident or '')
                match = re.search(r'(?:heading|titolo)\s*([1-6])', label, re.I)
                level = int(ol.get(W + 'val')) + 1 if ol is not None else int(match[1]) if match else 0
                styles[ident] = (level if 1 <= level <= 6 else 0, base.get(W + 'val') if base is not None else None)

        def heading(p):
            explicit = p.find(W + 'pPr/' + W + 'outlineLvl')
            if explicit is not None:
                n = int(explicit.get(W + 'val')) + 1
                return n if 1 <= n <= 6 else 0
            style = p.find(W + 'pPr/' + W + 'pStyle')
            key = style.get(W + 'val') if style is not None else None
            seen = set()
            while key in styles and key not in seen:
                seen.add(key)
                n, key = styles[key]
                if n:
                    return n
            return 0

        root = xml(z, 'word/document.xml')
        images = len(list(root.iter(W + 'drawing')))
        tables = len(list(root.iter(W + 'tbl')))
        warnings.append('DOCX: testo normalizzato; righe non corrispondenti alle pagine Word. Note e impaginazione escluse.')
        if images:
            warnings.append(f'{images} immagini/disegni non interpretati.')
        if tables:
            warnings.append(f'{tables} tabelle: una riga di testo per riga di tabella, celle separate da |; celle unite e tabelle annidate non ricostruite.')
        if any(e.tag in {W + 'ins', W + 'del', W + 'moveFrom', W + 'moveTo'} for e in root.iter()):
            warnings.append('DOCX con revisioni tracciate: lettura XML non equivalente a una versione accettata. Verificare originale.')

        def blocks(node):
            for child in node:
                if child.tag in {W + 'p', W + 'tbl'}:
                    yield child
                else:
                    yield from blocks(child)

        n = table = 0
        for block in blocks(root):
            if block.tag == W + 'p':
                n += 1
                text = wtext(block)
                level = heading(block) if text.strip() else 0
                yield Piece(('#' * level + ' ' if level else '') + text + '\n', level, clean(text, 400), 'stile Word', f'paragrafo {n}')
                continue
            table += 1
            # A row stays one line, so a value keeps the other cells of its row
            # next to it instead of becoming a column of unrelated paragraphs.
            # Direct rows only: a nested table is read as text of the cell that holds it.
            for r, row in enumerate(block.findall(W + 'tr'), 1):
                cells = []
                for cell in row.findall(W + 'tc'):
                    paragraphs = [wtext(p).strip() for p in cell.iter(W + 'p')]
                    n += len(paragraphs)
                    cells.append(' '.join(t for t in paragraphs if t).replace('|', '/'))
                if any(cells):
                    yield Piece(' | '.join(cells) + '\n', location=f'tabella {table}, riga {r}')


def pdf(path, warnings):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise AtlasError('Estrattore PDF assente. Usare il pacchetto atlas.pyz completo.')
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(''):
        raise AtlasError('PDF cifrato: password necessaria.')
    empty = []
    warnings.append('PDF: estrazione del testo; ordine di lettura e tabelle da verificare nell’originale. Nessun OCR.')
    for n, page in enumerate(reader.pages, 1):
        try:
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 32 * 1024 * 1024:
                warnings.append(f'Pagina {n}: flusso oltre 32 MiB, non estratto.')
                yield Piece(f'Pagina {n}: testo non estratto.\n', 1, f'Pagina {n}', 'pagina', f'pagina {n}')
                continue
            text = page.extract_text() or ''
        except Exception as exc:
            text = ''
            warnings.append(f'Pagina {n}: errore {type(exc).__name__}.')
        if not text.strip():
            empty.append(n)
        yield Piece(f'# Pagina {n}\n', 1, f'Pagina {n}', 'pagina', f'pagina {n}')
        yield Piece(text + '\n', location=f'pagina {n}')
    if empty:
        warnings.append('Pagine senza testo estraibile: ' + ', '.join(map(str, empty[:50])) + ('…' if len(empty) > 50 else ''))


def relationships(z, name):
    return {r.get('Id'): r.get('Target') for r in xml(z, name) if r.get('TargetMode') != 'External'}


def pptx(path, warnings):
    with archive(path) as z:
        rels = relationships(z, 'ppt/_rels/presentation.xml.rels')
        ns = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
        root = xml(z, 'ppt/presentation.xml')
        warnings.append('PPTX: testo delle diapositive; note relatore, immagini, grafici e disposizione esclusi.')
        for n, slide in enumerate(root.iter(ns + 'sldId'), 1):
            target = rels[slide.get(R + 'id')]
            part = target.lstrip('/') if target.startswith('/') else posixpath.normpath('ppt/' + target)
            doc = xml(z, part)
            paragraphs = [''.join(t.text or '' for t in p.iter(A + 't')) for p in doc.iter(A + 'p')]
            title = next((p for p in paragraphs if p.strip()), f'Diapositiva {n}')
            yield Piece(f'# {title}\n', 1, title, 'diapositiva', f'diapositiva {n}')
            yield Piece('\n'.join(paragraphs) + '\n', location=f'diapositiva {n}')


def xlsx(path, warnings):
    with archive(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in xml(z, 'xl/sharedStrings.xml').iter(S + 'si'):
                shared.append(''.join(t.text or '' for t in si.iter(S + 't')))
        rels = relationships(z, 'xl/_rels/workbook.xml.rels')
        warnings.append('XLSX: celle, coordinate e formule; nessun ricalcolo. Formati, grafici e immagini esclusi.')
        warnings.append('Sezioni: fogli e intervalli di 100 righe; una riga molto lunga può richiedere più passaggi. Celle unite non ricostruite.')
        for sheet in xml(z, 'xl/workbook.xml').iter(S + 'sheet'):
            name = sheet.get('name', 'Foglio')
            target = rels[sheet.get(R + 'id')]
            part = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            yield Piece(f'# {name}\n', 1, name, 'foglio', f'foglio {name}')
            band = None
            # First non-empty row, repeated only in the location of later bands: rows 101+ would otherwise
            # show bare coordinates. Location is read back, not indexed, so search counts are unchanged.
            header = None
            with z.open(part) as stream:
                for _, row in ET.iterparse(stream, events=('end',)):
                    if row.tag != S + 'row':
                        continue
                    cells = []
                    for cell in row.findall(S + 'c'):
                        kind = cell.get('t', '')
                        value = cell.findtext(S + 'v', '')
                        if kind == 's' and value:
                            value = shared[int(value)]
                        elif kind == 'inlineStr':
                            value = ''.join(t.text or '' for t in cell.iter(S + 't'))
                        formula = cell.findtext(S + 'f')
                        if formula is not None:
                            value = f'formula ={formula}; valore memorizzato: {value or "assente"}'
                        if value:
                            cells.append(cell.get('r', '?') + ': ' + value)
                    if cells:
                        row_number = int(row.get('r', '1'))
                        if header is None:
                            header = (row_number, ' | '.join(cells))
                        current_band = (row_number - 1) // 100
                        if current_band != band:
                            band = current_band
                            title = f'Righe {band * 100 + 1}–{(band + 1) * 100}'
                            location = f'foglio {name}; {title}'
                            if header[0] < band * 100 + 1:
                                first = header[1] if len(header[1]) <= 300 else header[1][:299] + '…'
                                location += f'; prima riga non vuota del foglio (riga {header[0]}, possibile intestazione, non verificata): {first}'
                            yield Piece('## ' + title + '\n', 2, title, 'intervallo di righe', location)
                        yield Piece(' | '.join(cells) + '\n', location=f'foglio {name}, riga {row.get("r", "?")}')
                    row.clear()


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.level = 0
        self.buffer = []
        self.ready = []

    def flush(self):
        text = ''.join(self.buffer).strip()
        if text:
            self.ready.append(Piece(text + '\n', self.level, clean(text, 400) if self.level else '', 'HTML'))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript'}:
            self.skip += 1
        if self.skip:
            return
        if tag in {'p', 'div', 'li', 'tr', 'pre', 'br', 'section', 'article'} or re.fullmatch('h[1-6]', tag):
            self.flush()
        if re.fullmatch('h[1-6]', tag):
            self.level = int(tag[1])
        elif tag in {'td', 'th'}:
            self.buffer.append(' | ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'}:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in {'p', 'div', 'li', 'tr', 'pre', 'section', 'article'} or re.fullmatch('h[1-6]', tag):
            self.flush()
        if re.fullmatch('h[1-6]', tag):
            self.level = 0

    def handle_data(self, data):
        if not self.skip:
            self.buffer.append(data)
            if sum(map(len, self.buffer)) > 32768:
                self.flush()


def html_stream(chunks):
    parser = HTMLText()
    for chunk in chunks:
        parser.feed(chunk)
        yield from parser.ready
        parser.ready = []
    parser.close()
    parser.flush()
    yield from parser.ready


def odf(path, warnings):
    with archive(path) as z:
        warnings.append('OpenDocument: testo in ordine XML; layout, formule calcolate e oggetti visivi non interpretati.')
        root = xml(z, 'content.xml')
        for el in root.iter():
            if el.tag in {ODT + 'h', ODT + 'p'}:
                text = ''.join(el.itertext())
                level = min(6, max(1, int(el.get(ODT + 'outline-level', '1')))) if el.tag == ODT + 'h' else 0
                yield Piece(('#' * level + ' ' if level else '') + text + '\n', level, text if level else '', 'OpenDocument')


def epub(path, warnings):
    with archive(path) as z:
        warnings.append('EPUB: testo dei capitoli; immagini e impaginazione escluse.')
        container = xml(z, 'META-INF/container.xml')
        package = next(e.get('full-path') for e in container.iter() if e.tag.endswith('rootfile'))
        root = xml(z, package)
        manifest = {e.get('id'): e.get('href') for e in root.iter() if e.tag.endswith('}item')}
        for item in root.iter():
            if not item.tag.endswith('}itemref'):
                continue
            part = posixpath.normpath(posixpath.join(posixpath.dirname(package), manifest[item.get('idref')]))
            yield from html_stream([z.read(part).decode('utf-8')])


def extract(path: Path, warnings: list[str]):
    suffix = path.suffix.lower()
    handlers = {'.docx': docx, '.pdf': pdf, '.pptx': pptx, '.xlsx': xlsx, '.odt': odf, '.ods': odf, '.odp': odf, '.epub': epub}
    if suffix in handlers:
        yield from handlers[suffix](path, warnings)
    elif suffix in {'.html', '.htm'}:
        enc = encoding(path)
        yield from html_stream(text for text, _ in lines(path, enc))
    elif suffix == '.ipynb':
        notebook = json.loads(path.read_text(encoding='utf-8'))
        warnings.append('Notebook: sorgenti delle celle; output e allegati esclusi.')
        for n, cell in enumerate(notebook.get('cells', []), 1):
            title = f'Cella {n} ({cell.get("cell_type", "?")})'
            yield Piece('# ' + title + '\n', 1, title, 'cella', title)
            yield Piece(''.join(cell.get('source', [])) + '\n')
    elif suffix in BINARY:
        raise AtlasError('Formato non estratto: ' + suffix + '. Originale disponibile.')
    else:
        if suffix in CODE and suffix not in {'.py', '.pyi'}:
            warnings.append('Codice indicizzato come testo; simboli e dipendenze non risolti per questo linguaggio.')
        yield from plain(path, warnings)
