"""Deterministic LaTeX renderer.

The AI returns tailored *plain-text* content (rewritten summary, skills, experience
bullets, and per-project bullets) — never raw LaTeX. This module escapes it, applies
a tiny safe **bold** markup, and wraps it in the fixed template + factual, non-editable
sections (contact, education, certs). So the model gets full latitude to rewrite and
reorder for the JD, but it can never emit broken LaTeX, and structure stays intact.
Fabrication is prevented upstream by the prompt (corpus-only) — here we just render.
Output compiles anywhere (the AWS badge is guarded by \\IfFileExists).
"""
import re

import profile as P

_TEX = {"\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#",
        "_": "\\_", "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}"}


def tex_escape(s):
    """Escape AI plain text so it's safe to drop into LaTeX."""
    return "".join(_TEX.get(ch, ch) for ch in (s or ""))


def _md(s):
    """Escape, then convert a minimal, safe **bold** markup to \\textbf{}."""
    out = tex_escape(s or "")
    return re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", out)


PREAMBLE = r"""%-------------------------
% Auto-generated, JD-tailored résumé — Job Hunt Command Center.
% Based on the Jake Gutierrez template. Every fact is drawn from the candidate's
% real corpus; the AI rewrote/reordered wording to fit the JD (no fabrication).
%------------------------
\documentclass[letterpaper,11pt]{article}
\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage[usenames,dvipsnames]{color}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\usepackage{graphicx}
\usepackage{tikz}
\ifdefined\pdfgentounicode\input{glyphtounicode}\fi
\pagestyle{fancy}\fancyhf{}\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}\renewcommand{\footrulewidth}{0pt}
\addtolength{\oddsidemargin}{-0.6in}\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1.19in}\addtolength{\topmargin}{-.7in}\addtolength{\textheight}{1.4in}
\urlstyle{same}\raggedbottom\raggedright\setlength{\tabcolsep}{0in}
\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large\bfseries}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]
\ifdefined\pdfgentounicode\pdfgentounicode=1\fi
\newcommand{\resumeItem}[1]{\item\small{{#1 \vspace{-2pt}}}}
\newcommand{\resumeSubheading}[4]{\vspace{-2pt}\item
  \begin{tabular*}{1.0\textwidth}[t]{l@{\extracolsep{\fill}}r}
    \textbf{#1} & \textbf{\small #2} \\ \textit{\small#3} & \textit{\small #4} \\
  \end{tabular*}\vspace{-7pt}}
\newcommand{\resumeProjectHeading}[1]{\item
  \begin{tabular*}{1.001\textwidth}{l@{\extracolsep{\fill}}r}\small#1 &\\ \end{tabular*}\vspace{-7pt}}
\renewcommand\labelitemi{$\vcenter{\hbox{\tiny$\bullet$}}$}
\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.0in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}[leftmargin=0.18in]}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}
"""


TEMPLATES = ("standard", "modern", "compact")


def preamble(template="standard"):
    """Three styles from one base: standard (black rules), modern (orange accent
    rules), compact (10pt + tighter, fits more). Low-risk string tweaks so all
    three compile identically."""
    p = PREAMBLE
    if template == "compact":
        p = p.replace("letterpaper,11pt", "letterpaper,10pt")
    elif template == "modern":
        p = p.replace(
            r"\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large\bfseries}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]",
            r"\definecolor{accent}{rgb}{0.925,0.447,0.067}" + "\n"
            r"\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large\bfseries}{}{0em}{}[\color{accent}\titlerule \vspace{-5pt}]")
    return p


def _header():
    c = P.CONTACT
    badge = (
        r"\begin{tikzpicture}[remember picture, overlay]"
        r"\node[anchor=north east, xshift=-0.2in, yshift=-0.4in] at (current page.north east) {"
        r"\IfFileExists{aws-certified-solutions-architect-associate.png}"
        r"{\href{https://www.credly.com/badges/e499fee9-1b8b-4fce-a65c-bc4ddcb2f8b9/public_url}"
        r"{\includegraphics[width=0.9in]{aws-certified-solutions-architect-associate.png}}}"
        r"{\fbox{\parbox[c][0.8in][c]{0.8in}{\centering\scriptsize\scshape AWS SAA\\[2pt]\upshape\itshape (badge)}}}"
        r"};\end{tikzpicture}"
    )
    return (
        badge + "\n\\begin{center}\n"
        f"    {{\\fontsize{{30}}{{34}}\\selectfont \\scshape \\href{{https://{c['site']}}}{{{c['name']}}}}} \\\\ \\vspace{{4pt}}\n"
        f"    {{\\large {c['location']} $|$ {c['phone']} $|$ \\href{{mailto:{c['email']}}}{{\\underline{{{c['email']}}}}}}} \\\\ \\vspace{{2pt}}\n"
        f"    {{\\large \\href{{https://{c['site']}}}{{\\underline{{{c['site']}}}}} $|$ "
        f"\\href{{https://{c['github']}}}{{\\underline{{{c['github']}}}}} $|$ "
        f"\\href{{https://{c['linkedin']}}}{{\\underline{{{c['linkedin']}}}}}}}\n    \\vspace{{-6pt}}\n\\end{{center}}\n"
    )


def _education():
    out = ["\\section{Education}\n  \\resumeSubHeadingListStart"]
    for e in P.EDUCATION:
        out.append(f"    \\resumeSubheading\n      {{{e['school']}}}{{{e['loc']}}}\n      {{{e['degree']}}}{{{e['date']}}}")
        if e.get("detail"):
            out.append(f"      \\resumeItemListStart\n        \\resumeItem{{{e['detail']}}}\n      \\resumeItemListEnd")
    out.append("  \\resumeSubHeadingListEnd\n\\vspace{-14pt}")
    return "\n".join(out)


def _experience(ai_bullets):
    """HCLTech header is factual; bullets are AI-rewritten (falls back to corpus)."""
    x = P.EXPERIENCE[0]
    bullets = [b for b in (ai_bullets or []) if b and b.strip()]
    if not bullets:
        bullets = x["bullets"]  # corpus is already LaTeX-safe
        rendered = [f"        \\resumeItem{{{b}}}" for b in bullets]
    else:
        rendered = [f"        \\resumeItem{{{_md(b)}}}" for b in bullets]
    body = "\n".join(rendered)
    return ("\\section{Experience}\n  \\resumeSubHeadingListStart\n"
            f"    \\resumeSubheading\n      {{{x['company']}}}{{{x['loc']}}}\n      {{{x['title']}}}{{{x['date']}}}\n"
            f"      \\resumeItemListStart\n{body}\n      \\resumeItemListEnd\n"
            "  \\resumeSubHeadingListEnd\n\\vspace{-12pt}")


def _skills(ai_skills):
    """ai_skills = [{category, items}] (rewritten/reordered). Falls back to corpus."""
    lines = []
    for s in (ai_skills or []):
        cat, items = (s.get("category") or "").strip(), (s.get("items") or "").strip()
        if cat and items:
            lines.append(f"     \\textbf{{{tex_escape(cat)}}}{{: {tex_escape(items)}}}")
    if not lines:
        lines = [f"     \\textbf{{{k}}}{{: {P.SKILLS[k]}}}" for k in P.SKILLS]
    body = " \\\\\n".join(lines)
    return ("\\section{Technical Skills}\n \\begin{itemize}[leftmargin=0.15in, label={}]\n"
            f"    \\small{{\\item{{\n{body}\n    }}}}\n \\end{{itemize}}\n \\vspace{{-16pt}}")


def _projects(ai_projects):
    out = ["\\section{Projects}\n    \\vspace{-5pt}\n    \\resumeSubHeadingListStart"]
    for pr in (ai_projects or []):
        corp = P.project_by_id(pr.get("id")) or {}
        name = pr.get("name") or corp.get("name") or pr.get("id", "")
        tech = pr.get("tech") or corp.get("tech") or ""
        # links always come from the trusted corpus, never the model
        links = []
        if corp.get("link_live"):
            links.append(f"\\href{{{corp['link_live']}}}{{\\underline{{Live}}}}")
        if corp.get("link_code"):
            links.append(f"\\href{{{corp['link_code']}}}{{\\underline{{Code}}}}")
        link_str = (" $\\cdot$ " + " / ".join(links)) if links else ""
        out.append(f"      \\resumeProjectHeading\n          {{\\textbf{{{_md(name)}}} $|$ \\emph{{{tex_escape(tech)}}}{link_str}}}\n          \\resumeItemListStart")
        bl = [b for b in (pr.get("bullets") or []) if b and b.strip()]
        if not bl and corp.get("bullets"):
            out += [f"            \\resumeItem{{{b}}}" for b in corp["bullets"]]
        else:
            out += [f"            \\resumeItem{{{_md(b)}}}" for b in bl]
        out.append("          \\resumeItemListEnd\n          \\vspace{-13pt}")
    out.append("\n    \\resumeSubHeadingListEnd\n\\vspace{-15pt}")
    return "\n".join(out)


def _certs():
    body = " \\\\\n".join(f"     {c}" for c in P.CERTIFICATIONS)
    return ("\\section{Certifications}\n \\begin{itemize}[leftmargin=0.15in, label={}]\n"
            f"    \\small{{\\item{{\n{body}\n    }}}}\n \\end{{itemize}}\n \\vspace{{-16pt}}")


def render_resume(sel, template="standard"):
    """sel = {summary, skills:[{category,items}], experienceBullets:[str],
             projects:[{id,name,tech,bullets:[str]}]}. All AI-rewritten, corpus-only."""
    summary = _md((sel.get("summary") or "").strip()) or P.SUMMARY_BASE
    parts = [
        preamble(template if template in TEMPLATES else "standard"),
        "\\begin{document}\n",
        _header(),
        "\n%-----------SUMMARY-----------\n\\section{Summary}\n\\small{\n" + summary + "\n}\n\\vspace{-6pt}\n",
        _education(),
        _experience(sel.get("experienceBullets")),
        _skills(sel.get("skills")),
        _projects(sel.get("projects") or []),
        _certs(),
        "\n\\end{document}\n",
    ]
    return "\n".join(parts)


COVER_PREAMBLE = r"""\documentclass[letterpaper,11pt]{article}
\usepackage[empty]{fullpage}
\usepackage[hidelinks]{hyperref}
\usepackage[margin=1in]{geometry}
\usepackage{parskip}
\pagestyle{empty}
"""


def render_cover_letter(company, role, body_paragraphs):
    c = P.CONTACT
    paras = "\n\n".join(_md(p).strip() for p in (body_paragraphs or []) if p and p.strip())
    company_s = tex_escape(company or "the team")
    return (
        COVER_PREAMBLE + "\\begin{document}\n"
        f"\\textbf{{\\large {c['name']}}}\\\\\n{c['location']} $|$ {c['phone']} $|$ "
        f"\\href{{mailto:{c['email']}}}{{{c['email']}}} $|$ \\href{{https://{c['site']}}}{{{c['site']}}}\n\n"
        f"\\vspace{{1em}}\nDear Hiring Team at {company_s},\n\n"
        f"{paras}\n\n"
        f"\\vspace{{1em}}\nSincerely,\\\\\n{c['name']}\n"
        "\\end{document}\n"
    )
