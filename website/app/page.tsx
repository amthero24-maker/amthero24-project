import Link from "next/link";
import { resolveBetaCtaUrl } from "../lib/beta-cta";

const tools = [
  { name: "Brief Scanner", text: "Brief, PDF oder Foto senden. Sam sortiert Absender, Frist, Betrag, Referenz und nächsten Schritt.", icon: "⌕", video: "/media/brief-scanner.mp4" },
  { name: "Termin Assistance", text: "Termin, Ort, Unterlagen und Vorbereitung an einem Platz – inklusive Erinnerung.", icon: "◷", video: "/media/termin.mp4" },
  { name: "Kündigung", text: "Einen prüfbaren Kündigungsentwurf erstellen, ohne Fristen oder Rechtslage zu erfinden.", icon: "✎", video: "/media/kuendigung.mp4" },
  { name: "Vertrags-Check", text: "Wichtige Klauseln, Laufzeit, Verlängerung und offene Punkte verständlich zusammenfassen.", icon: "◇", video: "/media/vertrag.mp4" },
  { name: "Geld zurück", text: "Rückerstattung strukturiert anfragen und Belege ordnen – ohne Erfolgsversprechen.", icon: "↩", video: "/media/geld-zurueck.mp4" },
  { name: "Nachrichten & E-Mails", text: "Formelle Nachrichten auf Deutsch vorbereiten und vor dem Senden selbst prüfen.", icon: "✉", video: "/media/nachrichten.mp4" },
];

const faq = [
  ["Ist Sam ein Mensch?", "Nein. Sam ist ein KI-gestützter persönlicher Assistent von AmtHero24. Diese KI-Rolle wird transparent ausgewiesen."],
  ["Ist AmtHero24 eine Behörde oder Kanzlei?", "Nein. AmtHero24 erklärt, organisiert und formuliert. Es ersetzt keine Behörde, Rechtsberatung, Steuerberatung oder medizinische Beratung."],
  ["Was passiert mit Dokumenten?", "Dokument- und Audiodaten werden für die Verarbeitung verwendet. Das Produktdesign sieht keine dauerhafte Speicherung der Rohdateien nach der Verarbeitung vor."],
  ["Speichert ihr meine Telefonnummer in Logs?", "Produktionsdiagnostik ist auf privacy-safe Kennungen und aggregierte Kennzahlen ausgelegt; rohe Telefonnummern oder Dokumentinhalte gehören nicht in Logs oder CI."],
  ["Kann Sam Fehler machen?", "Ja. KI und Dokumenterkennung können Fehler machen. Wichtige Daten, Fristen, Beträge und Entwürfe müssen am Original geprüft werden."],
  ["Kann ich gespeicherte Daten löschen?", "Die implementierten Datenschutzfunktionen unterstützen Export und Löschung. Wiederverwendbare Hero Memory ist zusätzlich einwilligungsgebunden."],
  ["Welche Sprachen werden unterstützt?", "Deutsch, Arabisch, Englisch, Ukrainisch und Griechisch gehören zum getesteten Produktumfang."],
  ["Wann kann ich der Beta beitreten?", "Wave 1 bleibt bis zu einem separaten GO geschlossen. Danach sind maximal fünf gleichzeitig zugelassene Beta-Nutzer vorgesehen."],
];

const legalLinks = [
  ["/impressum", "Impressum"], ["/datenschutz", "Datenschutz"], ["/agb", "AGB"],
  ["/widerruf", "Widerruf"], ["/kontakt", "Kontakt"], ["/beta", "Beta-Hinweis"],
  ["/cookie-einstellungen", "Cookies"], ["/barrierefreiheit", "Barrierefreiheit"],
];

export const metadata = {
  alternates: { canonical: "/" },
};

export default function Home() {
  const betaCtaUrl = resolveBetaCtaUrl();
  return <>
    <header className="nav"><a className="brand" href="#top"><b>A24</b> AmtHero24</a><nav><a href="#how">So funktioniert&apos;s</a><a href="#tools">Werkzeuge</a><a href="#trust">Sicherheit</a><a href="#faq">FAQ</a></nav></header>
    <main id="main">
      <section className="hero" id="top"><div className="heroCopy"><span className="pill">{betaCtaUrl ? "Closed Beta · Wave 1 kontrolliert geöffnet" : "Closed Beta · Zugang noch geschlossen"}</span><p className="eyebrow">Der Alltagsheld für Deutschland</p><h1>Papierkram in Deutschland.<br/><em>Endlich verständlich.</em></h1><p className="lead">Sam ist dein KI-gestützter persönlicher Assistent in WhatsApp. Er hilft dir, Briefe zu verstehen, Termine zu ordnen, Nachrichten vorzubereiten und Fristen im Blick zu behalten – in deiner Sprache.</p>{betaCtaUrl ? <a className="cta" href={betaCtaUrl} target="_blank" rel="noopener noreferrer">Closed Beta beitreten</a> : <button className="cta" disabled>Closed Beta – 5 Plätze nach GO</button>}<div className="chips"><span>✓ Keine neue App</span><span>✓ 5 Sprachen</span><span>✓ Prüfbarkeit vor Handlung</span></div></div><div className="phone"><div className="phoneHead"><span className="avatar">S</span><div><strong>Sam von AmtHero24</strong><small>KI-gestützter Assistent</small></div></div><div className="bubble user">Ich habe diesen Brief bekommen. Was muss ich tun?</div><div className="doc">📄 Schreiben_Jobcenter.pdf</div><div className="bubble sam"><b>Das Wichtigste:</b><br/>Frist: 14 Tage<br/>Nächster Schritt: Unterlagen nachreichen<br/><small>Bitte Datum und Aktenzeichen im Original prüfen.</small></div></div></section>

      <section className="section"><p className="eyebrow">Warum AmtHero24?</p><h2>Aus Druck und Unklarheit wird ein nächster Schritt.</h2><div className="grid3"><article><b>01</b><h3>„Ich verstehe den Brief nicht.“</h3><p>Behördensprache, Verträge und Fristen werden in Alltagssprache erklärt.</p></article><article><b>02</b><h3>„Ich verliere den Überblick.“</h3><p>Termin, Ort, Unterlagen und Erinnerung werden logisch zusammengeführt.</p></article><article><b>03</b><h3>„Wie antworte ich richtig?“</h3><p>Sam erstellt einen strukturierten Entwurf, den du vor dem Senden prüfst.</p></article></div></section>

      <section className="section dark" id="how"><p className="eyebrow">Drei Schritte</p><h2>Einfach schreiben wie in einem normalen Chat.</h2><div className="grid3"><article><span className="step">1</span><h3>Sende, was dich beschäftigt</h3><p>Text, Foto, PDF, Word-Datei oder Sprachnachricht.</p></article><article><span className="step">2</span><h3>Sam versteht und sortiert</h3><p>Fakten, Fristen, offene Punkte und Unsicherheiten werden klar getrennt.</p></article><article><span className="step">3</span><h3>Du gehst den nächsten Schritt</h3><p>Erklärung, Entwurf, Aufgabe oder Erinnerung – unter deiner Kontrolle.</p></article></div></section>

      <section className="section" id="tools"><p className="eyebrow">Sechs Werkzeuge, ein Assistent</p><h2>Für die häufigsten Alltagssituationen in Deutschland.</h2><div className="toolGrid">{tools.map((tool)=><article className="tool" key={tool.name}><span className="toolIcon">{tool.icon}</span><h3>{tool.name}</h3><p>{tool.text}</p><div className="demoVideoWrap"><video className="demoVideo" src={tool.video} controls muted playsInline preload="metadata" aria-label={`${tool.name} – 15-Sekunden-Demo`} /><small>15-Sekunden-Demo · startet nur nach deiner Auswahl · lokal gehostet · trackingfrei</small></div></article>)}</div></section>

      <section className="section before"><div><p className="eyebrow">Vorher</p><h2>„Was bedeutet das alles?“</h2><p>Ein langer Brief, eine Frist, ein Aktenzeichen – und keine klare Reihenfolge.</p></div><div className="arrow">→</div><div><p className="eyebrow">Mit Sam</p><h2>„Das sind die drei Dinge, die zählen.“</h2><p>Frist, benötigte Unterlagen und ein klarer nächster Schritt. Wichtige Angaben bleiben prüfbar.</p></div></section>

      <section className="section trust" id="trust"><p className="eyebrow">Vertrauen ist Produktfunktion</p><h2>Sicherheitsgrenzen sind nicht im Kleingedruckten versteckt.</h2><div className="grid3"><article><h3>Fail-closed</h3><p>Wenn kritischer Zustand oder Konfiguration nicht sicher verifiziert werden kann, soll der sensible Pfad nicht einfach weiterlaufen.</p></article><article><h3>Privacy-safe Observability</h3><p>Produktionsmetriken sind auf aggregierte Werte und sichere Identifikatoren ausgelegt – nicht auf rohe Telefonnummern oder Dokumentinhalte.</p></article><article><h3>Einwilligung getrennt</h3><p>Beta-Teilnahme und wiederverwendbare Hero Memory sind getrennte Entscheidungen. Ablehnung darf keine versteckte Erinnerung erzeugen.</p></article></div><div className="germany">🇩🇪 Für Alltag und Verwaltung in Deutschland entwickelt · WhatsApp-first · Version 4.7.0</div></section>

      <section className="section" id="faq"><p className="eyebrow">FAQ</p><h2>Die Fragen, die vor dem ersten Chat geklärt sein sollten.</h2><div className="faq">{faq.map(([q,a])=><details key={q}><summary>{q}</summary><p>{a}</p></details>)}</div></section>
    </main>
    <footer><div><b>AmtHero24</b><p>Sam – persönlicher KI-Assistent für Alltag und Verwaltung in Deutschland.</p></div><div className="links">{legalLinks.map(([href,label]) => <Link href={href} key={href}>{label}</Link>)}</div><div className="langs">DE · العربية · EN · Українська · Ελληνικά</div></footer>
  </>;
}
