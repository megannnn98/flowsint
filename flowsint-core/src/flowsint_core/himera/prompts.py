"""Prompts of the two LLM passes. Kept in one place so they can be reviewed."""

import json
from typing import Any, Dict, List

RECORDS_SYSTEM = """You segment Himera Search person-check reports (Russian PDF) into records.
You do NOT interpret the data, you only cut the document into records and copy values verbatim.

Report layout: "Отчет № <id>" line; sections УСТАНОВОЧНЫЕ ДАННЫЕ (summary), Результаты скоринга (scoring),
Метки (labels), Доходы (income), Адреса (addresses); then many detailed dataset records; then service footer.
A detailed record = a heading line in capitals naming the dataset, often with period and a short code
(e.g. "КЛИЕНТЫ БАНКА 2023 QV"; the heading can wrap over 2-3 lines) followed by "field  value" pairs.

Rules:
1. One output record per record in the document. Each summary/scoring/income/addresses section is one record;
   each label block inside Метки is its own record; each income row group may stay in one income record.
2. Two consecutive records with the same heading (even with identical values) are TWO records. Never merge them.
3. A record may continue on the next page without repeating the heading: it is still ONE record, page_end is the
   page where it ends.
4. Page numbers: use the numbering explained in the user message. ordinal_on_start_page = 1-based position of
   the record among records that START on page_start (a continuation from a previous page does not count).
5. starts_before_range = true if the first thing on the first page you received is the continuation of a record
   whose heading is on an earlier page you did not receive. Output that fragment as a record with the heading
   "<continuation>" and the fields you see.
6. continues_after_range = true if the record is cut by the end of what you received and may continue after it.
7. Copy field names and values EXACTLY as printed (keep case, odd characters, Cyrillic/Latin mix, spaces in
   numbers, broken quotes). Do not correct, translate, normalise or complete anything. Skip empty values.
   For tables (income) use field names like "Место работы", "Сумма", "Дата актуальности" per row.
8. name_match_only = true when the record belongs to a section marked "совпадения по фио" (matched by full name
   only) or otherwise states it is a name-only match.
9. source_name: the dataset name from the heading without period/code; source_period: the year/period from the
   heading if printed, else null.
10. report_id: the id after "Отчет №" if visible in what you received, else null. truncation_notice = true if the footer says
   the report may be truncated.
Return JSON only."""


def records_text_prompt(
    first_page: int, last_page: int, total_pages: int, text: str
) -> str:
    return (
        f"Below is the text of pages {first_page}-{last_page} of a {total_pages}-page report, "
        "extracted with the column layout kept. Each page starts with a line "
        "'=== PAGE N ===' where N is the report page: use these N as page_start/page_end. "
        "Extract all records.\n\n" + text
    )


def records_user_prompt(first_page: int, last_page: int, total_pages: int) -> str:
    return (
        f"The attached PDF contains pages {first_page}-{last_page} of a {total_pages}-page "
        f"report. Page 1 of the attachment is page {first_page} of the report, but report "
        f"pages in your answer must be numbered relative to the attachment (1.."
        f"{last_page - first_page + 1}). Extract all records."
    )


ENTITIES_SYSTEM = """You extract entities and relationships from records of a Himera Search report.
Input: the subject summary and a list of records (record_id, section, heading, pages, fields).

Entity types: Individual (full name; attributes birth_date, alt_surnames), Phone, Email, Username,
SocialAccount (VK/Telegram/other profiles; attributes platform, profile_url, user_id), Organization
(attributes inn, ogrn), Address (attributes city, zip, country only if printed), Document (passport, driver licence, vehicle plate ...; attributes doc_type,
issued_by, issue_date), Identifier (ИНН, СНИЛС, ОГРН, ИИН, bank card, bank account; attribute scheme =
inn|snils|ogrn|iin|bank_card|bank_account|other), Event (dated fact or source claim: label, score, departure,
registration, criminal record entry ...; attributes date, claim).

Rules:
1. One entity per value per record (the same phone in two records = two entities with different record_id).
   entity_id: short unique id inside your answer (e1, e2 ...).
2. raw_value: exactly as in the record. normalized_value: your suggestion (phone in E.164 like +79990000000,
   e-mail lower-case with look-alike Cyrillic letters replaced by Latin, dates as YYYY-MM-DD, names in upper case)
   or null if unsure. Never invent missing digits/characters.
3. evidence: a verbatim fragment of the record (field name + value) that contains raw_value.
4. page: the report page where the value is printed (within the record's page range).
5. Empty, placeholder or masked values ("-", "нет", "не указано", "***") are NOT entities.
6. A Username can be derived from an e-mail local part or a profile handle only if it looks like a nickname;
   set confidence <= 0.6 and role "derived_from_email" for derived ones.
7. subject_link for Individual: "subject" only if the record's name AND (birth date or a document/identifier of
   the subject summary) match the subject; "other_person" when other documents/ids/birth date are present; "unknown"
   when there is not enough data. Records with name_match_only=true are never "subject".
8. Phone role: personal | work | additional | related | unknown (from field names like "Телефон",
   "Рабочий телефон", "Дополнительные телефоны").
9. Relationships only when the record itself links the two entities (same record, about the same person or
   organisation). Never link entities only because they are in the same report. Types:
   HAS_PHONE, HAS_EMAIL, HAS_USERNAME, HAS_SOCIAL_ACCOUNT, HAS_DOCUMENT, HAS_IDENTIFIER, WORKED_AT,
   ASSOCIATED_WITH_ADDRESS, HAS_ALIAS, SUBJECT_OF_CLAIM (Individual -> Event), ORGANIZATION_HAS_IDENTIFIER,
   SOCIAL_ACCOUNT_USES_USERNAME, EMAIL_DERIVED_USERNAME.
   status: "confirmed" if the record states the link directly and is not a name-only match; "candidate" if the
   person identity is uncertain (name_match_only, subject_link unknown/other_person); "source_claim" for labels,
   scores, criminal/black-list entries and other assessments - these are claims of the source, not facts.
10. Both relationship ends and record_id must refer to entities/records of this answer/input.
Return JSON only."""


def entities_user_prompt(subject: Dict[str, Any], records: List[Dict[str, Any]]) -> str:
    return (
        "Subject summary (from УСТАНОВОЧНЫЕ ДАННЫЕ):\n"
        + json.dumps(subject, ensure_ascii=False)
        + "\n\nRecords:\n"
        + json.dumps(records, ensure_ascii=False)
    )


REPAIR_SYSTEM = """You fix JSON syntax/structure. Return the same data as valid JSON matching the schema.
Do not add, remove or change any values. If the input is cut off, drop the last incomplete item."""


def repair_user_prompt(broken: str, error: str, schema: Dict[str, Any]) -> str:
    return (
        f"Validation error: {error}\n\nSchema:\n{json.dumps(schema)}\n\n"
        f"Broken output:\n{broken}"
    )
