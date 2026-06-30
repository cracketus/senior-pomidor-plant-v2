#!/usr/bin/env bash
set -euo pipefail

WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
WIFI_PROFILE_DIR="${WIFI_PROFILE_DIR:-/etc/NetworkManager/system-connections}"
WIFI_PREFERRED_PROFILE="${WIFI_PREFERRED_PROFILE:-}"
NETWORK_CHECK_HOST="${NETWORK_CHECK_HOST:-1.1.1.1}"
NETWORK_DNS_CHECK_HOST="${NETWORK_DNS_CHECK_HOST:-example.com}"
WIFI_BACKUP_DIR="${WIFI_BACKUP_DIR:-data/backups/networkmanager}"
NETWORK_RECOVERY_STATUS_FILE="${NETWORK_RECOVERY_STATUS_FILE:-data/network-recovery/status.json}"

timestamp_utc() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_status() {
  local action="$1"
  local result="$2"
  local exit_code="$3"
  local message="${4:-}"
  local target_dir
  target_dir="$(dirname "$NETWORK_RECOVERY_STATUS_FILE")"
  mkdir -p "$target_dir"
  printf '{"timestamp_utc":"%s","action":"%s","result":"%s","exit_code":%s,"message":"%s"}\n' \
    "$(timestamp_utc)" \
    "$(json_escape "$action")" \
    "$(json_escape "$result")" \
    "$exit_code" \
    "$(json_escape "$message")" > "$NETWORK_RECOVERY_STATUS_FILE"
}

profile_count() {
  if [ ! -d "$WIFI_PROFILE_DIR" ]; then
    printf '0\n'
    return
  fi
  find "$WIFI_PROFILE_DIR" -maxdepth 1 -type f -name '*.nmconnection' | wc -l | tr -d ' '
}

backup_profiles() {
  local count
  count="$(profile_count)"
  [ "$count" -gt 0 ] || return 0
  local backup_target
  backup_target="$WIFI_BACKUP_DIR/$(date -u '+%Y%m%dT%H%M%SZ')"
  mkdir -p "$backup_target"
  cp -p "$WIFI_PROFILE_DIR"/*.nmconnection "$backup_target"/
}

newest_backup_dir() {
  if [ ! -d "$WIFI_BACKUP_DIR" ]; then
    return 1
  fi
  find "$WIFI_BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1
}

preferred_profile_exists() {
  [ -n "$WIFI_PREFERRED_PROFILE" ] || return 0
  [ -f "$WIFI_PROFILE_DIR/$WIFI_PREFERRED_PROFILE.nmconnection" ]
}

selected_profile() {
  if [ -n "$WIFI_PREFERRED_PROFILE" ]; then
    printf '%s\n' "$WIFI_PREFERRED_PROFILE"
    return 0
  fi
  local profile_path
  profile_path="$(find "$WIFI_PROFILE_DIR" -maxdepth 1 -type f -name '*.nmconnection' | sort | head -n 1)"
  [ -n "$profile_path" ] || return 1
  profile_path="$(basename "$profile_path")"
  printf '%s\n' "${profile_path%.nmconnection}"
}

wifi_connected() {
  nmcli -t -f DEVICE,STATE device status | grep -Eq "^${WIFI_INTERFACE}:connected$"
}

connect_wifi() {
  local profile
  profile="$(selected_profile)"
  [ -n "$profile" ] || return 1
  nmcli connection up "$profile"
}

reload_connections() {
  nmcli connection reload
}

restore_profiles() {
  local source_dir
  source_dir="$(newest_backup_dir)" || return 1
  [ -n "$source_dir" ] || return 1
  [ -d "$source_dir" ] || return 1
  find "$source_dir" -maxdepth 1 -type f -name '*.nmconnection' | grep -q . || return 1

  mkdir -p "$WIFI_PROFILE_DIR"
  cp -p "$source_dir"/*.nmconnection "$WIFI_PROFILE_DIR"/
  chmod 600 "$WIFI_PROFILE_DIR"/*.nmconnection
  chown root:root "$WIFI_PROFILE_DIR"/*.nmconnection
  reload_connections
}

gateway_reachable() {
  local gateway
  gateway="$(ip route show default | awk '/default/ {print $3; exit}')"
  [ -n "$gateway" ] || return 1
  ping -c 1 -W 2 "$gateway" >/dev/null 2>&1
}

dns_ok() {
  getent hosts "$NETWORK_DNS_CHECK_HOST" >/dev/null 2>&1
}

internet_reachable() {
  ping -c 1 -W 2 "$NETWORK_CHECK_HOST" >/dev/null 2>&1
}

main() {
  backup_profiles

  local count
  count="$(profile_count)"
  if [ "$count" -eq 0 ]; then
    if restore_profiles; then
      if connect_wifi; then
        write_status "restore_profiles" "recovered" 0 "Restored NetworkManager Wi-Fi profiles from backup."
        return 0
      fi
      write_status "restore_profiles" "failed" 2 "Profiles restored, but Wi-Fi connection did not come up."
      return 2
    fi
    write_status "restore_profiles" "failed" 3 "No Wi-Fi profiles and no usable backup were found."
    return 3
  fi

  if ! preferred_profile_exists; then
    write_status "check_profiles" "failed" 4 "Preferred Wi-Fi profile is missing."
    return 4
  fi

  if ! wifi_connected; then
    if connect_wifi; then
      write_status "connect_wifi" "recovered" 0 "Wi-Fi connection restored with nmcli."
      return 0
    fi
    systemctl restart NetworkManager
    if connect_wifi; then
      write_status "restart_networkmanager" "recovered" 0 "NetworkManager restart restored Wi-Fi."
      return 0
    fi
    write_status "connect_wifi" "failed" 5 "Wi-Fi connection could not be restored."
    return 5
  fi

  if gateway_reachable && dns_ok && internet_reachable; then
    write_status "check_network" "ok" 0 "Network health checks passed."
    return 0
  fi

  write_status "check_network" "degraded" 6 "Wi-Fi is connected, but one or more reachability checks failed."
  return 6
}

main "$@"
