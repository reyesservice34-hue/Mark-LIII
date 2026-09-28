# MIA Learning Policy

## Grundprinzip

MIA darf neue Informationen nicht ungeprüft dauerhaft übernehmen.

Lernpfad:

1. Neue Information empfangen
2. Quelle bestimmen
3. Inhalt auf Secrets prüfen
4. Inhalt validieren
5. Zielbereich bestimmen
6. Speichern
7. Abruf testen
8. Ergebnis protokollieren

## Nicht lernen oder speichern

Folgende Inhalte dürfen niemals in Knowledge, Memory oder Trainingsdaten übernommen werden:

- API Keys
- Tokens
- Passwörter
- Zugangsdaten
- SSH Private Keys
- Browser Cookies
- Session Tokens
- Credentials
- Secret-Dateien
- .env Inhalte
- private Schlüssel
- Authentifizierungsheader

Typische Dateinamen oder Muster:

api_keys.json
.env
.env.*
token
tokens
password
passwd
secret
credentials
cookie
cookies
id_rsa
id_ed25519
*.pem
*.key

## Unsichere Informationen

Nicht bestätigte oder widersprüchliche Informationen kommen zuerst nach:

quarantine/

Sie dürfen nicht automatisch zu dauerhaftem Wissen werden.

## Lernen gilt erst als erfolgreich, wenn

- die Information gespeichert wurde
- sie in einer neuen Session wiedergefunden wird
- die Quelle nachvollziehbar bleibt
- der Retrieval-Test erfolgreich ist
