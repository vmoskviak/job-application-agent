"""CV rendering engine — shared across all applications.

Усі дані кандидата (досвід, освіта, скіли, вкладення) приходять із `cv.yaml`
і `profile.yaml` активного клієнта; тут лише складання й рендер.
"""

import re
from pathlib import Path
from typing import Optional, List, Tuple

from bot import client as _client
from bot.apply.candidate import CITY, EMAIL, FULL_NAME, PHONE, STREET, ZIP_CODE

_c = _client.active()
_cv = _c.cv

# ── Fixed base data ───────────────────────────────────────────────────────────

NAME = FULL_NAME
CONTACT = [
    CITY,
    f"{PHONE}  ·  {EMAIL}",
    # додаткові рядки шапки: LinkedIn, Staatsangehörigkeit тощо
    *_c.profile.get("cv_contact_extra", []),
]

ATTACHMENTS_DIR = _c.attachments
CV_FILE_STEM = f"{_c.file_stem}_CV"

# Варіанти CV клієнта. Невідомий варіант движок зводить до дефолтного.
VARIANTS: list[str] = list(_cv["variants"])
DEFAULT_VARIANT: str = _cv.get("default_variant", VARIANTS[0])

# (файл, підпис у списку Anlagen). Одне джерело для вкладень і для списку в
# листі — інакше вони розходяться. Вкладення з ключем `variants` чіпляються
# лише до цих варіантів CV (напр. Zeugnisbewertung там, де роботодавець
# реально питає про рівень освіти).
_ATTACHMENTS: list[dict] = list(_cv.get("attachments", []))

ZEUGNIS_ATTACHMENTS: list[tuple[str, str]] = [
    (a["file"], a["label"]) for a in _ATTACHMENTS if not a.get("variants")
]
ZEUGNIS_FILES = [fname for fname, _ in ZEUGNIS_ATTACHMENTS]


def _attachments_for(variant: str | None) -> list:
    """(файл, підпис) для варіанта CV: базові + ті, що прив'язані до варіанта."""
    extra = [
        (a["file"], a["label"]) for a in _ATTACHMENTS
        if a.get("variants") and variant in a["variants"]
    ]
    return ZEUGNIS_ATTACHMENTS + extra


def anlagen_labels(variant: str | None = None) -> list:
    """Підписи для блоку «Anlagen:» у листі."""
    return ["Lebenslauf"] + [label for _, label in _attachments_for(variant)]


ANLAGEN_LABELS = anlagen_labels()

# ── Skills / Ehrenamt / Education ─────────────────────────────────────────────

_SKILLS_VARIANTS: dict[str, list[tuple[str, str]]] = {
    variant: [(row[0], row[1]) for row in rows]
    for variant, rows in _cv["skills"].items()
}

# Ehrenamt показуємо лише у варіантах, де воно додає ваги ролі; в решті
# з'їдає місце без користі.
_EHRENAMT_VARIANTS = set(_cv.get("ehrenamt", {}).get("variants", []))
EHRENAMT = list(_cv.get("ehrenamt", {}).get("entries", []))

EDUCATION = list(_cv.get("education", []))
# Окрема секція «Weiterbildung & Zertifikate»: ATS і рекрутери шукають
# сертифікати там, усередині Ausbildung їх не видно.
CERTIFICATES = list(_cv.get("certificates", []))


def _for_variant(entries: list[dict], variant: str) -> list[dict]:
    """Записи досвіду чи освіти для варіанта: без `variants` — скрізь, з ним —
    лише у перелічених (сертифікат Scrum Master у CV на Empfang марний)."""
    return [e for e in entries if not e.get("variants") or variant in e["variants"]]


def _bullets_for(entry: dict, variant: str) -> list[str]:
    """Буліти запису досвіду: під варіант, інакше нейтральні (`default`)."""
    bullets = entry["bullets"]
    if isinstance(bullets, list):
        chosen = list(bullets)
    elif variant in bullets:
        chosen = list(bullets[variant])
    elif "default" in bullets:
        chosen = list(bullets["default"])
    else:
        chosen = list(bullets[DEFAULT_VARIANT])
    # «Betreuung: Sprechstunden, …» без лапок YAML читає як словник, і в PDF
    # стояло `{'Betreuung': '…'}` (14.10.2026). Краще впасти тут.
    bad = [b for b in chosen if not isinstance(b, str)]
    if bad:
        raise ValueError(
            f"cv.yaml: {entry.get('company')}: буліт не рядок {bad[0]!r} "
            "(двокрапка без лапок?)")
    return chosen


# ── Public API ─────────────────────────────────────────────────────────────────

def normalize_cv_title(title: str) -> str:
    """Роль під іменем у CV, а не тема супровідного листа.

    Агент стабільно віддає cv_title у форматі «Bewerbung als Teamassistenz
    (m/w/d)» — це рядок для Anschreiben. У CV під іменем має стояти сама
    роль; «Bewerbung als» і гендер-суфікс роботодавця там зайві (перевірено
    на 26 пакетах 22–27.07 — 100 % мали цей префікс).
    """
    cleaned = re.sub(r"^\s*Bewerbung\s+als\s+", "", title, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s*\((?:[mwdxfa][/\s]*)+\)|\s*\(all\s+genders\)|\s*\(gn\)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip() or title.strip()


def build_cv_data(
    title: str,
    profile: str,
    kernkompetenzen: List[Tuple[str, str]],
    experience_variant: str,
    skills_order: str,
    photo_path: Optional[Path] = None,
) -> dict:
    """Builds full CV data dict from tailored parts + fixed base data."""
    variant = experience_variant if experience_variant in VARIANTS else DEFAULT_VARIANT
    skills_key = skills_order if skills_order in _SKILLS_VARIANTS else DEFAULT_VARIANT

    experience = [
        {
            "dates":   entry["dates"],
            "company": entry["company"],
            "title":   entry["title"],
            "bullets": _bullets_for(entry, variant),
            "note":    entry.get("note", ""),
        }
        for entry in _for_variant(_cv["experience"], variant)
    ]

    return {
        "name":            NAME,
        "title":           normalize_cv_title(title),
        "contact":         CONTACT,
        "profile":         profile,
        "kernkompetenzen": kernkompetenzen,
        "experience":      experience,
        "ehrenamt":        EHRENAMT if variant in _EHRENAMT_VARIANTS else [],
        "anlagen":         anlagen_labels(variant),
        "education":       _for_variant(EDUCATION, variant),
        "certificates":    _for_variant(CERTIFICATES, variant),
        "skills":          _SKILLS_VARIANTS[skills_key],
        "photo_path":      str(photo_path) if photo_path and Path(photo_path).exists() else None,
    }


def zeugnis_paths(variant: str | None = None) -> list:
    """Absolute paths of the shared Zeugnis/diploma PDFs in attachments/.

    Вкладення з `variants` у cv.yaml додаються лише для цих варіантів;
    без `variant` повертаються тільки базові.

    Raises if any file is missing — a package silently sent without
    Zeugnisse is worse than a loud failure.
    """
    paths = []
    for fname, _ in _attachments_for(variant):
        src = ATTACHMENTS_DIR / fname
        if not src.exists():
            raise FileNotFoundError(f"Zeugnis attachment missing: {src}")
        paths.append(src)
    return paths


def portal_zeugnisse() -> list:
    """Zeugnisse для портальних форм (cv.yaml → attachments[].portal: true).

    Портали приймають кілька файлів і мають ліміт розміру, тому туди йде
    лише коротка добірка найсвіжіших, а не весь email-набір.
    """
    return [
        ATTACHMENTS_DIR / a["file"] for a in _ATTACHMENTS
        if a.get("portal") and (ATTACHMENTS_DIR / a["file"]).exists()
    ]


PHOTO_PATH = _c.photo


def render_application(job_data, tailored, out_dir: Path,
                        photo_path: Optional[Path] = PHOTO_PATH) -> None:
    """Full pipeline: build cv_data → render all docs.

    Zeugnis/diploma PDFs are NOT copied into out_dir — they live once in
    attachments/ and are referenced at send time via zeugnis_paths().
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cv_data = build_cv_data(
        title=tailored.cv_title,
        profile=tailored.profile,
        kernkompetenzen=tailored.kernkompetenzen,
        experience_variant=tailored.experience_variant,
        skills_order=tailored.skills_order,
        photo_path=photo_path,
    )
    make_cv_docx(cv_data, out_dir / f"{CV_FILE_STEM}.docx")
    make_cv_pdf(cv_data,  out_dir / f"{CV_FILE_STEM}.pdf")
    letter = ensure_salary_sentence(tailored.anschreiben, getattr(tailored, "salary", None))
    # Портальна подача несе лише CV + сам Anschreiben, а повний список Anlagen
    # обіцяє Zeugnisse, яких портал не шле — кандидатка виглядає так, ніби
    # забула вкладення. Тому без
    # email-каналу список чесний: лише Lebenslauf.
    anlagen = cv_data["anlagen"] if getattr(job_data, "email", None) else ["Lebenslauf"]
    make_letter_pdf(letter, job_data, out_dir / "01_Anschreiben.pdf", anlagen)
    company_clean = job_data.company.replace(" ", "_").replace("/", "_")
    make_letter_docx(letter, job_data,
                     out_dir / f"Anschreiben_{company_clean}.docx", anlagen)


# ── Rendering functions ────────────────────────────────────────────────────────

def make_cv_docx(cv_data: dict, out_path: Path) -> None:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    DARK = RGBColor(0x1a, 0x1a, 0x2e)
    GREY = RGBColor(0x55, 0x55, 0x55)

    doc = Document()
    for s in doc.sections:
        s.top_margin    = Cm(2.0)
        s.bottom_margin = Cm(1.8)
        s.left_margin   = Cm(2.5)
        s.right_margin  = Cm(2.0)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)

    def add_p(text="", bold=False, italic=False, size=10.5, color=None, sb=0, sa=3):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(sb)
        p.paragraph_format.space_after  = Pt(sa)
        p.paragraph_format.line_spacing = Pt(13)
        if text:
            r = p.add_run(text)
            r.bold = bold; r.italic = italic
            r.font.size = Pt(size); r.font.name = "Calibri"
            if color: r.font.color.rgb = color
        return p

    def keep_block(paragraphs):
        """Запис не рветься між сторінками: злипаємо абзаци записом."""
        for p in paragraphs:
            p.paragraph_format.keep_together = True
        for p in paragraphs[:-1]:
            p.paragraph_format.keep_with_next = True

    def sec(title):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after  = Pt(2)
        p.paragraph_format.keep_with_next = True
        r = p.add_run(title.upper())
        r.bold = True; r.font.size = Pt(9.5)
        r.font.color.rgb = DARK; r.font.name = "Calibri"
        bdr = OxmlElement("w:pBdr")
        bot = OxmlElement("w:bottom")
        bot.set(qn("w:val"), "single"); bot.set(qn("w:sz"), "6")
        bot.set(qn("w:space"), "1"); bot.set(qn("w:color"), "1a1a2e")
        bdr.append(bot)
        p._p.get_or_add_pPr().append(bdr)

    photo_path = cv_data.get("photo_path")
    if photo_path and Path(photo_path).exists():
        # 1×2 table: left = name/title/contact, right = photo
        tbl = doc.add_table(rows=1, cols=2)
        tbl_pr = OxmlElement("w:tblPr")
        tbl_bdr = OxmlElement("w:tblBorders")
        for border_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{border_name}")
            el.set(qn("w:val"), "none")
            tbl_bdr.append(el)
        tbl_pr.append(tbl_bdr)
        tbl._tbl.insert(0, tbl_pr)

        left = tbl.cell(0, 0)
        right = tbl.cell(0, 1)
        right.width = Cm(3.8)

        def cp(cell, text="", bold=False, italic=False, size=10.5,
               color=None, sa=2, use_first=False):
            p = cell.paragraphs[0] if use_first else cell.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after  = Pt(sa)
            p.paragraph_format.line_spacing = Pt(13)
            if text:
                r = p.add_run(text)
                r.bold = bold; r.italic = italic
                r.font.size = Pt(size); r.font.name = "Calibri"
                if color:
                    r.font.color.rgb = color
            return p

        cp(left, cv_data["name"], bold=True, size=22, color=DARK, sa=2, use_first=True)
        cp(left, cv_data["title"], size=12, color=GREY, sa=4)
        for line in cv_data["contact"]:
            cp(left, line, size=10, color=GREY, sa=1)

        rp = right.paragraphs[0]
        rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = rp.add_run()
        run.add_picture(str(photo_path), width=Cm(3.5))
    else:
        add_p(cv_data["name"], bold=True, size=22, color=DARK, sa=2)
        add_p(cv_data["title"], size=12, color=GREY, sa=4)
        for line in cv_data["contact"]:
            add_p(line, size=10, color=GREY, sa=1)

    sec("Profil")
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(3); p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = Pt(13)
    r = p.add_run(cv_data["profile"]); r.font.size = Pt(10.5); r.font.name = "Calibri"

    if cv_data.get("kernkompetenzen"):
        sec("Kernkompetenzen")
        for label, value in cv_data["kernkompetenzen"]:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after  = Pt(1.5)
            p.paragraph_format.line_spacing = Pt(13)
            r1 = p.add_run(label + ":  "); r1.bold = True
            r1.font.size = Pt(9.5); r1.font.name = "Calibri"
            r2 = p.add_run(value); r2.font.size = Pt(9.5); r2.font.name = "Calibri"

    sec("Berufserfahrung")
    for job in cv_data["experience"]:
        entry = [
            add_p(job["dates"], size=9.5, color=GREY, sb=5, sa=0),
            add_p(job["company"], bold=True, size=10.5, color=DARK, sb=0, sa=0),
            add_p(job["title"], italic=True, size=10, sb=0, sa=1),
        ]
        for b in job["bullets"]:
            bp = doc.add_paragraph(style="List Bullet")
            bp.paragraph_format.space_before = Pt(0)
            bp.paragraph_format.space_after  = Pt(1)
            bp.paragraph_format.left_indent  = Cm(0.5)
            br = bp.add_run(b); br.font.size = Pt(10); br.font.name = "Calibri"
            entry.append(bp)
        if job.get("note"):
            entry.append(add_p(job["note"], italic=True, size=9.5, color=GREY, sa=2))
        else:
            entry.append(add_p(sa=2))
        keep_block(entry)

    if cv_data.get("ehrenamt"):
        sec("Ehrenamtliches Engagement")
        for item in cv_data["ehrenamt"]:
            entry = [
                add_p(item["dates"], size=9.5, color=GREY, sb=5, sa=0),
                add_p(item["org"], bold=True, size=10.5, color=DARK, sb=0, sa=0),
                add_p(item["role"], italic=True, size=10, sb=0, sa=1),
            ]
            for b in item["bullets"]:
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.space_before = Pt(0)
                bp.paragraph_format.space_after  = Pt(1)
                bp.paragraph_format.left_indent  = Cm(0.5)
                br = bp.add_run(b); br.font.size = Pt(10); br.font.name = "Calibri"
                entry.append(bp)
            entry.append(add_p(sa=2))
            keep_block(entry)

    def edu_section(title, entries):
        sec(title)
        for edu in entries:
            entry = [
                add_p(edu["dates"], size=9.5, color=GREY, sb=5, sa=0),
                add_p(edu["inst"], bold=True, size=10.5, color=DARK, sb=0, sa=0),
                add_p(edu["deg"], italic=True, size=10, sb=0, sa=0),
            ]
            if edu.get("note"):
                entry.append(add_p(edu["note"], size=9.5, color=GREY, sb=0, sa=2))
            else:
                entry.append(add_p(sa=2))
            keep_block(entry)

    if cv_data.get("certificates"):
        edu_section("Weiterbildung & Zertifikate", cv_data["certificates"])
    edu_section("Ausbildung", cv_data["education"])

    sec("Kenntnisse & Tools")
    for label, value in cv_data["skills"]:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2); p.paragraph_format.space_after = Pt(1)
        r1 = p.add_run(label + ":  "); r1.bold = True
        r1.font.size = Pt(10.5); r1.font.name = "Calibri"
        r2 = p.add_run(value); r2.font.size = Pt(10.5); r2.font.name = "Calibri"

    doc.save(str(out_path))


def make_cv_pdf(cv_data: dict, out_path: Path) -> None:
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML

    template_dir = _client.ENGINE_ROOT / "cv_templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    html_str = env.get_template("cv_template.html").render(**cv_data)
    HTML(string=html_str, base_url=str(template_dir) + "/").write_pdf(str(out_path))


def ensure_salary_sentence(anschreiben_text: str, salary: str | None) -> str:
    """Дописує Gehaltsvorstellung, якщо її в листі нема.

    Шаблонний шлях просить цей рядок у промпті tailor.py, а власний текст
    `anschreiben` у батчі його просто губив (Musterfirma 27.07). Багато німецьких
    оголошень вимагають вилку прямо в документах, тож дешевше дописати.
    """
    if not salary or not str(salary).strip().isdigit():
        return anschreiben_text
    if "Gehaltsvorstellung" in anschreiben_text:
        return anschreiben_text

    amount = f"{int(salary):,}".replace(",", ".")
    sentence = (
        f"Meine Gehaltsvorstellung liegt bei {amount} € brutto jährlich."
    )
    return f"{anschreiben_text.rstrip()}\n\n{sentence}"


_LOC_MODE_RE = re.compile(r"\s*\((?:hybrid|remote|on-site|vor ort)\)", re.I)
_LOC_REGION_RE = re.compile(
    r",\s*(?:north rhine-westphalia|nordrhein-westfalen|nrw|germany|deutschland)\b.*$", re.I)


def letter_location(location: str) -> str:
    """Місто під назвою фірми в шапці листа. LinkedIn віддає «Düsseldorf,
    North Rhine-Westphalia, Germany (Hybrid)», і цей рядок потрапляв у лист
    як адреса (16.10.2026). Регіон, країну й режим роботи зрізаємо."""
    raw = (location or "").strip()
    loc = _LOC_REGION_RE.sub("", _LOC_MODE_RE.sub("", raw)).strip(" ,")
    return loc or raw


DEFAULT_SALUTATION = "Sehr geehrte Damen und Herren,"


def letter_salutation(anschreiben_text: str) -> str:
    """Anrede з тексту листа, якщо агент її написав («Sehr geehrter Herr
    Muster,»): ім'я з оголошення читається краще за «Damen und Herren».
    Без неї лишається нейтральна. _letter_body цей абзац усе одно зрізає."""
    for para in (p.strip() for p in (anschreiben_text or "").split("\n\n")):
        if para.startswith(("Sehr geehrte", "Liebe")):
            first = para.splitlines()[0].strip()
            return first if first.endswith(",") else f"{first},"
    return DEFAULT_SALUTATION


def _letter_body(anschreiben_text: str) -> list:
    """Тіло листа без шапки/підпису — обидва рендери додають їх самі.

    Агенти інколи передають повний лист (адреса, дата, Betreff, Anrede,
    Grußformel, Anlagen) — без цієї зачистки шапка дублюється в PDF/DOCX.
    """
    paras = [p.strip() for p in anschreiben_text.split("\n\n") if p.strip()]

    for i, p in enumerate(paras):
        if p.startswith(("Sehr geehrte", "Liebe")):
            paras = paras[i + 1:]
            break

    body = []
    for p in paras:
        if p.startswith("Mit freundlichen"):
            break
        body.append(p)

    while body and (body[-1].startswith("Anlagen") or body[-1] == NAME):
        body.pop()

    return body


def make_letter_pdf(anschreiben_text: str, job_data, out_path: Path,
                    anlagen: list | None = None) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from datetime import date

    DARK = colors.HexColor("#1a1a2e")
    GREY = colors.HexColor("#555555")

    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.0*cm,
        topMargin=2.5*cm, bottomMargin=2.0*cm)

    def S(name, **kw):
        base = dict(fontName="Helvetica", fontSize=11, leading=15,
                    textColor=colors.black, spaceAfter=3)
        base.update(kw)
        return ParagraphStyle(name, **base)

    today = date.today().strftime("%d.%m.%Y")
    story = []
    story.append(Paragraph(NAME,
        S("sn", fontName="Helvetica-Bold", textColor=DARK, spaceAfter=2)))
    story.append(Paragraph(f"{STREET}, {ZIP_CODE} {CITY}",
        S("sr", fontSize=10, textColor=GREY, spaceAfter=2)))
    story.append(Paragraph(f"{PHONE}  ·  {EMAIL}",
        S("sr2", fontSize=10, textColor=GREY, spaceAfter=2)))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"{CITY}, {today}",
        S("dt", alignment=TA_RIGHT, spaceAfter=10)))
    story.append(Paragraph(job_data.company,
        S("rc", fontName="Helvetica-Bold", spaceAfter=2)))
    story.append(Paragraph(letter_location(job_data.location),
        S("rl", spaceAfter=12)))
    story.append(Paragraph(f"<b>Bewerbung als {job_data.title}</b>",
        S("su", spaceBefore=4, spaceAfter=14)))
    story.append(Paragraph(letter_salutation(anschreiben_text),
        S("sl", spaceAfter=10)))

    paragraphs = _letter_body(anschreiben_text)
    for i, para in enumerate(paragraphs):
        story.append(Paragraph(para,
            S(f"b{i}", spaceAfter=18 if i == len(paragraphs) - 1 else 10)))

    story.append(Paragraph("Mit freundlichen Grüßen", S("cl", spaceAfter=22)))
    story.append(Paragraph(NAME, S("nm", spaceAfter=14)))
    story.append(Paragraph("<b>Anlagen:</b>",
        S("ah", fontSize=10, textColor=GREY, spaceAfter=2)))
    for a in (anlagen or ANLAGEN_LABELS):
        story.append(Paragraph(a,
            S("ai", fontSize=10, textColor=GREY, leftIndent=12, spaceAfter=1)))
    doc.build(story)


def make_letter_docx(anschreiben_text: str, job_data, out_path: Path,
                     anlagen: list | None = None) -> None:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from datetime import date

    DARK = RGBColor(0x1a, 0x1a, 0x2e)
    GREY = RGBColor(0x55, 0x55, 0x55)
    today = date.today().strftime("%d.%m.%Y")

    doc = Document()
    for s in doc.sections:
        s.top_margin = Cm(2.5); s.bottom_margin = Cm(2.0)
        s.left_margin = Cm(2.5); s.right_margin = Cm(2.0)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    def p(text="", bold=False, color=None, size=11,
          align=WD_ALIGN_PARAGRAPH.LEFT, sb=0, sa=4):
        par = doc.add_paragraph()
        par.alignment = align
        par.paragraph_format.space_before = Pt(sb)
        par.paragraph_format.space_after  = Pt(sa)
        par.paragraph_format.line_spacing = Pt(14)
        if text:
            r = par.add_run(text)
            r.bold = bold; r.font.size = Pt(size); r.font.name = "Calibri"
            if color: r.font.color.rgb = color
        return par

    p(NAME, bold=True, color=DARK, sa=2)
    p(f"{STREET}, {ZIP_CODE} {CITY}", size=10, color=GREY, sa=1)
    p(f"{PHONE}  ·  {EMAIL}", size=10, color=GREY, sa=1)
    p(f"{CITY}, {today}", align=WD_ALIGN_PARAGRAPH.RIGHT, sb=8, sa=10)
    p(job_data.company, bold=True, sa=1)
    p(letter_location(job_data.location), sa=12)
    p(f"Bewerbung als {job_data.title}", bold=True, sb=4, sa=14)
    p(letter_salutation(anschreiben_text), sa=10)

    paragraphs = _letter_body(anschreiben_text)
    for i, para in enumerate(paragraphs):
        p(para, sa=18 if i == len(paragraphs) - 1 else 10)

    p("Mit freundlichen Grüßen", sa=22)
    p(NAME, sa=14)
    par = doc.add_paragraph()
    par.paragraph_format.space_before = Pt(0); par.paragraph_format.space_after = Pt(4)
    r1 = par.add_run("Anlagen:"); r1.bold = True
    r1.font.size = Pt(10); r1.font.name = "Calibri"; r1.font.color.rgb = GREY
    for a in (anlagen or ANLAGEN_LABELS):
        p(a, size=10, color=GREY, sa=1)

    doc.save(str(out_path))
