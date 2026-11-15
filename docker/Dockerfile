FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xfce4 xfce4-terminal dbus-x11 x11-xkb-utils adwaita-icon-theme fonts-dejavu-core \
      tigervnc-standalone-server tigervnc-tools \
      novnc websockify \
      remmina remmina-plugin-rdp remmina-plugin-vnc freerdp3-x11 \
      ca-certificates tini \
      iputils-ping iputils-tracepath traceroute mtr-tiny bind9-dnsutils iproute2 \
      netcat-openbsd telnet curl wget openssh-client \
      nano less \
      xdotool imagemagick xclip python3-websockets tesseract-ocr tesseract-ocr-nor \
      fonts-noto-color-emoji fonts-liberation \
 && rm -rf /var/lib/apt/lists/* \
 && cp /usr/share/applications/org.remmina.Remmina.desktop /etc/xdg/autostart/ \
 && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix \
 && printf '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=1&resize=remote">\n' \
      > /usr/share/novnc/index.html

# Ubuntu's firefox package is only a snap wrapper, which does not work in Docker; use Mozilla's APT repo
RUN install -d -m 0755 /etc/apt/keyrings \
 && curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg -o /etc/apt/keyrings/packages.mozilla.org.asc \
 && echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" \
      > /etc/apt/sources.list.d/mozilla.list \
 && printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' > /etc/apt/preferences.d/mozilla \
 && apt-get update \
 && apt-get install -y --no-install-recommends firefox \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /usr/lib/firefox/defaults/pref \
 && printf '// Keep normal browser behaviour when the remote agent is on (no automation prefs)\npref("remote.prefs.recommended", false);\n' \
      > /usr/lib/firefox/defaults/pref/vnc-remote.js

COPY --chmod=755 start.sh /usr/local/bin/start.sh
COPY --chmod=755 firefox /usr/local/bin/firefox
# No welcome tab or other first-run pages: the agent and the user start at the page they asked for
COPY firefox-policies.json /etc/firefox/policies/policies.json
# Firefox's terms dialog blocks the agent on a new home volume. Whoever builds the image may accept the terms for
# its users by setting FIREFOX_ACCEPT_TERMS=1 (see README); the default leaves the question to Firefox.
ARG FIREFOX_ACCEPT_TERMS=0
RUN if [ "${FIREFOX_ACCEPT_TERMS}" = "1" ]; then \
      sed -i 's/"policies": {/"policies": {\n    "SkipTermsOfUse": true,/' /etc/firefox/policies/policies.json; \
    fi
COPY --chmod=755 free-windows-shortcuts /usr/local/bin/free-windows-shortcuts
COPY free-windows-shortcuts.desktop /etc/xdg/autostart/

# Ubuntu 24.04 ships a non-root "ubuntu" user (uid 1000)
USER ubuntu
WORKDIR /home/ubuntu

ENV VNC_RESOLUTION=1920x1080 \
    VNC_PASSWORD= \
    KEYBOARD_LAYOUT=no \
    LANG=C.UTF-8 \
    NO_AT_BRIDGE=1

EXPOSE 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start.sh"]
