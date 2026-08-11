from __future__ import annotations
import pathlib
import subprocess

OUT = pathlib.Path(__file__).resolve().parents[1] / "public" / "media"
OUT.mkdir(parents=True, exist_ok=True)
VIDEOS = {
    "brief-scanner": "Brief verstehen · Frist erkennen · nächsten Schritt sehen",
    "termin": "Termin ordnen · Unterlagen vorbereiten · Erinnerung setzen",
    "kuendigung": "Kündigung vorbereiten · Angaben prüfen · selbst senden",
    "vertrag": "Vertrag strukturieren · Laufzeit sehen · offene Punkte markieren",
    "geld-zurueck": "Belege ordnen · Anfrage vorbereiten · Ergebnis offen lassen",
    "nachrichten": "Anliegen erklären · Entwurf erstellen · vor Versand prüfen",
}
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
for slug, title in VIDEOS.items():
    target = OUT / f"{slug}.mp4"
    safe = title.replace("'", "\\'").replace(":", "\\:")
    vf = (
        "drawbox=x=90+20*sin(t):y=120:w=1100:h=480:color=white@0.08:t=fill,"
        f"drawtext=fontfile={FONT}:text='AmtHero24':fontcolor=white:fontsize=38:x=110:y=80,"
        f"drawtext=fontfile={FONT}:text='{safe}':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=(h-text_h)/2,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='Sam · KI-gestützt · Deutschland':fontcolor=0x9fe0ca:fontsize=24:x=(w-text_w)/2:y=h-95"
    )
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x0b1d35:s=1280x720:r=24:d=15",
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "15", "-movflags", "+faststart", str(target)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(f"Generated {len(VIDEOS)} local 15-second H.264 demos in {OUT}")
