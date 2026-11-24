# jumpseat

The jump seat is the extra seat in a cockpit. Here the agent rides along on your desktop, and you see everything it does.

A Linux desktop in a Docker container (XFCE, Remmina RDP/VNC client, Firefox and network tools) that you use in the browser via noVNC,
and that an AI coding agent can see and control at the same time.

- **You** open <http://localhost:6080> and work in the desktop as usual: connect to Windows servers over RDP, browse, and so on.
- **The agent** takes screenshots, clicks, types, reads the clipboard and reads Firefox page text with [scripts/jumpseat.py](scripts/jumpseat.py).
  Everything it does is visible live in your browser tab.

It ships as an [Agent Skill](SKILL.md) (GitHub Copilot, Claude Code and other agents that read `SKILL.md`), but `jumpseat.py` works fine on its own.

## Requirements

- Docker with the Compose plugin (Docker Desktop with WSL 2 works)
- Python 3.8+ on the host (only the standard library)

## Quick start

```sh
git clone https://github.com/dnelre/jumpseat.git
cd jumpseat
python3 scripts/jumpseat.py up       # first build takes a few minutes
python3 scripts/jumpseat.py status   # prints the URL and the random VNC password
```

Open `http://localhost:6080/vnc.html?autoconnect=true&resize=remote&password=<password>`.

Stop it with `python3 scripts/jumpseat.py down`. The home folder (Remmina profiles, settings) is kept in the `jumpseat_home` volume.

## Use as an agent skill

Put (or symlink) the repo in your agent's skills folder, for example:

```sh
ln -s "$PWD" ~/.copilot/skills/jumpseat   # GitHub Copilot
ln -s "$PWD" ~/.claude/skills/jumpseat    # Claude Code
```

The agent then follows [SKILL.md](SKILL.md): it opens the desktop for you, works in small screenshot → action → screenshot steps,
never types passwords and asks before anything that changes state.

## Commands

| Command | Purpose |
|---|---|
| `jumpseat.py up` / `down` / `status` | Start, stop, show URL, password and screen size |
| `jumpseat.py screenshot [--crop X,Y,W,H] [--grid]` | Save a PNG and print its path |
| `jumpseat.py text [--find TEXT]` | Screenshot plus the text on the screen with positions (OCR) |
| `jumpseat.py click X Y [--double] [--button right]`, `move`, `drag`, `scroll` | Mouse |
| `jumpseat.py click --text "OK"` | Find a text on the screen and click it |
| `jumpseat.py type "text"`, `jumpseat.py key ctrl+a Return`, `jumpseat.py paste "text"` | Keyboard |
| `jumpseat.py do "click X Y" "paste text" "key Return"` | Several mouse and keyboard steps in one call |
| `jumpseat.py copy [--all]` | Press ctrl+c in the focused window and print what was copied |
| `jumpseat.py ps-setup` | Create the opt-in shared-folder PowerShell runner files (does not start it) |
| `jumpseat.py ps --server NAME "COMMAND"` | Run a script through the manually enabled visible runner and read its log |
| `jumpseat.py clipboard ["text"]` | Read or set the desktop clipboard (shared with RDP) |
| `jumpseat.py page [URL]`, `js "EXPR"`, `teams` | Open a page in Firefox and read its text via WebDriver BiDi |
| `jumpseat.py controls`, `webclick "NAME"`, `webfill "FIELD" "TEXT"` | List, click and fill the controls of the page by name, without coordinates |

Coordinates are pixels in the last screenshot, also when it is cropped or scaled; `jumpseat.py` converts them to screen pixels
(`--screen` takes raw screen pixels instead). Mouse and keyboard commands wait until the screen stops changing, then save a new
screenshot and print its path and the region that changed, so an agent needs one call per step instead of two (`--no-shot` skips this).

### PowerShell logs instead of clipboard execution

The old hidden clipboard launcher triggered Defender and has been removed. The replacement is opt-in:
share a dedicated `Jumpseat` folder from the Linux/noVNC Desktop over RDP, manually start a visible runner in Windows,
and read scripts and output logs from that folder. No Defender exclusions or execution-policy bypasses are used.
**SOC approval and Defender lab validation are still required; zero alerts cannot be guaranteed.**
See [setup and limitations](docs/powershell.md). GUI, clipboard copying and OCR remain available without a runner.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `VNC_PASSWORD` | random | Only the first 8 characters are used |
| `VNC_PASSWORD_FILE` | | Read the password from this file instead (e.g. a mounted Docker secret) |
| `JUMPSEAT_TARGET` | `desktop` | Image target for `jumpseat.py up`: `desktop`, or `dev` for coding in the browser (see [Dev desktop](#dev-desktop)) |
| `VNC_RESOLUTION` | `1920x1080` | Initial size; noVNC resizes it to the browser window |
| `KEYBOARD_LAYOUT` | `no` | X keymap; must match the RDP servers' layout (e.g. `us`, `de`) |
| `FIREFOX_ACCEPT_TERMS` | `0` | `1` accepts Firefox's Terms of Use when the image is built, see [Firefox terms](#firefox-terms) |
| `VNC_SHOT_MAX` | `1280x768` | Screenshots are scaled down to fit this, so a vision model sees the same pixels it clicks (read by every `jumpseat.py` command, not only `up`) |

Example: `KEYBOARD_LAYOUT=us python3 scripts/jumpseat.py up`.

For a custom environment, copy [docker/compose.override.example.yaml](docker/compose.override.example.yaml) to
`docker/compose.override.yaml` (git-ignored) and replace the example values with your environment's settings.
The override can also set DNS servers, extra hosts or mounts using Docker Compose syntax; no organization-specific
settings are built in. `jumpseat.py up` loads it automatically. Applying a changed override may recreate the container
and disconnect active RDP sessions.

### Dark mode

The desktop starts light, because the agent's OCR reads dark-on-light text best. For dark mode, choose
**Greybird-dark** in Settings → Appearance → Style and in Settings → Window Manager → Style. The choice is kept in
the home volume. Firefox follows it; VS Code has its own color theme, and an RDP session shows the remote desktop's look.

## Dev desktop

The `dev` target adds VS Code, a terminal icon, git, git-lfs, the GitHub CLI, Python with [uv](https://docs.astral.sh/uv/),
Node.js LTS with npm, a C/C++ toolchain (`build-essential`), and Claude Code (installed into the home volume on the
first start; it updates itself). VS Code keeps its secrets in its own store (`password-store: basic`), because the
container has no keyring, and runs with `--no-sandbox`, because Chromium's sandbox needs user namespaces that Docker
blocks by default (the container is the boundary).

```sh
JUMPSEAT_TARGET=dev python3 scripts/jumpseat.py up
```

### Browsing together with the agent

The dev target also has the [Playwright MCP server](https://github.com/microsoft/playwright-mcp) with its own
Chromium, and registers it with Claude Code on start. Ask Claude Code in the desktop (VS Code or terminal) to open a
page: a Chromium window appears on the desktop, you watch it fill in forms and click, and you can take over in the same
window at any time (log in yourself, scroll to something) and ask the agent to look again. This is separate from
Firefox: the agent only sees the Chromium window it opened.

- Logins and cookies are kept in `~/.cache/playwright-mcp/profile` in the home volume. Closing the window loses only
  the open pages; the agent opens a new one on its next browser step.
- Screenshots and page snapshots go to `/tmp/playwright-mcp`, not into the open project.
- One browser at a time: while one agent session has the window open, another gets "Browser is already in use".
  Close the window (or that session's browser) first.
- The server and Chromium are pinned in the image (`PLAYWRIGHT_MCP_VERSION`) and updated by rebuilding. An existing
  `playwright` entry in `~/.claude.json` is never overwritten; remove it with `claude mcp remove --scope user playwright`
  to get the image's default back on the next start.

Pushes to `main` publish both targets for amd64 and arm64 to GitHub Packages: `ghcr.io/dnelre/jumpseat:latest`
and `ghcr.io/dnelre/jumpseat:dev`. Each publish also gets a fixed tag such as `dev-20261116-e51627a` (date and commit)
to roll back to; the 3 newest per image are kept and older versions are removed. To serve the dev desktop over the internet, put it behind a login proxy
(e.g. Caddy + Authelia), set the VNC password with `VNC_PASSWORD_FILE` as a second lock, and never publish port 6080 directly.

## Security

**Know what you are handing over.** The agent gets the mouse, keyboard and clipboard of this desktop, and with them
everything the desktop is logged in to. In an RDP session it acts with the rights of the account you logged in with,
and an enabled `jumpseat.py ps` runner executes script files with those rights.
Anyone who can write to its shared folder can submit commands. Keep it enabled only while needed.

- The skill tells the agent never to type passwords and to ask before anything that changes state. That is an
  instruction, not a barrier: use your agent's own approval settings for commands, and watch what it does.
- Text on the screen, in web pages and in command output can be written to mislead an agent. The skill tells it to
  treat such text as data, which lowers the risk but does not remove it.
- The noVNC port is bound to `127.0.0.1` only, and VNC uses a random password per container start. The password is at
  most 8 characters and appears in the container log and in the address you open, so do not publish the port.
- Anyone who can run Docker commands on the host controls the desktop.
- Firefox's remote debugging port listens only inside the container and is not published.
- GTK's image loaders (glycin) still run in their bubblewrap sandbox, but see the container's `/dev` instead of a fresh
  one, because a container may not mount a new one (`docker/bwrap`).
- Remmina stores saved passwords unencrypted in the home volume. Screenshots go to `$TMPDIR/jumpseat-shots`, can
  show sensitive data and are deleted after 30 minutes.

To report a security problem, see [SECURITY.md](SECURITY.md).

## Firefox terms

On a new home volume Firefox shows its Terms of Use dialog once. Until someone accepts it in the browser tab, the agent
cannot open new tabs (`jumpseat.py page URL` then uses the current tab and says that a dialog may be waiting).

To skip the dialog, build the image with `FIREFOX_ACCEPT_TERMS=1 python3 scripts/jumpseat.py up`, or put that setting in
`docker/.env` (git-ignored). This sets Firefox's `SkipTermsOfUse` policy. Only do so if you accept the
[Firefox Terms of Use](https://www.mozilla.org/about/legal/terms/firefox/) and Privacy Notice, for yourself and for
everyone who will use your image.

## Disclaimer

This is a personal project. It is provided as is under the MIT license, without warranty and without support, and
the owner of this project is not responsible for what an agent or anyone else does with it. It is not affiliated with
or endorsed by Mozilla, Microsoft, Remmina, noVNC, GitHub or Anthropic; their names only say what it works with.

## License

[MIT](LICENSE)
