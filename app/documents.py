"""Read explicit order conditions from local documents, retaining their source text."""
import copy
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_PAGES = 80

ALIASES = {
    '상품명': ('제품명',), '할인율': ('할인', '할인 문구'),
    '브랜드': ('브랜드명',), '딸기 개수': ('딸기 수량',),
    '행사명': ('행사 이름',), '행사 날짜': ('날짜',),
    '운영 시간': ('시간', '행사 시간'), '행사 장소': ('장소',),
    '얼리버드 가격': ('가격', '입장료'), '세트 가격': ('가격',),
    '크기별 크루아상 수량': ('크루아상 수량',),
}


def fail(code, message, status=422):
    raise HTTPException(status, detail={'code': code, 'message': message})


def _key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def extract_blocks(raw, filename):
    suffix = Path(filename or '').suffix.lower()
    if suffix not in {'.txt', '.md', '.pdf', '.docx'}:
        fail('UNSUPPORTED_DOCUMENT', 'PDF, DOCX, TXT, MD 문서를 올려 주세요.', 415)
    if not raw:
        fail('EMPTY_DOCUMENT', '빈 문서입니다.')
    if len(raw) > MAX_DOCUMENT_BYTES:
        fail('DOCUMENT_TOO_LARGE', '10MB 이하 문서를 올려 주세요.', 413)
    blocks = []
    try:
        if suffix in {'.txt', '.md'}:
            encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                text = raw.decode('cp949')
            if any(ord(c) < 32 and c not in '\r\n\t' for c in text):
                fail('INVALID_DOCUMENT', '텍스트 문서의 내용이나 인코딩을 확인하세요.')
            blocks = [{'text': line.strip(), 'location': f'{i}행'}
                      for i, line in enumerate(text.splitlines(), 1) if line.strip()]
        elif suffix == '.pdf':
            from pypdf import PdfReader
            if not raw.startswith(b'%PDF-'):
                fail('INVALID_DOCUMENT', '파일 내용이 PDF 형식이 아닙니다.')
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                fail('ENCRYPTED_DOCUMENT', '암호를 해제한 PDF를 올려 주세요.')
            if len(reader.pages) > MAX_PAGES:
                fail('DOCUMENT_TOO_LONG', '80쪽 이하 PDF를 올려 주세요.')
            for page_number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ''
                if not text.strip():
                    fail('PDF_TEXT_REQUIRED', f'{page_number}쪽에서 텍스트를 읽지 못했습니다. 스캔 PDF는 텍스트 문서로 변환해 주세요.')
                blocks.extend({'text': line.strip(), 'location': f'{page_number}쪽 · {i}행'}
                              for i, line in enumerate(text.splitlines(), 1) if line.strip())
                if sum(len(b['text']) for b in blocks) > MAX_TEXT_CHARS:
                    fail('DOCUMENT_TOO_LONG', '문서 텍스트는 20만 자 이하로 제한됩니다.')
        else:
            from docx import Document
            from docx.table import Table
            with zipfile.ZipFile(io.BytesIO(raw)) as package:
                entries = package.infolist()
                if len(entries) > 2000 or sum(e.file_size for e in entries) > 20 * 1024 * 1024:
                    fail('DOCUMENT_TOO_LARGE', 'DOCX 압축을 푼 크기는 20MB 이하로 제한됩니다.', 413)
            document = Document(io.BytesIO(raw))
            for index, item in enumerate(document.iter_inner_content(), 1):
                if isinstance(item, Table):
                    for row_number, row in enumerate(item.rows, 1):
                        text = ' : '.join(cell.text.strip() for cell in row.cells)
                        blocks.append({'text': text, 'location': f'표 {index} · {row_number}행'})
                elif item.text.strip():
                    blocks.extend({'text': line.strip(), 'location': f'{index}문단 · {i}행'}
                                  for i, line in enumerate(item.text.splitlines(), 1) if line.strip())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, detail={'code': 'INVALID_DOCUMENT',
                            'message': '문서를 읽지 못했습니다. 손상되지 않은 PDF, DOCX 또는 텍스트 문서를 올려 주세요.'}) from exc
    if not blocks:
        fail('NO_DOCUMENT_TEXT', '문서에서 텍스트를 찾지 못했습니다.')
    if sum(len(b['text']) for b in blocks) > MAX_TEXT_CHARS:
        fail('DOCUMENT_TOO_LONG', '문서 텍스트는 20만 자 이하로 제한됩니다.')
    return blocks


def parse_document(raw, filename, template):
    blocks = extract_blocks(raw, filename)
    fields = {}
    for rule in template['rules']:
        for label in (rule['name'], rule['id'], *ALIASES.get(rule['name'], ())):
            fields[_key(label)] = rule
    found, unparsed = {}, []
    for block in blocks:
        text = re.sub(r'^(?:#{1,6}\s*|[-*•]\s*|\d+[.)]\s*)', '', block['text']).strip()
        if text.startswith('|') and text.endswith('|'):
            cells = [c.strip().strip('*') for c in text.strip('|').split('|')]
            pair = cells[:2] if len(cells) >= 2 else None
        else:
            pair = re.split(r'\s*[:：=]\s*|\t+', text, maxsplit=1)
        if not pair or len(pair) != 2:
            unparsed.append(block)
            continue
        label, value = pair
        label = re.sub(r'^C\d+\s+', '', label, flags=re.I).strip('* ')
        rule = fields.get(_key(label))
        if not rule:
            unparsed.append(block)
            continue
        value = value.strip().strip('*').strip()
        if not value:
            fail('EMPTY_CONDITION', f'{block["location"]}의 {rule["name"]} 기대값이 비어 있습니다.')
        if len(value) > 2000:
            fail('CONDITION_TOO_LONG', f'{rule["name"]} 기대값은 2,000자 이하로 입력하세요.')
        if rule['id'] in found:
            fail('DUPLICATE_CONDITION', f'{rule["name"]} 조건이 여러 번 나옵니다. 하나의 기대값으로 정리해 주세요.')
        item = copy.deepcopy(rule)
        item.update(expected=value, check=f'문서에 명시된 {rule["name"]} 조건과 비교합니다.',
                    document_source=block)
        item.pop('expected_counts', None)
        # The demo strawberry brand detector searches MORNING/CUP. A document
        # must select observations by layout, independent of its expected brand.
        if template['topic_id'] == 'strawberry-latte' and rule['id'] == 'C03':
            item['inspection'] = {'kind': 'text', 'region': [0, 0, 1, .06], 'join_lines': True}
        found[rule['id']] = item
    if not found:
        names = ' / '.join(rule['name'] for rule in template['rules'])
        fail('NO_DOCUMENT_CONDITIONS', f'검사 조건을 찾지 못했습니다. 항목명: 기대값 형식으로 작성하세요. 현재 주제 항목: {names}')
    rules = [found[rule['id']] for rule in template['rules'] if rule['id'] in found]
    missing = [rule['name'] for rule in template['rules'] if rule['id'] not in found]
    digest = hashlib.sha256(raw).hexdigest()
    version = hashlib.sha256(json.dumps([template['topic_id'], digest, rules],
                                        ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    warnings = []
    if missing:
        warnings.append('문서에 없는 항목은 검사하지 않습니다: ' + ', '.join(missing))
    if unparsed:
        warnings.append(f'조건으로 읽지 못한 문구 {len(unparsed)}개를 원문에서 확인하세요. 이 문구는 자동 검사에 포함하지 않습니다.')
    return {'topic_id': template['topic_id'], 'topic_label': template['topic_label'],
            'condition_id': None, 'condition_options': [], 'can_confirm': True,
            'version': version, 'rules': rules, 'warnings': warnings,
            'document': {'name': Path(filename.replace('\\', '/') if filename else '문서').name,
                         'sha256': digest, 'bytes': len(raw), 'blocks': blocks, 'unparsed': unparsed},
            'scope': '문서에서 명시적으로 읽은 조건과 광고 이미지 비교'}


class DocumentCatalog:
    """Immutable snapshots allow multiple documents, restarts and old histories."""
    def __init__(self, runtime):
        self.folder = Path(runtime) / 'criteria-documents'

    def save(self, snapshot):
        identifier = str(uuid4())
        result = {**snapshot, 'document_id': identifier}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / (identifier + '.json')
        with path.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False)
        return result

    def load(self, identifier, topic):
        try:
            identifier = str(UUID(identifier))
        except (ValueError, TypeError, AttributeError):
            fail('DOCUMENT_NOT_FOUND', '등록된 기준 문서를 찾지 못했습니다.', 404)
        try:
            result = json.loads((self.folder / (identifier + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError:
            fail('DOCUMENT_NOT_FOUND', '등록된 기준 문서를 찾지 못했습니다.', 404)
        if result['topic_id'] != topic:
            fail('DOCUMENT_TOPIC_MISMATCH', '문서와 검사 주제가 다릅니다. 현재 주제의 문서를 다시 올려 주세요.', 409)
        return result
