# aVinceCam

**Camera Control for Linux** – ein schlankes Cinnamon-Applet, das die Kameras eines
Rechners erkennt, verständlich anzeigt und eine passende Vorschau startet.

> Stand: frühe Entwicklung (Diagnosephase). Noch nicht nutzbar.

## Ziele

- Kameras automatisch erkennen: klassische V4L2-Geräte (USB-Webcams) und libcamera-Kameras
  (z. B. Intel IPU3/IPU6 oder andere MIPI-Sensoren)
- pro Kamera ein lesbarer Eintrag statt einer Liste roher Geräteknoten
- Vorschau mit vorhandenen Programmen (qcam, GStreamer) starten und beenden
- Auflösungen, Bildraten und Fehler ehrlich anzeigen – nur, was das System wirklich meldet
- kein Hintergrunddienst, keine Root-Rechte, keine Änderungen an Treibern oder Systemkonfiguration

Details zum Aufbau: [docs/architecture.md](docs/architecture.md) (Englisch).

## Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE).
