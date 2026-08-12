"""Reminder delivery for appointments.

This module exists to answer one design question honestly: what happens
when a clinic hasn't configured an SMS or Viber gateway (the common case
for a small private practice with no existing vendor relationship there)?

The answer here is: the reminder stays visible as "unconfigured" rather
than being silently marked "sent". A clinic relying on this feature to
actually reduce no-shows needs to know the difference between "the patient
was notified" and "nothing happened but the system didn't complain" --
the second one, mislabeled as the first, is worse than not having the
feature at all, because staff stop double-checking.

Email is the one channel that works out of the box for a real deployment
without a paid third-party account: any clinic that already has an email
address almost certainly already has SMTP credentials for it (their
existing provider, Gmail/Office365/a local mail relay), configured via
CLINIC_SMTP_* environment variables (see .env.example). SMS and Viber need
a paid gateway account (Twilio, a local telecom's SMS API, Viber Business
Messages) that this project cannot provision on a clinic's behalf --
the interface is ready; wiring in real credentials is a deployment step.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass
class DeliveryResult:
    status: str  # 'sent' | 'failed' | 'unconfigured'
    error: str | None = None


def _smtp_configured() -> bool:
    return bool(os.getenv('CLINIC_SMTP_HOST'))


def send_email(to_address: str, subject: str, body: str) -> DeliveryResult:
    if not to_address:
        return DeliveryResult('failed', 'Pacijent nema evidentiranu e-mail adresu.')
    if not _smtp_configured():
        return DeliveryResult('unconfigured', 'CLINIC_SMTP_HOST nije podešen — e-mail podsetnici zahtevaju SMTP nalog ordinacije.')
    host = os.getenv('CLINIC_SMTP_HOST')
    port = int(os.getenv('CLINIC_SMTP_PORT', '587'))
    user = os.getenv('CLINIC_SMTP_USER', '')
    password = os.getenv('CLINIC_SMTP_PASSWORD', '')
    from_addr = os.getenv('CLINIC_SMTP_FROM', user or 'ordinacija@localhost')
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = from_addr
    msg['To'] = to_address
    msg.set_content(body)
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=context)
            if user:
                server.login(user, password)
            server.send_message(msg)
        return DeliveryResult('sent')
    except (smtplib.SMTPException, OSError) as exc:
        return DeliveryResult('failed', f'Slanje e-maila nije uspelo: {exc}')


def send_sms(to_number: str, body: str) -> DeliveryResult:
    if not to_number:
        return DeliveryResult('failed', 'Pacijent nema evidentiran broj telefona.')
    if not os.getenv('CLINIC_SMS_PROVIDER_URL'):
        return DeliveryResult('unconfigured', 'SMS gejtvej nije podešen (potreban je nalog kod provajdera, npr. Twilio ili lokalni operater).')
    # A real integration point: POST to CLINIC_SMS_PROVIDER_URL with whatever
    # payload/auth the clinic's chosen SMS gateway expects. Deliberately not
    # hardcoded to one vendor -- providers and pricing for SMS in Serbia
    # vary, and picking one here would lock every deployment into it.
    return DeliveryResult('unconfigured', 'SMS integracija zahteva podešavanje odabranog provajdera (CLINIC_SMS_PROVIDER_URL je postavljen, ali slanje nije implementirano za ovog provajdera).')


def send_viber(to_number: str, body: str) -> DeliveryResult:
    if not to_number:
        return DeliveryResult('failed', 'Pacijent nema evidentiran broj telefona.')
    if not os.getenv('CLINIC_VIBER_TOKEN'):
        return DeliveryResult('unconfigured', 'Viber Business Messages API token nije podešen (CLINIC_VIBER_TOKEN).')
    return DeliveryResult('unconfigured', 'Viber integracija zahteva Viber Business nalog i dodatno podešavanje pošiljaoca.')


def dispatch(channel: str, patient_email: str | None, patient_phone: str | None, subject: str, body: str) -> DeliveryResult:
    if channel == 'email':
        return send_email(patient_email or '', subject, body)
    if channel == 'sms':
        return send_sms(patient_phone or '', body)
    if channel == 'viber':
        return send_viber(patient_phone or '', body)
    return DeliveryResult('failed', f'Nepoznat kanal podsetnika: {channel}')
