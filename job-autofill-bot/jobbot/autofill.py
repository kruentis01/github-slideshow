"""The ``apply`` command's engine: open an application form and fill it in.

How it stays safe:
* It never clicks a submit, send, next or continue button and never presses
  Enter. The only things it clicks are "Apply" *links* (to reach the form) and
  yes/no answers it is sure of.
* While it is typing, a guard on the page cancels any form submission.
* Consent, terms and marketing boxes are left for you to tick.
* Fields it isn't sure about are left empty and outlined in orange.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .profile import availability_phrase, start_phrase

# ------------------------------------------------------------ reading the page

COLLECT_FIELDS_JS = r"""
() => {
  const textOf = (n) => n ? (n.innerText || n.textContent || "").replace(/\s+/g, " ").trim() : "";
  const shown = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect(), s = getComputedStyle(n);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const out = [];
  window.__jobbotN = window.__jobbotN || 0;
  const FIELDS = 'input, textarea, select, [role="combobox"], button[aria-haspopup="listbox"]';
  // When a pop-up (like Reed's "Application questions") is open, fill only that.
  const dialogs = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], dialog[open]')]
    .filter(d => shown(d) && d.querySelector(FIELDS));
  const scope = dialogs.length ? dialogs[dialogs.length - 1] : document;
  for (const el of scope.querySelectorAll(FIELDS)) {
    // Site search bars ("job title", "town or postcode") are never part of an application.
    if (el.closest('header, nav, [role="search"], form[role="search"], form[action*="search" i]')) continue;
    const custom = el.getAttribute("role") === "combobox" || el.getAttribute("aria-haspopup") === "listbox";
    const rawTag = el.tagName.toLowerCase();
    const tag = custom && rawTag !== "select" ? "combobox" : rawTag;
    const type = tag === "combobox" ? (rawTag === "input" ? "combobox-input" : "combobox")
      : tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : tag;
    if (["hidden", "submit", "button", "reset", "image", "password", "search", "color", "range"].includes(type)) continue;
    if (el.disabled || (el.readOnly && tag !== "combobox")) continue;
    const labels = el.labels ? [...el.labels] : [];
    if (!el.labels && el.id) labels.push(...document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`));
    // File inputs and custom radios/checkboxes are often hidden behind a styled label.
    const visible = shown(el) || ((type === "file" || type === "radio" || type === "checkbox") && (labels.some(shown) || shown(el.parentElement)));
    if (!visible) continue;
    let id = el.getAttribute("data-jobbot-id");
    if (!id) { id = "jb" + (window.__jobbotN++); el.setAttribute("data-jobbot-id", id); }
    const labelText = labels.map(textOf);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) lb.split(/\s+/).forEach(i => labelText.push(textOf(document.getElementById(i))));
    let group = "";
    if (type === "radio" || type === "checkbox") {
      const box = el.closest('[role="radiogroup"], [role="group"], fieldset');
      if (box) {
        const legend = box.querySelector("legend");
        const glb = box.getAttribute("aria-labelledby");
        group = legend ? textOf(legend) : glb ? textOf(document.getElementById(glb)) : (box.getAttribute("aria-label") || "");
      }
      if (!group) {
        // Nearest heading-like text before the group of options.
        let p = el.parentElement;
        for (let d = 0; d < 3 && p && !group; d++, p = p.parentElement) {
          let sib = p.previousElementSibling;
          for (let k = 0; k < 2 && sib && !group; k++, sib = sib.previousElementSibling) {
            const t = textOf(sib); if (t && t.length < 200) group = t;
          }
        }
      }
    }
    let near = "";
    if (!labelText.join("").trim() && !el.getAttribute("aria-label")) {
      let node = tag === "combobox" && el.parentElement ? el.parentElement : el;
      for (let d = 0; d < 3 && node && !near; d++) {
        let sib = node.previousElementSibling;
        for (let k = 0; k < 2 && sib && !near; k++, sib = sib.previousElementSibling) {
          const t = textOf(sib);
          if (t && t.length < 200 && !sib.matches(FIELDS)) near = t;
        }
        node = node.parentElement;
      }
    }
    out.push({
      id, tag, type,
      name: el.getAttribute("name") || "", el_id: el.id || "",
      placeholder: el.getAttribute("placeholder") || "",
      aria: el.getAttribute("aria-label") || "",
      autocomplete: el.getAttribute("autocomplete") || "",
      labels: labelText.join(" ").trim(), group, near,
      value: (type === "checkbox" || type === "radio") ? (el.checked ? "checked" : "")
        : type === "file" ? (el.files.length ? "file" : "")
        : type === "combobox" ? (/^\s*((please )?(select|choose)\b.*|-+|)\s*$/i.test(textOf(el)) ? "" : textOf(el))
        : (el.value || ""),
      option_value: el.value || "",
      // Many forms only show a red "*" instead of marking the field required.
      required: !!el.required || el.getAttribute("aria-required") === "true"
        || /\*\s*$/.test(labelText.join(" ").trim()) || /\*\s*$/.test(near) || /\*\s*$/.test(group),
      maxlength: el.maxLength > 0 ? el.maxLength : null,
      options: tag === "select" ? [...el.options].map(o => ({ value: o.value, text: o.text.trim() })) : [],
    });
  }
  return out;
}
"""

GUARD_ON_JS = """
() => {
  if (window.__jobbotGuard) return;
  window.__jobbotGuard = (e) => { e.preventDefault(); e.stopImmediatePropagation(); };
  window.addEventListener("submit", window.__jobbotGuard, true);
}
"""
GUARD_OFF_JS = """
() => {
  if (!window.__jobbotGuard) return;
  window.removeEventListener("submit", window.__jobbotGuard, true);
  delete window.__jobbotGuard;
}
"""

MARK_JS = """
([filled, todo, message]) => {
  for (const id of filled) {
    const el = document.querySelector(`[data-jobbot-id="${id}"]`);
    if (el) { el.style.outline = "2px solid #2da44e"; el.style.outlineOffset = "2px"; }
  }
  for (const id of todo) {
    const el = document.querySelector(`[data-jobbot-id="${id}"]`);
    if (el) { el.style.outline = "2px dashed #d4a72c"; el.style.outlineOffset = "2px"; }
  }
  if (window.top !== window) return;
  let bar = document.getElementById("jobbot-banner");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "jobbot-banner";
    // A normal block at the top of the page, never floating over the site's own buttons.
    bar.style.cssText = "display:block;position:relative;z-index:2147483647;margin:0;padding:10px 40px 10px 14px;" +
      "background:#0d1117;color:#fff;font:14px/1.4 system-ui,sans-serif;text-align:left";
    const close = document.createElement("button");
    close.textContent = "×";
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss");
    close.style.cssText = "position:absolute;right:10px;top:6px;background:none;border:0;color:#fff;font-size:20px;cursor:pointer";
    close.onclick = () => bar.remove();
    bar.append(close, document.createElement("span"));
    document.body.prepend(bar);
    window.scrollTo(0, 0);
  }
  bar.lastChild.textContent = message;
}
"""

APPLY_LINK_JS = r"""
() => {
  const re = /^\s*(apply( now| online| for (this|the) (job|role|position))?|start (your )?application|apply on (the )?(company|employer)('s)? (site|website))\s*$/i;
  for (const a of document.querySelectorAll("a[href]")) {
    const t = (a.innerText || a.textContent || "").replace(/\s+/g, " ").trim();
    const href = a.getAttribute("href") || "";
    if (re.test(t) && !/^(javascript:|#|mailto:)/i.test(href)) return a.href;
  }
  return null;
}
"""

# ------------------------------------------------------------ deciding values

SKIP_RE = re.compile(
    r"captcha|password|card ?number|cvv|cvc|sort ?code|account ?number|national ?insurance|\bni\b ?number|"
    r"date of birth|\bdob\b|passport|search|coupon|promo",
    re.I,
)
CONSENT_RE = re.compile(
    r"agree|consent|terms|privacy|policy|accept|marketing|newsletter|subscribe|confirm that|declar|gdpr|"
    r"keep me updated|contact me about|job alerts",
    re.I,
)
NOT_PERSON_NAME = re.compile(
    r"company|employer|business|organi[sz]ation|referee|reference|school|college|university|user ?name|"
    r"file|job|manager|emergency|next of kin|contact person|account|card|bank",
    re.I,
)


@dataclass
class Rule:
    key: str
    pattern: str
    kinds: tuple[str, ...] = ("text",)  # text | textarea | select | radio | checkbox | file | date | number | email | tel | url

    def __post_init__(self):
        self.regex = re.compile(self.pattern, re.I)


TEXTY = ("text", "email", "tel", "url", "number", "textarea", "select")
RULES = [
    Rule("email", r"e-?mail", ("text", "email")),
    Rule("first_name", r"first[\s_-]*name|fore[\s_-]*name|given[\s_-]*name|\bfname\b|firstname|given-name", ("text",)),
    Rule("last_name", r"last[\s_-]*name|sur[\s_-]*name|family[\s_-]*name|\blname\b|lastname|family-name", ("text",)),
    Rule("full_name", r"full[\s_-]*name|^\s*name\s*\*?\s*$|your name|\bname\b", ("text",)),
    Rule("phone", r"phone|mobile|\btel\b|telephone|contact number|\bcell\b", ("text", "tel", "number")),
    Rule("skip", r"address[\s_-]*(line)?[\s_-]*[23]|address2|address3|address-line[23]|\bcounty\b|\bstate\b|\bprovince\b", TEXTY),
    Rule("address", r"address[\s_-]*(line)?[\s_-]*1|address1|street|address-line1|^\s*address\s*\*?\s*$|home address", ("text", "textarea")),
    Rule("postcode", r"post[\s_-]*code|postal|\bzip\b", ("text",)),
    Rule("town", r"\btown\b|\bcity\b|locality|address-level2", ("text",)),
    Rule("country", r"country|nationality of residence", ("text", "select")),
    Rule("linkedin", r"linked[\s_-]*in", ("text", "url")),
    Rule("website", r"website|portfolio|personal (site|url)|github", ("text", "url")),
    Rule("hear_about", r"how did you (hear|find)|where did you (hear|see|find)|source of application|referral source", ("select", "text", "radio")),
    Rule("right_to_work", r"right to work|eligib\w* to work|legally (entitled|able|allowed|permitted) to work|"
                          r"permission to work|entitled to work|work in the uk", ("select", "radio", "checkbox")),
    Rule("sponsorship", r"sponsor", ("select", "radio", "checkbox")),
    Rule("driving_licence", r"driving licen[cs]e|driver'?s licen[cs]e|full (uk )?licen[cs]e|can you drive", ("select", "radio", "checkbox")),
    Rule("own_transport", r"own transport|own (car|vehicle)|access to (a )?(car|vehicle)", ("select", "radio", "checkbox")),
    Rule("current_employee", r"(currently|presently) (work|employed)\b.{0,20}\b(for|by|with|at)\b|do you (currently )?work (for|at) (us|the company)|"
                             r"current (employee|member of staff|colleague|team member)|(existing|internal) (employee|colleague|candidate)",
         ("select", "radio")),
    Rule("over_18", r"\bover (the age of )?18\b|\baged? 18\b|\bat least 18\b|\b18 (years|or over|or older)|\b18\+", ("select", "radio", "checkbox")),
    Rule("nights", r"(work|available|availability|able|willing|happy).{0,25}\b(nights?|overnight)\b|night (shifts?|work)", ("select", "radio", "checkbox")),
    Rule("evenings", r"(work|available|availability|able|willing|happy).{0,25}\bevenings?\b|evening (shifts?|work)|late shifts?", ("select", "radio", "checkbox")),
    Rule("weekends", r"(work|available|availability|able|willing|happy).{0,25}\bweekends?\b|weekend (shifts?|work|availability)|"
                     r"work (on )?(saturdays?|sundays?)", ("select", "radio", "checkbox")),
    Rule("start_date", r"start[\s_-]*date|available from|availability date|when can you start|earliest start|date available", ("date", "text")),
    Rule("notice_period", r"notice", ("text", "select")),
    Rule("hours_per_week", r"hours (per|a|each) week|how many hours|weekly hours|hours available|hours do you want", ("number", "text", "select")),
    Rule("availability", r"availab|days (can|are) you|when are you free|shifts? (can|could) you", ("text", "textarea")),
    Rule("expected_rate", r"expected (hourly )?(rate|pay|salary)|salary expectation|desired (pay|salary|rate)|"
                          r"hourly rate|rate of pay|pay expectation", ("text", "number")),
    Rule("current_title", r"current (job )?title|current role|current position|most recent (job|role|position)|^\s*job title", ("text",)),
    Rule("current_employer", r"current employer|current company|most recent employer|^\s*employer", ("text",)),
    Rule("years_experience", r"years? of (relevant )?experience|how many years", ("text", "number", "select")),
    Rule("cover_letter", r"cover(ing)? (letter|note)|supporting (statement|information)|personal statement|"
                         r"why (do )?you want|why are you (interested|applying)|tell us (a bit )?about (yourself|you)|"
                         r"additional information|message to (the )?(hiring|employer|recruiter)|motivation", ("textarea",)),
    Rule("cv", r"\bcv\b|resume|résumé|curriculum", ("file",)),
    Rule("summary", r"summary|about (you|me)|profile", ("textarea",)),
]

YES_RE = re.compile(r"^\s*(yes|y|true|i do|i have|i am)\b", re.I)
NO_RE = re.compile(r"^\s*(no|n|false|i do not|i don't|i have not|i am not)\b", re.I)


def describe(f: dict) -> str:
    """Human words first (label, aria, placeholder, question), then code names."""
    human = " | ".join(x for x in (f.get("labels"), f.get("aria"), f.get("placeholder"), f.get("group"), f.get("near")) if x)
    code = " | ".join(x for x in (f.get("name"), f.get("el_id"), f.get("autocomplete")) if x)
    code = re.sub(r"([a-z])([A-Z])", r"\1 \2", code).replace("_", " ").replace("-", " ").replace("[", " ").replace("]", " ")
    return f"{human} || {code}".strip(" |")


def field_kind(f: dict) -> str:
    if f["tag"] == "combobox":
        return "select"  # custom dropdowns answer the same questions as real ones
    if f["tag"] in ("textarea", "select"):
        return f["tag"]
    return {"email": "email", "tel": "tel", "url": "url", "number": "number", "date": "date",
            "radio": "radio", "checkbox": "checkbox", "file": "file"}.get(f["type"], "text")


def match_rule(f: dict, desc: str | None = None) -> Rule | None:
    desc = desc if desc is not None else describe(f)
    kind = field_kind(f)
    if SKIP_RE.search(desc):
        return None
    for rule in RULES:
        allowed = rule.kinds
        if kind not in allowed and not (kind in ("email", "tel", "url") and "text" in allowed):
            continue
        if not rule.regex.search(desc):
            continue
        if rule.key == "full_name" and NOT_PERSON_NAME.search(desc):
            continue
        if rule.key == "cv" and re.search(r"cover|photo|picture|image|portfolio", desc, re.I):
            continue
        return rule
    if kind == "file" and not re.search(r"cover|photo|picture|image", desc, re.I):
        return RULES[[r.key for r in RULES].index("cv")]
    return None


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def cover_letter(profile: dict, job: dict) -> str:
    cv = profile.get("cv", {})
    av = profile.get("availability", {})
    skills = job.get("fit", {}).get("matched_skills") or cv.get("skills", [])
    skills = [s for s in skills if len(s) > 2][:4]
    if skills:
        listed = ", ".join(skills[:-1]) + (f" and {skills[-1]}" if len(skills) > 1 else skills[0])
        skills_sentence = f"My experience includes {listed}, which I believe makes me a strong fit for this role."
    else:
        skills_sentence = "I am hard-working, reliable and quick to learn."
    summary = (cv.get("summary") or "").strip()
    if summary:
        first = re.split(r"(?<=[.!?])\s", summary)[0]
        summary_sentence = first if first.endswith((".", "!", "?")) else first + "."
    elif cv.get("current_title"):
        where = f" at {cv['current_employer']}" if cv.get("current_employer") else ""
        summary_sentence = f"I currently work as a {cv['current_title']}{where}."
    else:
        summary_sentence = ""
    values = _SafeDict(
        job_title=job.get("title") or "advertised",
        company=job.get("company") or "your organisation",
        summary_sentence=summary_sentence,
        skills_sentence=skills_sentence,
        start_phrase=start_phrase(av),
        availability_phrase=availability_phrase(av),
        full_name=cv.get("full_name", ""),
        phone=cv.get("phone", ""),
        email=cv.get("email", ""),
        current_title=cv.get("current_title", ""),
        current_employer=cv.get("current_employer", ""),
    )
    from .profile import DEFAULT_COVER_LETTER

    template = profile.get("cover_letter_template") or DEFAULT_COVER_LETTER
    text = template.format_map(values)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"  +", " ", text)).strip()


def _full_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url.removeprefix("//")
    return url


def profile_values(profile: dict, job: dict) -> dict:
    cv = profile.get("cv", {})
    prefs = profile.get("preferences", {})
    av = profile.get("availability", {})
    rate = prefs.get("expected_hourly_rate")
    return {
        "first_name": cv.get("first_name", ""),
        "last_name": cv.get("last_name", ""),
        "full_name": cv.get("full_name", ""),
        "email": cv.get("email", ""),
        "phone": cv.get("phone", ""),
        "address": cv.get("address", ""),
        "town": cv.get("town", ""),
        "postcode": cv.get("postcode", ""),
        "country": "United Kingdom",
        "linkedin": _full_url(cv.get("linkedin", "")),
        "website": _full_url(cv.get("website", "")),
        "current_title": cv.get("current_title", ""),
        "current_employer": cv.get("current_employer", ""),
        "years_experience": str(cv["years_experience"]) if cv.get("years_experience") is not None else "",
        "expected_rate": f"{float(rate):.2f}" if rate else "",
        # "Immediately" (or any date now passed) means today, not the day setup was run.
        "start_date": max(av.get("start_date") or "", date.today().isoformat()),
        "notice_period": av.get("notice_period", ""),
        "hours_per_week": str(av.get("hours_per_week", "")),
        "availability": availability_phrase(av),
        "right_to_work": av.get("right_to_work"),
        "sponsorship": av.get("needs_sponsorship"),
        "driving_licence": av.get("driving_licence"),
        "own_transport": av.get("own_transport"),
        # Someone using JobBot is applying from outside, so "Do you currently work for us?" is No.
        "current_employee": bool(av.get("current_employee", False)),
        "over_18": av.get("over_18"),
        "weekends": av.get("weekends"),
        "evenings": av.get("evenings"),
        "nights": av.get("nights"),
        "hear_about": {"reed": "Reed.co.uk", "adzuna": "Adzuna"}.get(job.get("source", ""), "Job board"),
        "cover_letter": cover_letter(profile, job),
        "summary": cv.get("summary", ""),
        "cv": profile.get("cv_path", ""),
    }


@dataclass
class Action:
    field_id: str
    key: str
    op: str  # fill | select | choose | click | upload
    value: str
    label: str


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    unfilled_required: list[tuple[str, str]] = field(default_factory=list)  # (field id, label)
    unanswered: list[str] = field(default_factory=list)  # optional questions left empty
    skipped_existing: int = 0


def _option_for(options: list[dict], value, key: str) -> dict | None:
    real = [o for o in options if o["value"].strip() or o["text"].strip()]
    real = [o for o in real if not re.match(r"^\s*(please )?(select|choose)|^\s*-+\s*$|^\s*$", o["text"], re.I)] or real
    if isinstance(value, bool):
        pattern = YES_RE if value else NO_RE
        return next((o for o in real if pattern.search(o["text"]) or pattern.search(o["value"])), None)
    if key == "country":
        return next((o for o in real if re.search(r"united kingdom|^uk$|great britain|^gb$", o["text"], re.I)), None)
    if key == "hear_about":
        src = re.split(r"[.\s]", str(value).lower())[0]
        return (next((o for o in real if src and src in o["text"].lower()), None)
                or next((o for o in real if re.search(r"job (board|site|website)|online|internet|website", o["text"], re.I)), None))
    want = str(value).strip().lower()
    if not want:
        return None
    for test in (lambda t: t == want, lambda t: t.startswith(want), lambda t: want in t):
        hit = next((o for o in real if test(o["text"].lower()) or test(o["value"].lower())), None)
        if hit:
            return hit
    if key in ("hours_per_week", "years_experience"):
        try:
            n = float(want)
        except ValueError:
            return None
        for o in real:  # ranges like "30-40" or "5+"
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", o["text"])]
            if len(nums) == 2 and nums[0] <= n <= nums[1]:
                return o
            if len(nums) == 1 and "+" in o["text"] and n >= nums[0]:
                return o
    return None


def _uk_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def plan_fills(fields: list[dict], values: dict, overwrite: bool = False) -> Plan:
    plan = Plan()
    radios: dict[str, list[dict]] = defaultdict(list)
    for f in fields:
        if f["type"] == "radio":
            radios[f["name"] or f["id"]].append(f)
            continue
        desc = describe(f)
        kind = field_kind(f)
        label = f.get("labels") or f.get("aria") or f.get("placeholder") or f.get("near") or f.get("name") or f["id"]

        if kind == "checkbox":
            rule = None if CONSENT_RE.search(desc) else match_rule(f, desc)
            if rule and isinstance(values.get(rule.key), bool) and values[rule.key] and not f["value"]:
                plan.actions.append(Action(f["id"], rule.key, "click", "check", label))
            elif f["required"] and not f["value"]:
                plan.unfilled_required.append((f["id"], label))
            continue

        if f["value"] and not overwrite:
            plan.skipped_existing += 1
            continue
        rule = match_rule(f, desc)
        value = values.get(rule.key) if rule and rule.key != "skip" else None
        action = None
        if value not in (None, ""):
            if kind == "file":
                action = Action(f["id"], rule.key, "upload", value, label)
            elif f["tag"] == "combobox":
                if isinstance(value, bool):
                    action = Action(f["id"], rule.key, "choose", "Yes" if value else "No", label)
                elif f["type"] == "combobox-input" and rule.key not in ("country", "hear_about"):
                    action = Action(f["id"], rule.key, "fill", str(value), label)
                else:
                    action = Action(f["id"], rule.key, "choose", str(value), label)
            elif kind == "select":
                opt = _option_for(f["options"], value, rule.key)
                if opt:
                    action = Action(f["id"], rule.key, "select", opt["value"], label)
            elif not isinstance(value, bool):
                text = str(value)
                if rule.key == "start_date" and kind != "date":
                    text = _uk_date(text)
                if kind == "number":
                    text = (re.findall(r"\d+(?:\.\d+)?", text) or [""])[0]
                if f.get("maxlength") and len(text) > f["maxlength"]:
                    text = text[: f["maxlength"]]
                if text:
                    action = Action(f["id"], rule.key, "fill", text, label)
        if action:
            plan.actions.append(action)
        elif f["required"]:
            plan.unfilled_required.append((f["id"], label))
        elif not (rule and rule.key == "skip") and (f.get("labels") or f.get("near") or f.get("aria")):
            plan.unanswered.append(label)

    for name, group in radios.items():
        question = " | ".join(x for x in (group[0].get("group"), group[0].get("near"), name) if x)
        probe = {**group[0], "labels": "", "aria": "", "placeholder": "", "group": question, "near": ""}
        rule = match_rule(probe, describe(probe))
        already = any(g["value"] for g in group)
        if already and not overwrite:
            plan.skipped_existing += 1
            continue
        value = values.get(rule.key) if rule else None
        target = None
        if isinstance(value, bool):
            pattern = YES_RE if value else NO_RE
            target = next((g for g in group if pattern.search(g.get("labels") or "") or pattern.search(g["option_value"])), None)
        elif rule and value:
            opts = [{"value": g["id"], "text": g.get("labels") or g["option_value"]} for g in group]
            hit = _option_for(opts, value, rule.key)
            target = next((g for g in group if hit and g["id"] == hit["value"]), None)
        if target:
            plan.actions.append(Action(target["id"], rule.key, "click", target.get("labels") or target["option_value"],
                                       group[0].get("group") or name))
        elif any(g["required"] for g in group):
            plan.unfilled_required.append((group[0]["id"], group[0].get("group") or name))
        elif group[0].get("group"):
            plan.unanswered.append(group[0]["group"])
    return plan


# ------------------------------------------------------------ doing it

@dataclass
class FillReport:
    url: str
    filled: list[str] = field(default_factory=list)
    needs_you: list[str] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fields_seen: int = 0


def _choose_option(frame, loc, wanted: str) -> None:
    """Pick an answer in a custom dropdown: open it, click the matching option.

    Opening a dropdown and picking an option never submits anything. If no
    option matches, the dropdown is closed again by clicking it (not Escape,
    which would close a pop-up form).
    """
    loc.click(timeout=5000)
    options = frame.locator('[role="option"]:visible, [role="listbox"] li:visible')
    try:
        options.first.wait_for(state="visible", timeout=3000)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("the dropdown's options didn't appear") from exc
    if wanted in ("Yes", "No"):
        pattern = YES_RE if wanted == "Yes" else NO_RE
    else:
        pattern = re.compile(rf"^\s*{re.escape(wanted)}", re.I)
    for i in range(options.count()):
        opt = options.nth(i)
        if pattern.search(opt.inner_text(timeout=2000)):
            opt.click(timeout=3000)
            return
    loc.click(timeout=3000)
    raise RuntimeError(f"no '{wanted}' option")


def _run_actions(frame, plan: Plan, report: FillReport) -> list[str]:
    done_ids = []
    for a in plan.actions:
        loc = frame.locator(f'[data-jobbot-id="{a.field_id}"]').first
        try:
            if a.op == "fill":
                loc.fill(a.value, timeout=5000)
            elif a.op == "select":
                loc.select_option(value=a.value, timeout=5000)
            elif a.op == "upload":
                loc.set_input_files(a.value, timeout=5000)
            elif a.op == "choose":
                _choose_option(frame, loc, a.value)
            elif a.op == "click":
                # JS click works for visually hidden custom radios/checkboxes and
                # fires the same events a real click would.
                loc.evaluate("el => { if (!el.checked) el.click(); }")
            done_ids.append(a.field_id)
            flat = " ".join(a.value.split())
            shown = flat if len(flat) < 40 else flat[:37] + "…"
            if a.op == "upload":
                shown = a.value.rsplit("/", 1)[-1]
            report.filled.append(f"{a.label[:50]}: {shown}" if a.op != "select" else f"{a.label[:50]}: (option chosen)")
        except Exception as exc:  # noqa: BLE001 - one bad field shouldn't stop the rest
            report.errors.append(f"{a.label[:50]}: {str(exc).splitlines()[0][:120]}")
    return done_ids


def fill_page(page, values: dict, overwrite: bool = False) -> FillReport:
    """Fill every form on the page (and in its iframes). Never submits."""
    report = FillReport(url=page.url)
    for frame in page.frames:
        try:
            frame.evaluate(GUARD_ON_JS)
            fields = frame.evaluate(COLLECT_FIELDS_JS)
        except Exception:  # noqa: BLE001 - detached or cross-process frame
            continue
        if not fields:
            continue
        report.fields_seen += len(fields)
        plan = plan_fills(fields, values, overwrite=overwrite)
        done = _run_actions(frame, plan, report)
        todo = [fid for fid, _ in plan.unfilled_required]
        report.needs_you.extend(label for _, label in plan.unfilled_required)
        report.unanswered.extend(plan.unanswered)
        try:
            frame.evaluate(MARK_JS, [done, todo, ""])
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                frame.evaluate(GUARD_OFF_JS)
            except Exception:  # noqa: BLE001
                pass
    message = (f"JobBot filled {len(report.filled)} field(s) (green). "
               + (f"{len(report.needs_you)} required field(s) need you (orange dashed). " if report.needs_you else "")
               + "Check everything, tick any consent boxes yourself, then submit when you're happy.")
    try:
        page.evaluate(MARK_JS, [[], [], message])
    except Exception:  # noqa: BLE001
        pass
    return report


def find_apply_link(page) -> str | None:
    try:
        return page.evaluate(APPLY_LINK_JS)
    except Exception:  # noqa: BLE001
        return None


def open_application(page, url: str, follow_apply_links: bool = True, min_fields: int = 3) -> None:
    """Go to the job and, if the advert page has no form, follow its "Apply" link."""
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001 - busy pages never go idle; that's fine
        pass
    if not follow_apply_links:
        return
    for _ in range(2):
        count = sum(len(f.evaluate(COLLECT_FIELDS_JS)) for f in page.frames if not f.is_detached())
        if count >= min_fields:
            return
        link = find_apply_link(page)
        if not link or link == page.url:
            return
        page.goto(link, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
