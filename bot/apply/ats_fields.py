"""Витяг і класифікація полів ATS-форм (Ashby, Greenhouse, Lever, Workable).

Чиста частина: `FIELD_JS` віддається в `page.evaluate`, `fields_from` і
`classify` працюють на списках словників, тож тестуються без браузера.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FIELD_SELECTOR = "input,select,textarea"

# Один запис на елемент FIELD_SELECTOR у порядку DOM, разом із прихованими:
# індекс `i` збігається з page.locator(FIELD_SELECTOR).nth(i).
FIELD_JS = r"""
(() => {
  // Текст без aria-hidden вставок: зірочка обов'язковості Greenhouse (<span aria-hidden>*</span>).
  const txt = (n) => {
    if (!n) return "";
    const c = n.cloneNode(true);
    if (c.querySelectorAll) c.querySelectorAll("[aria-hidden=true]").forEach((x) => x.remove());
    return (c.textContent || "").replace(/\s+/g, " ").trim();
  };
  // Обгортаючий <label> містить текст усіх <option> вкладеного <select>
  // («LinkedIn», «Company website»), що ламає класифікацію за підписом;
  // тож читаємо клон мітки без керуючих елементів.
  const labelTxt = (l) => {
    const c = l.cloneNode(true);
    c.querySelectorAll("select,option,textarea,input").forEach((x) => x.remove());
    return txt(c);
  };
  // Ids and label[for] resolve inside the node's own tree first: a shadow root has its own ids.
  const rootOf = (n) => { const r = n && n.getRootNode ? n.getRootNode() : document; return r.getElementById ? r : document; };
  const byIds = (ids, n) => (ids || "").split(/\s+/).filter(Boolean)
    .map((id) => txt(rootOf(n).getElementById(id) || document.getElementById(id))).filter(Boolean).join(" ");
  const labelFor = (key, n) => {
    if (!key) return null;
    const q = "label[for=" + CSS.escape(key) + "]";
    return rootOf(n).querySelector(q) || document.querySelector(q);
  };
  const rectOk = (n) => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  // Dropzones hide the file input itself and show its container: the input counts as visible when it
  // or one of its 4 nearest ancestors (a shadow host included) is on screen. A hidden tab shows none.
  const nearOk = (n) => {
    for (let k = 0; k < 5 && n; k++, n = n.parentElement || (n.getRootNode() && n.getRootNode().host)) {
      if (n.nodeType === 1 && rectOk(n)) return true;
    }
    return false;
  };
  const els = Array.from(document.querySelectorAll("input,select,textarea"));
  // Web components (SmartRecruiters, SAP UI5) keep their inputs in open shadow roots. Those follow the
  // light fields; when there are any, every field gets a data-js-field mark and is located by it.
  const shadowOf = (sel) => {
    const out = [];
    const walk = (root) => {
      for (const h of root.querySelectorAll("*")) {
        if (h.shadowRoot) { out.push(...h.shadowRoot.querySelectorAll(sel)); walk(h.shadowRoot); }
      }
    };
    walk(document);
    return out;
  };
  const shadow = shadowOf("input,select,textarea");
  const marked = shadow.length > 0;
  if (marked) { els.push(...shadow); els.forEach((el, k) => el.setAttribute("data-js-field", String(k))); }
  // A shadow input without its own label: the label sits on a host element up the chain.
  const hostLabel = (el) => {
    let r = el.getRootNode();
    for (let k = 0; k < 4 && r && r.host; k++, r = r.host.getRootNode()) {
      const h = r.host;
      const t = h.getAttribute("label") || h.getAttribute("aria-label") || h.getAttribute("placeholder")
        || txt(h.querySelector(":scope > label, :scope > [slot=label]"));
      if (t) return t.trim();
    }
    return "";
  };
  const hostRequired = (el) => {
    let r = el.getRootNode();
    for (let k = 0; k < 4 && r && r.host; k++, r = r.host.getRootNode()) {
      if (r.host.hasAttribute("required") || r.host.getAttribute("aria-required") === "true") return true;
    }
    return false;
  };
  return els.map((el, i) => {
    const parts = [];
    if (el.labels) for (const l of el.labels) parts.push(labelTxt(l));
    // Abacus (FormKit inside <input-field>): the wrapper repeats the input's id, so label[for] binds to
    // the wrapper and el.labels is empty. A label naming this id is still this input's.
    if (!parts.some(Boolean) && el.id) {
      const idLabel = labelFor(el.id, el);
      if (idLabel) parts.push(labelTxt(idLabel));
    }
    // A radio/checkbox whose <label> is an empty styled circle (LinkedIn): the option text sits in a
    // nearby block. The first short text up the tree is the option («Ja»).
    if ((el.type === "radio" || el.type === "checkbox") && el.labels && el.labels.length
        && !Array.from(el.labels).some((l) => labelTxt(l))) {
      let n = el.parentElement;
      for (let k = 0; k < 3 && n; k++, n = n.parentElement) {
        const t = labelTxt(n);
        if (t) { if (t.length <= 60) parts.push(t); break; }
      }
    }
    const aria = el.getAttribute("aria-label");
    if (aria) parts.push(aria.trim());
    parts.push(byIds(el.getAttribute("aria-labelledby"), el));
    // Ashby: <label for=<uuid>>, де uuid це name прихованого checkbox Yes/No (не id);
    // автодоповнення без id підписане label[for=data-field-path] контейнера.
    const box = el.closest("[data-field-path]");
    const boxLabel = box ? labelFor(box.getAttribute("data-field-path"), box) : null;
    const nameLabel = !parts.some(Boolean) && el.name ? labelFor(el.name, el) : null;
    if (nameLabel) parts.push(labelTxt(nameLabel));
    if (!parts.some(Boolean) && boxLabel) parts.push(labelTxt(boxLabel));
    if (!parts.some(Boolean) && marked) parts.push(hostLabel(el));
    // Greenhouse дає той самий текст і через <label for>, і через aria-labelledby: без дублів.
    const label = Array.from(new Set(parts.filter(Boolean))).join(" ");
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : tag;
    const choice = type === "checkbox" || type === "radio";
    const fs = el.closest("fieldset");
    const legend = fs ? fs.querySelector("legend") : null;
    const rg = el.closest("[role=group]");
    // Greenhouse: <div role=group aria-labelledby=upload-label-resume aria-required=false>.
    const rgLabel = rg ? ((rg.getAttribute("aria-label") || "").trim() || byIds(rg.getAttribute("aria-labelledby"), rg)) : "";
    const group = txt(legend) || rgLabel || (choice && boxLabel ? labelTxt(boxLabel) : "");
    // Ashby Yes/No: checkbox display:none, видимі лише кнопки aria-pressed у тому ж контейнері.
    const yesno = choice && box ? Array.from(box.querySelectorAll("button[data-option]")) : [];
    const pressed = yesno.some((b) => b.getAttribute("aria-pressed") === "true");
    let value = choice ? (el.checked ? (el.value || "on") : (pressed ? "on" : "")) : (el.value || "");
    const combobox = el.getAttribute("role") === "combobox" || el.hasAttribute("aria-autocomplete") || !!el.getAttribute("list");
    if (combobox && !value) {
      // react-select після вибору тримає значення не в input, а в сусідньому single-value.
      const ctl = el.closest('[class*="control"], [class*="value-container"]');
      const sv = ctl ? ctl.querySelector('[class*="single-value"], [class*="multi-value"]') : null;
      if (sv) value = txt(sv);
    }
    return {
      i, tag, type,
      name: el.getAttribute("name") || "",
      id: el.id || "",
      // Ashby ставить обов'язковість класом _required_* на підписі контейнера, не атрибутом поля.
      required: el.required || el.getAttribute("aria-required") === "true" || (!!rg && rg.getAttribute("aria-required") === "true")
        || (!!boxLabel && /(^|\s)_required_/.test(boxLabel.className || "")) || (marked && hostRequired(el)),
      label,
      placeholder: el.getAttribute("placeholder") || "",
      // aria-hidden: прихований дзеркальний required-input react-select, не поле форми.
      // A styled radio/checkbox hides its input and shows its label: the visible label counts.
      visible: el.getAttribute("aria-hidden") !== "true" && (rectOk(el) || yesno.some(rectOk) || (type === "file" && nearOk(el))
        || (choice && ((!!el.labels && Array.from(el.labels).some(rectOk)) || nearOk(el)))),
      // Для checkbox/radio value — стан вибору: атрибут value є і в невибраного.
      value,
      // Атрибут value окремо: сусідні radio з одним name розрізняються лише ним і підписом.
      attr_value: el.getAttribute("value") || "",
      options: tag === "select" ? Array.from(el.options).map((o) => o.text.trim()) : null,
      group,
      // Автодоповнення (Greenhouse location, react-select): текст лише друкується, далі клік по опції.
      combobox,
      marked,
      // File inputs: several files at once, the accepted types, and Ashby's «Autofill from resume»
      // uploader (an unlabelled input that is not the application's CV field).
      multiple: type === "file" && !!el.multiple,
      accept: type === "file" ? (el.getAttribute("accept") || "") : "",
      ashby_autofill: type === "file" && !!el.closest('[class*="ashby-application-form-autofill"]'),
    };
  });
})()
"""

Kind = Literal[
    "first_name", "last_name", "full_name", "preferred_name", "email", "phone",
    "linkedin", "github", "website", "location", "country", "resume",
    "cover_letter_file", "cover_letter_text", "free_text", "eeo", "ack", "date",
    "choice", "select", "years", "unknown",
    # ринок найму (німецькі форми): адреса, дата народження, громадянство, звертання,
    # річна зарплата, звідки дізнались
    "street", "house_number", "zip", "city", "birth_date", "nationality", "salutation", "salary", "source",
    # German file uploads: a generic documents field («Anlagen», «Bewerbungsunterlagen»), a photo
    "documents", "photo",
]


@dataclass
class Field:
    i: int
    tag: str
    type: str
    name: str
    id: str
    required: bool
    label: str
    placeholder: str
    visible: bool
    value: str
    options: list[str] | None
    group: str
    combobox: bool = False
    attr_value: str = ""
    marked: bool = False   # located by [data-js-field=i]: the page has fields in shadow roots
    multiple: bool = False   # a file input that takes several files
    accept: str = ""         # a file input's accept attribute
    ashby_autofill: bool = False   # inside Ashby's «Autofill from resume» uploader


# A question marked required only in its text: «… *», a screen-reader «required», «Pflichtfeld».
# onlyfy (02.11.2026) sets no required attribute on its radio groups.
_REQUIRED_MARK = re.compile(r"(?:\*|\brequired|\bpflichtfeld|\berforderlich)\s*$", re.IGNORECASE)


def _text_required(d: dict) -> bool:
    text = d.get("group") if d.get("type") in ("radio", "checkbox") and d.get("group") else d.get("label")
    return bool(_REQUIRED_MARK.search(text or ""))


def fields_from(raw: list[dict]) -> list[Field]:
    """Список словників із FIELD_JS у список Field."""
    return [
        Field(
            i=int(d["i"]), tag=d["tag"], type=d.get("type", ""), name=d.get("name", ""),
            id=d.get("id", ""), required=bool(d.get("required")) or _text_required(d), label=d.get("label", ""),
            placeholder=d.get("placeholder", ""), visible=bool(d.get("visible")),
            value=d.get("value", ""), options=d.get("options"), group=d.get("group", ""),
            combobox=bool(d.get("combobox")), attr_value=d.get("attr_value", ""), marked=bool(d.get("marked")),
            multiple=bool(d.get("multiple")), accept=d.get("accept") or "", ashby_autofill=bool(d.get("ashby_autofill")),
        )
        for d in raw
    ]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


# «Ethnic group», «protected Veterans», «Form CC-305» (a Greenhouse form, 03.11.2026) slipped past the singular forms.
_EEO = (r"\b(gender|race|ethnic\w*|hispanic|veterans?|disabilit(?:y|ies)|pronouns|transgender|sexual orientation)\b"
        r"|self-identif\w*|\bcc-305\b")
_ACK = (r"acknowledge|i confirm|i agree|consent|privacy|datenschutz|einverstanden|einwillig\w*"
        r"|zur kenntnis|kenntnis genommen")
# A consent asked as a radio: the option itself says yes and the question names terms or privacy.
_CONSENT = (r"\bagbs?\b|terms (?:of use|and conditions|of service)|nutzungsbedingungen|datenschutz\w*"
            r"|privacy (?:policy|notice|statement)|einwillig\w*|consent to|i agree|zur kenntnis")
_YES_OPTION = r"^\s*(?:ja|yes|i agree|ich stimme zu|einverstanden)\b"
# «Are you currently located in …?» це Yes/No питання, а не поле локації: його відсіює lookbehind;
# так само «relocation» («Are you open to relocation?»).
_LOCATION = (r"(?<!re)location|\bcity\b|where are you (?:currently )?(?:located|based)"
             r"|(?<!are you )currently located|current location")
# Без \b: «_systemfield_resume» і «cv_upload» мають збігатися через підкреслення.
_RESUME = r"(?<![a-z])(resume|cv|lebenslauf)(?![a-z])"
# A generic documents upload on German forms; the CV goes there when the form has no CV field.
# English words only whole: «canvas_upload» or «profile» are not documents.
_DOCUMENTS = (r"anlage|unterlage|dokument|datei|anhang|anh(?:ä|ae)nge"
              r"|(?<![a-z])(?:attachments?|documents?|files?)(?![a-z])")
# Certificates are no place for the CV: such a field keeps the cover-letter rule.
_CERTIFICATES = r"zeugnis|zertifikat|nachweis|certificate|diplom"
# «Bild» only whole: «Weiterbildung» is education, not a picture.
_PHOTO = (r"foto|photo|lichtbild|profilbild|bewerbungsbild|picture|portrait|avatar|headshot"
          r"|(?<![a-z])bild(?![a-z])")
# SmartRecruiters' avatar lists .jfif among its types (30.10.2026); one unknown image type used to
# make the whole list look like more than images.
_IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".jfif", ".pjpeg", ".pjp", ".png", ".gif", ".bmp", ".webp", ".heic",
              ".heif", ".avif", ".tif", ".tiff", ".svg", ".ico"}
# Числове питання про роки досвіду («How many years of work experience do you have with X?»).
YEARS_ALTERNATIVES = (
    r"how many years", r"years of (?:work )?experience", r"jahre (?:berufs)?erfahrung", r"wie viele jahre",
)
_YEARS = "|".join(YEARS_ALTERNATIVES)
# Німецькі форми роботодавців (ринок найму). «Land» лише цілим словом: «Deutschland»
# у підписі, «Landesvorwahl» телефону. «Ort» теж цілим словом: «Standort» вакансії
# це не місто кандидата.
_STREET = r"stra(?:ß|ss)e|\bstr\.|street address|\badresse\b|\baddress line"
# A house number field of its own (Abacus «Hausnummer*»); «Straße & Hausnr.» stays street.
_HOUSE_NUMBER = r"hausnummer|haus-?nr|house number|\bhnr\b"
_ZIP = r"\bplz\b|postleitzahl|\bzip\b|postal code|postcode"
_CITY_DE = r"\bort\b|wohnort|\bstadt\b"
_COUNTRY_DE = r"\bland\b|wohnsitzland"
# A question about the right to work «in the country …» is never a country pick.
_WORK_PERMIT = r"eligib|authori[sz]|sponsor|\bvisa\b|work permit"
_BIRTH = r"geburt|date of birth|birthday|birth date"
_NATIONALITY = r"staatsangeh|nationalit"
_SALUTATION = r"\banrede\b|salutation"
_SALARY = r"gehalt|jahresgehalt|salary expectation|expected salary|desired salary|vergütungsvorstellung"
_SOURCE = r"aufmerksam geworden|wie haben sie .{0,30}erfahren|how did you hear|woher kennen sie"
# A textarea that asks for a cover letter, or a generic message box («Nachricht», «Comments») on a
# contact-style form: both take the cover note that is already written. English words only whole:
# «messaging architecture» is a real question.
_COVER_TEXT = (r"cover letter|additional information|anything else|\bwhy\b|motivation|anschreiben"
               r"|nachricht|mitteilung|anmerkung|bemerkung|kommentar"
               r"|(?<![a-z])(?:message|comments?)(?![a-z])")
_DATE_DE = r"eintrittstermin|eintrittsdatum|verf(?:ü|ue)gbar ab|fr(?:ü|ue)hest|starttermin|arbeitsbeginn"


def _image_only(accept: str) -> bool:
    """The input's accept attribute lists images only («image/*», «.jpg,.png»)."""
    kinds = [a.strip().lower() for a in (accept or "").split(",") if a.strip()]
    return bool(kinds) and all(k.startswith("image/") or k in _IMAGE_EXT for k in kinds)


def accepts_file(accept: str, path: str) -> bool:
    """Whether a file input with this accept attribute takes the file: an extension («.pdf», also a
    sloppy «pdf»), a MIME type («application/pdf») or a family («image/*»). No accept takes anything."""
    kinds = [a.strip().lower() for a in (accept or "").split(",") if a.strip()]
    if not kinds:
        return True
    suffix = Path(path).suffix.lower()
    mime = (mimetypes.guess_type(path)[0] or "").lower()
    for k in kinds:
        if k in ("*", "*/*"):
            return True
        if "/" not in k:
            if suffix and "." + k.lstrip(".") == suffix:
                return True
        elif k.endswith("/*"):
            if mime.startswith(k[:-1]):
                return True
        elif k == mime:
            return True
    return False


def documents_label(f: Field) -> bool:
    """The field's visible text names documents or certificates: a CV field like that takes the whole
    package. Name and id do not count: «cv_file» is a CV field."""
    text = f"{f.label} {f.placeholder} {f.group}".lower()
    return _has(_DOCUMENTS, text) or _has(_CERTIFICATES, text)


def classify(f: Field) -> Kind:
    """Field kind by label, placeholder, name, id and group; the first rule that matches wins.

    A file field: an image-only accept or a photo word in its name or id → photo; resume/cv/lebenslauf
    in the label or placeholder → resume; a photo word (foto, avatar, profile picture, bild) anywhere →
    photo; resume/cv/lebenslauf anywhere → resume; cover letter/anschreiben → cover_letter_file; a
    generic documents word (anlagen, unterlagen,
    dokumente, dateien, anhänge) without certificates → documents (which file it takes depends on
    the whole form, see ats_form.fill_form); otherwise required → resume (one field does not see the
    page, so any required one); otherwise with any text → cover_letter_file; no text at all →
    unknown (Ashby's autofill uploader, or a lone unlabelled upload elsewhere).
    """
    text = f"{f.label} {f.placeholder} {f.name} {f.id} {f.group}".lower()
    if f.type == "file":
        # The photo goes first: the CV in an avatar input breaks the form (SmartRecruiters, 30.10.2026).
        # An image-only input, or one whose own name or id says photo, is a photo even under a «Resume»
        # label borrowed from a container; a visible CV label beats a photo word in the group.
        if _image_only(f.accept) or _has(_PHOTO, f"{f.name} {f.id}".lower()):
            return "photo"
        if _has(_RESUME, f"{f.label} {f.placeholder}".lower()):
            return "resume"
        if _has(_PHOTO, text):
            return "photo"
        if _has(_RESUME, text):
            return "resume"
        if _has(r"cover letter|anschreiben", text):
            return "cover_letter_file"
        if _has(_DOCUMENTS, text) and not _has(_CERTIFICATES, text):
            return "documents"
        if f.required:
            return "resume"
        if text.strip():
            return "cover_letter_file"
        return "unknown"
    # Для checkbox/radio типові правила йдуть раніше за ключові слова:
    # «I agree to receive email updates» — не email.
    if f.type in ("checkbox", "radio"):
        if f.type == "radio" and _has(_SALUTATION, text):
            return "salutation"
        # "How did you hear about us" as radios (Recruitee): options come from the group in fill_form.
        if f.type == "radio" and _has(_SOURCE, text):
            return "source"
        # «Stimmst du den AGBs … zu? ◯ Ja» (LinkedIn/JOIN): acknowledged like a consent checkbox.
        if f.type == "radio" and re.match(_YES_OPTION, f.label, re.IGNORECASE) and _has(_CONSENT, text):
            return "ack"
        if _has(_EEO, text):
            return "eeo"
        # Згода на розсилку лишається незаповненою: це choice, не ack.
        if f.type == "checkbox" and _has(r"receive|newsletter|marketing|subscribe|updates", text):
            return "choice"
        if f.type == "checkbox" and _has(_ACK, text):
            return "ack"
        return "choice"
    # Лише <input>: textarea «describe your years of experience…» лишається вільним текстом.
    # Комбобокс і select не беруть друкований текст: вони лишаються на шляху select/choice.
    if (f.tag == "input" and not f.combobox and f.type in ("text", "number", "")
            and _has(_YEARS, text)):
        return "years"
    if _has(r"legal name|full name", text):
        return "full_name"
    # «Preferred First Name» (Greenhouse) раніше за first name.
    if _has(r"preferred (first )?name", text):
        return "preferred_name"
    if _has(r"first name|vorname", text):
        return "first_name"
    if _has(r"last name|surname|nachname", text):
        return "last_name"
    # Ashby: <label for=_systemfield_name>Name</label>, без «Legal».
    if f.label.strip().lower() == "name" or "_systemfield_name" in (f.id, f.name):
        return "full_name"
    # Комбобокс із «country» у власному підписі це вибір країни, навіть у групі «Phone»
    # (код країни телефону Greenhouse): інакше правило phone надрукувало б туди номер.
    # «Are you eligible to work in the country you are applying?» is a work-permit question, not a country pick.
    if f.combobox and "country" in f.label.lower() and not _has(_WORK_PERMIT, text):
        return "country"
    if f.type == "email" or _has(r"e-mail|email", text):
        return "email"
    if f.type == "tel" or _has(r"phone|telefon", text):
        return "phone"
    if "linkedin" in text:
        return "linkedin"
    if "github" in text:
        return "github"
    if _has(r"website|portfolio|personal site", text):
        return "website"
    if _has(_BIRTH, text):
        return "birth_date"
    if _has(_SALARY, text):
        return "salary"
    if _has(_NATIONALITY, text):
        return "nationality"
    if _has(_SALUTATION, text):
        return "salutation"
    if _has(_SOURCE, text):
        return "source"
    if _has(_STREET, text):
        return "street"
    if _has(_HOUSE_NUMBER, text):
        return "house_number"
    if _has(_ZIP, text):
        return "zip"
    if _has(_CITY_DE, text):
        return "city"
    if _has(_COUNTRY_DE, text):
        return "country"
    if _has(_EEO, text):
        return "eeo"
    if f.type == "checkbox" and _has(_ACK, text):
        return "ack"
    if f.type == "date" or "pick date" in text or _has(
        r"start date|available|availability", text
    ) or _has(_DATE_DE, text):
        return "date"
    # Комбобокс країни/міста (react-select Greenhouse, автодоповнення Ashby) лишається
    # country/location навіть з довгим питанням: «Which country do you intend to work from?».
    if f.combobox and "country" in text and not _has(_WORK_PERMIT, text):
        return "country"
    if f.combobox and _has(_LOCATION, text):
        return "location"
    # Довге питання з «?» — вільний текст, навіть якщо в ньому є «location»
    # чи «country» (напр. питання про спонсорство візи).
    label = f.label.strip()
    if f.tag == "input" and f.type == "text" and len(label.split()) >= 6 and label.endswith("?"):
        return "free_text"
    if "country" in text and not _has(_WORK_PERMIT, text):
        return "country"
    if _has(_LOCATION, text):
        return "location"
    if f.tag == "textarea":
        if _has(_COVER_TEXT, text):
            return "cover_letter_text"
        return "free_text"
    if f.tag == "select":
        return "select"
    if f.type in ("radio", "checkbox"):
        return "choice"
    return "unknown"


def visible_required(fields: list[Field]) -> list[Field]:
    """Видимі обов'язкові поля без EEO: його закон не вимагає, форма приймає decline."""
    return [f for f in fields if f.visible and f.required and classify(f) != "eeo"]
