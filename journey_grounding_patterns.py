"""Patterns and constants for selected official-draft journey grounding."""
from __future__ import annotations

import re
from typing import Final

JOURNEY_REFUND: Final[str] = "refund"
JOURNEY_APPOINTMENT: Final[str] = "appointment"
JOURNEY_CONTRACT: Final[str] = "contract_follow_up"

_SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})

_DATE_PATTERN = re.compile(
    r"(?<!\w)(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})(?!\w)"
)
_TIME_PATTERN = re.compile(r"(?<!\w)(?:(?:[01]?\d|2[0-3]):\d{2}|(?:[01]?\d|2[0-3])\.\d{2}\s*Uhr)(?!\w)", re.IGNORECASE)
_AMOUNT_PATTERN = re.compile(
    r"(?<![\w@])\d{1,9}(?:[.,]\d{1,2})?\s*(?:€|EUR|USD|CHF|GBP)(?!\w)",
    re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"(?<!\w)\d+\s*(?:"
    r"Tage?|Tagen|Wochen?|Monate?|Monaten|Stunden?|"
    r"days?|weeks?|months?|hours?|"
    r"أيام|ايام|يوم|أسابيع|اسابيع|أسبوع|اسبوع|أشهر|اشهر|شهر|ساعات|ساعة|"
    r"днів|дні|день|тижнів|тижні|тиждень|місяців|місяці|місяць|годин|години|година|"
    r"ημέρες|ημερες|ημέρα|ημερα|εβδομάδες|εβδομαδες|εβδομάδα|εβδομαδα|"
    r"μήνες|μηνες|μήνας|μηνας|ώρες|ωρες|ώρα|ωρα"
    r")(?!\w)",
    re.IGNORECASE,
)
_IDENTIFIER_PATTERN = re.compile(
    r"(?<![\w@])"
    r"(?=[A-Za-z0-9ÄÖÜäöüß._/-]{4,64}(?![\w@]))"
    r"(?=[A-Za-z0-9ÄÖÜäöüß._/-]*[A-Za-zÄÖÜäöüß])"
    r"(?=[A-Za-z0-9ÄÖÜäöüß._/-]*\d)"
    r"[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß._/-]{3,63}"
    r"(?![\w@])"
)
_COMPANY_PATTERN = re.compile(
    r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.'/-]*"
    r"(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.'/-]*){0,5}\s+"
    r"(?:GmbH|AG|UG(?:\s*\(haftungsbeschränkt\))?|KG|OHG|GbR|e\.V\.)\b"
)

_ADDRESS_PATTERN = re.compile(
    r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.'/-]{0,45}"
    r"(?:straße|strasse|str\.|weg|platz|allee|gasse|ufer|ring)\s+\d{1,5}[A-Za-z]?\b",
    re.IGNORECASE,
)

_LABELLED_VALUE_PATTERN = re.compile(
    r"(?im)^\s*("
    r"anbieter|händler|haendler|merchant|provider|organizer|organisator|veranstalter|organisation|"
    r"vertragspartner|vertragspartei|partei|empfänger|empfaenger|recipient|"
    r"ort|location|channel|kanal|adresse|address|"
    r"الجهة|الشركة|المزوّد|المزود|التاجر|المنظم|الطرف|المستلم|المكان|القناة|العنوان|"
    r"організатор|постачальник|продавець|сторона|одержувач|місце|канал|адреса|"
    r"διοργανωτής|διοργανωτης|πάροχος|παροχος|έμπορος|εμπορος|μέρος|μερος|"
    r"παραλήπτης|παραληπτης|τοποθεσία|τοποθεσια|κανάλι|καναλι|διεύθυνση|διευθυνση"
    r")\s*:\s*([^\n]{2,120})$",
    re.IGNORECASE,
)

_LOCATION_LABEL_MARKERS: Final[tuple[str, ...]] = (
    "ort", "location", "channel", "kanal", "adresse", "address",
    "المكان", "القناة", "العنوان", "місце", "канал", "адреса",
    "τοποθεσία", "τοποθεσια", "κανάλι", "καναλι", "διεύθυνση", "διευθυνση",
)

_FACT_LINE_PATTERN = re.compile(
    r"(?im)^\s*(?:"
    r"anbieter|händler|haendler|merchant|provider|organizer|organisator|veranstalter|organisation|"
    r"vertragspartner|vertragspartei|partei|empfänger|empfaenger|recipient|"
    r"ort|location|channel|kanal|adresse|address|"
    r"betrag|summe|amount|preis|kosten|monatlicher\s+preis|recurring\s+cost|kaufdatum|transaktionsdatum|transaction\s+date|vertragsdatum|contract\s+date|datum|date|"
    r"uhrzeit|time|termin|bisheriger\s+termin|alter\s+termin|neuer\s+wunschtermin|neuer\s+termin|appointment|current\s+appointment|requested\s+appointment|"
    r"bestellnummer|auftragsnummer|referenz|reference|vertragsnummer|kundennummer|aktenzeichen|"
    r"الجهة|الشركة|المزوّد|المزود|التاجر|المنظم|الطرف|المستلم|المكان|القناة|العنوان|"
    r"المبلغ|التاريخ|تاريخ\s+الشراء|الوقت|الموعد|رقم\s+الطلب|رقم\s+العقد|المرجع|"
    r"організатор|постачальник|продавець|сторона|одержувач|місце|канал|адреса|"
    r"сума|дата|час|номер\s+замовлення|номер\s+договору|посилання|"
    r"διοργανωτής|διοργανωτης|πάροχος|παροχος|έμπορος|εμπορος|μέρος|μερος|"
    r"παραλήπτης|παραληπτης|τοποθεσία|τοποθεσια|κανάλι|καναλι|διεύθυνση|διευθυνση|"
    r"ποσό|ποσο|ημερομηνία|ημερομηνια|ώρα|ωρα|αριθμός\s+παραγγελίας|αριθμος\s+παραγγελιας|"
    r"αριθμός\s+σύμβασης|αριθμος\s+συμβασης|αναφορά|αναφορα"
    r")\s*:\s*[^\n]{1,160}$",
    re.IGNORECASE,
)

_NEGATION_PATTERN = re.compile(
    r"(?:\b(?:nicht|kein|keine|keinen|ohne|do\s+not|don't|without|not)\b|"
    r"\b(?:لا|بدون|ليس)\b|مو\s+لازم|"
    r"\b(?:не|без)\b|"
    r"\b(?:χωρίς|μην|δεν)\b)",
    re.IGNORECASE,
)

_REFUND_PATTERNS = (
    re.compile(r"(?:rückerstatt|rueckerstatt|erstattung|rückzahl|rueckzahl|geld\s+zurück|geld\s+zurueck)", re.IGNORECASE),
    re.compile(r"(?:استرداد|إرجاع\s+المبلغ|ارجاع\s+المبلغ|تعويض|رجع(?:وا|ولي|لي)\s+المبلغ)", re.IGNORECASE),
    re.compile(r"\b(?:refund|reimbursement|money\s+back|repayment)\b", re.IGNORECASE),
    re.compile(r"(?:поверненн.*кошт|відшкодуван|гроші\s+назад)", re.IGNORECASE),
    re.compile(r"(?:επιστροφή\s+χρημάτων|επιστροφη\s+χρηματων|αποζημίω|αποζημιω)", re.IGNORECASE),
)
_APPOINTMENT_PATTERNS = (
    re.compile(r"(?:\btermin\b|terminverschieb|terminabsag|umbuch|sprechstunde)", re.IGNORECASE),
    re.compile(r"(?:موعد|تأجيل\s+الموعد|تغيير\s+الموعد|إلغاء\s+الموعد|الغاء\s+الموعد)", re.IGNORECASE),
    re.compile(r"\b(?:appointment|reschedule|booking|meeting)\b", re.IGNORECASE),
    re.compile(r"(?:зустріч|прийом|перенес.*зустріч|скасув.*зустріч)", re.IGNORECASE),
    re.compile(r"(?:ραντεβού|ραντεβου|μεταφορ.*ραντεβού|ακύρω.*ραντεβού)", re.IGNORECASE),
)
_CONTRACT_PATTERNS = (
    re.compile(r"(?:\bvertrag\b|vertragsnummer|vertragsklausel|klausel|laufzeit|verlängerung|verlaengerung)", re.IGNORECASE),
    re.compile(r"(?:عقد|رقم\s+العقد|بند|مدة\s+العقد|تجديد\s+العقد)", re.IGNORECASE),
    re.compile(r"\b(?:contract|agreement|clause|renewal|contractual)\b", re.IGNORECASE),
    re.compile(r"(?:договір|договор|угода|пункт\s+договор|продовжен.*договор)", re.IGNORECASE),
    re.compile(r"(?:σύμβαση|συμβαση|ρήτρα|ρητρα|ανανέωσ.*σύμβασ|ανανεωσ.*συμβασ)", re.IGNORECASE),
)
_CANCELLATION_PATTERNS = (
    re.compile(r"(?:kündig|kuendig)", re.IGNORECASE),
    re.compile(r"(?:إلغاء|الغاء|ألغي|الغي|فسخ)", re.IGNORECASE),
    re.compile(r"\b(?:cancel|cancellation|terminate|termination)\b", re.IGNORECASE),
    re.compile(r"(?:скасув|розірв|припинен)", re.IGNORECASE),
    re.compile(r"(?:ακύρ|ακυρ|καταγγελ|τερματισ)", re.IGNORECASE),
)

_CANCELLATION_ACTION_PATTERNS = (
    re.compile(r"(?:hiermit\s+k[üu]ndige|vertrag\s+k[üu]ndigen|k[üu]ndigung\s+zum|zum\s+nächstmöglichen\s+zeitpunkt)", re.IGNORECASE),
    re.compile(r"(?:إلغاء\s+العقد|الغاء\s+العقد|ألغي\s+العقد|الغي\s+العقد|فسخ\s+العقد)", re.IGNORECASE),
    re.compile(r"(?:cancel|terminate)\s+(?:my|the)\s+(?:contract|subscription|membership)", re.IGNORECASE),
    re.compile(r"(?:розірвати|скасувати)\s+(?:договір|підписку)", re.IGNORECASE),
    re.compile(r"(?:καταγγελία|τερματισμός|ακύρωση)\s+(?:σύμβασης|συμβασης|συνδρομής|συνδρομης)", re.IGNORECASE),
)

_REFUND_PROBLEM_PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "undelivered": (
        re.compile(r"(?:nicht\s+(?:angekommen|geliefert|erhalten)|lieferung\s+blieb\s+aus|leistung\s+nicht\s+erbracht)", re.IGNORECASE),
        re.compile(r"(?:لم\s+يصل|ما\s+وصل|لم\s+يتم\s+التسليم|الخدمة\s+لم\s+تُقد[َّ]?م|الخدمة\s+لم\s+تقدم)", re.IGNORECASE),
        re.compile(r"(?:not\s+(?:delivered|received)|did\s+not\s+arrive|service\s+was\s+not\s+provided)", re.IGNORECASE),
        re.compile(r"(?:не\s+достав|не\s+отрим|послуг.*не\s+надан)", re.IGNORECASE),
        re.compile(r"(?:δεν\s+παραδόθ|δεν\s+παραδοθ|δεν\s+έφτασ|δεν\s+εφτασ|δεν\s+ελήφθη|δεν\s+εληφθη|υπηρεσία.*δεν\s+παρασχ)", re.IGNORECASE),
    ),
    "duplicate_charge": (
        re.compile(r"(?:doppelt\s+(?:abgebucht|berechnet)|doppelte\s+(?:abbuchung|belastung|buchung))", re.IGNORECASE),
        re.compile(r"(?:خصم\s+مكرر|سحب\s+مرتين|دفع\s+مكرر|تم\s+الخصم\s+مرتين)", re.IGNORECASE),
        re.compile(r"(?:duplicate\s+(?:charge|payment)|charged\s+twice)", re.IGNORECASE),
        re.compile(r"(?:подвійн.*списан|списал.*двічі|подвійн.*платіж)", re.IGNORECASE),
        re.compile(r"(?:διπλή\s+χρέωση|διπλη\s+χρεωση|χρεώθηκ.*δύο\s+φορές|χρεωθηκ.*δυο\s+φορες)", re.IGNORECASE),
    ),
    "incorrect_charge": (
        re.compile(r"(?:falsch(?:er|en)\s+betrag|falsch\s+berechnet|unrichtige\s+belastung)", re.IGNORECASE),
        re.compile(r"(?:مبلغ\s+خاطئ|خصم\s+خاطئ|تم\s+احتساب\s+مبلغ\s+غير\s+صحيح)", re.IGNORECASE),
        re.compile(r"(?:incorrect\s+amount|wrong\s+charge|charged\s+the\s+wrong\s+amount)", re.IGNORECASE),
        re.compile(r"(?:неправильн.*сум|помилков.*списан)", re.IGNORECASE),
        re.compile(r"(?:λανθασμένο\s+ποσό|λανθασμενο\s+ποσο|εσφαλμένη\s+χρέωση|εσφαλμενη\s+χρεωση)", re.IGNORECASE),
    ),
    "cancelled_service": (
        re.compile(r"(?:bestellung|leistung|reise|veranstaltung|dienstleistung).{0,35}(?:storniert|abgesagt)", re.IGNORECASE),
        re.compile(r"(?:تم\s+إلغاء|تم\s+الغاء).{0,35}(?:الطلب|الخدمة|الرحلة|الفعالية)", re.IGNORECASE),
        re.compile(r"(?:cancelled|canceled).{0,35}(?:order|service|trip|event)", re.IGNORECASE),
        re.compile(r"(?:скасован).{0,35}(?:замовлен|послуг|поїзд|захід)", re.IGNORECASE),
        re.compile(r"(?:ακυρώθ|ακυρωθ).{0,35}(?:παραγγελία|παραγγελια|υπηρεσία|υπηρεσια|ταξίδι|ταξιδι|εκδήλωση|εκδηλωση)", re.IGNORECASE),
    ),
    "defective": (
        re.compile(r"(?:defekt|beschädigt|beschaedigt|mangelhaft)", re.IGNORECASE),
        re.compile(r"(?:تالف|معطّل|معطل|فيه\s+عيب|معيب)", re.IGNORECASE),
        re.compile(r"(?:defective|damaged|faulty)", re.IGNORECASE),
        re.compile(r"(?:дефект|пошкоджен|несправн)", re.IGNORECASE),
        re.compile(r"(?:ελαττωματικ|κατεστραμμ|χαλασμέν|χαλασμεν)", re.IGNORECASE),
    ),
}

_APPOINTMENT_ACTION_PATTERNS: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "reschedule": (
        re.compile(r"(?:termin.{0,30}(?:verschieb|verleg|umbuch)|(?:verschieb|verleg|umbuch).{0,30}termin)", re.IGNORECASE),
        re.compile(r"(?:تأجيل\s+الموعد|تغيير\s+الموعد|نقل\s+الموعد)", re.IGNORECASE),
        re.compile(r"(?:reschedul|move\s+the\s+appointment|change\s+the\s+appointment)", re.IGNORECASE),
        re.compile(r"(?:перенес.*(?:зустріч|прийом)|змін.*дат.*(?:зустріч|прийом))", re.IGNORECASE),
        re.compile(r"(?:μεταφορ.*ραντεβού|μεταφορ.*ραντεβου|αλλαγ.*ραντεβού|αλλαγ.*ραντεβου)", re.IGNORECASE),
    ),
    "cancel": (
        re.compile(r"(?:termin.{0,25}(?:absag|stornier)|(?:absag|stornier).{0,25}termin)", re.IGNORECASE),
        re.compile(r"(?:إلغاء\s+الموعد|الغاء\s+الموعد)", re.IGNORECASE),
        re.compile(r"(?:cancel\s+the\s+appointment|appointment\s+cancell)", re.IGNORECASE),
        re.compile(r"(?:скасув.*(?:зустріч|прийом))", re.IGNORECASE),
        re.compile(r"(?:ακύρω.*ραντεβού|ακυρω.*ραντεβου)", re.IGNORECASE),
    ),
    "confirm": (
        re.compile(r"(?:termin.{0,25}(?:bestätig|bestaetig)|(?:bestätig|bestaetig).{0,25}termin)", re.IGNORECASE),
        re.compile(r"(?:تأكيد\s+الموعد|تثبيت\s+الموعد)", re.IGNORECASE),
        re.compile(r"(?:confirm\s+the\s+appointment|appointment\s+confirmation)", re.IGNORECASE),
        re.compile(r"(?:підтверд.*(?:зустріч|прийом))", re.IGNORECASE),
        re.compile(r"(?:επιβεβαι.*ραντεβού|επιβεβαι.*ραντεβου)", re.IGNORECASE),
    ),
}

_CONTRACT_UNCERTAINTY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:nicht\s+bekannt|unklar|keine\s+angabe|weiß\s+nicht|weiss\s+nicht).{0,80}(?:laufzeit|frist|verlänger|verlaenger|gebühr|gebuehr|kosten|klausel)", re.IGNORECASE),
    re.compile(r"(?:لا\s+أعرف|لا\s+اعرف|غير\s+معروف|غير\s+واضح|غير\s+مذكور).{0,80}(?:المدة|المهلة|التجديد|الرسوم|البند)", re.IGNORECASE),
    re.compile(r"(?:do\s+not\s+know|don't\s+know|unknown|unclear|not\s+stated).{0,80}(?:term|period|renewal|fee|cost|clause)", re.IGNORECASE),
    re.compile(r"(?:не\s+знаю|невідом|неясн|не\s+вказан).{0,80}(?:строк|період|продовжен|плат|пункт)", re.IGNORECASE),
    re.compile(r"(?:δεν\s+γνωρίζ|άγνωστ|αγνωστ|ασαφ|δεν\s+αναφέρετ).{0,80}(?:διάρκεια|διαρκεια|προθεσμ|ανανέωσ|ανανεωσ|χρέωσ|χρεωσ|ρήτρα|ρητρα)", re.IGNORECASE),
)
_CONTRACT_CLARIFICATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:bitte\s+(?:teilen\s+sie\s+mir\s+mit|erläutern|erlaeutern|klären|klaeren|bestätigen|bestaetigen)|"
    r"ich\s+bitte\s+um\s+(?:auskunft|klärung|klaerung|erläuterung|erlaeuterung)|"
    r"\?|يرجى\s+(?:التوضيح|إبلاغي|تأكيد)|أطلب\s+توضيح|اطلب\s+توضيح|"
    r"please\s+(?:clarify|explain|confirm|tell\s+me)|i\s+request\s+(?:clarification|confirmation)|"
    r"будь\s+ласка.{0,20}(?:уточніть|поясніть|підтвердьте)|"
    r"παρακαλώ.{0,20}(?:διευκρινίστε|διευκρινιστε|εξηγήστε|εξηγηστε|επιβεβαιώστε|επιβεβαιωστε))",
    re.IGNORECASE,
)
_CONTRACT_FOLLOWUP_PATTERNS = (
    re.compile(r"(?:klär|klaer|rückfrag|rueckfrag|auskunft|schriftlich\s+bestätig|schriftlich\s+bestaetig|mitteilen|frage)", re.IGNORECASE),
    re.compile(r"(?:توضيح|استفسار|سؤال|تأكيد\s+كتابي|شرح\s+البند)", re.IGNORECASE),
    re.compile(r"\b(?:clarif|question|explain|written\s+confirmation|information)\b", re.IGNORECASE),
    re.compile(r"(?:уточнен|пояснен|питання|письмов.*підтвердж)", re.IGNORECASE),
    re.compile(r"(?:διευκρίν|διευκριν|ερώτη|ερωτη|γραπτή\s+επιβεβαί|γραπτη\s+επιβεβαι)", re.IGNORECASE),
)

_REFUND_UNSUPPORTED = (
    ("refund-success", re.compile(
        r"(?:rückerstattung|erstattung|rückzahlung|refund|reimbursement|استرداد|تعويض|поверненн|επιστροφή|επιστροφη)"
        r".{0,45}(?:wurde|ist|has\s+been|تم|було|έχει|εχει)"
        r".{0,35}(?:genehmigt|bewilligt|bestätigt|bestaetigt|überwiesen|ueberwiesen|erfolgt|"
        r"approved|accepted|paid|processed|الموافقة|قبول|تحويل|دفع|схвален|виплачен|εγκριθ|καταβληθ)",
        re.IGNORECASE,
    )),
    ("refund-guarantee", re.compile(
        r"(?:garantiert|garantie|gewährleistet|gewaehrleistet|sicher\s+zurück|"
        r"guaranteed|definitely\s+(?:get|receive)|مضمون|مؤكد\s+أنك\s+ستحصل|"
        r"гарантован|εγγυημέν|εγγυημεν)",
        re.IGNORECASE,
    )),
    ("refund-legal-entitlement", re.compile(
        r"(?:mir\s+steht\s+.*\s+zu|ich\s+habe\s+einen\s+rechtsanspruch|sie\s+sind\s+gesetzlich\s+verpflichtet|"
        r"i\s+am\s+legally\s+entitled|you\s+are\s+legally\s+required|"
        r"من\s+حقي\s+قانونًا|ملزمون\s+قانونًا|"
        r"я\s+маю\s+законне\s+право|ви\s+зобов.?язані\s+законом|"
        r"έχω\s+νομικό\s+δικαίωμα|εχω\s+νομικο\s+δικαιωμα|είστε\s+νομικά\s+υποχρεωμένοι)",
        re.IGNORECASE,
    )),
    ("refund-threat", re.compile(
        r"(?:andernfalls|sonst).{0,60}(?:anwalt|gericht|anzeige|mahnbescheid)|"
        r"(?:otherwise).{0,60}(?:lawyer|court|police|legal\s+action)|"
        r"(?:وإلا|والا).{0,60}(?:محام|محكمة|شرطة|إجراء\s+قانوني)",
        re.IGNORECASE,
    )),
    ("refund-deadline", re.compile(
        r"(?:unverzüglich|unverzueglich|innerhalb\s+der\s+gesetzlichen\s+frist|"
        r"immediately\s+as\s+required\s+by\s+law|within\s+the\s+statutory\s+period|"
        r"فورًا\s+بموجب\s+القانون|خلال\s+المهلة\s+القانونية)",
        re.IGNORECASE,
    )),
)
_APPOINTMENT_UNSUPPORTED = (
    ("appointment-completed", re.compile(
        r"(?:"
        r"(?:der\s+termin|ihr\s+termin|the\s+appointment|موعد(?:ك|ي)?|зустріч|ραντεβού|ραντεβου)"
        r".{0,35}(?:ist|wurde|has\s+been|تم|було|έχει|εχει)"
        r".{0,30}(?:gebucht|bestätigt|bestaetigt|verschoben|verlegt|abgesagt|storniert|"
        r"booked|confirmed|rescheduled|moved|cancelled|canceled|"
        r"حجز|تأكيد|تأجيل|تغيير|إلغاء|الغاء|"
        r"заброньован|підтверджен|перенесен|скасован|"
        r"κλειστ|επιβεβαιωθ|μεταφέρθ|μεταφερθ|ακυρωθ)"
        r"|"
        r"(?:der\s+termin|ihr\s+termin|the\s+appointment)"
        r".{0,35}(?:bereits\s+)?(?:gebucht|bestätigt|bestaetigt|verschoben|verlegt|abgesagt|storniert|"
        r"booked|confirmed|rescheduled|moved|cancelled|canceled)"
        r".{0,12}(?:ist|wurde|has\s+been)"
        r")",
        re.IGNORECASE,
    )),
    ("appointment-guarantee", re.compile(
        r"(?:der\s+termin\s+ist\s+garantiert|appointment\s+is\s+guaranteed|"
        r"الموعد\s+مضمون|зустріч\s+гарантован|ραντεβού\s+είναι\s+εγγυημέν)",
        re.IGNORECASE,
    )),
)
_CONTRACT_UNSUPPORTED = (
    ("contract-validity", re.compile(
        r"(?:der\s+vertrag|die\s+klausel|the\s+contract|the\s+clause|العقد|البند|договір|пункт|σύμβαση|συμβαση|ρήτρα|ρητρα)"
        r".{0,40}(?:ist|is|يعتبر|هو|є|είναι|ειναι)"
        r".{0,25}(?:rechtswirksam|unwirksam|gültig|gueltig|ungültig|ungueltig|"
        r"durchsetzbar|nicht\s+durchsetzbar|rechtswidrig|"
        r"valid|invalid|enforceable|unenforceable|illegal|"
        r"صحيح\s+قانونًا|باطل|ملزم\s+قانونًا|غير\s+قابل\s+للتنفيذ|"
        r"чинним|недійсним|обов.?язковим|"
        r"έγκυρ|εγκυρ|άκυρ|ακυρ|εκτελεστ)",
        re.IGNORECASE,
    )),
    ("contract-term-assertion", re.compile(
        r"(?:kündigungsfrist\s+beträgt|kuendigungsfrist\s+betraegt|"
        r"vertrag\s+verlängert\s+sich|vertrag\s+verlaengert\s+sich|"
        r"eine\s+gebühr\s+fällt\s+an|eine\s+gebuehr\s+faellt\s+an|"
        r"notice\s+period\s+is|contract\s+renews|a\s+fee\s+applies|"
        r"مدة\s+الإلغاء\s+هي|العقد\s+يتجدد|تُفرض\s+رسوم|تفرض\s+رسوم)",
        re.IGNORECASE,
    )),
    ("contract-legal-entitlement", re.compile(
        r"(?:mir\s+steht\s+.*\s+zu|ich\s+habe\s+einen\s+rechtsanspruch|sie\s+sind\s+rechtlich\s+verpflichtet|"
        r"i\s+am\s+legally\s+entitled|you\s+are\s+legally\s+required|"
        r"من\s+حقي\s+قانونًا|ملزمون\s+قانونًا)",
        re.IGNORECASE,
    )),
)

_REVISION_CHANGE_PATTERN = re.compile(
    r"(?:عد[ّلل]|غي[ّرر]|بد[ّلل]|صح[ّحح]|احذف|أزل|ازل|"
    r"änder|aender|korrigier|entfern|lösch|loesch|ersetz|"
    r"change|correct|replace|remove|delete|"
    r"зміни|виправ|заміни|видали|"
    r"άλλαξε|διορθ|αντικατάστησε|αντικαταστησε|αφαίρεσε)",
    re.IGNORECASE,
)
_REVISION_TYPE_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "amount": re.compile(r"(?:betrag|summe|preis|kosten|amount|price|مبلغ|سعر|сума|ціна|ποσό|ποσο|τιμή|τιμη)", re.IGNORECASE),
    "date": re.compile(r"(?:datum|date|تاريخ|дата|ημερομην)", re.IGNORECASE),
    "time": re.compile(r"(?:uhrzeit|time|وقت|час|ώρα|ωρα)", re.IGNORECASE),
    "duration": re.compile(r"(?:frist|dauer|period|مدة|مهلة|строк|період|προθεσμ|διάρκεια|διαρκεια)", re.IGNORECASE),
    "id": re.compile(r"(?:nummer|referenz|reference|رقم|مرجع|номер|посилання|αριθμ|αναφορά|αναφορα)", re.IGNORECASE),
    "name": re.compile(r"(?:anbieter|firma|organisation|empfänger|empfaenger|provider|company|recipient|"
                       r"الجهة|الشركة|المستلم|організатор|постачальник|одержувач|πάροχος|παροχος|παραλήπτης|παραληπτης)", re.IGNORECASE),
    "address": re.compile(r"(?:adresse|anschrift|ort|location|address|العنوان|المكان|адрес|місце|διεύθυν|τοποθεσία|τοποθεσια)", re.IGNORECASE),
}
