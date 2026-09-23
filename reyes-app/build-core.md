# core.mjs neu erzeugen

`core.mjs` ist aus `command_center/frontend/src/modules/home/BrainCore.tsx` erzeugt (Zeichencode unverändert, nur Typen entfernt und die
React-Teile durch `core.set(zustand, pegel)` ersetzt). Ändert sich der Kern im Dashboard, wird die Datei neu erzeugt: TypeScript-Compiler
(`npm i typescript`), den Inhalt des `useEffect` aus `BrainCore.tsx` ausschneiden, in `createCore(cv)` einwickeln und mit
`ts.transpileModule` (ES2020) umwandeln. Nicht von Hand bearbeiten.
