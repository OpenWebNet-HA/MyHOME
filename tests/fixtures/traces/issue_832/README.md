# #832 Legrand 067292 (Céliane MyHOME_Screen 3.5") Gateway Diagnostics Trace

- **Gateway Model**: Legrand `067292` (Céliane *MyHOME_Screen 3.5"*, shares board architecture, UPnP device description, and firmware family with BTicino `H4890`, `LN4890`, `AM4890`, `LN4890A`, `HW4890`, Legrand Arteor `573958`, and Mosaic `078479`; `WHO=1013` `OBJECT_MODEL = 30`)
- **Firmware Version**: `4.0.15` (Firmware family 4; exact build match to `H4890` observed in issue #466)
- **Contributed by**: **@Yvan13120** in [#832 (comment 6607331908)](https://github.com/OpenWebNet-HA/MyHOME/issues/832#issuecomment-6607331908)
- **Environment**: Home Assistant 2026.10.4, MyHOME 2.0.0b16, OWNd 2.0.0b11

| File | Content | Frame Count |
|---|---|:---:|
| `config_entry-myhome-067292.json` | Diagnostics export with 500-frame bus monitor capture | 500 |

---

## 1. Hardware & UPnP Discovery Fingerprint

The diagnostics export reveals the exact UPnP device descriptor and SSDP discovery payload broadcast by the Legrand Céliane 3.5" wall touchscreen:

- **UPnP `modelName`**: `"067292"` (the pure Legrand catalog code, without prefix)
- **UPnP `manufacturer`**: `"BTicino S.p.A."` (retaining the BTicino OEM manufacturer identity across Legrand-branded devices)
- **UPnP `deviceType`**: `"urn:schemas-bticino-it:device:touchscreen:1"`
- **UPnP `port`**: `20000`
- **Firmware**: `"4.0.15"`

```json
{
  "deviceType": "urn:schemas-bticino-it:device:touchscreen:1",
  "firmware": "4.0.15",
  "manufacturer": "BTicino S.p.A.",
  "manufacturerURL": "http://www.bticino.it",
  "name": "067292",
  "port": 20000,
  "ssdp_st": "upnp:rootdevice"
}
```

---

## 2. Bus Topology & Warm Standby Pairing

The plant features a multi-gateway configuration on a shared SCS bus:
- **Primary Gateway**: BTicino `F454` (**Tier 1**, 4 command sessions)
- **Secondary Gateway**: Legrand `067292` (**Tier 3**, 1 command session)
- **Topology Role**: `standby` (Warm Standby failover)
- **Entity Suppression**: Active entities are suppressed (`platforms` count = 0) to avoid duplicates, with the follower listening for failover activation.

---

## 3. Bus Monitor Traffic Breakdown (500 Frames)

The 500 captured bus frames verify active passive reception on the event session:
- **`WHO = 1` (Lighting / Switches)**: 88 frames (`*1*1000#1*12##`, `*1*1*12##`, etc.)
- **`WHO = 4` (Thermoregulation)**: 274 frames across zones 1, 2, and 4 (ambient temperatures `20.1 °C` and `21.8 °C` via `*#4*<WHERE>*0*<TEMP>##`)
- **`WHO = 13` (Gateway Management & RTC Clock)**: 138 frames (periodic time sync `*#13**#0*HH*MM*SS*##` and date sync `*#13**#1*WD*DD*MM*YYYY##`)

---

## 4. OpenWebNet Encyclopedia & Firmware Oracle Corroboration

- **OpenWebNet Encyclopedia Machine KB (`ownkb-build-0.9.0`)**:
  - Direct corroboration of `ownkb:claim:c017265` under `OWN-DEV-0015: MyHOME_Screen 3.5` (*Commercial identities: Legrand - Céliane / Reference: `067292`*).
  - Closes open question `ownkb:question:q300071` by providing real-world runtime proof of the SSDP fingerprint, UPnP device type, and firmware version `4.0.15` for the Legrand Céliane branch of the `4890` screen family.
