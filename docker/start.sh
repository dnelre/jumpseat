#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=:1

# Clean up stale X locks from a previous run of the same container
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1

# VNC_PASSWORD_FILE (e.g. a Docker secret) wins over VNC_PASSWORD, so the password stays out of the environment
if [[ -n "${VNC_PASSWORD_FILE:-}" ]]; then
  VNC_PASSWORD="$(head -n 1 "${VNC_PASSWORD_FILE}")"
fi
if [[ -z "${VNC_PASSWORD:-}" ]]; then
  VNC_PASSWORD="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
  VNC_PASSWORD="${VNC_PASSWORD:0:8}"
  echo "Generated VNC password: ${VNC_PASSWORD}"
fi

mkdir -p ~/.vnc
# VNC auth only uses the first 8 characters of the password
(umask 077 && printf '%s\n' "${VNC_PASSWORD}" | vncpasswd -f > ~/.vnc/passwd)

Xvnc "${DISPLAY}" \
  -geometry "${VNC_RESOLUTION}" -depth 24 \
  -rfbport 5901 -localhost yes \
  -SecurityTypes VncAuth -PasswordFile ~/.vnc/passwd \
  -AlwaysShared &

for _ in $(seq 50); do
  [[ -S /tmp/.X11-unix/X1 ]] && break
  sleep 0.1
done

# Must match the remote servers' layout, otherwise RDP gets wrong characters (e.g. "_" becomes "?")
setxkbmap "${KEYBOARD_LAYOUT}"

# Desktop icons for the apps this image has (VS Code and the terminal only in the dev target)
for app in org.remmina.Remmina:remmina firefox:firefox code:code xfce4-terminal:terminal; do
  src=/usr/share/applications/${app%%:*}.desktop
  dst=~/Desktop/${app#*:}.desktop
  if [[ -e "${src}" && ! -e "${dst}" ]]; then
    install -D -m 755 "${src}" "${dst}"
  fi
done

# VS Code: there is no keyring daemon in the container, so keep its secrets (e.g. GitHub login) in its own store
if command -v code >/dev/null && [[ ! -e ~/.vscode/argv.json ]]; then
  mkdir -p ~/.vscode
  printf '{\n  "password-store": "basic"\n}\n' > ~/.vscode/argv.json
fi

# Hooks added by an image target (e.g. installing Claude Code in dev). They must not stop the desktop.
for hook in /etc/jumpseat/start.d/*.sh; do
  if [[ -x "${hook}" ]]; then
    "${hook}" || echo "start hook ${hook} failed" >&2
  fi
done

# Remmina: new connections open maximised and the remote desktop follows the window size, so an RDP session fills
# the screen without toolbar clicks. Remmina keeps the defaults for new connections in the [remmina] group of its
# preferences ("Save as default" in the profile editor); set them only while the user has not chosen their own.
remmina_pref=~/.config/remmina/remmina.pref
if ! grep -qs '^resolution_mode=' "${remmina_pref}"; then
  mkdir -p ~/.config/remmina
  grep -qs '^\[remmina\]$' "${remmina_pref}" || printf '\n[remmina]\n' >> "${remmina_pref}"
  sed -i '/^\[remmina\]$/a resolution_mode=2\nscale=2\nwindow_maximize=1\nviewmode=1' "${remmina_pref}"
fi

dbus-launch --exit-with-session startxfce4 &

websockify --web /usr/share/novnc 6080 localhost:5901 &

echo "noVNC ready on port 6080"

# Exit (and let Docker restart/stop) if any of the services dies
wait -n
