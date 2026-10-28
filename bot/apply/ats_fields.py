"""Витяг і класифікація полів ATS-форм (Ashby, Greenhouse, Lever, Workable).

Чиста частина: `FIELD_JS` віддається в `page.evaluate`, `fields_from` і
`classify` працюють на списках словників, тож тестуються без браузера.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
  const byIds = (ids) => (ids || "").split(/\s+/).filter(Boolean).map((id) => txt(document.getElementById(id))).filter(Boolean).join(" ");
  const labelFor = (key) => (key ? document.querySelector("label[for=" + CSS.escape(key) + "]") : null);
  const rectOk = (n) => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const els = Array.from(document.querySelectorAll("input,select,textarea"));
  return els.map((el, i) => {
    const parts = [];
    if (el.labels) for (const l of el.labels) parts.push(labelTxt(l));
    const aria = el.getAttribute("aria-label");
    if (aria) parts.push(aria.trim());
    parts.push(byIds(el.getAttribute("aria-labelledby")));
    // Ashby: <label for=<uuid>>, де uuid це name прихованого checkbox Yes/No (не id);
    // автодоповнення без id підписане label[for=data-field-path] контейнера.
    const box = el.closest("[data-field-path]");
    const boxLabel = box ? labelFor(box.getAttribute("data-field-path")) : null;
    const nameLabel = !parts.some(Boolean) && el.name ? labelFor(el.name) : null;
    if (nameLabel) parts.push(labelTxt(nameLabel));
    if (!parts.some(Boolean) && boxLabel) parts.push(labelTxt(boxLabel));
    // Greenhouse дає той самий текст і через <label for>, і через aria-labelledby: без дублів.
    const label = Array.from(new Set(parts.filter(Boolean))).join(" ");
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : tag;
    const choice = type === "checkbox" || type === "radio";
    const fs = el.closest("fieldset");
    const legend = fs ? fs.querySelector("legend") : null;
    const rg = el.closest("[role=group]");
    // Greenhouse: <div role=group aria-labelledby=upload-label-resume aria-required=false>.
    const rgLabel = rg ? ((rg.getAttribute("aria-label") || "").trim() || byIds(rg.getAttribute("aria-labelledby"))) : "";
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
        || (!!boxLabel && /(^|\s)_required_/.test(boxLabel.className || "")),
      label,
      placeholder: el.getAttribute("placeholder") || "",
      // aria-hidden: прихований дзеркальний required-input react-select, не поле форми.
      visible: el.getAttribute("aria-hidden") !== "true" && (rectOk(el) || yesno.some(rectOk)),
      // Для checkbox/radio value — стан вибору: атрибут value є і в невибраного.
      value,
      // Атрибут value окремо: сусідні radio з одним name розрізняються лише ним і підписом.
      attr_value: el.getAttribute("value") || "",
      options: tag === "select" ? Array.from(el.options).map((o) => o.text.trim()) : null,
      group,
      // Автодоповнення (Greenhouse location, react-select): текст лише друкується, далі клік по опції.
      combobox,
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
    "street", "zip", "city", "birth_date", "nationality", "salutation", "salary", "source",
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


def fields_from(raw: list[dict]) -> list[Field]:
    """Список словників із FIELD_JS у список Field."""
    return [
        Field(
            i=int(d["i"]), tag=d["tag"], type=d.get("type", ""), name=d.get("name", ""),
            id=d.get("id", ""), required=bool(d.get("required")), label=d.get("label", ""),
            placeholder=d.get("placeholder", ""), visible=bool(d.get("visible")),
            value=d.get("value", ""), options=d.get("options"), group=d.get("group", ""),
            combobox=bool(d.get("combobox")), attr_value=d.get("attr_value", ""),
        )
        for d in raw
    ]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


_EEO = r"\b(gender|race|ethnicity|hispanic|veteran|disability|pronouns|transgender|sexual orientation)\b|self-identify"
_ACK = r"acknowledge|i confirm|i agree|consent|privacy|datenschutz"
# «Are you currently located in …?» це Yes/No питання, а не поле локації: його відсіює lookbehind;
# так само «relocation» («Are you open to relocation?»).
_LOCATION = (r"(?<!re)location|\bcity\b|where are you (?:currently )?(?:located|based)"
             r"|(?<!are you )currently located|current location")
# Без \b: «_systemfield_resume» і «cv_upload» мають збігатися через підкреслення.
_RESUME = r"(?<![a-z])(resume|cv|lebenslauf)(?![a-z])"
# Числове питання про роки досвіду («How many years of work experience do you have with X?»).
YEARS_ALTERNATIVES = (
    r"how many years", r"years of (?:work )?experience", r"jahre (?:berufs)?erfahrung", r"wie viele jahre",
)
_YEARS = "|".join(YEARS_ALTERNATIVES)
# Німецькі форми роботодавців (ринок найму). «Land» лише цілим словом: «Deutschland»
# у підписі, «Landesvorwahl» телефону. «Ort» теж цілим словом: «Standort» вакансії
# це не місто кандидата.
_STREET = r"stra(?:ß|ss)e|street address|hausnummer|\badresse\b|\baddress line"
_ZIP = r"\bplz\b|postleitzahl|\bzip\b|postal code|postcode"
_CITY_DE = r"\bort\b|wohnort|\bstadt\b"
_COUNTRY_DE = r"\bland\b|wohnsitzland"
_BIRTH = r"geburt|date of birth|birthday|birth date"
_NATIONALITY = r"staatsangeh|nationalit"
_SALUTATION = r"\banrede\b|salutation"
_SALARY = r"gehalt|jahresgehalt|salary expectation|expected salary|desired salary|vergütungsvorstellung"
_SOURCE = r"aufmerksam geworden|wie haben sie .{0,30}erfahren|how did you hear|woher kennen sie"
_DATE_DE = r"eintrittstermin|eintrittsdatum|verf(?:ü|ue)gbar ab|fr(?:ü|ue)hest|starttermin|arbeitsbeginn"


def classify(f: Field) -> Kind:
    """Вид поля за підписом, placeholder, name, id і групою; перше правило, що спрацювало, виграє.

    Файлове поле: слово resume/cv/lebenslauf у підписі → resume; інакше
    обов'язкове → resume (перше обов'язкове на сторінці, але одне поле
    сторінки не бачить, тож береться будь-яке обов'язкове); інше з підписом →
    cover_letter_file; без підпису → unknown (на Ashby це автозаповнення).
    """
    text = f"{f.label} {f.placeholder} {f.name} {f.id} {f.group}".lower()
    if f.type == "file":
        if _has(_RESUME, text):
            return "resume"
        if _has(r"cover letter|anschreiben", text):
            return "cover_letter_file"
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
    if f.combobox and "country" in f.label.lower():
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
    if f.combobox and "country" in text:
        return "country"
    if f.combobox and _has(_LOCATION, text):
        return "location"
    # Довге питання з «?» — вільний текст, навіть якщо в ньому є «location»
    # чи «country» (напр. питання про спонсорство візи).
    label = f.label.strip()
    if f.tag == "input" and f.type == "text" and len(label.split()) >= 6 and label.endswith("?"):
        return "free_text"
    if "country" in text:
        return "country"
    if _has(_LOCATION, text):
        return "location"
    if f.tag == "textarea":
        if _has(r"cover letter|additional information|anything else|\bwhy\b|motivation|anschreiben", text):
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
