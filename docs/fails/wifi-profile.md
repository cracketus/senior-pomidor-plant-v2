# Wi-Fi Profile Loss Postmortem

**Incident ID:** SP-2026-06-29-NET-001

**Subsystem:** Raspberry Pi edge node

**Category:** Network configuration failure

**Severity:** High

**Status:** Resolved

This public postmortem preserves the operational lesson from a Raspberry Pi network outage without publishing private network names or credentials.

## Summary

A Raspberry Pi edge node lost remote connectivity and stopped delivering telemetry even though the operating system, Docker services, and SSH daemon were still running. Local console access showed that the Wi-Fi interface existed and could scan nearby access points, but NetworkManager no longer had any saved Wi-Fi connection profiles.

Recreating the Wi-Fi profile restored LAN connectivity, SSH access, and telemetry delivery. The new profile persisted after reboot.

## Impact

- Remote SSH access was unavailable.
- Telemetry delivery stopped while the node was offline.
- Local collection could continue as long as the edge application and storage remained healthy.

## Root Cause

All persistent NetworkManager Wi-Fi connection profiles were missing from:

```text
/etc/NetworkManager/system-connections/
```

Without a saved profile, NetworkManager had no SSID, security settings, credentials, or autoconnect configuration for the Wi-Fi network. The Wi-Fi hardware and driver were functioning, but the node had no usable LAN connection.

## Diagnosis

The failure initially looked like an SSH problem because the client could reach TCP port 22, but the SSH handshake closed immediately. Local checks showed:

- `ssh.service` was running.
- Disk usage was normal.
- Docker bridge addresses were present, but no LAN address was assigned.
- `wlan0` existed but was disconnected.
- RF kill was not blocking Wi-Fi.
- NetworkManager was running.
- Wi-Fi scans found access points.
- `nmcli connection show` listed no Wi-Fi profile.

## Recovery

The operator recreated the Wi-Fi profile locally:

```bash
nmcli device wifi connect "<network-name>" password "<network-password>"
```

After connection, the node received a LAN address, SSH worked again, telemetry resumed, and the generated `.nmconnection` profile remained present after reboot.

## Preventive Controls

The edge node now exposes Wi-Fi and network-health fields in system telemetry, including:

- Wi-Fi connection state
- Interface state
- Active SSID
- LAN IP address
- Gateway reachability
- DNS resolution
- Internet reachability
- Stored Wi-Fi profile count
- Active profile presence
- Preferred profile presence when configured
- Last host-level recovery exit code when the optional guard is installed

The optional Wi-Fi guard installed by `scripts/setup_raspberry_pi.sh --hardware --install-wifi-guard` can back up NetworkManager profiles and attempt host-level recovery without interrupting plant telemetry collection.

## Public Example

Example health payload fragment:

```json
{
  "network": {
    "wifi_connected": true,
    "interface_up": true,
    "ssid": "example-wifi",
    "ip_address": "192.0.2.42",
    "default_gateway_reachable": true,
    "dns_resolution_ok": true,
    "internet_reachable": true,
    "wifi_profile_count": 1,
    "active_profile_present": true,
    "preferred_profile_present": true,
    "last_recovery_exit_code": 0
  }
}
```

`192.0.2.0/24` is reserved for documentation examples and is not a real deployment address.
