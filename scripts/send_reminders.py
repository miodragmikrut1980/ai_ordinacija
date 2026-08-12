#!/usr/bin/env python3
"""Send due appointment reminders.

Usage:
    python scripts/send_reminders.py [data_dir]

What it does:
    1. Opens the clinic database directly (same pattern as backup.py /
       rotate_key.py -- this runs as a periodic job, not inside a request,
       so it talks to PersistentStore directly rather than through the
       HTTP API).
    2. Finds every reminder across every organization whose send_at has
       passed and whose status is still 'pending'.
    3. Dispatches each one through app.notifications (email works if
       CLINIC_SMTP_* is configured; SMS/Viber report 'unconfigured' until
       a gateway is wired in -- see app/notifications.py).
    4. Records the result back onto the reminder (sent / failed /
       unconfigured) so the clinic's UI reflects what actually happened.

Deployment: run this on a timer (cron or a systemd .timer unit) every few
minutes -- there is no in-process background scheduler in this app (see
deploy/clinic-ai-assistant.service, which only runs the web server).
A sample systemd timer pair is at deploy/clinic-ai-assistant-reminders.timer
and deploy/clinic-ai-assistant-reminders.service.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.notifications import dispatch as dispatch_reminder  # noqa: E402
from app.store import PersistentStore  # noqa: E402


def main():
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data"
    store = PersistentStore(data_dir)
    due = store.list_due_reminders(datetime.now(timezone.utc))
    if not due:
        print("No due reminders.")
        return

    sent = failed = unconfigured = 0
    for reminder in due:
        appointment = store.get_appointment(reminder.appointment_id)
        if not appointment or appointment.status not in ("scheduled", "checked_in"):
            # The appointment was cancelled/completed/no-show since the
            # reminder was scheduled without going through cancel_pending_reminders
            # (e.g. a status change made outside the API in a data-migration
            # scenario) -- don't notify about a visit that isn't happening.
            store.mark_reminder_result(reminder.id, "cancelled", "Termin više nije aktivan.")
            continue
        patient = store.get_patient(appointment.organization_id, appointment.patient_id)
        if not patient:
            store.mark_reminder_result(reminder.id, "failed", "Pacijent nije pronađen.")
            failed += 1
            continue
        subject = f"Podsetnik: termin {appointment.starts_at.strftime('%d.%m.%Y. u %H:%M')}"
        body = (
            f"Poštovani/a {patient.full_name},\n\n"
            f"Podsećamo vas na zakazani termin {appointment.starts_at.strftime('%d.%m.%Y. u %H:%M')}"
            f"{f' ({appointment.service_type})' if appointment.service_type else ''}.\n\n"
            "Ukoliko ne možete da dođete, molimo vas da otkažete termin unapred.\n\n"
            "Vaša ordinacija"
        )
        result = dispatch_reminder(reminder.channel, patient.email, patient.phone, subject, body)
        store.mark_reminder_result(reminder.id, result.status, result.error)
        sent += result.status == "sent"
        failed += result.status == "failed"
        unconfigured += result.status == "unconfigured"

    print(f"Reminders processed: {len(due)} (sent={sent}, failed={failed}, unconfigured={unconfigured})")
    if unconfigured:
        print("Some reminders could not be sent because their channel isn't configured yet -- see app/notifications.py and .env.example.")


if __name__ == "__main__":
    main()
