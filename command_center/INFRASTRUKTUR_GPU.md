# Infrastruktur und GPU-Ausbau von MIA

> **Stand:** 24.09.2026 · Wird laufend ergänzt. Preise und Zustände haben ein Datum,
> weil sie sich ändern. Was nicht geprüft ist, steht als **ungeprüft** da.

## 1. Ist-Zustand (geprüft am 24.09.2026)

Drei Server, alle erreichbar, alle Container laufen.

| Server | Rolle | Ressourcen | Auffälligkeit |
|---|---|---|---|
| MIA-BRAIN-01 | Modelle, Stimme, Command Center, n8n, Open WebUI | 8 vCPU, 15 GB RAM, 464 GB Platte (20 % belegt) | RAM knapp: etwa 390 MB frei, 6,7 GB bei Bedarf freigebbar |
| MIA-KNOWLEDGE-01 | Gedächtnis, Vektor-DB, Embeddings, Lernen | 15 GB RAM, 464 GB Platte (4 % belegt) | 8 Container, meist gesund |
| MIA-CONTROL-01 | Dashboard, Monitoring, n8n, eigenes Ollama | 4 vCPU, 3,8 GB RAM, 116 GB Platte (66 % belegt) | Platte und RAM eng |

**Modelle heute (Ollama, nur CPU, keine GPU):** `qwen2.5:3b` (dauerhaft geladen),
`qwen2.5:7b`, `qwen3:8b`, `qwen3:1.7b`, `bge-m3` (Embeddings). Der Engpass ist die
Rechenleistung, nicht der Plattenplatz.

**Korrektur an älteren Texten:** `MIA_COMPLETE_CONFIG.md` und
`MIA_FINAL_MASTERY_SUMMARY.txt` auf KNOWLEDGE-01 nennen Phi-2 und „132+ Skills
trainiert". Das entspricht nicht dem Betrieb. Es läuft Qwen über Ollama, die Häkchen
sind Texteinträge und keine geprüften Fähigkeiten.

## 2. Passende Modelle

- Qwen3 ist gesetzt (schon im Einsatz, gutes Deutsch).
- **Qwen3-30B-A3B (MoE):** deutlich klüger als 8B, schnell, da pro Token nur etwa 3 Mrd.
  Parameter aktiv sind. Quantisiert rund 18–20 GB Grafikspeicher.
- **Qwen3-14B** braucht etwa 10 GB, **Qwen3-32B (Q4)** etwa 20 GB (knapper Kontext).
- `bge-m3` braucht etwa 1 GB. Stimme (Piper/Whisper) läuft weiter getrennt.

Richtwert: **24 GB reichen, 32 GB sind bequem, 48 GB und mehr lassen Spielraum.**

## 3. Miete (IONOS, Dedicated, geprüft 24.09.2026, inkl. MwSt.)

Quelle: `ionos.de/server/gpu-server`. Feste Konfigurationen, **nicht aufrüstbar**;
Aufrüsten heißt Tarifwechsel. Teilweise Mindestlaufzeiten, im Bestellformular prüfen.

| Modell | GPU | CPU / RAM | Monat (max.) | Einrichtung |
|---|---|---|---|---|
| AP1-10 GPU | Tesla T4, 16 GB | EPYC 7302P, 128 GB | 590 € | 590 € |
| AP2-10 GPU | A10, 24 GB | EPYC 7313P, 128 GB | 750 € | 750 € |
| IP3-10 GPU | Intel Flex 170, 16 GB | Xeon Gold 5412U, 256 GB | 790 € | 790 € |
| IP4-50 GPU | RTX PRO 6000 Blackwell, 96 GB | 2× Xeon 6517P, 256 GB | 1.800 € | 1.800 € |

Hetzner: GEX45 (RTX PRO 4000 Blackwell SFF, 24 GB) und GEX131 (RTX PRO 6000
Blackwell Max-Q, 96 GB). Preise dort werden erst im Browser geladen, **ungeprüft**.

## 4. Kauf neu (Alternate.de, geprüft 24.09.2026)

| Karte | Speicher | Preis |
|---|---|---|
| Radeon AI PRO R9700 (AMD) | 32 GB | 1.833 – 1.951 € |
| RTX 5080 | 16 GB | 1.552 – 1.599 € |
| RTX PRO 4500 Blackwell | 32 GB | 4.962 – 5.174 € |
| RTX 5090 | 32 GB | 5.634 – 6.999 € |
| RTX PRO 5000 Blackwell | 48 / 72 GB | 8.667 – 11.503 € |
| RTX PRO 6000 Blackwell | 96 GB | 17.627 – 19.152 € |

Nicht geprüft: Gehäuse, Mainboard, CPU, RAM, Netzteil. RTX 4090 und 3090 gibt es
neu nicht mehr. AMD R9700: Ollama-Unterstützung **ungeprüft**, bei KI-Software
riskanter als NVIDIA.

## 5. Kauf gebraucht (Kleinanzeigen, Stichprobe 24.09.2026)

Nur die erste Ergebnisseite, gefiltert um Gesuche, Defekte und Komplett-PCs. eBay
war nicht erreichbar (403). **Stichprobe, kein Marktüberblick.**

| Karte | Speicher | Plausible Preise |
|---|---|---|
| RTX 3090 | 24 GB | 900 – 1.300 € (Median etwa 1.100 €) |
| RTX 4090 | 24 GB | 1.800 – 3.150 € |
| RTX 5090 | 32 GB | 2.700 – 3.500 € |
| RTX 4080 | 16 GB | 870 – 1.000 € |
| Tesla P40 | 24 GB | 300 € (eine Anzeige; alt, langsam, nicht empfohlen) |

Anzeigen weit unter Markt (z. B. „RTX 5090 Astral" für 200 €) sind sehr
wahrscheinlich Betrug. Nur mit Abholung und Test vor Ort, PayPal Waren und
Dienstleistungen oder Händler mit Gewährleistung.

## 6. Empfehlung

1. **Erst mieten, dann entscheiden:** A10 (24 GB, 750 € im Monat) für einen Testlauf.
   Erst kaufen, wenn die Last dauerhaft ist. Die 5090 allein entspricht 8–9 Monatsmieten.
2. **Günstig kaufen:** gebrauchte RTX 3090 (24 GB, etwa 1.000 €), mit Platz für eine
   zweite Karte (2 × 24 GB = 48 GB).
3. **Aufrüstbarer Bau:** Tower oder 4U, mindestens 2 PCIe-x16-Slots, Netzteil
   1.200–1.600 W, 128 GB ECC-RAM erweiterbar auf 256 GB, 2 × NVMe im Spiegel.
4. Standort offen: Zuhause/Büro oder Rechenzentrum (Colocation)?

## 7. Offene Punkte

- [ ] Preise für Mainboard, CPU, RAM, Netzteil, Gehäuse ermitteln.
- [ ] Standort des Servers klären.
- [ ] Websuche im Werkzeug prüfen (Modell `deepseek/deepseek-v4-flash-0731:free`
      existiert nicht mehr; Fix in `~/.claude/settings.json`, in neuer Sitzung testen).
- [ ] BRAIN-01: RAM entlasten; CONTROL-01: Platte (66 %) prüfen; Backups und offene
      Ports prüfen.
- [ ] Alte MIA-Dokumente auf KNOWLEDGE-01 an den Ist-Zustand angleichen.
- [ ] n8n-Cloud-Webhooks liefern seit 20.09. HTTP 500.
- [ ] Bash-Verlauf enthält einen `git push` mit Token in der URL: prüfen, ob dort je
      ein echter Token stand, dann erneuern.
- [ ] Ungenutzten SSH-Schlüssel `~/.ssh/mia_servers_ed25519` löschen, falls nicht nötig.
