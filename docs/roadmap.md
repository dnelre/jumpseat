# Roadmap

What makes an agent fast with this desktop, what has been measured, and what is still open.

## What decides the speed

`jumpseat.py` itself is fast: about 0.2 s for a screenshot and 1 to 2 s for an action with its follow-up screenshot.
The time goes to the agent's round trips to its model, and each one costs more when it carries an image.
So changes are judged by, in this order:

1. fewer round trips per task,
2. fewer images per round trip (text instead),
3. fewer misses (no coordinate guessing).

## Measurements

One new chat per task with Opus 5.5 in Claude Code, against a throwaway Windows Server 2022 domain controller with
test users and groups (kept outside this repository). "Before" has one call per action, text search on the screen
and copy and paste; "after" adds `jumpseat.py ps` and the rule to prefer it for information requests. All answers were
correct in both runs.

| Task | Before: time, tool calls, images | After: time, tool calls, images |
|---|---|---|
| Which groups is an account in? | 45 s, 10, 2 | 20 s, 4, 0 |
| Title and department of a user | 46 s, 13, 4 | 25 s, 3, 0 |
| Disabled or expired accounts | 105 s, 18, 2 | 20 s, 4, 0 |
| First paragraph of a page in Edge on the server | 49 s, 11, 3 | 37 s, 9, 2 |

About three of the calls per chat went to finding and reading `SKILL.md`, because the skill was not installed as a skill.
Not measured yet: GitHub Copilot, and an agent that looks at the desktop through a shared browser tab.

## Open

**Agent loop**
- After an action, return only the changed region as the image when it is small, and offer text instead of an image.
- Shorten `SKILL.md`: a short core, with tips and configuration in a reference file read on demand.
- Find the best screenshot size per model (`VNC_SHOT_MAX`).
- A lock, so that two agents on one desktop cannot collide on the clipboard, the keyboard or Firefox's remote session.

**RDP in Remmina**
- Give the server the whole screen: Remmina's tab bar and toolbar take space and shift coordinates.
- Tune the RDP quality settings for sharp text and no animations, and the "screen stopped changing" wait from
  measured repaint times.
- Print the session name with every screenshot, and report a dropped session as text.
- Check the keyboard layout end to end once per session.
- Find out why keys stop reaching the server after `Control_R` (Remmina's host key), or unbind that key.
- Make `copy --all` remove the selection it leaves behind; improve OCR on selected rows.

**Firefox**
- Controls inside frames and shadow DOM.
- List tabs and read one that is not in front.
- Return only the main content of a page, one selector, or the lines around a search word.

**If the agent uses a shared browser tab to look or act**
- A noVNC page without the control bar, so page coordinates equal desktop coordinates.
- A fixed desktop size instead of following the window.

**Housekeeping**
- CI: ShellCheck, Hadolint and Ruff, and a smoke test that starts the container and runs `screenshot`, `text` and `copy`.
- Unit tests for the coordinate conversion and the text matching.
- Image size (about 1.2 GB).
- A configurable screenshot folder for sandboxed editors.
