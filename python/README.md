# Optolink API (Python)

Python-Port des Java-Optolink-Adapters: liest und schreibt Werte einer Viessmann-Heizung
(Vitotronic) über die Optolink-Schnittstelle und stellt sie als **REST/JSON-API mit Swagger UI** bereit.
Keine Abhängigkeit zu openHAB – jedes Smart-Home-System, das HTTP kann
(Home Assistant, ioBroker, Node-RED, FHEM, openHAB, …), kann die Werte abfragen und setzen.

- Protokolle **300** (VS2, neuere Steuerungen) und **KW** (VS1, ältere Steuerungen)
- Optolink-Kopf am **seriellen Port/USB** oder **im Netzwerk** (ser2net, ESP-Link, …)
- verwendet die bestehende `optolink.xml` der Java-Version unverändert weiter

## Installation

Python 3.9 oder neuer wird benötigt.

```sh
cd python
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install .
```

## Konfiguration

`optolink.example.xml` nach `optolink.xml` kopieren und anpassen – oder die bisherige
`optolink.xml` der Java-Version verwenden. Wichtig sind:

| Element / Attribut            | Bedeutung |
|-------------------------------|-----------|
| `<optolink protocol="…">`     | `300` oder `KW` |
| `<tty>`                       | `/dev/ttyUSB0`, `COM3`, `192.168.178.25:10001` (TCP) oder eine pyserial-URL wie `rfc2217://host:port` |
| `<ttytimeout>`                | Lese-Timeout in Millisekunden |
| `<telegram address type divider>` | Adresse (hex), Datentyp, Teiler |
| `<telegram readonly="true">`  | **neu:** verhindert Schreiben über die API |

Channels/Things mit `*` vor der ID werden nicht aufgelistet und nicht beim Sammel-Lesen gelesen,
sind aber direkt (ohne `*`) abrufbar. `<port>` aus der Java-Konfiguration wird ignoriert,
der HTTP-Port wird per `--port` gesetzt.

## Starten

```sh
optolink-api --config optolink.xml --port 8000
# oder: python -m optolink --config optolink.xml
```

Weitere Optionen: `--host`, `--tty` (überschreibt `<tty>`), `--log-level debug`
(zeigt jedes gesendete/empfangene Byte).

Die **Swagger UI** ist dann unter <http://raspberrypi:8000/docs> erreichbar,
die OpenAPI-Beschreibung unter `/openapi.json`.

## Testen ohne Hardware

Mit `--simulate` wird statt des Optolink-Adapters eine simulierte Steuerung verwendet.
Sie legt für alle Channels der Konfiguration plausible Werte an (Temperaturen ändern sich
langsam, die Systemzeit läuft mit) und merkt sich geschriebene Werte bis zum Neustart:

```sh
optolink-api --config optolink.example.xml --simulate
```

## API

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/api/info` | Adapter, Protokoll, Verbindungsstatus |
| GET | `/api/things` | alle Things und Channels aus der Konfiguration |
| GET | `/api/things/{thing}` | Definition eines Things |
| GET | `/api/values` | alle Werte aller Things in einer Abfrage |
| GET | `/api/things/{thing}/values?channels=a,b` | mehrere Werte eines Things |
| GET | `/api/things/{thing}/channels/{channel}` | einen Wert lesen |
| PUT | `/api/things/{thing}/channels/{channel}` | einen Wert schreiben, Body `{"value": …}` |
| GET | `/api/raw/{adresse}?type=short&divider=10` | beliebige Adresse lesen (zum Suchen neuer Adressen) |

Werte sind JSON-typisiert: Zahlen (Teiler bereits angewendet), `true`/`false`, Datum als
ISO-String und Zeitprogramme als Liste:

```json
[{"on": "05:30", "off": "08:00"}, {"on": "16:00", "off": "22:00"}]
```

### Per Kommandozeile (curl)

Auf dem Rechner, auf dem die API läuft, `localhost` verwenden, sonst Hostname/IP (z.B. `raspberrypi`).
Thing- und Channel-IDs stammen aus der `optolink.xml` (Liste: `curl -s http://raspberrypi:8000/api/things`).

Werte lesen:

```sh
# alle Werte, formatiert
curl -s http://raspberrypi:8000/api/values | python3 -m json.tool

# einen Wert
curl -s http://raspberrypi:8000/api/things/heating/channels/outside_temp
# {"thing":"heating","channel":"outside_temp","value":7.4}

# laufend beobachten (alle 10 s)
watch -n 10 'curl -s http://raspberrypi:8000/api/values | python3 -m json.tool'
```

Werte setzen, z.B. Raumsolltemperatur auf 20 °C:

```sh
# 1. aktuellen Wert merken
curl -s http://raspberrypi:8000/api/things/circuit2/channels/room_set_temp

# 2. setzen
curl -s -X PUT http://raspberrypi:8000/api/things/circuit2/channels/room_set_temp \
     -H 'Content-Type: application/json' -d '{"value": 20}'
# {"thing":"circuit2","channel":"room_set_temp","value":20}
```

Die Antwort enthält den Wert, der nach dem Schreiben von der Steuerung zurückgelesen wurde.
Zahlen werden ohne Teiler angegeben (`{"value": 1.6}` bei `divider="10"`), Schalter als
`true`/`false`, Zeitprogramme als Liste wie oben.

Vor dem ersten Schreiben empfiehlt es sich, alle reinen Messwerte (Temperaturen, Zähler, …) in der
`optolink.xml` mit `readonly="true"` zu schützen – ohne dieses Attribut ist jeder Channel schreibbar.

Statuscodes: `404` unbekanntes Thing/Channel, `405` Channel ist schreibgeschützt,
`422` ungültiger Wert, `502` Steuerung lehnt die Adresse ab, `503` keine Verbindung zum Adapter.
Beim Sammel-Lesen erhalten abgelehnte Adressen den Wert `null`.

### Beispiel Home Assistant

Eine vollständige Konfiguration mit Sensoren und Reglern zum Setzen der Solltemperaturen
(passend zu `optolink.example.xml`) liegt in [`homeassistant/optolink.yaml`](homeassistant/optolink.yaml).
Ein minimales Beispiel:

```yaml
rest:
  - resource: http://raspberrypi:8000/api/values
    scan_interval: 60
    sensor:
      - name: Außentemperatur
        value_template: "{{ value_json.heating.outside_temp }}"
        unit_of_measurement: "°C"
        device_class: temperature
      - name: Warmwasser
        value_template: "{{ value_json.hotwater.temp }}"
        unit_of_measurement: "°C"
        device_class: temperature
```

## Als Dienst (systemd)

`/etc/systemd/system/optolink.service`:

```ini
[Unit]
Description=Optolink API
After=network.target

[Service]
WorkingDirectory=/opt/optolink/python
ExecStart=/opt/optolink/python/.venv/bin/optolink-api --config optolink.xml
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now optolink
```

## Debian-Paket

Auf einem Debian / Raspberry Pi OS (mit Internetzugang) bauen, optional mit der eigenen Konfiguration:

```sh
cd python
bash debian/build-deb.sh optolink.xml     # erzeugt dist/optolink-api_<version>_all.deb
sudo apt install ./dist/optolink-api_*_all.deb
```

Das Paket legt den Benutzer `optolink` (Gruppe `dialout`) an, installiert die Abhängigkeiten in
`/opt/optolink/venv` und startet den Dienst `optolink`. Konfiguration: `/etc/optolink/optolink.xml`,
Startoptionen (z.B. Port): `/etc/default/optolink`. Nach Änderungen: `sudo systemctl restart optolink`,
Log: `journalctl -u optolink -f`.

## Tests

```sh
pip install ".[test]"
pytest
```

Die Tests laufen gegen simulierte 300- und KW-Steuerungen, es wird keine Hardware benötigt.

## Unterschiede zur Java-Version

- REST/JSON + Swagger UI statt des TCP-Textprotokolls (`list`/`get`/`set`) und des UDP-Broadcasts
- Fehler der Java-Version behoben:
  - Protokoll 300 schrieb beim `set` immer `0` statt des Werts (und Zeitprogramme gar nicht)
  - Werte wurden beim Schreiben big endian kodiert, die Steuerung erwartet little endian
  - Schreiben war nur für 1-Byte-Typen möglich, jetzt für alle Zahlentypen
  - Antwort-Prüfsumme und Fehlerantworten (0x03) wurden nur geloggt, jetzt wird der Request wiederholt bzw. ein Fehler gemeldet
- Verbindungsabbrüche (z.B. Netzwerkadapter) werden automatisch neu aufgebaut
- Zeitprogramme erlauben `24:00` als Ausschaltzeit; Minuten müssen ein Vielfaches von 10 sein
- Datum/Systemzeit ist weiterhin nur lesbar
