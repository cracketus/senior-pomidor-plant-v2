# Senior Pomidor Incident Report

**Incident ID:** SP-2026-06-29-NET-001

**Subsystem:** Raspberry Pi Edge Node

**Category:** Network Configuration Failure

**Severity:** High

**Impact:** Loss of remote access and telemetry

**Status:** Resolved

---

# Executive Summary

The Raspberry Pi edge node unexpectedly lost its Wi-Fi configuration, causing complete loss of network connectivity while the operating system, Docker services, and SSH daemon remained operational.

The incident initially appeared to be an SSH failure because TCP connections to port 22 could still be established from the client, but the SSH handshake was terminated immediately.

Further investigation revealed that the root cause was not the SSH service itself but the complete disappearance of all NetworkManager Wi-Fi connection profiles.

Recreating the Wi-Fi profile restored normal operation, and the configuration persisted after reboot.

---

# Timeline

## Initial Symptoms

The Grafana dashboard stopped receiving telemetry from the Raspberry Pi.

Remote SSH access failed with:

```
kex_exchange_identification: Connection closed by remote host
```

Windows diagnostics showed:

```
TcpTestSucceeded : True
```

indicating that TCP connectivity to port 22 still existed.

---

## Local Console Investigation

The Raspberry Pi booted successfully.

Login via local keyboard was possible.

Boot screen displayed:

```
My IP address is 127.0.1.1
```

indicating that no external network interface had obtained an IP address.

---

# Investigation

## 1. SSH Service

Status:

```
systemctl status ssh
```

Result:

- ssh.service running
- Listening on IPv4
- Listening on IPv6

Conclusion:

SSH server was healthy.

---

## 2. Disk Space

```
df -h
```

Filesystem usage:

```
10%
```

Conclusion:

No storage exhaustion.

---

## 3. Network Interfaces

```
hostname -I
```

Returned:

```
172.17.x.x
172.18.x.x
```

These addresses belonged only to Docker bridge networks.

No LAN address existed.

---

## 4. Wi-Fi Interface

```
ip addr show wlan0
```

Result:

```
state DOWN
NO-CARRIER
```

Conclusion:

Wireless interface existed but was not connected.

---

## 5. Driver Verification

```
dmesg | grep brcm
```

Result:

Broadcom firmware loaded correctly.

No driver errors.

---

## 6. RF Kill

```
rfkill list
```

Result:

```
Soft blocked: no
Hard blocked: no
```

Conclusion:

Wireless radio was enabled.

---

## 7. NetworkManager

```
nmcli device
```

Result:

```
wlan0 disconnected
```

NetworkManager itself was operational.

---

## 8. Wi-Fi Scan

```
nmcli device wifi list
```

Result:

All nearby access points were detected successfully.

Hardware and radio were functioning correctly.

---

## 9. Connection Profiles

```
nmcli connection show
```

Returned only:

- loopback
- docker bridge
- ethernet

No Wi-Fi profile existed.

---

## 10. NetworkManager Configuration Directory

```
ls /etc/NetworkManager/system-connections/
```

Result:

```
(empty)
```

This confirmed that every persistent Wi-Fi connection profile had disappeared.

---

# Root Cause

The Raspberry Pi lost all persistent NetworkManager Wi-Fi connection profiles.

Without any stored profile, NetworkManager had no knowledge of:

- SSID
- security settings
- credentials
- auto-connect configuration

Consequently:

- Wi-Fi hardware remained functional.
- Driver remained functional.
- NetworkManager remained functional.
- SSH daemon remained functional.

However, the node had no network connectivity beyond local Docker bridges.

---

# Recovery Procedure

A new Wi-Fi profile was created:

```
nmcli device wifi connect "<SSID>" password "<PASSWORD>"
```

Immediately after connection:

```
hostname -I
```

returned a valid LAN address.

SSH became available again.

---

# Verification

After reboot:

```
ls /etc/NetworkManager/system-connections/
```

showed:

```
<SSID>.nmconnection
```

Auto-connect worked normally.

Node successfully rejoined the network.

Telemetry resumed.

---

# Root Cause Confidence

| Hypothesis | Confidence |
|------------|-----------:|
| Wi-Fi profile deleted or lost | High |
| Driver failure | Very Low |
| SSH failure | None |
| Hardware failure | None |
| Docker networking issue | None |
| SD card corruption | Low |

---

# Lessons Learned

The incident demonstrated that successful boot does not imply successful network availability.

Several core services remained operational while the edge node became completely isolated from the network.

Traditional service monitoring would not detect this failure.

---

# Recommended Monitoring Metrics

The edge node should continuously monitor:

- Wi-Fi connection state
- Active IP address
- Internet reachability
- Gateway reachability
- DNS resolution
- SSH daemon state
- NetworkManager state
- Presence of Wi-Fi profiles
- Docker bridge isolation
- Disk usage
- Memory usage
- CPU temperature

---

# Senior Pomidor Technical Specification

## Feature

Edge Node Self-Healing and Health Monitoring

---

## Objective

Enable the Raspberry Pi edge node to autonomously detect network failures, diagnose probable causes, attempt recovery, and report anomalies to the central server.

---

## Health Checks

Execute every 60 seconds.

### Network

Check:

- wlan0 state
- active SSID
- signal strength
- IP address
- default gateway
- DNS
- Internet connectivity

---

### Configuration Integrity

Verify:

```
/etc/NetworkManager/system-connections/
```

Requirements:

- directory exists
- contains at least one `.nmconnection`
- active profile matches current SSID

Missing profiles should generate a **Critical Configuration Alert**.

---

### SSH

Verify:

- ssh.service running
- port 22 listening

---

### Docker

Verify:

- Docker daemon running
- required containers healthy

---

### Storage

Check:

- filesystem usage
- inode usage
- SD card health (when available)

---

### System

Check:

- CPU temperature
- RAM usage
- load average
- uptime

---

## Automatic Recovery Strategy

### Level 1

If Wi-Fi is disconnected:

```
nmcli connection up <preferred_profile>
```

---

### Level 2

If unsuccessful:

```
systemctl restart NetworkManager
```

Retry connection.

---

### Level 3

If Wi-Fi profiles are missing:

Generate:

```
CRITICAL_CONFIGURATION_LOSS
```

Attempt recovery from a local backup of `.nmconnection` files.

---

### Level 4

If network cannot be restored:

Store anomaly locally.

Continue collecting sensor data.

Retry periodically without interrupting plant monitoring.

---

## Telemetry

Publish node health metrics alongside plant telemetry.

Suggested payload:

```json
{
  "network": {
    "wifi_connected": true,
    "ssid": "WLAN16849707",
    "signal_percent": 98,
    "ip_address": "192.168.1.42",
    "internet": true
  },
  "system": {
    "disk_percent": 10,
    "memory_percent": 31,
    "cpu_temp": 54.1
  },
  "services": {
    "ssh": true,
    "docker": true,
    "network_manager": true
  },
  "configuration": {
    "wifi_profiles": 1
  }
}
```

---

## Grafana Dashboard

Add a dedicated **Node Health** dashboard containing:

- Wi-Fi status
- Signal quality
- Current SSID
- IP address
- Internet availability
- SSH status
- Docker status
- Filesystem usage
- CPU temperature
- RAM usage
- Uptime
- Number of stored Wi-Fi profiles
- Recovery attempts
- Last successful server contact

---

## Long-Term Goal

The Raspberry Pi should evolve from a passive telemetry collector into a self-managing autonomous edge node capable of detecting infrastructure failures, recovering from common faults, preserving scientific observations during outages, and minimizing the need for human intervention.