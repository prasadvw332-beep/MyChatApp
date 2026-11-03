"""PII redaction: replace personal data with placeholders ("[EMAIL]", "[PHONE]") before it is stored
or sent to a model.

    PII_REDACTION=index   redact chunks before they are embedded and stored: the knowledge base
                          never holds the personal data (only the original file in MinIO does)
    PII_REDACTION=llm     redact the question and the retrieved context before they go to the model:
                          for a hosted model (OpenAI, Anthropic), data that must not leave the company
    PII_REDACTION=both

Two engines:
    regex     (default) patterns with checks, for the structured kinds: email, phone, card numbers
              (Luhn checksum, so 16 random digits aren't a "card"), IBAN, IP address, US SSN,
              Indian PAN and Aadhaar. Fast, no dependencies; can't find names or addresses.
    presidio  Microsoft Presidio: the same pattern recognizers plus a language model (spaCy)
              for names, locations and organisations. uv add presidio-analyzer presidio-anonymizer
              and python -m spacy download en_core_web_lg

Redaction trades recall for privacy: "Asha's phone number?" can't be answered once the number
is [PHONE]. Decide per data class which side wins.
"""

import re
from functools import cache

from app.core.config import PII_ENGINE, PII_REDACTION


def _luhn(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)][::-1]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return 13 <= len(digits) <= 19 and total % 10 == 0


# (placeholder, pattern, extra check). Order matters: cards before phones, which would match parts of them.
PATTERNS = [
    ("[EMAIL]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), None),
    ("[CARD]", re.compile(r"\b(?:\d[ -]?){13,19}\b"), _luhn),
    ("[IBAN]", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), None),
    ("[SSN]", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), None),
    ("[AADHAAR]", re.compile(r"\b\d{4} \d{4} \d{4}\b"), None),
    ("[PAN]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), None),
    ("[IP]", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), None),
    ("[PHONE]", re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d\b"), None),
]


def _regex(text: str) -> str:
    for placeholder, pattern, check in PATTERNS:
        text = pattern.sub(lambda m, p=placeholder, c=check: p if c is None or c(m.group()) else m.group(), text)
    return text


@cache
def _presidio():
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
    except ImportError as e:
        raise RuntimeError("PII_ENGINE=presidio needs: uv add presidio-analyzer presidio-anonymizer") from e
    return AnalyzerEngine(), AnonymizerEngine()  # loads the spaCy model once per process


def _presidio_redact(text: str) -> str:
    analyzer, anonymizer = _presidio()
    findings = analyzer.analyze(text=text, language="en")
    # the default operator replaces each finding with "<ENTITY_TYPE>", e.g. <PERSON>
    return anonymizer.anonymize(text=text, analyzer_results=findings).text


def redact(text: str) -> str:
    return _presidio_redact(text) if PII_ENGINE == "presidio" else _regex(text)


def at_index() -> bool:
    return PII_REDACTION in ("index", "both")


def before_llm() -> bool:
    return PII_REDACTION in ("llm", "both")
