"""Deterministic LaTeX renderer.

The AI only returns a *selection* (which projects, bullet indices, skills order) plus
tailored plain-text prose (summary, cover-letter body). This module renders that into
compilable LaTeX using the fixed template + the vetted corpus strings — so the model
can never emit broken LaTeX or invent facts, and the output compiles anywhere
(the AWS badge is guarded by \\IfFileExists, so a missing PNG degrades gracefully).
"""
import profile as P

# Characters that must be escaped when injecting AI plain-text prose into LaTeX.
_TEX = {"\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#",
        "_": "\\_", "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}"}


def tex_escape(s):
    """Escape AI-authored plain text so it's safe to drop into LaTeX."""
    return "".join(_TEX.get(ch, ch) for ch in (s or ""))


PREAMBLE = r"""%-------------------------
% Auto-generated, JD-tailored résumé — Job Hunt Command Center.
% Based on the Jake Gutierrez template. Every fact is drawn from the candidate's
% real corpus; the AI only selected, ordered, and tailored the summary.
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


def _experience():
    out = ["\\section{Experience}\n  \\resumeSubHeadingListStart"]
    for x in P.EXPERIENCE:
        out.append(f"    \\resumeSubheading\n      {{{x['company']}}}{{{x['loc']}}}\n      {{{x['title']}}}{{{x['date']}}}\n      \\resumeItemListStart")
        for b in x["bullets"]:
            out.append(f"        \\resumeItem{{{b}}}")
        out.append("      \\resumeItemListEnd")
    out.append("  \\resumeSubHeadingListEnd\n\\vspace{-12pt}")
    return "\n".join(out)


def _skills(order):
    keys = [k for k in (order or []) if k in P.SKILLS] or list(P.SKILLS.keys())
    lines = [f"     \\textbf{{{k}}}{{: {P.SKILLS[k]}}}" for k in keys]
    body = " \\\\\n".join(lines)
    return ("\\section{Technical Skills}\n \\begin{itemize}[leftmargin=0.15in, label={}]\n"
            f"    \\small{{\\item{{\n{body}\n    }}}}\n \\end{{itemize}}\n \\vspace{{-16pt}}")


def _projects(selection):
    out = ["\\section{Projects}\n    \\vspace{-5pt}\n    \\resumeSubHeadingListStart"]
    for sel in selection:
        p = P.project_by_id(sel["id"])
        if not p:
            continue
        links = []
        if p.get("link_live"):
            links.append(f"\\href{{{p['link_live']}}}{{\\underline{{Live}}}}")
        if p.get("link_code"):
            links.append(f"\\href{{{p['link_code']}}}{{\\underline{{Code}}}}")
        link_str = (" $\\cdot$ " + " / ".join(links)) if links else ""
        out.append(f"      \\resumeProjectHeading\n          {{\\textbf{{{p['name']}}} $|$ \\emph{{{p['tech']}}}{link_str}}}\n          \\resumeItemListStart")
        idxs = sel.get("bulletIdx") or list(range(len(p["bullets"])))
        for i in idxs:
            if 0 <= i < len(p["bullets"]):
                out.append(f"            \\resumeItem{{{p['bullets'][i]}}}")
        out.append("          \\resumeItemListEnd\n          \\vspace{-13pt}")
    out.append("\n    \\resumeSubHeadingListEnd\n\\vspace{-15pt}")
    return "\n".join(out)


def _certs():
    body = " \\\\\n".join(f"     {c}" for c in P.CERTIFICATIONS)
    return ("\\section{Certifications}\n \\begin{itemize}[leftmargin=0.15in, label={}]\n"
            f"    \\small{{\\item{{\n{body}\n    }}}}\n \\end{{itemize}}\n \\vspace{{-16pt}}")


def render_resume(selection):
    """selection = {summary, skillsOrder:[...], projects:[{id,bulletIdx}]}."""
    summary = tex_escape(selection.get("summary") or "").strip() or P.SUMMARY_BASE
    parts = [
        PREAMBLE,
        "\\begin{document}\n",
        _header(),
        "\n%-----------SUMMARY-----------\n\\section{Summary}\n\\small{\n" + summary + "\n}\n\\vspace{-6pt}\n",
        _education(),
        _experience(),
        _skills(selection.get("skillsOrder")),
        _projects(selection.get("projects") or []),
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
    paras = "\n\n".join(tex_escape(p).strip() for p in (body_paragraphs or []) if p and p.strip())
    company_s = tex_escape(company or "the team")
    role_s = tex_escape(role or "the role")
    return (
        COVER_PREAMBLE + "\\begin{document}\n"
        f"\\textbf{{\\large {c['name']}}}\\\\\n{c['location']} $|$ {c['phone']} $|$ "
        f"\\href{{mailto:{c['email']}}}{{{c['email']}}} $|$ \\href{{https://{c['site']}}}{{{c['site']}}}\n\n"
        f"\\vspace{{1em}}\nDear Hiring Team at {company_s},\n\n"
        f"{paras}\n\n"
        f"\\vspace{{1em}}\nSincerely,\\\\\n{c['name']}\n"
        "\\end{document}\n"
    )
