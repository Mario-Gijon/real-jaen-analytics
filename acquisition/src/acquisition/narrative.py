"""Validate narrative-comment responses without importing or persisting them."""
import json
COMMENT_PARTICIPANTS = ('player', 'playerIn', 'playerOut', 'assist1')

COMMENT_CONTEXT = ('injury', 'footballPassingNetworkAction')

class NarrativeCommentImportRejected(ValueError):
    """The saved response is not the reviewed, valid response for the local event."""

def _strict_json(text: str):
    def reject_constant(value):
        raise NarrativeCommentImportRejected(f'Constante JSON no válida: {value}')
    try:
        return json.loads(text, parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise NarrativeCommentImportRejected(f'La respuesta no es JSON UTF-8 válido: {error}') from error

def _optional_string(value, field):
    if value is None:
        return None
    if not isinstance(value, str):
        raise NarrativeCommentImportRejected(f'{field} debe ser texto o null')
    return value

def _optional_integer(value, field, *, minimum=0):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise NarrativeCommentImportRejected(f'{field} debe ser un entero válido o null')
    return value

def _comment_rows(document):
    if not isinstance(document, dict) or not isinstance(document.get('comments'), list):
        raise NarrativeCommentImportRejected('Se esperaba un objeto con una lista comments')
    seen = set()
    rows = []
    for position, item in enumerate(document['comments']):
        if not isinstance(item, dict):
            raise NarrativeCommentImportRejected(f'comments[{position}] no es un objeto')
        source_id = item.get('id')
        if isinstance(source_id, bool) or not isinstance(source_id, (int, str)) or str(source_id).strip() == '':
            raise NarrativeCommentImportRejected(f'comments[{position}].id no es un identificador válido')
        source_id = str(source_id)
        if source_id in seen:
            raise NarrativeCommentImportRejected(f'Identificador de comentario repetido: {source_id}')
        seen.add(source_id)
        text = item.get('text')
        kind = item.get('type')
        if not isinstance(text, str) or not text.strip():
            raise NarrativeCommentImportRejected(f'comments[{position}].text está vacío o no es texto')
        if not isinstance(kind, str) or not kind.strip():
            raise NarrativeCommentImportRejected(f'comments[{position}].type está vacío o no es texto')
        minute = _optional_integer(item.get('time'), f'comments[{position}].time')
        period = _optional_string(item.get('periodName'), f'comments[{position}].periodName')
        is_home = item.get('isHome')
        if is_home is not None and not isinstance(is_home, bool):
            raise NarrativeCommentImportRejected(f'comments[{position}].isHome debe ser booleano o null')
        home_score = _optional_integer(item.get('homeScore'), f'comments[{position}].homeScore')
        away_score = _optional_integer(item.get('awayScore'), f'comments[{position}].awayScore')
        incident_id = item.get('incidentId')
        if incident_id is not None and (isinstance(incident_id, bool) or not isinstance(incident_id, (int, str))):
            raise NarrativeCommentImportRejected(f'comments[{position}].incidentId no es válido')
        participants = {}
        for field in COMMENT_PARTICIPANTS:
            value = item.get(field)
            if value is not None and not isinstance(value, dict):
                raise NarrativeCommentImportRejected(f'comments[{position}].{field} debe ser objeto o null')
            if field in item:
                participants[field] = value
        context = {field: item[field] for field in COMMENT_CONTEXT if field in item}
        rows.append((source_id, position, text, kind, minute, period, is_home, home_score, away_score,
                     str(incident_id) if incident_id is not None else None,
                     json.dumps(participants, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                     json.dumps(context, ensure_ascii=False, separators=(',', ':'), allow_nan=False)))
    return rows

def validar_respuesta(response: bytes) -> int:
    """Validate the JSON shape and normalized comment fields without writing them."""
    try:
        document = _strict_json(response.decode('utf-8'))
    except UnicodeDecodeError as error:
        raise NarrativeCommentImportRejected('La respuesta no está codificada como UTF-8') from error
    return len(_comment_rows(document))
