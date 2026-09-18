# Gateway Timezone Configuration

> [!NOTE]
> What the **"Unconfigured timezone on MyHOME gateway"** repair issue means, why it happens, how to resolve it across different gateway models, and how Home Assistant automatically clears it.

**Summary**: When an OpenWebNet gateway has never had its timezone explicitly configured (or was reset), it reports a placeholder sentinel value `999` in its WHO=13 time telemetry frames. Home Assistant flags this via a Repair issue (`unconfigured_timezone`). Once you configure the correct timezone in the gateway's web UI or MyHOME_Suite, the gateway emits a valid offset and Home Assistant automatically dismisses the repair issue.

---

## Why This Issue Is Raised

OpenWebNet gateways manage an internal real-time clock (RTC). The Home Assistant MyHOME integration reads or monitors this clock via **WHO=13 (Gateway Management)**:

- **Dimension 0**: Time and Date (`*#13**0##`)
- **Dimension 22**: Time, Date and Timezone (`*#13**22##`)

When a gateway's timezone has not been configured, firmware defaults to emitting a sentinel placeholder value `999` in the timezone parameter:

- Dimension 0 frame: `*#13**0*<HH>*<MM>*<SS>*999##`
- Dimension 22 frame: `*#13**22*<HH>*<MM>*<SS>*999*<DAY>*<MONTH>*<YEAR>##`

### Why This Matters
1. **Clock & Timestamp Drift**: Without a configured timezone, scheduled scenario triggers and time broadcasts on the SCS bus may diverge from your local daylight saving time or Home Assistant's clock.
2. **Diagnostic Reliability**: Underlying protocol parsers expect a standard UTC offset format (such as `+01:00` or numeric minute offsets). While the integration handles `999` gracefully without crashing, leaving it unconfigured prevents proper clock synchronization.

---

## Step-by-Step Resolution by Gateway Model

### 1. F454, F455, and MyHomeServer1 (Web Administration Interface)
1. Open a web browser and navigate to the IP address of your gateway (e.g., `http://192.168.1.xxx`).
2. Log in using your installer credentials (default username/password is typically `admin` / `admin`).
3. Navigate to **System Configuration** → **Date & Time** (or **Device Settings** → **Clock**).
4. Select your geographic region or local timezone (e.g. `Europe/Rome`, `Europe/Paris`, `Europe/Amsterdam`, or UTC offset).
5. *(Recommended)* Enable **NTP Synchronization** and configure an NTP server (such as `pool.ntp.org`).
6. Click **Save** / **Apply** and allow the gateway to restart its network services if prompted.

### 2. MH200N, MH201, MH202, and F452 (MyHOME_Suite / TiMyHome)
1. Launch **MyHOME_Suite** or **TiMyHome** on your PC.
2. Connect to your gateway via Ethernet (IP) or USB.
3. Receive or open the gateway's project configuration.
4. Navigate to **Gateway Settings** / **General Parameters** → **Date & Time**.
5. Set the correct local time zone and check **Automatic Daylight Saving Time (DST)** if supported.
6. Send the configuration to the gateway and perform a reboot.

---

## Automatic Resolution in Home Assistant

You do **not** need to manually dismiss the repair issue in Home Assistant:

1. When the gateway completes its reboot or applies the new clock settings, it broadcasts an updated WHO=13 frame carrying a valid timezone offset.
2. Home Assistant listens for this telemetry:
   - When a valid offset is received (e.g. `+01:00`, `+02:00`), the integration automatically removes the `unconfigured_timezone` issue from **Settings → System → Repairs**.

---

## Related Documentation

- [Gateway Identification](identification.md) — Model resolution precedence and model mismatch repairs.
- [v2.0 Documentation](../../latest/) — Modern UI-first documentation.
