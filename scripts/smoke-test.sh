#!/usr/bin/env bash
# Start the image in a throwaway container and check that the desktop really comes up.
# A successful build does not prove that: a new base image can build fine and still crash XFCE at start.
#   scripts/smoke-test.sh [IMAGE]   (default: jumpseat, as built by "docker compose -f docker/compose.yaml build")
# It uses its own container and publishes no port, so it does not disturb a running jumpseat desktop.
set -euo pipefail

image="${1:-jumpseat}"
name="jumpseat-smoke-$$"
log="$(mktemp)"

finish() {
  status=$?
  if (( status != 0 )); then
    echo "--- container log ---"
    grep -v 'Generated VNC password' "${log}" || true
  fi
  docker stop -t 2 "${name}" >/dev/null 2>&1 || true
  rm -f "${log}"
  exit "${status}"
}
trap finish EXIT

# --rm removes the container as soon as it stops, so the log is followed into a file from the start
docker run --rm -d --name "${name}" --shm-size 1g "${image}" >/dev/null
docker logs -f "${name}" >"${log}" 2>&1 &

inside() {
  docker exec -e DISPLAY=:1 "${name}" "$@"
}

# wait_for SECONDS DESCRIPTION COMMAND...: retry the command inside the container until it succeeds
wait_for() {
  local seconds="$1" what="$2"
  shift 2
  for (( i = 0; i < seconds * 2; i++ )); do
    if inside "$@" >/dev/null 2>&1; then
      echo "ok: ${what}"
      return 0
    fi
    if ! docker inspect "${name}" >/dev/null 2>&1; then
      echo "FAILED: ${what} (the container exited)"
      return 1
    fi
    sleep 0.5
  done
  echo "FAILED: ${what} (waited ${seconds} s)"
  return 1
}

wait_for 30 "X display answers" xdotool getdisplaygeometry
wait_for 60 "XFCE panel is on screen" xdotool search --onlyvisible --class xfce4-panel
wait_for 60 "Remmina window is on screen" xdotool search --onlyvisible --class org.remmina.Remmina
wait_for 30 "noVNC page is served" curl -fsS -o /dev/null http://localhost:6080/vnc.html

# A desktop that crashes does so within seconds of showing up, and takes the container with it
sleep 10
wait_for 1 "desktop is still up after 10 s" xdotool search --onlyvisible --class xfce4-panel

echo "Smoke test passed for image '${image}'"
