# Security

## What this project does

jumpseat gives an AI agent the mouse, keyboard and clipboard of a Linux desktop, and through it of whatever that
desktop is logged in to, such as Windows servers over RDP. That is its purpose and not a vulnerability. Read the
Security section of the [README](README.md) before you use it with systems that matter.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use **Security → Report a vulnerability** on the GitHub page of this repository. That sends a private report to
the owner of this project. If that option is not shown, open an issue that only asks for a private way to make
contact, without details of the problem.

Useful to include: what you did, what happened, what you expected, and the commit you tested.

This is a personal project maintained in spare time. There is no guaranteed response time and no bounty.

## Supported versions

Only the latest commit on `main`.

## In scope

- Ways to reach or control the desktop without the VNC password or access to Docker on the host.
- Ways for a web page, a remote server or on-screen content to run commands on the host through `jumpseat.py`.
- Secrets leaking into logs, screenshots or files outside the documented places.

## Out of scope

- What an agent does with the access it was given, including after reading misleading text on the screen.
  The skill tells the agent to treat such text as data and to ask before changing anything, but that is an
  instruction to the agent and not a technical barrier.
- Anyone who can already run Docker commands on the host: they control the container by design.
- Vulnerabilities in the packaged software (Firefox, Remmina, noVNC, TigerVNC, XFCE); report those upstream.
  Rebuild the image to get their fixes.
