"""Unicode shriftlar: sarlavha, yorliq va tugmalar uchun turli uslublar.

Faqat lotin harflari (A-Z, a-z) va raqamlar almashtiriladi; boshqa belgilar
(o', g', emoji, tinish belgilari) o'zgarmaydi. HTML teg ichiga qo'llamang —
faqat oddiy matnga.
"""
import re

_UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LO = "abcdefghijklmnopqrstuvwxyz"
_DG = "0123456789"


def _build(upper_start, lower_start, digit_start=None, holes=None):
    holes = holes or {}
    table = {}
    for i, ch in enumerate(_UP):
        table[ch] = holes.get(ch, chr(upper_start + i))
    for i, ch in enumerate(_LO):
        table[ch] = holes.get(ch, chr(lower_start + i))
    if digit_start:
        for i, ch in enumerate(_DG):
            table[ch] = chr(digit_start + i)
    return table


STYLES = {
    # 𝗦𝗮𝗻𝘀 𝗯𝗼𝗹𝗱
    "bold": _build(0x1D5D4, 0x1D5EE, 0x1D7EC),
    # 𝘚𝘢𝘯𝘴 𝘪𝘵𝘢𝘭𝘪𝘤
    "italic": _build(0x1D608, 0x1D622),
    # 𝙎𝙖𝙣𝙨 𝙗𝙤𝙡𝙙 𝙞𝙩𝙖𝙡𝙞𝙘
    "bolditalic": _build(0x1D63C, 0x1D656),
    # 𝐒𝐞𝐫𝐢𝐟 𝐛𝐨𝐥𝐝
    "serif": _build(0x1D400, 0x1D41A, 0x1D7CE),
    # 𝓢𝓬𝓻𝓲𝓹𝓽 𝓫𝓸𝓵𝓭
    "script": _build(0x1D4D0, 0x1D4EA),
    # 𝙼𝚘𝚗𝚘𝚜𝚙𝚊𝚌𝚎
    "mono": _build(0x1D670, 0x1D68A, 0x1D7F6),
    # 𝔻𝕠𝕦𝕓𝕝𝕖
    "double": _build(0x1D538, 0x1D552, 0x1D7D8, holes={
        "C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}),
    # 𝔉𝔯𝔞𝔨𝔱𝔲𝔯
    "fraktur": _build(0x1D504, 0x1D51E, holes={
        "C": "ℭ", "H": "ℌ", "I": "ℑ", "R": "ℜ", "Z": "ℨ"}),
}

# ꜱᴍᴀʟʟ ᴄᴀᴘꜱ (kichik bosh harflar)
_SC = dict(zip(_LO, "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"))
STYLES["smallcaps"] = {**_SC, **{c: _SC[c.lower()] for c in _UP}}

# ⓒⓘⓡⓒⓛⓔⓓ
_CIRC = {c: chr(0x24B6 + i) for i, c in enumerate(_UP)}
_CIRC.update({c: chr(0x24D0 + i) for i, c in enumerate(_LO)})
STYLES["circled"] = _CIRC

_TAG = re.compile(r"(<[^>]+>|&[a-z]+;|&#\d+;)")


def style(text, name="bold"):
    """Matnni tanlangan shriftga o'giradi. HTML teglar va &entity'lar saqlanadi."""
    table = STYLES.get(name)
    text = "" if text is None else str(text)
    if not table:
        return text
    parts = _TAG.split(text)
    out = []
    for part in parts:
        if _TAG.fullmatch(part or ""):
            out.append(part)
        else:
            out.append("".join(table.get(ch, ch) for ch in part))
    return "".join(out)


# Rollarga bog'langan qisqa yordamchilar -------------------------------------
def h1(text):
    """Asosiy sarlavha: 𝗦𝗮𝗻𝘀 𝗯𝗼𝗹𝗱."""
    return style(text, "bold")


def h2(text):
    """Kichik sarlavha: 𝘚𝘢𝘯𝘴 𝘪𝘵𝘢𝘭𝘪𝘤."""
    return style(text, "italic")


def brand(text):
    """Brend nomi: 𝓢𝓬𝓻𝓲𝓹𝓽."""
    return style(text, "script")


def label(text):
    """Yorliq: ꜱᴍᴀʟʟ ᴄᴀᴘꜱ."""
    return style(text, "smallcaps")


def accent(text):
    """Urg'u: 𝐒𝐞𝐫𝐢𝐟 𝐛𝐨𝐥𝐝."""
    return style(text, "serif")


def code(text):
    """𝙼𝚘𝚗𝚘 (tugmalarda; xabarda <code> ishlating)."""
    return style(text, "mono")


def available():
    return sorted(STYLES)
