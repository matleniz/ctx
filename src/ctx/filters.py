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
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token|mot de passe)\b\s*[:=]\s*\S{6,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
]

INSTR_RES = [re.compile(p, re.I) for p in [
    r"\b(ignore|disregard|forget)\b.{0,30}"
    r"\b(previous|prior|above|earlier|all|every|instructions?)\b",
    r"\bignore\s+(everything|all)\b",
    r"\b(tell|inform|warn|instruct)\s+(the\s+)?(user|human|assistant|agent)\b",
    r"\byou\s+(must|should|shall|need to|have to|are to|will)\b",
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
    r"\b(curl|wget)\b.*\|\s*(ba)?sh\b",
    r"</?\s*(system|assistant|ctx-data|instructions?)\b",
    r"^\s*(IMPORTANT|ATTENTION|NOTE TO (THE )?(AI|ASSISTANT|AGENT))\s*:",
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


def instruction_shaped(line: str) -> bool:
    """True when the line looks like an instruction to a reader or an agent."""
    return any(pattern.search(line) for pattern in INSTR_RES)


def filter_lines(text: str) -> tuple[str, int]:
    """Redact secrets and drop instruction-shaped lines.

    Returns (text, number of redactions and dropped lines).
    """
    kept, n = [], 0
    for line in text.split("\n"):
        line, k = redact(line)
        n += k
        if instruction_shaped(line):
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
