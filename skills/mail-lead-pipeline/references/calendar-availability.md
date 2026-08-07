# Calendar Availability Reference

## Goal

Use Lukasz's calendars only to suggest free meeting slots in Zoho Mail drafts. Calendar data must never create, edit, delete, or reserve events in this pipeline.

## Sources

Use both sources when available:

1. Google Calendar API `freebusy`.
2. Apple Calendar on the MacBook through the Mac bridge.

The final availability is the intersection of free time after combining busy blocks from all available sources.

If one source is unavailable:
- continue with the available source,
- state the limitation in the internal briefing,
- never pretend that unavailable calendar data was checked.

If the bridge has exceeded its health TTL or both sources are unavailable, do
not propose concrete hours. The customer-facing draft asks the client to send a
convenient day/time; it must not mention that the calendar was checked.

If the Mac bridge is unavailable, pipeline runs in degraded mode:
- Google Calendar freebusy may still be used,
- Apple Calendar and Obsidian CRM are marked unavailable,
- no local CRM write is attempted.

## MacBook Apple Calendar

Preferred behavior:
- read local Apple Calendar data read-only,
- extract busy blocks only,
- do not read private event notes unless needed and explicitly approved,
- do not create, edit, or delete events,
- do not trigger Calendar invitations.

If macOS privacy/TCC blocks terminal access, ask Lukasz for the needed permission before trying again.

## Slot Policy

Default meeting proposal:
- timezone: Europe/Warsaw,
- duration: 30 minutes,
- working window: Monday-Friday, 09:00-17:00 local time,
- propose 2-3 slots,
- avoid same-day slots unless the inbound email is urgent and availability is clear.

Do not write:
- "zarezerwowalem termin",
- "termin jest zablokowany",
- "wyslalem zaproszenie".

Use:
- "Moge zaproponowac",
- "Widze nastepujace wolne okna",
- "Jesli ktorys pasuje, potwierdz prosze".
