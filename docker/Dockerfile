# Two targets:
#   desktop (default)  XFCE, Remmina, Firefox and network tools
#   dev                desktop + VS Code, git, GitHub CLI, Python/uv, Claude Code and a Playwright browser for the agent,
#                      for coding in the browser
FROM ubuntu:26.04 AS desktop

ARG DEBIAN_FRONTEND=noninteractive

# greybird-gtk-theme and gnome-themes-extra add dark themes (Greybird-dark, Adwaita-dark) to choose in Settings; the
# default stays light, which the agent's OCR reads best
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xfce4 xfce4-terminal dbus-x11 x11-xkb-utils adwaita-icon-theme fonts-dejavu-core \
      greybird-gtk-theme gnome-themes-extra \
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
# GTK's image loader (glycin) sandboxes itself with bwrap, whose new /dev mount a container may not make; see the file
COPY --chmod=755 bwrap /usr/local/bin/bwrap
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

# ---------------------------------------------------------------------------------------------------------------------
FROM desktop AS dev

ARG DEBIAN_FRONTEND=noninteractive
USER root

# uv (Python versions and virtual environments) from its official multi-arch image
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /usr/local/bin/

# GitHub CLI from GitHub's APT repo; Node.js LTS with npm from NodeSource (Ubuntu's is Node 18, without npm);
# VS Code as Microsoft's .deb (updated by rebuilding the image). Its postinst would add Microsoft's APT repo with a
# second key path, which breaks apt; the debconf answer stops that.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /etc/apt/keyrings/nodesource.asc \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.asc] https://deb.nodesource.com/node_26.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list \
 && printf 'Package: nodejs\nPin: origin deb.nodesource.com\nPin-Priority: 1000\n' > /etc/apt/preferences.d/nodesource \
 && case "$(dpkg --print-architecture)" in \
      amd64) code_arch=x64 ;; arm64) code_arch=arm64 ;; *) echo "No VS Code build for this architecture" >&2; exit 1 ;; \
    esac \
 && curl -fsSL "https://update.code.visualstudio.com/latest/linux-deb-${code_arch}/stable" -o /tmp/code.deb \
 && echo "code code/add-microsoft-repo boolean false" | debconf-set-selections \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      /tmp/code.deb gh git git-lfs python3 python3-venv python3-pip python3-dev python-is-python3 \
      nodejs build-essential pkg-config make jq ripgrep unzip zip file \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /tmp/code.deb

# VS Code (Electron) starts a sandbox that needs user namespaces, which Docker's default seccomp profile blocks, so
# it crashes at start without a window. The container is the boundary here: start it without that sandbox, from the
# menu and desktop icon and from the terminal.
RUN sed -i 's|^Exec=/usr/share/code/code|& --no-sandbox|' /usr/share/applications/com.microsoft.VSCode*.desktop \
 && printf '#!/bin/sh\nexec /usr/share/code/bin/code --no-sandbox "$@"\n' > /usr/local/bin/code \
 && chmod 755 /usr/local/bin/code

# Playwright MCP server: lets an agent in the desktop (Claude Code in VS Code or the terminal) open a Chromium window on
# this screen, which the user watches and uses over noVNC. Pinned together with the Chromium build its Playwright
# expects (a newer server wants a newer build); update by raising the version and rebuilding. The browser lives in /opt,
# outside the home volume, so it always matches the image. Only the full Chromium: the window must be visible, so the
# separate headless build (about 280 MB) is left out.
ARG PLAYWRIGHT_MCP_VERSION=0.0.85
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install -g "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" \
 && "$(npm root -g)/@playwright/mcp/node_modules/.bin/playwright" install --with-deps --no-shell chromium \
 && rm -rf /var/lib/apt/lists/* /root/.npm

# Run by start.sh on every start: install Claude Code into the home volume (it then updates itself), register the
# Playwright MCP server with it, point git at gh
COPY --chmod=755 start.d/ /etc/jumpseat/start.d/

USER ubuntu
# Claude Code lives in ~/.local/bin; put it on PATH for the whole desktop, not only for login shells
ENV PATH=/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
