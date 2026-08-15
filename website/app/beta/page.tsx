import Link from "next/link";

const notices = [
  {
    lang: "Deutsch",
    dir: "ltr",
    title: "AmtHero24 Closed Beta",
    paragraphs: [
      "Du nimmst an einer kleinen, kontrollierten Testphase von AmtHero24 mit Sam teil. Sam unterstützt dich bei Alltag und Verwaltung in Deutschland, zum Beispiel beim Verstehen von Briefen, Formulieren von Schreiben, Organisieren von Aufgaben, Terminen und Erinnerungen.",
      "Wichtig: Sam ist keine Behörde, kein Anwalt und kein Arzt und ersetzt keine professionelle Beratung. KI- und Dokumentauswertungen können Fehler enthalten. Prüfe wichtige Angaben, Fristen und Entwürfe, bevor du danach handelst.",
      "Die Teilnahme an der Beta und die Einwilligung in wiederverwendbare persönliche Erinnerungen sind getrennte Entscheidungen. Die vorgesehenen Export- und Löschfunktionen bleiben verfügbar. Dokumente und Audio werden nach dem umgesetzten Datenschutzkonzept zur Verarbeitung verwendet und nicht dauerhaft als Rohdateien gespeichert.",
      "Bei besonders sensiblen oder riskanten Fällen kann Sam dich an eine geeignete professionelle oder offizielle Stelle verweisen. Die Teilnahme ist freiwillig. Du kannst die Closed Beta jederzeit verlassen.",
    ],
    question: "Möchtest du an der Closed Beta teilnehmen? Bitte antworte eindeutig mit Ja oder Nein.",
  },
  {
    lang: "العربية",
    dir: "rtl",
    title: "النسخة التجريبية المغلقة من AmtHero24",
    paragraphs: [
      "أنت مدعو للمشاركة في مرحلة تجريبية صغيرة ومحكومة من AmtHero24 مع Sam. يساعدك Sam في أمور الحياة اليومية والمعاملات في ألمانيا، مثل فهم الرسائل، صياغة المراسلات، وتنظيم المهام والمواعيد والتذكيرات.",
      "مهم: Sam ليس جهة حكومية ولا محاميًا ولا طبيبًا، ولا يستبدل الاستشارة المهنية. يمكن أن تخطئ أنظمة الذكاء الاصطناعي أو تحليل المستندات، لذلك راجع المعلومات المهمة والمواعيد والنصوص قبل اتخاذ إجراء.",
      "الموافقة على المشاركة في النسخة التجريبية منفصلة عن الموافقة على الذاكرة الشخصية القابلة لإعادة الاستخدام. تبقى وظائف التصدير والحذف المتاحة فعّالة. تتم معالجة المستندات والصوت وفق تصميم الخصوصية المطبق، ولا يتم الاحتفاظ بالملفات الخام بشكل دائم بعد المعالجة.",
      "في الحالات الحساسة أو عالية المخاطر قد يوجّهك Sam إلى جهة مهنية أو رسمية مناسبة. المشاركة اختيارية، ويمكنك مغادرة النسخة التجريبية المغلقة في أي وقت.",
    ],
    question: "هل ترغب بالمشاركة في النسخة التجريبية المغلقة؟ أجب بوضوح بنعم أو لا.",
  },
  {
    lang: "English",
    dir: "ltr",
    title: "AmtHero24 Closed Beta",
    paragraphs: [
      "You are invited to take part in a small, controlled test of AmtHero24 with Sam. Sam helps with everyday administrative tasks in Germany, such as understanding letters, drafting messages, and organizing tasks, appointments, and reminders.",
      "Important: Sam is not a government authority, lawyer, or doctor and does not replace professional advice. AI and document interpretation can make mistakes, so review important facts, deadlines, and drafts before acting on them.",
      "Closed Beta participation and consent to reusable personal memory are separate decisions. Supported export and deletion controls remain available. Documents and audio are processed according to the implemented privacy design and are not retained permanently as raw files after processing.",
      "For sensitive or high-risk situations, Sam may direct you to an appropriate professional or official service. Participation is voluntary, and you can leave the Closed Beta at any time.",
    ],
    question: "Would you like to participate in the Closed Beta? Please answer clearly with Yes or No.",
  },
  {
    lang: "Українська",
    dir: "ltr",
    title: "Закрите бета-тестування AmtHero24",
    paragraphs: [
      "Вас запрошено взяти участь у невеликому контрольованому тестуванні AmtHero24 із Sam. Sam допомагає з повсякденними адміністративними справами в Німеччині: розуміти листи, готувати повідомлення та організовувати завдання, зустрічі й нагадування.",
      "Важливо: Sam не є державним органом, адвокатом або лікарем і не замінює професійну консультацію. ШІ та аналіз документів можуть помилятися, тому перевіряйте важливі факти, строки й тексти перед дією.",
      "Участь у закритій Beta та згода на повторне використання персональної пам’яті є окремими рішеннями. Доступні функції експорту та видалення залишаються чинними. Документи й аудіо обробляються відповідно до реалізованої моделі приватності та не зберігаються постійно як сирі файли після обробки.",
      "У чутливих або ризикованих ситуаціях Sam може порадити звернутися до відповідного фахівця або офіційної установи. Участь добровільна, і ви можете вийти із закритої Beta будь-коли.",
    ],
    question: "Бажаєте взяти участь у закритій Beta? Будь ласка, чітко відповідайте Так або Ні.",
  },
  {
    lang: "Ελληνικά",
    dir: "ltr",
    title: "Κλειστή δοκιμαστική έκδοση AmtHero24",
    paragraphs: [
      "Σας προσκαλούμε να συμμετάσχετε σε μια μικρή, ελεγχόμενη δοκιμή του AmtHero24 με τον Sam. Ο Sam βοηθά σε καθημερινές διοικητικές υποθέσεις στη Γερμανία, όπως κατανόηση επιστολών, σύνταξη μηνυμάτων και οργάνωση εργασιών, ραντεβού και υπενθυμίσεων.",
      "Σημαντικό: Ο Sam δεν είναι δημόσια αρχή, δικηγόρος ή γιατρός και δεν αντικαθιστά επαγγελματική συμβουλή. Η τεχνητή νοημοσύνη και η ανάλυση εγγράφων μπορεί να κάνουν λάθη, επομένως ελέγχετε σημαντικά στοιχεία, προθεσμίες και κείμενα πριν ενεργήσετε.",
      "Η συμμετοχή στην κλειστή Beta και η συγκατάθεση για επαναχρησιμοποιήσιμη προσωπική μνήμη είναι ξεχωριστές αποφάσεις. Οι διαθέσιμες λειτουργίες εξαγωγής και διαγραφής παραμένουν ενεργές. Έγγραφα και ήχος επεξεργάζονται σύμφωνα με τον εφαρμοσμένο σχεδιασμό απορρήτου και δεν διατηρούνται μόνιμα ως ακατέργαστα αρχεία μετά την επεξεργασία.",
      "Σε ευαίσθητες ή υψηλού κινδύνου περιπτώσεις, ο Sam μπορεί να σας παραπέμψει σε κατάλληλο επαγγελματία ή επίσημη υπηρεσία. Η συμμετοχή είναι εθελοντική και μπορείτε να αποχωρήσετε από την κλειστή Beta οποιαδήποτε στιγμή.",
    ],
    question: "Θέλετε να συμμετάσχετε στην κλειστή Beta; Απαντήστε καθαρά Ναι ή Όχι.",
  },
] as const;

export const metadata = {
  title: "Closed Beta Hinweis | AmtHero24",
  description: "Closed-Beta-Hinweis von AmtHero24 in fünf unterstützten Sprachen.",
  alternates: { canonical: "/beta" },
};

export default function BetaNoticePage() {
  return <main id="main" className="legal betaNoticePage">
    <Link href="/">← Zurück zu AmtHero24</Link>
    <p className="eyebrow">AmtHero24 · Closed Beta</p>
    <h1>Closed-Beta-Hinweis</h1>
    <p className="warning"><strong>Zugang ist noch geschlossen.</strong> Diese Seite informiert nur. Sie öffnet keine Beta-Teilnahme und aktiviert keine Speicherung.</p>
    {notices.map((notice) => <section className="betaNotice" key={notice.lang} dir={notice.dir} lang={notice.lang === "Deutsch" ? "de" : notice.lang === "العربية" ? "ar" : notice.lang === "English" ? "en" : notice.lang === "Українська" ? "uk" : "el"}>
      <p className="eyebrow">{notice.lang}</p>
      <h2>{notice.title}</h2>
      {notice.paragraphs.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
      <p><strong>{notice.question}</strong></p>
    </section>)}
  </main>;
}
