from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def zip_parts(path, parts):
    with ZipFile(path, 'w') as z:
        for name, text in parts.items():
            z.writestr(name, text)


def make_docx(path):
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    zip_parts(path, {
        'word/styles.xml': f'<w:styles xmlns:w="{ns}"><w:style w:styleId="Base"><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style><w:style w:styleId="Custom"><w:basedOn w:val="Base"/></w:style></w:styles>',
        'word/document.xml': f'<w:document xmlns:w="{ns}"><w:body><w:p><w:pPr><w:pStyle w:val="Custom"/></w:pPr><w:r><w:t>Gestione faro</w:t></w:r></w:p><w:p><w:r><w:t>La lanterna costiera usa la chiave MARINA-582. Non attivare durante la manutenzione.</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Valvola</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>chiusa</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})


def make_pdf(path, text='The amber telescope uses calibration code ORBIT-734.'):
    stream = f'BT /F1 12 Tf 50 750 Td ({text}) Tj ET'.encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    data = b'%PDF-1.4\n'
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:])
    data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    path.write_bytes(data)


def make_pptx(path):
    zip_parts(path, {
        'ppt/presentation.xml': '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        'ppt/_rels/presentation.xml.rels': '<Relationships><Relationship Id="rId1" Target="slides/slide1.xml"/></Relationships>',
        'ppt/slides/slide1.xml': '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Protocollo arcipelago</a:t></a:r></a:p><a:p><a:r><a:t>Il canale di soccorso è ISOLA-826.</a:t></a:r></a:p></p:sld>'})


def make_xlsx(path):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    zip_parts(path, {
        'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Magazzino" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{ns}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Ricambio QUARZO-913</t></is></c><c r="B1"><v>47</v></c></row><row r="2"><c r="B2"><f>SUM(B1:B1)</f></c></row></sheetData></worksheet>'})


def make_odt(path):
    zip_parts(path, {'content.xml': '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text><text:h text:outline-level="1">Orto botanico</text:h><text:p>La serra tropicale richiede il sensore FELCE-271.</text:p></office:text></office:body></office:document-content>'})


def make_epub(path):
    zip_parts(path, {
        'META-INF/container.xml': '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>',
        'OEBPS/book.opf': '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c1" href="chapter.xhtml"/></manifest><spine><itemref idref="c1"/></spine></package>',
        'OEBPS/chapter.xhtml': '<html><body><h1>Apicoltura</h1><p>Il registro delle arnie porta il codice MIELE-648.</p></body></html>'})


def mixed(root):
    root.mkdir(parents=True, exist_ok=True)
    put(root / 'manuale.md', '# Pianificazione\nPremessa da conservare.\n\n#### Eccezioni\nIl codice TORNIO-419 richiede controllo manuale.\n\n```python\n# Questo non è un titolo\n```\n')
    put(root / 'flat.md', ''.join(f'# Sezione {i}\nLa risorsa elemento{i:03d} ha identificatore SPEC-{i:03d}.\n' for i in range(200)))
    put(root / 'lungo.txt', ('geologia sedimenti erosione falda terreno minerali. ' * 300) + 'Il pozzo profondo usa il marcatore BASALTO-367. ' + ('geologia sedimenti erosione falda terreno minerali. ' * 300))
    put(root / 'src' / 'app.py', '"""Utilities for sensor calibration."""\nclass CalibrationRegistry:\n    def getCalibrationWindow(self, sensor_id):\n        """WINDOW-592: return the calibration interval in seconds."""\n        return 1800\n')
    put(root / 'web' / 'page.html', '<html><body><h1>Sentieri montani</h1><script>INVISIBLE_SCRIPT</script><p>Il rifugio usa la sigla NEVE-185.</p></body></html>')
    put(root / 'config.yaml', 'service: irrigation\nretry_limit: 8\nmarker: PIOGGIA-763\n')
    make_docx(root / 'porto.docx')
    make_pdf(root / 'observatory.pdf')
    make_pptx(root / 'isole.pptx')
    make_xlsx(root / 'stock.xlsx')
    make_odt(root / 'serra.odt')
    make_epub(root / 'api.epub')
    (root / 'legacy.doc').write_bytes(b'\xd0\xcf\x11\xe0fake legacy document')
    return root
