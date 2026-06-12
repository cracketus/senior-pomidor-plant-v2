#!/usr/bin/env bash
set -euo pipefail

MODE="hardware"
AUTO_REBOOT="false"
SKIP_START="false"
REBOOT_NEEDED="false"
DEVICE_ID_VALUE=""
MQTT_HOST_VALUE=""
POD1_ROM_VALUE=""
POD2_ROM_VALUE=""
POLL_INTERVAL_VALUE=""
POD2_ENABLED_VALUE=""

usage() {
  cat <<'EOF'
Usage: scripts/setup_raspberry_pi.sh [--hardware|--mock] [--auto-reboot] [--skip-start]

Options:
  --hardware      Prepare Raspberry Pi real sensor mode and run docker-compose.yml. Default.
  --mock          Prepare mock sensor mode and run docker-compose.mock.yml.
  --device-id ID  Set DEVICE_ID in .env.
  --mqtt-host IP  Set MQTT_HOST in .env.
  --pod1-rom ID   Set DS18B20_POD1_ROM in .env.
  --pod2-rom ID   Set DS18B20_POD2_ROM in .env.
  --pod2-disabled Set POD2_ENABLED=false in .env.
  --interval SEC  Set POLL_INTERVAL_SECONDS in .env.
  --auto-reboot   Reboot automatically if I2C or 1-Wire was enabled during this run.
  --skip-start    Prepare the host and .env, but do not start the container.
  -h, --help      Show this help.

Run from the repository root on Raspberry Pi OS:
  chmod +x scripts/setup_raspberry_pi.sh
  ./scripts/setup_raspberry_pi.sh --hardware --mqtt-host 192.168.1.10 --auto-reboot
EOF
}

log() {
  printf '[setup] %s\n' "$1"
}

die() {
  printf '[setup] ERROR: %s\n' "$1" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --hardware)
      MODE="hardware"
      ;;
    --mock)
      MODE="mock"
      ;;
    --device-id)
      shift
      [ "$#" -gt 0 ] || die "--device-id requires a value"
      DEVICE_ID_VALUE="$1"
      ;;
    --mqtt-host)
      shift
      [ "$#" -gt 0 ] || die "--mqtt-host requires a value"
      MQTT_HOST_VALUE="$1"
      ;;
    --pod1-rom)
      shift
      [ "$#" -gt 0 ] || die "--pod1-rom requires a value"
      POD1_ROM_VALUE="$1"
      ;;
    --pod2-rom)
      shift
      [ "$#" -gt 0 ] || die "--pod2-rom requires a value"
      POD2_ROM_VALUE="$1"
      ;;
    --pod2-disabled)
      POD2_ENABLED_VALUE="false"
      ;;
    --interval)
      shift
      [ "$#" -gt 0 ] || die "--interval requires a value"
      POLL_INTERVAL_VALUE="$1"
      ;;
    --auto-reboot)
      AUTO_REBOOT="true"
      ;;
    --skip-start)
      SKIP_START="true"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

[ "$(uname -s)" = "Linux" ] || die "This setup script must run on Linux/Raspberry Pi OS."
[ -f "docker-compose.yml" ] || die "Run this script from the repository root."
[ -f ".env.example" ] || die ".env.example is missing."

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
  TARGET_USER="${SUDO_USER:-root}"
else
  command -v sudo >/dev/null 2>&1 || die "sudo is required."
  SUDO=(sudo)
  TARGET_USER="$(id -un)"
fi

detect_boot_config() {
  if [ -f /boot/firmware/config.txt ]; then
    printf '%s\n' /boot/firmware/config.txt
  elif [ -f /boot/config.txt ]; then
    printf '%s\n' /boot/config.txt
  else
    die "Could not find Raspberry Pi boot config at /boot/firmware/config.txt or /boot/config.txt."
  fi
}

ensure_line() {
  local file="$1"
  local line="$2"
  local pattern="$3"

  if ! grep -Eq "$pattern" "$file"; then
    log "Adding '$line' to $file"
    printf '\n%s\n' "$line" | "${SUDO[@]}" tee -a "$file" >/dev/null
    REBOOT_NEEDED="true"
  fi
}

set_env_value() {
  local key="$1"
  local value="$2"

  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

install_host_packages() {
  log "Installing host packages"
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y ca-certificates curl git i2c-tools fswebcam v4l-utils libgpiod2 wireless-tools
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker is already installed"
  else
    log "Installing Docker"
    curl -fsSL https://get.docker.com | "${SUDO[@]}" sh
  fi

  if ! docker compose version >/dev/null 2>&1 && ! "${SUDO[@]}" docker compose version >/dev/null 2>&1; then
    die "Docker Compose plugin is not available after Docker installation."
  fi

  if [ "$TARGET_USER" != "root" ]; then
    "${SUDO[@]}" usermod -aG docker "$TARGET_USER" || true
  fi
}

enable_hardware_interfaces() {
  local config_file
  config_file="$(detect_boot_config)"

  log "Ensuring I2C and 1-Wire are enabled in $config_file"
  ensure_line "$config_file" "dtparam=i2c_arm=on" "^dtparam=i2c_arm=on$"
  ensure_line "$config_file" "dtoverlay=w1-gpio" "^dtoverlay=w1-gpio"
}

prepare_env_file() {
  if [ ! -f .env ]; then
    log "Creating .env from .env.example"
    cp .env.example .env
  else
    log ".env already exists; preserving existing values except MOCK_SENSORS"
  fi

  if [ "$MODE" = "hardware" ]; then
    set_env_value "MOCK_SENSORS" "false"
  else
    set_env_value "MOCK_SENSORS" "true"
  fi

  [ -z "$DEVICE_ID_VALUE" ] || set_env_value "DEVICE_ID" "$DEVICE_ID_VALUE"
  [ -z "$MQTT_HOST_VALUE" ] || set_env_value "MQTT_HOST" "$MQTT_HOST_VALUE"
  [ -z "$POD1_ROM_VALUE" ] || set_env_value "DS18B20_POD1_ROM" "$POD1_ROM_VALUE"
  [ -z "$POD2_ROM_VALUE" ] || set_env_value "DS18B20_POD2_ROM" "$POD2_ROM_VALUE"
  [ -z "$POD2_ENABLED_VALUE" ] || set_env_value "POD2_ENABLED" "$POD2_ENABLED_VALUE"
  [ -z "$POLL_INTERVAL_VALUE" ] || set_env_value "POLL_INTERVAL_SECONDS" "$POLL_INTERVAL_VALUE"
}

start_container() {
  if [ "$SKIP_START" = "true" ]; then
    log "Skipping container start"
    return
  fi

  if [ "$MODE" = "hardware" ]; then
    log "Starting hardware container"
    "${SUDO[@]}" docker compose up --build -d
  else
    log "Starting mock container"
    "${SUDO[@]}" docker compose -f docker-compose.mock.yml up --build -d
  fi
}

install_host_packages
install_docker

if [ "$MODE" = "hardware" ]; then
  enable_hardware_interfaces
fi

prepare_env_file

if [ "$REBOOT_NEEDED" = "true" ]; then
  log "A reboot is required before hardware interfaces are available."
  if [ "$AUTO_REBOOT" = "true" ]; then
    log "Rebooting now. Run this script again after the Raspberry Pi starts."
    "${SUDO[@]}" reboot
  fi
  log "Run 'sudo reboot', then rerun this script."
  exit 0
fi

start_container
log "Setup complete."
