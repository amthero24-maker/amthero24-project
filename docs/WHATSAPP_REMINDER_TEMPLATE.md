# AmtHero24 WhatsApp reminder template

Long-term reminders require an approved WhatsApp Utility template whenever the
recipient's 24-hour customer-service window is closed. The runtime uses one
template name with five language variants:

- Name: `amthero24_reminder_v1`
- Category: `UTILITY`
- Languages: `ar`, `de`, `en_US`, `uk`, `el`
- Body parameters: first name, reminder title, local reminder date
- No marketing text, header, footer, URL, or button

## Safe lifecycle

The management command is read-only by default and emits no credentials or raw
provider response bodies:

```bash
python scripts/manage_reminder_template.py
```

Submit only missing language variants:

```bash
python scripts/manage_reminder_template.py --apply
```

Wait for every variant to become `APPROVED`:

```bash
python scripts/manage_reminder_template.py --require-approved
```

Only after all five variants are approved, set:

```text
WHATSAPP_REMINDER_TEMPLATE=amthero24_reminder_v1
```

Changing this variable redeploys the bot. Verify `/ready` reports
`reminder_template=configured`, then perform one controlled reminder outside the
24-hour window. Keep `REMINDER_CANARY_SENDERS` restricted during certification.

Never enable the template variable while a variant is pending or rejected. Meta
template review and quality status remain external launch gates.
