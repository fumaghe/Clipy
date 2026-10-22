# Riferimento TikTok per il template

- URL: https://vm.tiktok.com/ZN8Ap3hEK/
- Stato: **`REFERENCE_NOT_VERIFIED`**

Durante M0 il link breve non è raggiungibile dall'ambiente di sviluppo (nessuna risoluzione DNS verso tiktok.com) e comunque non è un video
scaricabile in modo affidabile. **Non è stata osservata nessuna caratteristica** del video: font, timing, animazioni e posizione NON sono stati inventati.

## Cosa fa Clipy in attesa della verifica
- Preset neutro (`templates/default.json`): testo bianco bold (DejaVu Sans Bold), contorno nero 6 px, ombra, centrato, max 3 righe, fade 0.25 s, durata «intera clip».
- Template Studio permette di riprodurre qualunque stile con controlli reali (colore, contorno, ombra, posizione, dimensione, wrap, durata full / primi N secondi / intervallo, fade).
- Ogni render registra `reference_verified=false` finché il proprietario non conferma.

## Come completare la verifica (onboarding)
1. Template Studio → «Importa screenshot/video» e carica uno screenshot (o screen recording) del TikTok di riferimento.
2. Regola il template finché l'anteprima reale coincide; annota font/timing osservati qui sotto.
3. Premi «Segna come verificato»: solo allora `reference_verified=true`.

## Attributi osservati
_Nessuno (non verificato)._
