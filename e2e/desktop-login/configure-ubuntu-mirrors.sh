#!/usr/bin/env bash
set -euo pipefail

# Select a reachable archive before apt retries every unavailable Ubuntu suite.
# Every probe must carry a signature from the runner's Ubuntu archive keyring;
# apt retains its normal signature and package hash verification afterward.
mirror_probe="$(mktemp)"
trap 'rm -f "$mirror_probe"' EXIT
selected_mirror=""
for candidate in \
  https://mirrors.kernel.org/ubuntu \
  https://mirror.math.princeton.edu/pub/ubuntu \
  https://archive.ubuntu.com/ubuntu; do
  if curl --fail --location --silent --show-error --retry 1 --retry-all-errors \
      --connect-timeout 5 --max-time 15 \
      "$candidate/dists/noble-updates/InRelease" --output "$mirror_probe" \
    && gpgv --keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg "$mirror_probe" > /dev/null 2>&1; then
    selected_mirror="$candidate"
    break
  fi
done
if [ -z "$selected_mirror" ]; then
  echo "No reachable, Ubuntu-signed package mirror was found." >&2
  exit 1
fi
echo "Using Ubuntu package mirror: $selected_mirror"
for mirror_list in /etc/apt/apt-mirrors.txt /etc/apt/apt-mirrors-security.txt; do
  if [ -f "$mirror_list" ]; then
    printf '%s\n' "$selected_mirror" | sudo tee "$mirror_list" > /dev/null
  fi
done
printf '%s\n' 'Acquire::Retries "3";' 'Acquire::http::Timeout "30";' 'Acquire::https::Timeout "30";' \
  | sudo tee /etc/apt/apt.conf.d/80puppyone-ci-network > /dev/null
