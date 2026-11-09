"""Typed relation cues and explicit cross-references. Deterministic, no model.

A cue sentence is classified into one relation and split into a shallow
condition/consequence pair. This is pattern grammar over one sentence, not a
syntactic parse: it does not resolve pronouns, nested conditions or scope
across sentences. Every result carries the sentence it came from so a reader
can check it against the source.
"""
from __future__ import annotations

import re

# (stored kind, relation, ranking weight, pattern). First match wins, strongest claim first.
CUES = [
    ('sostituzione_versione', 'supersedes', 1.40, re.compile(
        r"\b(?:supersed\w+|repeal\w*|no longer (?:valid|applies|applicable|in force)|"
        r"(?:replaces|amends) (?:the |this |all |any )?(?:previous|earlier|prior|former|existing)|"
        r"annulla e sostituisce|sostituisc\w+ (?:il |la |le |i |gli |l')?precedent\w+|abrog\w+|"
        r"non (?:è |sono )?più (?:valid\w+|applicabil\w+|in vigore)|superat[oaie] da\w*)\b", re.I)),
    ('eccezione_deroga', 'exception', 1.35, re.compile(
        r"\b(?:except|unless|excluding|notwithstanding|not applicable|does not apply|do not apply|incompatible|"
        r"eccezion\w+|in deroga|salvo|esclus[oiae]|non si applica\w*|incompatibil\w+|fatta eccezione|tranne)\b", re.I)),
    ('ambito_applicazione', 'scope', 1.20, re.compile(
        r"\b(?:appl(?:y|ies) only to|only appl(?:y|ies) to|limited to|restricted to|exclusively for|"
        r"si applica\w* (?:solo|soltanto|esclusivamente)|limitatamente a\w*|esclusivamente per|solo per|riservat\w+ a\w*)\b", re.I)),
    ('prerequisito_vincolo', 'precondition', 1.25, re.compile(
        r"\b(?:prerequisite\w*|only if|before proceeding|provided that|requires|must first|prerequisit\w+|"
        r"prima di procedere|previa verifica|solo se|a condizione che|obbligatori\w+|richiede che|"
        r"verificare prima|accertarsi che|vincol\w+)\b", re.I)),
    ('biforcazione_caso', 'condition', 1.10, re.compile(
        r"\b(?:if|in case of|depending on|otherwise|nel caso in cui|qualora|se il|se la|in caso di|"
        r"a seconda che|se invece|laddove|distinguere se)\b", re.I)),
]
RELATION_OF = {kind: relation for kind, relation, _, _ in CUES} | {
    'appunto_volatile': 'volatile_note', 'riferimento_esplicito': 'reference'}
REFERENCE_CUE = re.compile(
    r"\b(?:see also|see|refer(?:s|ring)? to|as (?:described|defined|specified|stated|set out|provided) (?:in|by|under)|"
    r"according to|in accordance with|pursuant to|subject to|defined in|notwithstanding|"
    r"vedi anche|vedi|vedere|cfr|si veda|ai sensi d\w+|di cui a\w*|in deroga a\w*|"
    r"come (?:previsto|descritto|definito|indicato|stabilito) (?:da\w*|in|ne\w*)|"
    r"secondo quanto previsto (?:da\w*|in|ne\w*)|in conformità a\w*|fatto salvo quanto previsto (?:da\w*|in|ne\w*))\b", re.I)
MARKDOWN_LINK = re.compile(r'\[([^\]\n]{1,200})\]\(\s*<?([^)\s>#]+)(?:#[^)\s>]*)?>?\s*\)')
_NEGATION = re.compile(r"\b(?:not|no|never|cannot|without|non|mai|senza|nessun\w*)\b|n't\b", re.I)
_SPLIT = re.compile(r',\s+|\s+then\s+|\s+allora\s+', re.I)


def sentence_spans(text):
    """(start, end) character spans of sentences or lines, without altering the text."""
    position = 0
    for piece in re.split(r'((?<=[.!?])\s+|\n+)', text):
        if piece.strip() and len(piece.strip()) >= 18:
            lead = len(piece) - len(piece.lstrip())
            yield position + lead, position + len(piece.rstrip())
        position += len(piece)


def analyze(sentence):
    """Relation of one sentence, or None. Returns kind, relation, weight and a clause split."""
    for kind, relation, weight, pattern in CUES:
        match = pattern.search(sentence)
        if not match:
            continue
        before = sentence[:match.start()].strip(' ,;:—-')
        after = sentence[match.end():].strip(' ,;:.—-')
        if len(before.split()) >= 3:
            consequence, condition = before, after      # "X unless Y", "X requires Y"
        else:
            parts = _SPLIT.split(after, maxsplit=1)      # "If Y, X", "Except for Y, X"
            condition, consequence = parts[0], parts[1] if len(parts) > 1 else ''
        return {'kind': kind, 'relation': relation, 'weight': weight, 'cue': match.group(0).casefold(),
                'condition': condition[:240], 'consequence': consequence[:240],
                'negated': bool(_NEGATION.search(consequence or sentence))}
    return None
