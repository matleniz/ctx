"""Content filters: secrets, instruction-shaped lines, structure lines."""
import re


# filters: secrets, instruction-shaped lines, structure lines
SECRET_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    # password: ..., AWS_SECRET_ACCESS_KEY=..., db_password=...: the word, alone or in a snake name
    # the key name may carry a prefix (PGPASSWORD, AWS_SECRET) and a known suffix only (_key, _id),
    # so names like password_reset or secret_santa stay
    re.compile(r"(?i)\w*?(password|passwd|secret|api[_-]?key|access[_-]?key|token|mot de passe)"
               r"(_(key|id|token|secret|access_key))?[\"']?\s*[:=]\s*[\"']?[^\s\"']{6,}"),
    re.compile(r"(?<=://)[^\s/:@]+:[^\s/@]+(?=@)"),  # the credentials of a URL, before its @host
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
]

# Rule: a line is dropped when it speaks TO a reader (you must, tell the user, "Assistant:"),
# tries to override (ignore previous..., system prompt, from now on) or holds a dangerous command
# (rm -rf, sudo, a download piped into a shell or interpreter). On written state (Status, Next,
# summary: model or agent output) policy lines are dropped too: please + action, always/never +
# action, a precondition (before editing anything), run-this-first. Notes and Decisions keep
# them: "decision: never store raw CSVs in git" is a decision, not an attack.
# State lines ("- not done: tests not run", "- next: add --sum to cli.py") match none of these.
INSTR_RES = [re.compile(p, re.I) for p in [
    r"\b(ignore|disregard|forget)\b.{0,30}"
    r"\b(previous|prior|above|earlier|all|every|instructions?)\b",
    r"\bignore\s+(everything|all)\b",
    r"\b(tell|inform|warn|instruct)\s+(the\s+)?(user|human|assistant|agent)\b",
    r"\byou\s+(must|should|shall|need to|have to|are to|will)\b",
    r"\b(the\s+)?(agent|assistant|ai)\s+(must|should|shall|has to)\b",
    r"\b(do not|don't|never)\s+(tell|mention|reveal|show)\b",
    r"\b(tu|vous)\s+(dois|devez|ne dois pas|ne devez pas)\b",
    r"\bil\s+faut\s+que\s+(tu|vous)\b",
    r"\b(ignore[zr]?|oublie[zr]?)\b.{0,30}\b(précédent|instructions?|consignes?)\b",
    r"\b(dis|dites)\s+à\s+l.utilisateur\b",
    r"\bsystem\s+prompt\b",
    r"\bnew\s+instructions?\b",
    r"\bfrom\s+now\s+on\b",
    r"\brm\s+-[a-z]*r[a-z]*f?\b",
    r"\bsudo\b",
    r"--no-verify\b",
    r"\b(curl|wget)\b.*\|\s*(sudo\s+)?((ba|z|da)?sh|python[\d.]*|perl|ruby|node)\b",
    r"<\(\s*(curl|wget)\b|\$\(\s*(curl|wget)\b",
    r"^\s*(?:[-*]\s*)?((assistant|ai|claude)\s*,|assistant\s*:)",
    r"</?\s*(system|assistant|ctx-data|instructions?)\b",
    r"^\s*(IMPORTANT|ATTENTION|NOTE TO (THE )?(AI|ASSISTANT|AGENT))\s*:",
]]
_ACT = (r"(run|execute|install|push|commit|delete|remove|ignore|skip|use|call|send|merge|deploy"
        r"|bypass|disable|trust|edit|modify|force|upload)")
POLICY_RES = [re.compile(p, re.I) for p in [
    rf"\bplease\s+{_ACT}\b|\bs.il\s+(te|vous)\s+pla[iî]t\b|\bmerci\s+de\b",
    rf"^\s*(?:[-*]\s*)?(always|never)\s*:?\s+{_ACT}\b",
    r"^\s*(?:[-*]\s*)?(toujours|jamais)\s+\w+(er|ir|re)\b",
    r"\bbefore\s+(you\s+)?(edit|do|start|chang|touch|writ|modif)\w*\s+"
    r"(anything|any\s+\w+|everything)\b",
    r"\bavant\s+(de\s+\w+\s+)?(toute?|quoi que ce soit)\b",
    r"\bmake\s+sure\s+to\s+(run|execute|install)\b",
    r"\b(run|execute)\s+(the\s+script\b|\S+\.sh\b).*\b(first|before)\b",
]]

STRUCT_RE = re.compile(
    r"\b(dossier|folder|directory|répertoire|sous-dossiers?|subfolders?)\b"
    r"[^.;]{0,30}\b(contient|contains)\b"
    r"|\b(liste des|list of)\s+(\w+\s+)?"
    r"(fichiers|files|dossiers|folders|notebooks|exercices?|exercises|sous-dossiers|subfolders)\b"
    r"|\barborescence\b|\b(file|directory|folder|dir) tree\b|\btree of\b"
    r"|\b\d+\s+(fichiers?|files?|notebooks?|dossiers?|folders?)\b",
    re.I,
)
STATE_RE = re.compile(
    r"\b(fait\w*|done|ok|cass\w+|broken|fix\w*|fail\w*|pass\w*|rest\w*|manqu\w*|missing|todo"
    r"|écrit\w*|written|add\w*|ajout\w*|cré\w+|creat\w*|modifi\w*|chang\w*|updated?|mis à jour"
    r"|rendus?|runs?|ran|non|not|pas|stub|decision|lève|raises?|erreur|errors?)\b"
    r"|^\s*next\s*:|\S\.\w+:\d+\b",
    re.I,
)
FILE_RE = re.compile(
    r"[\w-]+\.(?:py|md|txt|csv|json|ipynb|ya?ml|toml|js|ts|html|sh|cfg|ini|pdf|png"
    r"|rs|go|cpp|c|h)\b",
    re.I,
)


def redact(text: str) -> tuple[str, int]:
    """Return (text, number_of_redactions)."""
    n = 0
    for pattern in SECRET_RES:
        text, k = pattern.subn("[REDACTED]", text)
        n += k
    return text, n


def instruction_shaped(line: str, policy: bool = True) -> bool:
    """True when the line looks like an instruction to a reader or an agent.

    policy=False (Notes, Decisions) leaves out the policy rules (always/never, please, before...).
    """
    return any(p.search(line) for p in INSTR_RES + (POLICY_RES if policy else []))


def filter_lines(text: str, policy: bool = True) -> tuple[str, int]:
    """Redact secrets (whole text first: a PEM block spans lines), drop instruction-shaped lines.

    Returns (text, number of redactions and dropped lines).
    """
    text, n = redact(text)
    kept = []
    for line in text.split("\n"):
        if instruction_shaped(line, policy):
            n += 1
        else:
            kept.append(line.rstrip())
    return "\n".join(kept), n


def structure_line(line: str) -> bool:
    """True if `line` describes structure (what a folder holds) instead of state.

    Structure is: wording, "N files", or a bare list of >= 3 file names.
    """
    if STATE_RE.search(line):
        return False
    if STRUCT_RE.search(line):
        return True
    if len(FILE_RE.findall(line)) < 3:
        return False
    return len(re.findall(r"[^\W\d_]{2,}", FILE_RE.sub(" ", line))) <= 4
