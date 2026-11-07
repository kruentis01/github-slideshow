"""The "next step" keybind for ``jobbot apply``.

Press the hotkey (Alt+N by default) in the browser and JobBot clicks the
page's Apply / Continue / Next button for you, then fills in the step that
appears. Buttons are found by what they say rather than by colour, because
sites often colour Continue and the final Submit button the same way.

It never presses a final button: anything saying Submit, Send, Confirm,
Finish, Complete or Pay, or a "Quick apply" / "Easy apply" style button that
might send your CV straight away, is left for you. "Apply" buttons are only
pressed on the job advert itself, never inside a form, where "Apply" often
means send.
"""

from __future__ import annotations

import json
import re

FIND_NEXT_JS = r"""
() => {
  const shown = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect(), s = getComputedStyle(n);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const norm = (t) => (t || "").replace(/\s+/g, " ").replace(/[→›»>←‹«<]+/g, "").trim();
  const FINAL = /\b(submit|send|confirm|finish|complete|pay|purchase|place order)\b/i;
  const RISKY = /\b(quick|easy|one[\s-]?click|1[\s-]?click|instant|express)\b/i;
  const NEXT = /^(next( step| page)?|continue( to [a-z ]+)?|proceed( to [a-z ]+)?|save (and|&) (continue|next)|next: .+)$/i;
  const APPLY = /^(apply( now| online| here)?|apply for (this|the) (job|role|position|vacancy)|start (your |my )?application|apply on (the )?(company|employer)('s)? (site|website))$/i;
  const SKIP_AREA = 'header, nav, footer, [role="search"], form[role="search"]';

  document.querySelectorAll("[data-jobbot-next]").forEach(e => e.removeAttribute("data-jobbot-next"));
  const dialogs = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], dialog[open]')].filter(shown);
  const scope = dialogs.length ? dialogs[dialogs.length - 1] : document;
  const fields = [...scope.querySelectorAll('input:not([type="hidden"]):not([type="search"]):not([type="submit"]):not([type="button"]), textarea, select')]
    .filter(e => shown(e) && !e.closest(SKIP_AREA)).length;
  // No pop-up and (almost) no form fields: this is the job advert, where "Apply" just opens the form.
  const onAdvert = !dialogs.length && fields <= 2;

  const label = (e) => norm(e.tagName === "INPUT" ? e.value : (e.innerText || e.getAttribute("aria-label") || e.title));
  let next = null, apply = null, final = null;
  for (const e of scope.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"], a')) {
    if (!shown(e) || e.disabled || e.getAttribute("aria-disabled") === "true" || e.closest(SKIP_AREA)) continue;
    const text = label(e);
    if (!text || text.length > 40) continue;
    if (FINAL.test(text) || RISKY.test(text)) { final = final || text; continue; }
    if (!next && NEXT.test(text)) next = e;
    if (!apply && APPLY.test(text)) apply = e;
  }
  const pick = onAdvert ? apply : next;
  if (pick) {
    pick.setAttribute("data-jobbot-next", "1");
    return { status: "found", text: label(pick), advert: onAdvert };
  }
  if (final) return { status: "final", text: final };
  if (!onAdvert && apply) return { status: "final", text: label(apply) };  // "Apply" inside a form may send it
  return { status: "none" };
}
"""


def parse_hotkey(spec: str) -> dict:
    """"alt+n" -> {"alt": True, "ctrl": False, "shift": False, "key": "n"}."""
    parts = [p.strip().lower() for p in re.split(r"[+\s]+", spec or "") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = {"alt": False, "ctrl": False, "shift": False}
    for p in parts[:-1]:
        name = {"control": "ctrl", "option": "alt"}.get(p, p)
        if name not in mods:
            raise ValueError(f"unknown modifier '{p}' (use alt, ctrl or shift)")
        mods[name] = True
    key = parts[-1]
    if not (len(key) == 1 or re.fullmatch(r"f([1-9]|1[0-2])", key)):
        raise ValueError(f"unsupported key '{key}' (use a letter, digit or F1-F12)")
    return {**mods, "key": key}


def hotkey_label(spec: dict) -> str:
    mods = [name.capitalize() for name in ("ctrl", "alt", "shift") if spec[name]]
    return "+".join([*mods, spec["key"].upper()])


def listener_js(spec: dict) -> str:
    """Script added to every page and frame: the hotkey calls back into JobBot."""
    return f"""
(() => {{
  if (window.__jobbotKeys) return;
  window.__jobbotKeys = true;
  const want = {json.dumps(spec)};
  window.addEventListener("keydown", (e) => {{
    if (e.repeat) return;
    if (e.altKey !== want.alt || e.ctrlKey !== want.ctrl || e.shiftKey !== want.shift || e.metaKey) return;
    if ((e.key || "").toLowerCase() !== want.key && (e.code || "").toLowerCase() !== "key" + want.key) return;
    e.preventDefault();
    e.stopPropagation();
    if (window.__jobbotHotkey) window.__jobbotHotkey();
  }}, true);
}})();
"""


class Hotkey:
    """Install the hotkey in a browser context and collect presses.

    Presses only arrive while Playwright is running (for example during
    ``page.wait_for_timeout``), so callers poll ``take()`` in a loop.
    """

    def __init__(self, spec: str = "alt+n"):
        self.spec = parse_hotkey(spec)
        self.label = hotkey_label(self.spec)
        self._presses: list = []

    def install(self, context) -> None:
        script = listener_js(self.spec)
        context.expose_binding("__jobbotHotkey", lambda source: self._presses.append(source.get("page")))
        context.add_init_script(script)
        for page in context.pages:
            for frame in page.frames:
                try:
                    frame.evaluate(script)
                except Exception:  # noqa: BLE001 - frame gone or not scriptable
                    pass

    def take(self):
        """Return the page where the hotkey was last pressed, or None."""
        if not self._presses:
            return None
        page = self._presses[-1]
        self._presses.clear()
        return page


def find_next_button(page) -> tuple[str, object | None, str]:
    """Return (status, frame, button text). status is "found", "final" or "none"."""
    final_text = ""
    for frame in [page.main_frame, *[f for f in page.frames if f != page.main_frame]]:
        try:
            info = frame.evaluate(FIND_NEXT_JS)
        except Exception:  # noqa: BLE001
            continue
        if info["status"] == "found":
            return "found", frame, info["text"]
        if info["status"] == "final" and not final_text:
            final_text = info["text"]
    return ("final", None, final_text) if final_text else ("none", None, "")


def advance(page, context=None, settle_ms: int = 1500):
    """Click the page's next-step button. Returns (status, button text, page now in use).

    If the click opened a new tab, that tab is returned so filling carries on there.
    """
    status, frame, text = find_next_button(page)
    if status != "found":
        return status, text, page
    before = list(context.pages) if context is not None else []
    frame.locator('[data-jobbot-next="1"]').first.click(timeout=5000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(settle_ms)  # let pop-ups and the next step render
    if context is not None:
        new = [p for p in context.pages if p not in before]
        if new:
            page = new[-1]
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
    return "clicked", text, page
