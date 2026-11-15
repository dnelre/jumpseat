#!/usr/bin/env python3
"""Start, inspect and control the jumpseat container (XFCE + Remmina, viewed in the browser via noVNC)."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

CONTAINER = "jumpseat"
URL = "http://localhost:6080"
DOCKER_DIR = Path(__file__).resolve().parent.parent / "docker"
SHOT_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "jumpseat-shots"
SHOT_MAX_AGE_S = 30 * 60
# Screenshots are scaled down to fit this box. Vision models shrink larger images themselves, and then the
# pixels they see no longer match the pixels they must click.
SHOT_MAX = os.environ.get("VNC_SHOT_MAX", "1280x768")
# The view (screen region and image size) of the last screenshot; pointer coordinates are pixels in that image
VIEW_FILE = SHOT_DIR / "last-view.json"
SETTLE_TIMEOUT_S = 3.0
# Tesseract languages for finding text on the screen (the image ships eng and nor)
OCR_LANG = os.environ.get("VNC_OCR_LANG", "eng+nor")
BUTTONS = {"left": "1", "middle": "2", "right": "3"}
PAGE_MAX_CHARS = 30000

# Runs inside the container: performs the steps in JUMPSEAT_JOB, waits until the screen stops changing and writes one
# JSON line (screen size, view, changed region, OCR text) followed by the PNG screenshot to stdout.
DESKTOP_JOB = r"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

job = json.loads(os.environ["JUMPSEAT_JOB"])
tmp = tempfile.mkdtemp(prefix="jumpseat-job-")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
OCR_SCALE = 2  # screen fonts are too small for Tesseract at 1:1

class Stop(Exception):
    pass

def run(*cmd, quiet=False):
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE if quiet else None).stdout
    except FileNotFoundError:
        sys.exit("%s is not installed in the container. Rebuild the image with: jumpseat.py up" % cmd[0])
    except subprocess.CalledProcessError as e:
        sys.exit("%s failed (%d) %s" % (cmd[0], e.returncode, (e.stderr or b"").decode(errors="replace").strip()))

def grab(name):
    path = os.path.join(tmp, name)
    run("import", "-window", "root", "-depth", "8", "ppm:" + path)
    with open(path, "rb") as f:
        return path, hashlib.md5(f.read()).digest()

def settle(before, timeout, grace):
    # Capture until two consecutive frames are equal. While nothing differs from `before`,
    # keep waiting up to `grace` seconds for a reaction to start.
    start = time.monotonic()
    time.sleep(0.1)
    path, digest = grab("cur.ppm")
    while True:
        time.sleep(0.15)
        new_path, new_digest = grab("new.ppm")
        stable = new_digest == digest
        os.replace(new_path, path)
        digest = new_digest
        elapsed = time.monotonic() - start
        if stable and (digest != before or elapsed >= grace):
            return path, digest, True
        if elapsed >= timeout:
            return path, digest, False

def ocr(path, region=None):
    # Text lines on the frame (or a region of it), top to bottom: {"text", "box", "words": [[text, x, y, w, h]]}
    # in screen pixels.
    ox = oy = 0
    args = ["convert", path]
    if region:
        ox, oy = region[0], region[1]
        args += ["-crop", "%dx%d+%d+%d" % (region[2], region[3], ox, oy), "+repage"]
    # Two passes side by side, because no single setting reads everything on a screen: adaptive thresholding for
    # normal text, and the negative with a global threshold for selected rows and other light-on-colour text.
    # psm 11 is Tesseract's mode for sparse text such as user interfaces.
    procs = []
    for n, (negate, method) in enumerate(((False, "2"), (True, "0"))):
        pgm = os.path.join(tmp, "ocr%d.pgm" % n)
        run(*args, "-colorspace", "Gray", *(["-negate"] if negate else []), "-filter", "Catrom",
            "-resize", "%d%%" % (OCR_SCALE * 100), "pgm:" + pgm)
        try:
            procs.append(subprocess.Popen(["tesseract", pgm, "-", "-l", job["lang"], "--psm", "11", "-c",
                                           "thresholding_method=" + method, "tsv"],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        except FileNotFoundError:
            sys.exit("tesseract is not installed in the container. Rebuild the image with: jumpseat.py up")
    lines, seen = [], []
    for proc in procs:
        out, err = proc.communicate()
        if proc.returncode:
            sys.exit("tesseract failed (%d) %s" % (proc.returncode, err.decode(errors="replace").strip()))
        groups = {}
        for row in out.decode("utf-8", "replace").splitlines():
            c = row.split("\t")
            if len(c) != 12 or c[0] != "5" or not c[11].strip() or float(c[10]) < 30:
                continue
            x, y, w, h = (int(v) // OCR_SCALE for v in c[6:10])
            # Skip words the earlier pass already has (their centre lies in one of its word boxes)
            cx, cy = ox + x + w / 2, oy + y + h / 2
            if any(sx <= cx <= sx + sw and sy <= cy <= sy + sh for sx, sy, sw, sh in seen):
                continue
            groups.setdefault(tuple(c[2:5]), []).append([c[11].strip(), ox + x, oy + y, max(w, 1), max(h, 1)])
        for words in groups.values():
            lines.append({"text": " ".join(w[0] for w in words), "box": union(words), "words": words})
        seen = [w[1:] for ln in lines for w in ln["words"]]
    lines.sort(key=lambda ln: (ln["box"][1], ln["box"][0]))
    return lines

def union(words):
    x0, y0 = min(w[1] for w in words), min(w[2] for w in words)
    x1, y1 = max(w[1] + w[3] for w in words), max(w[2] + w[4] for w in words)
    return [x0, y0, x1 - x0, y1 - y0]

def find(lines, query):
    # Occurrences of the query (case and spacing ignored) as [{"text", "box", "rank"}], in reading order.
    # rank 2: the whole line, 1: whole words, 0: part of a word ("Lab" in "lab.test")
    norm = lambda t: "".join(t.casefold().split())
    needle = norm(query)
    hits = []
    for ln in lines:
        hay, owner = "", []
        for i, w in enumerate(ln["words"]):
            t = norm(w[0])
            hay += t
            owner += [i] * len(t)
        start = hay.find(needle) if needle else -1
        if start >= 0:
            end = start + len(needle)
            words = ln["words"][owner[start]:owner[end - 1] + 1]
            whole = (start == 0 or owner[start - 1] != owner[start]) and (end == len(hay) or owner[end] != owner[end - 1])
            hits.append({"text": " ".join(w[0] for w in words), "box": union(words),
                         "rank": 2 if hay == needle else 1 if whole else 0})
    return hits

def resolve(step, frame, done):
    # The xdotool arguments of a step; a "text" target is located on the current frame with OCR
    args = step["args"]
    if "text" in step:
        # The text may still be on its way (page loading, slow server): look again until the wait runs out
        deadline = time.monotonic() + job["wait"]
        while True:
            hits = find(ocr(frame), step["text"])
            if hits or time.monotonic() >= deadline:
                break
            time.sleep(0.3)
            frame = grab("look.ppm")[0]
        nth = step.get("nth")
        if nth:
            pick = hits[nth - 1] if nth <= len(hits) else None
        else:
            # The best kind of match wins when there is exactly one of it
            best = [h for h in hits if h["rank"] == max(x["rank"] for x in hits)]
            pick = best[0] if len(best) == 1 else None
        if not pick:
            raise Stop({"error": "ambiguous" if hits and not nth else "no_match", "query": step["text"],
                        "done": done, "candidates": [[h["text"]] + h["box"] for h in hits]}, frame)
        x, y, w, h = pick["box"]
        args = [str(x + w // 2) if a == "{x}" else str(y + h // 2) if a == "{y}" else a for a in args]
    if "clipboard" in step:
        # xclip stays in the background to serve the selection; give Remmina a moment to offer it to the RDP server
        subprocess.run("xclip -i -selection clipboard >/dev/null 2>&1", shell=True, check=True,
                       input=step["clipboard"].encode())
        time.sleep(0.2)
    return args

def grid(w, h, step=100):
    xs, ys = range(step, w, step), range(step, h, step)
    lines = ["line %d,0 %d,%d" % (x, x, h) for x in xs] + ["line 0,%d %d,%d" % (y, w, y) for y in ys]
    if not lines:
        return []
    args = ["-fill", "none", "-stroke", "rgba(255,0,255,0.5)", "-strokewidth", "1", "-draw", " ".join(lines),
            "-font", FONT, "-pointsize", "11", "-stroke", "none", "-fill", "white",
            "-undercolor", "rgba(190,0,190,0.9)"]
    for x in xs:
        args += ["-annotate", "+%d+11" % (x + 2), str(x)]
    for y in ys:
        args += ["-annotate", "+2+%d" % (y - 3), str(y)]
    return args

def render(info, path, sw, sh, crop=None, zoom=None, with_grid=False, with_text=False):
    # Add the view to info and return the PNG of the frame (or of a region of it)
    if crop:
        x, y = min(max(crop[0], 0), sw - 1), min(max(crop[1], 0), sh - 1)
        w, h = max(1, min(crop[2], sw - x)), max(1, min(crop[3], sh - y))
    else:
        x, y, w, h = 0, 0, sw, sh
    if not zoom:
        # Shrink to fit the box; enlarge crops by a whole factor (sharp pixels) when there is room
        fit = min(job["max"][0] / w, job["max"][1] / h)
        zoom = fit if fit < 1 else (min(3, int(fit)) if crop else 1)
    ow, oh = max(1, round(w * zoom)), max(1, round(h * zoom))
    info.update(origin=[x, y], region=[w, h], image=[ow, oh])
    if with_text:
        info["lines"] = [[ln["text"]] + ln["box"] for ln in ocr(path, [x, y, w, h] if crop else None)]

    args = ["convert", path]
    if crop:
        args += ["-crop", "%dx%d+%d+%d" % (w, h, x, y), "+repage"]
    if (ow, oh) != (w, h):
        if zoom > 1:
            args += ["-filter", "point"]
        args += ["-resize", "%dx%d!" % (ow, oh)]
    if with_grid:
        args += grid(ow, oh)
    return run(*args, "png:-")

def main():
    sw, sh = (int(v) for v in run("xdotool", "getdisplaygeometry").split())
    info = {"screen": [sw, sh]}
    if job.get("expect_screen") not in (None, [sw, sh]):
        info["error"] = "screen_changed"
        return info, b""
    steps = job["steps"]
    if not job["shot"]:
        for i, step in enumerate(steps):
            if i:
                time.sleep(0.2)
            frame = grab("cur.ppm")[0] if "text" in step else None
            try:
                run("xdotool", *resolve(step, frame, i))
            except Stop as stop:
                # Show where things are now, so the caller can continue with coordinates
                info.update(stop.args[0])
                return info, render(info, stop.args[1], sw, sh)
        return info, b""

    path, digest = grab("before.ppm")
    if steps:
        before_path, before = path, digest
        for i, step in enumerate(steps):
            if i:
                path = settle(None, 1.0, 0)[0]
            try:
                run("xdotool", *resolve(step, path, i))
            except Stop as stop:
                info.update(stop.args[0])
                return info, render(info, stop.args[1], sw, sh)
        path, digest, info["settled"] = settle(before, job["wait"], min(1.2, job["wait"]))
        info["changed"] = None
        if digest != before:
            # Bounding box of all differing pixels; the black border makes black the colour that is trimmed
            box = run("convert", before_path, path, "-compose", "difference", "-composite", "-threshold", "0",
                      "-bordercolor", "black", "-border", "1", "-format", "%@", "info:").decode()
            m = re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", box)
            if m:
                w, h, x, y = (int(v) for v in m.groups())
                info["changed"] = [x - 1, y - 1, w, h]
    return info, render(info, path, sw, sh, job.get("crop"), job.get("zoom"), job.get("grid"), job.get("text"))

try:
    info, png = main()
    sys.stdout.buffer.write(json.dumps(info).encode() + b"\n" + png)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
"""

# Runs inside the container: presses the copy keys in the focused window and prints what arrived on the clipboard.
# A marker is put on the clipboard first, so stale content is never mistaken for the result.
COPY_JOB = r"""
import json, os, subprocess, sys, time, uuid

job = json.loads(os.environ["JUMPSEAT_JOB"])

def get():
    try:
        r = subprocess.run(["xclip", "-o", "-selection", "clipboard"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=5)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 else None

def put(data):
    # xclip stays in the background to serve the selection; detach its output so docker exec returns
    subprocess.run("xclip -i -selection clipboard >/dev/null 2>&1", shell=True, check=True, input=data)

old = get()
marker = ("jumpseat-copy-" + uuid.uuid4().hex).encode()
put(marker)
time.sleep(0.1)
subprocess.run(["xdotool", "key", "--delay", "80", "--", *job["keys"]], check=True)
deadline = time.monotonic() + job["wait"]
while time.monotonic() < deadline:
    time.sleep(0.1)
    data = get()
    if data is not None and data != marker:
        sys.stdout.buffer.write(data)
        sys.exit(0)
put(old or b"")
sys.exit("Nothing was copied. Is the text selected and the right window focused? (Slow server: raise --wait.)")
"""

# Runs inside the container: runs a PowerShell command on the Windows server of the open RDP session and prints its
# output. The command travels on the clipboard, so its length and quoting do not matter: the Run box only gets a
# fixed starter that executes what is on the clipboard, and the script puts its output back there behind a marker.
PS_JOB = r"""
import hashlib, json, os, subprocess, sys, time, uuid

job = json.loads(os.environ["JUMPSEAT_JOB"])
MAIN_WINDOW = "Remmina Remote Desktop Client"
STARTER = 'powershell -NoP -W Hidden -C "iex (Get-Clipboard -Raw)"'

def out(*cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode(errors="replace").strip()

def get():
    try:
        r = subprocess.run(["xclip", "-o", "-selection", "clipboard"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=5)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None

def put(text):
    # xclip stays in the background to serve the selection; detach its output so docker exec returns
    subprocess.run("xclip -i -selection clipboard >/dev/null 2>&1", shell=True, check=True, input=text.encode())
    time.sleep(0.3)  # let Remmina offer it to the server

def key(*keys):
    subprocess.run(["xdotool", "key", "--delay", "80", "--", *keys], check=True)

def frame():
    png = subprocess.run(["import", "-window", "root", "-depth", "8", "ppm:-"], stdout=subprocess.PIPE, check=True)
    return hashlib.md5(png.stdout).digest()

# The keys must go to an RDP session: use the focused Remmina connection window, or the only one there is
sessions = {w: out("xdotool", "getwindowname", w) for w in out("xdotool", "search", "--onlyvisible", "--class", "remmina").split()}
sessions = {w: n for w, n in sessions.items() if n and n != MAIN_WINDOW}
active = out("xdotool", "getactivewindow")
if active not in sessions:
    if not sessions:
        sys.exit("No RDP session window is open in Remmina. Connect to the server first.")
    if len(sessions) > 1:
        sys.exit("Several RDP windows are open (%s) and none has focus. Click in the one you mean, then try again."
                 % ", ".join(sorted(sessions.values())))
    active = next(iter(sessions))
    subprocess.run(["xdotool", "windowactivate", "--sync", active], check=True)
    time.sleep(0.3)
sys.stderr.write("session: %s\n" % sessions[active])

marker = "<<jumpseat-ps %s>>" % uuid.uuid4().hex[:12]
script = "\n".join([
    "$ErrorActionPreference='Stop'",
    "$c=@'", job["command"], "'@",
    "$o=try{(&([scriptblock]::Create($c))|Out-String -Width 250).TrimEnd()}catch{'ERROR: '+$_.Exception.Message}",
    "Set-Clipboard -Value ('%s'+$o)" % marker, ""])

old = get()
put(STARTER)
before = frame()
key("super+r")
deadline = time.monotonic() + 5
while frame() == before:
    if time.monotonic() > deadline:
        put(old or "")
        sys.exit("The Run box did not open: nothing changed on the screen after Win+R. Nothing was run. "
                 "Is the session connected, unlocked and able to take keys? Take a screenshot.")
    time.sleep(0.15)
time.sleep(0.5)  # the box is still being drawn
key("ctrl+a", "ctrl+v")
put(script)
key("Return")

deadline = time.monotonic() + job["wait"]
while time.monotonic() < deadline:
    time.sleep(0.2)
    data = get()
    if data and data.startswith(marker):
        text = data[len(marker):].replace("\r\n", "\n").lstrip("\n")
        sys.stdout.write(text if text.strip() else "(no output)")
        sys.exit(1 if text.startswith("ERROR: ") else 0)
put(old or "")
sys.exit("No answer from PowerShell within %g s (raise --wait for slow commands). Take a screenshot to see "
         "what the server shows." % job["wait"])
"""

# Runs inside the container: talks to Firefox's WebDriver BiDi endpoint (started by the docker/firefox wrapper) and
# performs the one operation in JUMPSEAT_JOB on the focused, visible tab: evaluate, open a URL, list or use page controls.
BIDI_CLIENT = r'''
import asyncio, json, os, sys, time
import websockets

job = json.loads(os.environ["JUMPSEAT_JOB"])

class BidiError(Exception):
    pass

PAGE_JS = r"""`${document.title}\n${location.href}\n\n${document.body ? document.body.innerText : ''}`"""

# (mode, target, extra) => JSON. mode "list": the visible links, buttons and fields; "click" / "fill": find one by
# its name or by a ref from an earlier list, and prepare the click or put the text in. Refs are kept on the window
# under a symbol, so they survive between calls for as long as the page does, without touching the page's own data.
FIND_JS = r"""(mode, target, extra) => {
  const KEY = Symbol.for('jumpseat.refs');
  const SEL = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=tab],' +
    '[role=menuitem],[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox],[role=textbox],' +
    '[role=searchbox],[contenteditable=""],[contenteditable=true],[onclick]';
  const FILL = 'input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=reset]):not([type=checkbox])' +
    ':not([type=radio]):not([type=image]):not([type=file]),select,textarea,[role=textbox],[role=searchbox],' +
    '[role=combobox],[contenteditable=""],[contenteditable=true]';
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || el.disabled) return false;
    return el.checkVisibility ? el.checkVisibility({checkVisibilityCSS: true}) : getComputedStyle(el).visibility !== 'hidden';
  };
  const inView = el => {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };
  const kind = el => {
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (el.getAttribute('role')) return el.getAttribute('role');
    if (tag === 'a') return 'link';
    if (tag === 'input') return ['submit', 'button', 'reset', 'image'].includes(type) ? 'button' :
      (type === 'checkbox' || type === 'radio') ? type : 'input';
    return {textarea: 'input', summary: 'button'}[tag] || tag;
  };
  const nameOf = el => {
    const tag = el.tagName.toLowerCase();
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const labelled = clean(ids.map(i => document.getElementById(i)?.innerText || '').join(' '));
    const label = el.labels && el.labels.length ? clean([...el.labels].map(l => l.innerText).join(' ')) : '';
    const own = ['input', 'select', 'textarea'].includes(tag)
      ? (['submit', 'button', 'reset'].includes(el.type) ? el.value : '') : (el.innerText || el.textContent);
    return clean(el.getAttribute('aria-label')) || labelled || label || clean(own) ||
      clean(el.getAttribute('placeholder')) || clean(el.getAttribute('title')) ||
      clean(el.querySelector('img[alt]')?.alt) || clean(el.getAttribute('name'));
  };
  const describe = (el, ref) => {
    const tag = el.tagName.toLowerCase(), d = {ref, kind: kind(el), name: nameOf(el).slice(0, 90)};
    if (tag === 'a') d.to = (el.getAttribute('href') || '').slice(0, 90);
    if (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')) d.value = el.checked ? 'checked' : 'unchecked';
    else if (tag === 'input' && el.type === 'password') d.value = el.value ? '(set)' : '';
    else if (tag === 'input' || tag === 'textarea') d.value = (el.value || '').slice(0, 60);
    else if (tag === 'select') d.value = clean(el.selectedOptions[0]?.innerText);
    return d;
  };
  const collect = () => {
    const found = [...document.querySelectorAll(SEL)].filter(visible);
    const set = new Set(found), names = new Map(found.map(el => [el, nameOf(el)]));
    // An element inside another listed one with the same name (a button inside a link) is the same control
    const all = found.filter(el => {
      const outer = el.parentElement?.closest(SEL);
      return !(outer && set.has(outer) && names.get(outer) === names.get(el));
    });
    window[KEY] = all;
    return [all, names];
  };

  if (mode === 'list') {
    const [all] = collect(), q = clean(target).toLowerCase();
    const rows = all.map((el, i) => describe(el, i + 1))
      .filter(d => !q || d.name.toLowerCase().includes(q) || (d.to || '').toLowerCase().includes(q));
    return JSON.stringify({status: 'ok', total: rows.length, rows: rows.slice(0, extra)});
  }

  let el, ref;
  const byRef = /^ref:(\d+)$/.exec(target);
  if (byRef) {
    ref = +byRef[1];
    el = (window[KEY] || [])[ref - 1];
    if (!el || !el.isConnected) return JSON.stringify({status: 'stale'});
    if (mode === 'fill' && !el.matches(FILL)) return JSON.stringify({status: 'not_fillable', rows: [describe(el, ref)]});
  } else {
    const [all, names] = collect(), q = clean(target).toLowerCase();
    const wordEdge = c => !c || !/[\p{L}\p{N}]/u.test(c);
    const scored = [];
    for (const cand of (mode === 'fill' ? all.filter(e => e.matches(FILL)) : all)) {
      const n = names.get(cand).toLowerCase(), at = q ? n.indexOf(q) : -1;
      if (at < 0) continue;
      // 2: the whole name, 1: whole words, 0: part of a word
      scored.push([n === q ? 2 : wordEdge(n[at - 1]) && wordEdge(n[at + q.length]) ? 1 : 0, cand]);
    }
    if (!scored.length) return JSON.stringify({status: 'none'});
    const top = Math.max(...scored.map(s => s[0]));
    let best = scored.filter(s => s[0] === top).map(s => s[1]);
    // Several equally good matches: the only one on screen is the one meant
    if (best.length > 1 && best.filter(inView).length === 1) best = best.filter(inView);
    if (best.length > 1) {
      return JSON.stringify({status: 'ambiguous', total: best.length,
                             rows: best.slice(0, 15).map(e => describe(e, all.indexOf(e) + 1))});
    }
    el = best[0];
    ref = all.indexOf(el) + 1;
  }

  el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
  if (mode === 'click') {
    const r = el.getClientRects()[0] || el.getBoundingClientRect();
    const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
    const hit = document.elementFromPoint(x, y);
    const reachable = !!hit && (hit === el || el.contains(hit) || hit.contains(el));
    const what = describe(el, ref);
    // Something lies on top of it (a sticky header, an overlay): click it by script instead of with the pointer
    if (!reachable) el.click();
    return JSON.stringify({status: 'ok', what, x, y, scripted: !reachable});
  }

  const tag = el.tagName.toLowerCase(), fire = type => el.dispatchEvent(new Event(type, {bubbles: true}));
  el.focus();
  if (tag === 'select') {
    const want = clean(extra).toLowerCase();
    const option = [...el.options].find(o => clean(o.innerText).toLowerCase() === want || o.value === extra);
    if (!option) return JSON.stringify({status: 'no_option', options: [...el.options].map(o => clean(o.innerText)).slice(0, 40)});
    el.value = option.value;
    fire('input'); fire('change');
  } else if (tag === 'input' || tag === 'textarea') {
    // Through the native setter, so frameworks that track the value (React) notice the change
    const proto = tag === 'input' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, extra);
    fire('input'); fire('change');
  } else {
    getSelection().selectAllChildren(el);
    document.execCommand('insertText', false, extra);
  }
  return JSON.stringify({status: 'ok', what: describe(el, ref)});
}"""

def row(d):
    line = 'ref:%d\t%s\t"%s"' % (d["ref"], d["kind"], d["name"])
    if d.get("value"):
        line += "\t= " + d["value"]
    if d.get("to"):
        line += "\t-> " + d["to"]
    return line

async def main():
    try:
        ws = await websockets.connect("ws://127.0.0.1:9222/session", max_size=None, open_timeout=5)
    except (OSError, asyncio.TimeoutError, websockets.exceptions.InvalidStatusCode) as e:
        sys.exit(f"NO_BIDI: {e}")
    seq = 0

    async def call(method, params, timeout=10):
        nonlocal seq
        seq += 1
        mine = seq
        await ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while True:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), max(0.01, deadline - time.monotonic())))
            except asyncio.TimeoutError:
                # Seen with the terms dialog of a new profile on top: some commands then never return
                raise BidiError(f"Firefox did not answer ({method}) within {timeout:g} s. A dialog may be waiting "
                                "in Firefox, for example its terms on the first start: take a screenshot.")
            if msg.get("id") == mine:
                if msg.get("type") == "error":
                    raise BidiError(f"{method} failed: {msg.get('error')}: {msg.get('message')}")
                return msg["result"]

    async def evaluate(context, expression):
        r = await call("script.evaluate", {"expression": expression, "target": {"context": context},
                                           "awaitPromise": True, "resultOwnership": "none"})
        if r.get("type") == "exception":
            raise BidiError("JavaScript error: " + r["exceptionDetails"].get("text", "unknown"))
        value = r["result"]
        return value["value"] if value.get("type") == "string" else json.dumps(value)

    async def active_tab(required=True):
        ranked = []
        for n, c in enumerate((await call("browsingContext.getTree", {"maxDepth": 0}))["contexts"]):
            try:
                state = await evaluate(c["context"], "document.visibilityState + (document.hasFocus() ? '+focus' : '')")
            except BidiError:
                continue  # Firefox's own pages (new tab, about:welcome, settings) cannot be read
            ranked.append(({"visible+focus": 0, "visible": 1}.get(state, 2), n, c["context"]))
        if not ranked and required:
            sys.exit("Firefox has no web page open (only its own pages). Open one with: jumpseat.py page URL")
        return min(ranked)[2] if ranked else None

    async def settle(context, before):
        # After a click: give a navigation a moment to start, then wait until the page has loaded
        async def state():
            try:
                return await evaluate(context, "location.href + ' ' + document.readyState")
            except BidiError:
                return None  # between two documents
        start = time.monotonic()
        while time.monotonic() - start < 0.6 and await state() == before + " complete":
            await asyncio.sleep(0.1)
        while time.monotonic() - start < job["wait"]:
            s = await state()
            if s and s.endswith(" complete"):
                return
            await asyncio.sleep(0.15)

    async def now_line():
        # The tab in front afterwards: a link may have opened a new one
        return "now: " + await evaluate(await active_tab(), "document.title + ' | ' + location.href")

    async def run():
        op = job["op"]
        if op == "eval":
            print(await evaluate(await active_tab(), job["expression"]))
            return 0
        if op == "open":
            context = await active_tab(required=False) if job["here"] else None
            notes = []
            if not context:
                try:
                    context = (await call("browsingContext.create", {"type": "tab"}, 5))["context"]
                except BidiError:
                    # Seen while the terms dialog of a new profile is up: no new tab, but the current one works
                    context = await active_tab(required=False)
                    if not context:
                        raise
                    notes.append("Firefox could not open a new tab, so the page was opened in the current one")
            loaded = True
            try:
                await asyncio.wait_for(call("browsingContext.navigate", {"context": context, "url": job["url"],
                                                                         "wait": "complete"}, job["wait"] + 5), job["wait"])
            except asyncio.TimeoutError:
                loaded = False
            # Bring the tab to the front: the user should see it, and the next command works on the visible tab
            try:
                await call("browsingContext.activate", {"context": context}, 3)
            except BidiError:
                notes.append("the tab could not be brought to the front, so the next commands may work on another tab")
            if not loaded:
                notes.append("the page was still loading after %g s; this is what it showed so far" % job["wait"])
            text = await evaluate(context, PAGE_JS)
            if notes:
                # First, so that it survives truncation of a long page
                print("[%s. A dialog may be waiting in Firefox, for example its terms on the first start: "
                      "take a screenshot.]\n" % "; ".join(notes))
            print(text)
            return 0

        context = await active_tab()
        before = await evaluate(context, "location.href")
        r = json.loads(await evaluate(context, "(%s)(%s, %s, %s)" % (
            FIND_JS, json.dumps(op), json.dumps(job.get("target", "")), json.dumps(job.get("extra")))))
        status = r["status"]
        if op == "list":
            print("\n".join(row(d) for d in r["rows"]) or "(no links, buttons or fields found)")
            if r["total"] > len(r["rows"]):
                print("[%d more; narrow down with --find TEXT or raise --max]" % (r["total"] - len(r["rows"])))
            return 0
        if status == "none":
            what = "field" if op == "fill" else "link, button or field"
            print("No visible %s named '%s' on the page. List what is there with: jumpseat.py controls --find TEXT"
                  % (what, job["target"]))
            return 1
        if status == "stale":
            print("%s is not on the page any more. Run jumpseat.py controls again." % job["target"])
            return 1
        if status == "not_fillable":
            print("%s is not a field that takes text:\n%s" % (job["target"], row(r["rows"][0])))
            return 1
        if status == "ambiguous":
            print("'%s' matches %d controls; nothing was done. Use one of these refs:" % (job["target"], r["total"]))
            print("\n".join(row(d) for d in r["rows"]))
            return 1
        if status == "no_option":
            print("The list has no option '%s'. Options: %s" % (job["extra"], ", ".join(r["options"])))
            return 1

        what = '%s "%s"' % (r["what"]["kind"], r["what"]["name"])
        if op == "click":
            if not r["scripted"]:
                await call("input.performActions", {"context": context, "actions": [{
                    "type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": [
                        {"type": "pointerMove", "x": r["x"], "y": r["y"], "origin": "viewport"},
                        {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}]}]})
                await call("input.releaseActions", {"context": context})
            print("clicked " + what)
        else:
            if job["enter"]:
                await call("input.performActions", {"context": context, "actions": [{
                    "type": "key", "id": "keyboard", "actions": [
                        {"type": "keyDown", "value": ""}, {"type": "keyUp", "value": ""}]}]})
                await call("input.releaseActions", {"context": context})
            print("filled " + what + (" and pressed Enter" if job["enter"] else ""))
        await settle(context, before)
        print(await now_line())
        if job.get("page"):
            print("\n" + await evaluate(await active_tab(), PAGE_JS))
        return 0

    code = 1
    try:
        await call("session.new", {"capabilities": {}})
        try:
            code = await run()
        finally:
            try:
                await call("session.end", {}, 3)
            except Exception:
                pass
    except BidiError as e:
        print(e, file=sys.stderr)
    finally:
        await ws.close()
    sys.exit(code)

asyncio.run(main())
'''

PAGE_JS = r'''`${document.title}\n${location.href}\n\n${document.body ? document.body.innerText : ''}`'''

# Teams changes its markup now and then, so these selectors may need updates.
TEAMS_JS = r'''(() => {
  const bodySel = ['[data-tid="message-body"]', '[data-tid="message-content"]',
    '[data-tid="chat-pane-message-content"]', '[data-tid="chat-pane-message-body"]',
    '[id^="content-"][id$="-message"]', '[data-tid="chat-pane-message"] [id^="content-"]'].join(',');
  // In current Teams, sender and time sit in chat-pane-item, outside the chat-pane-message that holds the body
  const itemSel = '[data-tid="chat-pane-item"]';
  const rowSel = ['[data-tid="chat-pane-message"]', '[data-tid="message-thread-item"]', '[data-tid="message"]',
    '[data-message-id]', '[role="article"]', '[role="listitem"]'].join(',');
  const senderSel = '[data-tid="message-author-name"], [data-tid="message-sender-name"], ' +
    '[data-tid="chat-pane-message-sender"]';
  const timeSel = 'time, [data-tid="message-timestamp"], [data-tid="chat-pane-message-timestamp"]';
  const text = el => el ? (el.innerText ?? el.textContent ?? '').trim() : '';
  const fmtTime = t => {
    if (!t) return '';
    const raw = t.getAttribute('datetime') || t.getAttribute('title') || text(t);
    const d = new Date(raw);
    return isNaN(d) ? raw : d.toISOString().slice(0, 16).replace('T', ' ') + 'Z';
  };
  const docs = [document];
  for (const f of document.querySelectorAll('iframe')) {
    try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
  }
  const out = [document.title, ''];
  let count = 0;
  for (const doc of docs) {
    const bodies = [...doc.querySelectorAll(bodySel)].filter(el => !el.parentElement?.closest(bodySel));
    for (const body of bodies) {
      const content = text(body);
      if (!content) continue;
      const row = body.closest(itemSel) ?? body.closest(rowSel) ?? body.parentElement ?? body;
      const sender = text(row.querySelector(senderSel));
      const time = fmtTime(row.querySelector(timeSel));
      const head = (sender || '  \u21b3') + (time ? ` (${time})` : '');
      out.push(`${head}: ${content.replace(/\n/g, '\n    ')}`);
      count++;
    }
  }
  return count ? out.join('\n') : 'NO_MESSAGES';
})()'''


def docker(*args, capture=False, check=True, stdin=None):
    return subprocess.run(["docker", *args], check=check, capture_output=capture, input=stdin)


def on_display(*args, capture=False, check=True, stdin=None):
    """Run a command on the container's X display (:1)."""
    exec_opts = ["-i"] if stdin is not None else []
    return docker("exec", *exec_opts, "-e", "DISPLAY=:1", "-e", "LANG=C.UTF-8", CONTAINER, *args,
                  capture=capture, check=check, stdin=stdin)


def is_running():
    r = docker("inspect", "-f", "{{.State.Running}}", CONTAINER, capture=True, check=False)
    return r.returncode == 0 and r.stdout.decode().strip() == "true"


def require_running():
    if not is_running():
        sys.exit(f"Container '{CONTAINER}' is not running. Start it with: {Path(sys.argv[0]).name} up")


def vnc_password():
    r = docker("logs", CONTAINER, capture=True, check=False)
    lines = (r.stdout + r.stderr).decode(errors="replace").splitlines()
    generated = [l.split(":", 1)[1].strip() for l in lines if l.startswith("Generated VNC password:")]
    return generated[-1] if generated else "(fixed, set via VNC_PASSWORD)"


def screen_size():
    return on_display("xdotool", "getdisplaygeometry", capture=True).stdout.decode().strip().replace(" ", "x")


def print_status():
    print(f"Running:  yes\nURL:      {URL}\nPassword: {vnc_password()}\nScreen:   {screen_size()}")


def compose(*args):
    """Run docker compose with compose.yaml plus the optional, git-ignored compose.override.yaml."""
    files = ["-f", str(DOCKER_DIR / "compose.yaml")]
    override = DOCKER_DIR / "compose.override.yaml"
    if override.exists():
        files += ["-f", str(override)]
    docker("compose", *files, *args)


def cmd_up(_):
    compose("up", "-d", "--build")
    for _ in range(60):
        if is_running() and on_display("xdotool", "getdisplaygeometry", capture=True, check=False).returncode == 0:
            break
        time.sleep(0.5)
    print_status()


def cmd_down(_):
    compose("down")


def cmd_status(_):
    if not is_running():
        print(f"Running:  no (start with: {Path(sys.argv[0]).name} up)")
        return
    print_status()


def shot_max():
    try:
        w, h = (int(v) for v in SHOT_MAX.lower().split("x"))
        if w < 1 or h < 1:
            raise ValueError
    except ValueError:
        sys.exit(f"VNC_SHOT_MAX must look like 1280x768, not '{SHOT_MAX}'")
    return [w, h]


def load_view():
    try:
        return json.loads(VIEW_FILE.read_text())
    except (OSError, ValueError):
        return None


class Points:
    """Converts pointer coordinates, given as pixels in the last screenshot, to screen pixels."""

    def __init__(self):
        self.view = None

    def __call__(self, raw, x, y):
        if raw:
            return x, y
        if self.view is None:
            self.view = load_view()
        if self.view is None:
            sys.exit("Take a screenshot first: coordinates are pixels in the last screenshot "
                     "(or pass --screen to give raw screen pixels).")
        (ox, oy), (rw, rh), (iw, ih) = self.view["origin"], self.view["region"], self.view["image"]
        if not (0 <= x < iw and 0 <= y < ih):
            sys.exit(f"{x},{y} is outside the last screenshot ({iw}x{ih}). Take a new screenshot, "
                     "or pass --screen to give raw screen pixels.")
        # The centre of the image pixel, so zoomed and shrunken views both land on the right screen pixel
        return ox + int((x + 0.5) * rw / iw), oy + int((y + 0.5) * rh / ih)

    def expect_screen(self):
        """The screen size the converted coordinates assume; the job refuses to act if it has changed."""
        return self.view["screen"] if self.view else None


def desktop_job(steps=(), shot=True, wait=SETTLE_TIMEOUT_S, crop=None, zoom=None, grid=False, text=False,
                expect_screen=None):
    """Run steps and/or take a screenshot in one docker exec. Returns (info, png bytes)."""
    job = {"steps": list(steps), "shot": shot, "wait": wait, "crop": crop, "zoom": zoom, "grid": grid, "text": text,
           "expect_screen": expect_screen, "max": shot_max(), "lang": OCR_LANG}
    r = docker("exec", "-i", "-e", "DISPLAY=:1", "-e", "LANG=C.UTF-8", "-e", f"JUMPSEAT_JOB={json.dumps(job)}",
               CONTAINER, "python3", "-", capture=True, check=False, stdin=DESKTOP_JOB.encode())
    if r.returncode != 0:
        require_running()
        sys.exit(r.stderr.decode(errors="replace").strip() or f"Desktop job failed ({r.returncode})")
    # xdotool only warns (and exits 0) about e.g. an unknown key name; do not hide that
    sys.stderr.write(r.stderr.decode(errors="replace"))
    head, _, png = r.stdout.partition(b"\n")
    info = json.loads(head)
    if info.get("error") == "screen_changed":
        was, now = expect_screen, info["screen"]
        sys.exit(f"The screen size changed since the last screenshot ({was[0]}x{was[1]} -> {now[0]}x{now[1]}), "
                 "so its coordinates are no longer valid. Nothing was done; take a new screenshot.")
    if info.get("error") in ("no_match", "ambiguous"):
        # The job stopped at this step and sent a fresh screenshot; list the candidates in its pixels
        save_shot(info, png)
        found = [f"  {n}: {x},{y}  {t}" for n, (t, x, y) in enumerate(image_points(info, info["candidates"]), 1)]
        done = f" The {info['done']} step(s) before it were done." if info["done"] else ""
        if info["error"] == "ambiguous":
            sys.exit(f"'{info['query']}' is on the screen {len(found)} times; nothing was clicked.{done} "
                     "Add --nth N, or use the coordinates (pixels in the screenshot above):\n" + "\n".join(found))
        sys.exit(f"'{info['query']}' was not found on the screen.{done} Look at the screenshot above and use "
                 "coordinates, or try a shorter text." + ("\nMatches:\n" + "\n".join(found) if found else ""))
    return info, png


def image_points(info, boxes):
    """Convert [text, x, y, w, h] boxes in screen pixels to (text, x, y): their centre in pixels of the image."""
    (ox, oy), (rw, rh), (iw, ih) = info["origin"], info["region"], info["image"]
    return [(t, int((x + w / 2 - ox) * iw / rw), int((y + h / 2 - oy) * ih / rh)) for t, x, y, w, h in boxes]


def save_shot(info, png):
    """Save the PNG, remember its view for the next pointer command and print the path and a summary line."""
    SHOT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = time.time()
    for old in SHOT_DIR.glob("*.png"):
        if now - old.stat().st_mtime > SHOT_MAX_AGE_S:
            old.unlink(missing_ok=True)

    out = SHOT_DIR / f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06d}.png"
    out.write_bytes(png)
    VIEW_FILE.write_text(json.dumps({k: info[k] for k in ("screen", "origin", "region", "image")}))

    (rw, rh), (iw, ih) = info["region"], info["image"]
    summary = [f"image={iw}x{ih}", "view=full" if info["region"] == info["screen"] else "view=crop"]
    if "changed" in info:
        if info["changed"]:
            # Reported in pixels of this (full-screen) image, ready to pass to screenshot --crop
            cx, cy, cw, ch = info["changed"]
            x0, y0 = cx * iw // rw, cy * ih // rh
            x1, y1 = -(-(cx + cw) * iw // rw), -(-(cy + ch) * ih // rh)
            summary.append(f"changed={x0},{y0},{x1 - x0},{y1 - y0}")
        else:
            summary.append("changed=none")
        if not info["settled"]:
            summary.append("settled=no")
    print(out)
    print(" ".join(summary))


def cmd_screenshot(a):
    crop, expect = None, None
    if a.crop:
        x, y, w, h = a.crop
        if w < 1 or h < 1:
            sys.exit("--crop width and height must be at least 1")
        if a.screen:
            crop = [x, y, w, h]
        else:
            pt = Points()
            x0, y0 = pt(False, x, y)
            iw, ih = pt.view["image"]
            x1, y1 = pt(False, min(x + w, iw) - 1, min(y + h, ih) - 1)
            crop, expect = [x0, y0, x1 - x0 + 1, y1 - y0 + 1], pt.expect_screen()
    save_shot(*desktop_job(crop=crop, zoom=a.zoom, grid=a.grid, expect_screen=expect))


def cmd_text(a):
    crop, expect = None, None
    if a.crop:
        pt = Points()
        x, y, w, h = a.crop
        x0, y0 = pt(False, x, y)
        iw, ih = pt.view["image"]
        x1, y1 = pt(False, min(x + w, iw) - 1, min(y + h, ih) - 1)
        crop, expect = [x0, y0, x1 - x0 + 1, y1 - y0 + 1], pt.expect_screen()
    info, png = desktop_job(crop=crop, text=True, expect_screen=expect)
    save_shot(info, png)
    wanted = "".join(a.find.casefold().split()) if a.find else ""
    for text, x, y in image_points(info, info["lines"]):
        if wanted in "".join(text.casefold().split()):
            print(f"{x},{y}\t{text}")


def target(a, pt):
    """The pointer target of a click or move: a step with coordinates, or with a text for the job to find."""
    if a.text is not None:
        if a.x is not None or not a.text.strip():
            sys.exit("Give either X Y or --text TEXT")
        step = {"args": ["mousemove", "--sync", "{x}", "{y}"], "text": a.text}
        if a.nth:
            step["nth"] = a.nth
        return step
    if a.x is None or a.y is None:
        sys.exit("Give X Y, or --text TEXT to find a text on the screen")
    return {"args": ["mousemove", "--sync", *pt(a.screen, a.x, a.y)]}


def build_click(a, pt):
    step = target(a, pt)
    step["args"] += ["click"] + (["--repeat", "2", "--delay", "120"] if a.double else []) + [BUTTONS[a.button]]
    return step


def build_move(a, pt):
    return target(a, pt)


def build_drag(a, pt):
    return {"args": ["mousemove", "--sync", *pt(a.screen, a.x1, a.y1), "mousedown", "1", "sleep", "0.2",
                     "mousemove", "--sync", *pt(a.screen, a.x2, a.y2), "sleep", "0.2", "mouseup", "1"]}


def build_scroll(a, pt):
    return {"args": ["mousemove", "--sync", *pt(a.screen, a.x, a.y),
                     "click", "--repeat", a.clicks, "--delay", "50", "4" if a.direction == "up" else "5"]}


def build_type(a, _):
    return {"args": ["type", "--delay", "60", "--", a.text]}


def build_key(a, _):
    return {"args": ["key", "--delay", "80", "--", *a.keys]}


def build_paste(a, _):
    # Faster than type and independent of the keyboard layout; Remmina passes the clipboard on to the RDP server
    return {"args": ["key", "--", a.key], "clipboard": a.text}


def run_steps(a, steps, pt):
    """Perform the steps, then (unless --no-shot) wait for the screen to settle and save a screenshot of it."""
    for step in steps:
        step["args"] = [str(v) for v in step["args"]]
    info, png = desktop_job(steps, shot=not a.no_shot, wait=a.wait, expect_screen=pt.expect_screen())
    if not a.no_shot:
        save_shot(info, png)


def cmd_action(a):
    pt = Points()
    run_steps(a, [a.build(a, pt)], pt)


def cmd_do(a):
    step_parser = argparse.ArgumentParser(prog="step", add_help=False)
    add_actions(step_parser.add_subparsers(dest="cmd", required=True), [])
    pt = Points()
    steps = []
    for text in a.steps:
        s = step_parser.parse_args(shlex.split(text))
        steps.append(s.build(s, pt))
    run_steps(a, steps, pt)


def cmd_copy(a):
    keys = (["ctrl+a"] if a.all else []) + [a.key]
    r = docker("exec", "-i", "-e", "DISPLAY=:1", "-e", "LANG=C.UTF-8",
               "-e", f"JUMPSEAT_JOB={json.dumps({'keys': keys, 'wait': a.wait})}", CONTAINER, "python3", "-",
               capture=True, check=False, stdin=COPY_JOB.encode())
    if r.returncode != 0:
        require_running()
        sys.exit(r.stderr.decode(errors="replace").strip() or f"Copy failed ({r.returncode})")
    text = r.stdout.decode(errors="replace")
    sys.stdout.write(text[:a.max])
    if len(text) > a.max:
        print(f"\n[truncated: {len(text)} chars, showing {a.max}; use --max to see more]")


def cmd_ps(a):
    command = a.command.strip()
    if not command or "\n'@" in "\n" + command:
        sys.exit("Give a PowerShell command (a line may not start with '@).")
    r = docker("exec", "-i", "-e", "DISPLAY=:1", "-e", "LANG=C.UTF-8",
               "-e", f"JUMPSEAT_JOB={json.dumps({'command': command, 'wait': a.wait})}", CONTAINER, "python3", "-",
               capture=True, check=False, stdin=PS_JOB.encode())
    err = r.stderr.decode(errors="replace").strip()
    if r.returncode != 0 and not r.stdout:
        require_running()
        sys.exit(err or f"PowerShell job failed ({r.returncode})")
    text = r.stdout.decode(errors="replace")
    print(text[:a.max])
    if len(text) > a.max:
        print(f"\n[truncated: {len(text)} chars, showing {a.max}; use --max to see more]")
    if err:
        print(f"[{err}]", file=sys.stderr)
    sys.exit(r.returncode)


def cmd_clipboard(a):
    require_running()
    if a.text is None:
        r = on_display("xclip", "-o", "-selection", "clipboard", capture=True, check=False)
        sys.stdout.write(r.stdout.decode(errors="replace"))
    else:
        # xclip stays in the background to serve the selection; detach its output so docker exec returns
        on_display("sh", "-c", "xclip -i -selection clipboard >/dev/null 2>&1", stdin=a.text.encode())


def bidi(job, check=True):
    """Run one operation in Firefox through WebDriver BiDi. Returns (exit code, output); exits on failure if check."""
    job.setdefault("wait", SETTLE_TIMEOUT_S)
    r = docker("exec", "-i", "-e", f"JUMPSEAT_JOB={json.dumps(job)}", CONTAINER, "python3", "-",
               capture=True, check=False, stdin=BIDI_CLIENT.encode())
    out, err = r.stdout.decode(errors="replace").rstrip("\n"), r.stderr.decode(errors="replace").strip()
    if "NO_BIDI" in err:
        require_running()
        if not check:
            return None, ""
        sys.exit("Firefox is not running with remote debugging. Start it with: jumpseat.py page URL\n"
                 "(If Firefox was started before the image was rebuilt, close it first.)")
    if err:
        if check:
            sys.exit(err)
        print(err, file=sys.stderr)
    return r.returncode, out


def run_js(expression):
    """Evaluate JavaScript in Firefox's focused tab and return the result as text."""
    return bidi({"op": "eval", "expression": expression})[1]


def start_firefox(*urls):
    # setsid detaches Firefox so docker exec returns; an already running Firefox just opens the URL
    on_display("sh", "-c", 'setsid -f firefox "$@" >/dev/null 2>&1', "sh", *urls)


def cmd_firefox(a):
    require_running()
    start_firefox(*([a.url] if a.url else []))


def print_page(text, limit):
    print(text[:limit])
    if len(text) > limit:
        print(f"\n[truncated: {len(text)} chars, showing {limit}; use --max to see more]")


def cmd_page(a):
    if not a.url:
        print_page(run_js(PAGE_JS), a.max)
        return
    url = a.url if "://" in a.url or a.url.startswith("about:") else "https://" + a.url
    job = {"op": "open", "url": url, "here": a.here, "wait": a.wait}
    code, text = bidi(job, check=False)
    if code is None:
        # Firefox is not running yet: start it and wait until it answers
        start_firefox()
        for _ in range(60):
            time.sleep(0.5)
            code, text = bidi(dict(job, here=True), check=False)
            if code is not None:
                break
        else:
            sys.exit("Firefox did not start within 30 s. Take a screenshot to see what it shows.")
    if code != 0:
        sys.exit(1)
    print_page(text, a.max)


def cmd_js(a):
    print(run_js(f"(async () => {{ const v = await ({a.expression}); "
                 f"return typeof v === 'string' ? v : JSON.stringify(v, null, 2); }})()"))


def cmd_teams(_):
    text = run_js(TEAMS_JS)
    if text == "NO_MESSAGES":
        sys.exit("No Teams messages found in the active Firefox tab. Open the chat in Firefox and make it the active tab.")
    print(text)


def cmd_controls(a):
    print(bidi({"op": "list", "target": a.find or "", "extra": a.max})[1])


def cmd_web(a):
    job = {"op": a.op, "target": a.target, "wait": a.wait, "page": a.page}
    if a.op == "fill":
        job.update(extra=a.text, enter=a.enter)
    code, text = bidi(job, check=False)
    if code is None:
        sys.exit("Firefox is not running with remote debugging. Start it with: jumpseat.py page URL")
    print_page(text, a.max)
    sys.exit(code)


def crop_arg(value):
    parts = value.split(",")
    if len(parts) != 4 or not all(p.strip().isdigit() for p in parts):
        raise argparse.ArgumentTypeError("expected X,Y,W,H in pixels of the last screenshot, e.g. 300,150,600,400")
    return tuple(int(p) for p in parts)


def add_actions(sub, parents):
    """Add the mouse and keyboard commands. They are top-level commands and also the steps of `do`."""
    pointer = argparse.ArgumentParser(add_help=False)
    pointer.add_argument("--screen", action="store_true",
                         help="coordinates are raw screen pixels, not pixels in the last screenshot")

    by_text = argparse.ArgumentParser(add_help=False)
    by_text.add_argument("x", type=int, nargs="?")
    by_text.add_argument("y", type=int, nargs="?")
    by_text.add_argument("--text", help="find this text on the screen (OCR) and use its centre instead of X Y")
    by_text.add_argument("--nth", type=int, metavar="N", help="with --text: use the Nth occurrence, top to bottom")

    s = sub.add_parser("click", parents=[by_text, pointer, *parents],
                       help="click at a point in the last screenshot, or on a text")
    s.add_argument("--button", choices=BUTTONS, default="left")
    s.add_argument("--double", action="store_true")
    s.set_defaults(fn=cmd_action, build=build_click)

    s = sub.add_parser("move", parents=[by_text, pointer, *parents],
                       help="move the mouse (hover, e.g. to show a tooltip)")
    s.set_defaults(fn=cmd_action, build=build_move)

    s = sub.add_parser("drag", parents=[pointer, *parents], help="drag with the left button from (x1,y1) to (x2,y2)")
    for name in ("x1", "y1", "x2", "y2"):
        s.add_argument(name, type=int)
    s.set_defaults(fn=cmd_action, build=build_drag)

    s = sub.add_parser("scroll", parents=[pointer, *parents], help="scroll the mouse wheel at a position")
    s.add_argument("x", type=int)
    s.add_argument("y", type=int)
    s.add_argument("direction", choices=("up", "down"))
    s.add_argument("clicks", type=int, nargs="?", default=3)
    s.set_defaults(fn=cmd_action, build=build_scroll)

    s = sub.add_parser("type", parents=parents, help="type text into the focused window")
    s.add_argument("text")
    s.set_defaults(fn=cmd_action, build=build_type)

    s = sub.add_parser("key", parents=parents, help="press keys, e.g. ctrl+a Return Escape Tab alt+F4")
    s.add_argument("keys", nargs="+")
    s.set_defaults(fn=cmd_action, build=build_key)

    s = sub.add_parser("paste", parents=parents,
                       help="put text on the clipboard and paste it into the focused window (fast, any characters)")
    s.add_argument("text")
    s.add_argument("--key", default="ctrl+v", help="paste shortcut (default ctrl+v; terminals: ctrl+shift+v)")
    s.set_defaults(fn=cmd_action, build=build_paste)


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        epilog="Pointer coordinates are pixels in the last screenshot. click, move, drag, scroll, type, key, paste "
               "and do wait until the screen stops changing and then save a new full-screen screenshot.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("up", help="build and start the container, print URL and password").set_defaults(fn=cmd_up)
    sub.add_parser("down", help="stop the container (home volume is kept)").set_defaults(fn=cmd_down)
    sub.add_parser("status", help="print URL, password and screen size").set_defaults(fn=cmd_status)

    s = sub.add_parser("screenshot", help="save a PNG of the desktop and print its path")
    s.add_argument("--crop", type=crop_arg, help="X,Y,W,H region of the last screenshot to look at more closely")
    s.add_argument("--zoom", type=float, help="image pixels per screen pixel (default: fit the image to "
                                              f"VNC_SHOT_MAX, {SHOT_MAX}; crops are enlarged up to 3x)")
    s.add_argument("--grid", action="store_true", help="draw a labelled 100 px coordinate grid on the image")
    s.add_argument("--screen", action="store_true", help="--crop is in raw screen pixels")
    s.set_defaults(fn=cmd_screenshot)

    s = sub.add_parser("text", help="screenshot plus the text on the screen (OCR), one line each: X,Y<tab>text")
    s.add_argument("--crop", type=crop_arg, help="X,Y,W,H region of the last screenshot to read")
    s.add_argument("--find", metavar="TEXT", help="only lines containing TEXT (case and spacing ignored)")
    s.set_defaults(fn=cmd_text)

    after = argparse.ArgumentParser(add_help=False)
    after.add_argument("--no-shot", action="store_true", help="do not wait and take a screenshot afterwards")
    after.add_argument("--wait", type=float, default=SETTLE_TIMEOUT_S, metavar="S",
                       help=f"max seconds to wait for the screen to stop changing (default {SETTLE_TIMEOUT_S:g})")
    add_actions(sub, [after])

    s = sub.add_parser("do", parents=[after],
                       help='several steps in one go, e.g. do "click 300 200" "type hello" "key Return"')
    s.add_argument("steps", nargs="+", metavar="STEP",
                   help="click, move, drag, scroll, type, key or paste with its arguments, quoted as one string")
    s.set_defaults(fn=cmd_do)

    s = sub.add_parser("copy", help="press ctrl+c in the focused window and print what was copied")
    s.add_argument("--all", action="store_true", help="press ctrl+a first (whole page or field)")
    s.add_argument("--key", default="ctrl+c", help="copy shortcut (default ctrl+c; terminals: ctrl+shift+c)")
    s.add_argument("--wait", type=float, default=SETTLE_TIMEOUT_S, metavar="S",
                   help=f"max seconds to wait for the clipboard (default {SETTLE_TIMEOUT_S:g})")
    s.add_argument("--max", type=int, default=PAGE_MAX_CHARS, help=f"max characters (default {PAGE_MAX_CHARS})")
    s.set_defaults(fn=cmd_copy)

    s = sub.add_parser("ps", help="run a PowerShell command on the Windows server of the open RDP session, print its output")
    s.add_argument("command", help='e.g. "Get-ADUser kanord -Properties Title | fl Name,Title"')
    s.add_argument("--wait", type=float, default=30, metavar="S", help="max seconds to wait for the answer (default 30)")
    s.add_argument("--max", type=int, default=PAGE_MAX_CHARS, help=f"max characters (default {PAGE_MAX_CHARS})")
    s.set_defaults(fn=cmd_ps)

    s = sub.add_parser("clipboard", help="print the desktop clipboard, or set it to TEXT (shared with RDP via Remmina)")
    s.add_argument("text", nargs="?")
    s.set_defaults(fn=cmd_clipboard)

    s = sub.add_parser("firefox", help="start Firefox (with remote debugging) or open URL in the running one")
    s.add_argument("url", nargs="?")
    s.set_defaults(fn=cmd_firefox)

    s = sub.add_parser("page", help="print title, URL and visible text of Firefox's active tab; with URL, open it "
                                    "first (starting Firefox if needed) and wait until it has loaded")
    s.add_argument("url", nargs="?", help="open this address in a new tab")
    s.add_argument("--here", action="store_true", help="open URL in the current tab instead of a new one")
    s.add_argument("--wait", type=float, default=20, metavar="S", help="max seconds to wait for the load (default 20)")
    s.add_argument("--max", type=int, default=PAGE_MAX_CHARS, help=f"max characters (default {PAGE_MAX_CHARS})")
    s.set_defaults(fn=cmd_page)

    s = sub.add_parser("controls", help="list the links, buttons and fields of Firefox's active tab with refs")
    s.add_argument("--find", metavar="TEXT", help="only those whose name or address contains TEXT")
    s.add_argument("--max", type=int, default=100, help="max lines (default 100)")
    s.set_defaults(fn=cmd_controls)

    web = argparse.ArgumentParser(add_help=False)
    web.add_argument("--wait", type=float, default=15, metavar="S", help="max seconds to wait for a page load (default 15)")
    web.add_argument("--page", action="store_true", help="also print the page text afterwards")
    web.add_argument("--max", type=int, default=PAGE_MAX_CHARS, help=f"max characters (default {PAGE_MAX_CHARS})")

    s = sub.add_parser("webclick", parents=[web],
                       help="click a link, button or field of Firefox's active tab by its name, without coordinates")
    s.add_argument("target", help='the visible name, e.g. "Sign in", or a ref from controls, e.g. ref:12')
    s.set_defaults(fn=cmd_web, op="click")

    s = sub.add_parser("webfill", parents=[web], help="put text in a field of Firefox's active tab (or pick a list option)")
    s.add_argument("target", help='the field\'s label or placeholder, e.g. "Search", or a ref from controls')
    s.add_argument("text")
    s.add_argument("--enter", action="store_true", help="press Enter in the field afterwards")
    s.set_defaults(fn=cmd_web, op="fill")

    s = sub.add_parser("js", help="evaluate a JavaScript expression in Firefox's active tab and print the result")
    s.add_argument("expression")
    s.set_defaults(fn=cmd_js)

    sub.add_parser("teams", help="print the messages of the Teams chat open in Firefox's active tab").set_defaults(
        fn=cmd_teams)

    a = p.parse_args()
    try:
        a.fn(a)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="replace").strip()
        sys.exit(f"Command failed ({e.returncode}): {' '.join(map(str, e.cmd))}\n{err}")


if __name__ == "__main__":
    main()
