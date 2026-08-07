#!/usr/bin/env python3
"""Shared Polish name helpers for Orchesta RFQ customer-facing copy.

Deterministic by design: vocative/diminutive mapping must be correct and
stable. LLM drafts may propose a salutation; post-processors overwrite it
via these helpers.
"""

from __future__ import annotations

import re

# Formal full name -> (gender, vocative)
VOCATIVE_BY_FORMAL: dict[str, tuple[str, str]] = {
    "Adam": ("male", "Adamie"),
    "Adrian": ("male", "Adrianie"),
    "Agnieszka": ("female", "Agnieszko"),
    "Aleksander": ("male", "Aleksandrze"),
    "Aleksandra": ("female", "Aleksandro"),
    "Andrzej": ("male", "Andrzeju"),
    "Anna": ("female", "Anno"),
    "Barbara": ("female", "Barbaro"),
    "Bartosz": ("male", "Bartoszu"),
    "Damian": ("male", "Damianie"),
    "Daniel": ("male", "Danielu"),
    "Dawid": ("male", "Dawidzie"),
    "Dominik": ("male", "Dominiku"),
    "Ewa": ("female", "Ewo"),
    "Filip": ("male", "Filipie"),
    "Grzegorz": ("male", "Grzegorzu"),
    "Hubert": ("male", "Hubercie"),
    "Iga": ("female", "Igo"),
    "Igor": ("male", "Igorze"),
    "Iwona": ("female", "Iwono"),
    "Jacek": ("male", "Jacku"),
    "Jakub": ("male", "Jakubie"),
    "Jan": ("male", "Janie"),
    "Jerzy": ("male", "Jerzy"),
    "Joanna": ("female", "Joanno"),
    "Jolanta": ("female", "Jolanto"),
    "Julia": ("female", "Julio"),
    "Kamil": ("male", "Kamilu"),
    "Karol": ("male", "Karolu"),
    "Karolina": ("female", "Karolino"),
    "Katarzyna": ("female", "Katarzyno"),
    "Kazimierz": ("male", "Kazimierzu"),
    "Krystyna": ("female", "Krystyno"),
    "Krzysztof": ("male", "Krzysztofie"),
    "Kuba": ("male", "Kubo"),
    "Lech": ("male", "Lechu"),
    "Lukasz": ("male", "Łukaszu"),
    "Maciej": ("male", "Macieju"),
    "Magdalena": ("female", "Magdaleno"),
    "Małgorzata": ("female", "Małgorzato"),
    "Malgorzata": ("female", "Małgorzato"),
    "Marcin": ("male", "Marcinie"),
    "Marek": ("male", "Marku"),
    "Maria": ("female", "Mario"),
    "Mariusz": ("male", "Mariuszu"),
    "Marta": ("female", "Marto"),
    "Mateusz": ("male", "Mateuszu"),
    "Michal": ("male", "Michale"),
    "Michał": ("male", "Michale"),
    "Mieczysław": ("male", "Mieczysławie"),
    "Monika": ("female", "Moniko"),
    "Natalia": ("female", "Natalio"),
    "Olaf": ("male", "Olafie"),
    "Patryk": ("male", "Patryku"),
    "Paweł": ("male", "Pawle"),
    "Pawel": ("male", "Pawle"),
    "Piotr": ("male", "Piotrze"),
    "Rafał": ("male", "Rafale"),
    "Rafal": ("male", "Rafale"),
    "Robert": ("male", "Robercie"),
    "Ryszard": ("male", "Ryszardzie"),
    "Sebastian": ("male", "Sebastianie"),
    "Stanisław": ("male", "Stanisławie"),
    "Szymon": ("male", "Szymonie"),
    "Tomasz": ("male", "Tomaszu"),
    "Wiktor": ("male", "Wiktorze"),
    "Wojciech": ("male", "Wojciechu"),
    "Władysław": ("male", "Władysławie"),
    "Zbigniew": ("male", "Zbigniewie"),
    "Zofia": ("female", "Zofio"),
    "Łukasz": ("male", "Łukaszu"),
}

# Diminutive / informal signature -> formal name for business correspondence.
# Tomek -> Tomasz -> Tomaszu (not Tomku).
DIMINUTIVE_TO_FORMAL: dict[str, str] = {
    "Tomek": "Tomasz",
    "Tomaszek": "Tomasz",
    "Tomuś": "Tomasz",
    "Kasia": "Katarzyna",
    "Kaśka": "Katarzyna",
    "Kasiula": "Katarzyna",
    "Krysia": "Krystyna",
    "Krystka": "Krystyna",
    "Jola": "Jolanta",
    "Jolka": "Jolanta",
    "Piotrek": "Piotr",
    "Piotruś": "Piotr",
    "Jurek": "Jerzy",
    "Maciek": "Maciej",
    "Maciuś": "Maciej",
    "Zbyszek": "Zbigniew",
    "Krzysiek": "Krzysztof",
    "Krzyś": "Krzysztof",
    "Rysiek": "Ryszard",
    "Ryś": "Ryszard",
    "Marysia": "Maria",
    "Maryska": "Maria",
    "Ania": "Anna",
    "Anka": "Anna",
    "Anulka": "Anna",
    "Basia": "Barbara",
    "Baśka": "Barbara",
    "Bartek": "Bartosz",
    "Bartuś": "Bartosz",
    "Kazik": "Kazimierz",
    "Leszek": "Lech",
    "Mietek": "Mieczysław",
    "Władek": "Władysław",
    "Staś": "Stanisław",
    "Staszek": "Stanisław",
    "Stasiek": "Stanisław",
    "Grzesiek": "Grzegorz",
    "Grześ": "Grzegorz",
    "Michałek": "Michał",
    "Pawełek": "Paweł",
    "Pawelek": "Paweł",
    "Kamilek": "Kamil",
    "Adrianek": "Adrian",
}


def formal_first_name(first_name: str) -> str:
    name = re.sub(r"\s+", " ", first_name or "").strip(" ,.;:-–—")
    if not name:
        return ""
    return DIMINUTIVE_TO_FORMAL.get(name, name)


def salutation_vocative(first_name: str) -> tuple[str, str]:
    """Return (gender, vocative) for a first name. Empty gender = leave name as-is."""
    name = formal_first_name(first_name)
    if not name:
        return "", ""
    if name in VOCATIVE_BY_FORMAL:
        return VOCATIVE_BY_FORMAL[name]
    if name.endswith("a"):
        return "female", name[:-1] + "o"
    if name.endswith("ek") and len(name) > 3:
        return "male", name[:-2] + "ku"
    if name.endswith(("sz", "cz")):
        return "male", name + "u"
    if name.endswith("r"):
        return "male", name + "ze"
    if name.endswith("ł"):
        return "male", name[:-1] + "le"
    if name.endswith(("el", "ol", "ej", "ij")):
        return "male", name + "u"
    if name.endswith(("k", "g")):
        return "male", name + "u"
    return "", name


def polish_salutation(first_name: str) -> str:
    """Full greeting line, e.g. 'Dzień dobry Panie Tomaszu,'. Unknown -> 'Dzień dobry,'."""
    gender, vocative = salutation_vocative(first_name)
    if gender and vocative:
        title = "Pani" if gender == "female" else "Panie"
        return f"Dzień dobry {title} {vocative},"
    return "Dzień dobry,"


_POLISH_GREETING_LINE = re.compile(
    r"(?is)^\s*dzie[nń]\s+dobry"
    r"(?:\s*,\s*[^,\n]+|\s+(?:pani|panie)\s+[^,\n]+)?,"
    r"\s*(.*)$"
)
_INFORMAL_GREETING_LINE = re.compile(
    r"(?is)^\s*(?:cze[sś][cć]|witam|hej|siema)"
    r"(?:\s*,?\s*[^,\n]+)?,"
    r"\s*(.*)$"
)
_ENGLISH_GREETING_LINE = re.compile(
    r"(?is)^\s*(?:hello|hi|dear|good\s+morning)\b[^,\n]*,?\s*(.*)$"
)


def lowercase_sentence_start(text: str) -> str:
    """After a Polish greeting comma, the next sentence starts lowercase.

    Leaves leading ALL-CAPS tokens alone (CRM, RFQ, ...) so we do not turn
    ``CRM nie musi`` into ``cRM nie musi``.
    """
    body = (text or "").lstrip()
    if not body:
        return body
    first_word = re.match(r"^([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż0-9]+)", body)
    if first_word:
        token = first_word.group(1)
        if len(token) > 1 and token.isupper():
            return body
    first = body[0]
    if first.isupper() and first.isalpha():
        return first.lower() + body[1:]
    return body


def _remainder_after_first_line(lines: list[str], same_line_rest: str) -> str:
    rest_lines = list(lines[1:])
    while rest_lines and not rest_lines[0].strip():
        rest_lines.pop(0)
    remainder = (same_line_rest or "").strip()
    if rest_lines:
        joined = "\n".join(rest_lines).strip()
        remainder = f"{remainder}\n{joined}".strip() if remainder else joined
    return remainder


def strip_leading_greeting(body_text: str) -> tuple[str, bool]:
    """Remove a leading greeting line/prefix. Returns (remainder, had_greeting)."""
    body = (body_text or "").lstrip()
    if not body:
        return "", False
    lines = body.splitlines()
    first = lines[0].strip()
    polish = _POLISH_GREETING_LINE.match(first)
    if polish:
        return _remainder_after_first_line(lines, polish.group(1) or ""), True
    informal = _INFORMAL_GREETING_LINE.match(first)
    if informal:
        return _remainder_after_first_line(lines, informal.group(1) or ""), True
    english = _ENGLISH_GREETING_LINE.match(first)
    if english and re.match(r"(?i)^(hello|hi|dear|good\s+morning)\b", first):
        return _remainder_after_first_line(lines, english.group(1) or ""), True
    return body, False


def enforce_formal_greeting(body_text: str, first_name: str, *, english: bool = False) -> str:
    """Force formal Polish greeting and lowercase body start.

    Correct:
      Dzień dobry Panie Tomaszu,

      tak, można zacząć...

    Forbidden:
      Dzień dobry, Tomku,
      Dzień dobry, Tomasz,
    """
    remainder, had_greeting = strip_leading_greeting(body_text)
    if english:
        greeting = "Hello,"
        body = remainder if had_greeting else (body_text or "").lstrip()
        if body:
            return f"{greeting}\n\n{body}"
        return greeting

    greeting = polish_salutation(first_name) if first_name else "Dzień dobry,"
    body = remainder if had_greeting else (body_text or "").lstrip()
    # If model put greeting-less prose, still lowercase first sentence after our greeting.
    body = lowercase_sentence_start(body) if body else body
    if body:
        return f"{greeting}\n\n{body}"
    return greeting


def prefer_contact_name(parsed_first: str, parsed_last: str, deal_contact_name: str) -> tuple[str, str]:
    """Prefer registry contact_name when it looks like a person name."""
    deal = re.sub(r"\s+", " ", deal_contact_name or "").strip()
    if deal:
        parts = [p for p in re.split(r"\s+", deal) if p]
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""
        # Block obvious non-person tokens; keep short person names.
        blocked = {
            "crm", "rfq", "telegram", "orchesta", "mail", "poczta", "system",
            "skrzynki", "skrzynek", "oferta", "pakiet", "pan", "pani",
        }
        if first and first.lower() not in blocked and not any(ch.isdigit() for ch in first):
            return first, last or parsed_last
    return parsed_first, parsed_last
