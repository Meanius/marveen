#!/usr/bin/env python3
"""
Cobra: rovid tipusnev, amit egy SAJAT NEVTER arnyekol be.

MIERT VAN. A Cobra nevterei tobbnyire uzleti fogalmakrol kapjak a nevuket
(Cobra.Conto.Worksheet, Cobra.Light.CashBook, Cobra.Conto.Invoice...), es ezek a nevek
utkoznek kulso konyvtarak TIPUSNEVEIVEL (DevExpress.Spreadsheet.Worksheet) vagy testver
szerelvenyekevel (Cobra.CashBook). C#-ban a kozelebbi nevter nyer, ezert a rovid alak
NEM arra oldodik fel, amire a szerzo gondolt:

    CS0118: 'Worksheet' is a namespace but is used like a type
    CS1503: cannot convert from 'DevExpress.Spreadsheet.Worksheet' to 'Cobra.Conto.Worksheet'

A csapda alattomos, mert a SZOMSZEDOS kod evek ota mukodik ugyanabban a fajlban: ha `var`-t
hasznal, sosem nevezi meg a tipust, tehat sosem utkozik bele. Az elso explicit tipus-megnevezes
buktatja ki. 2026-08-17-en ketszer leptem bele egy napon (Cobra.Light.CashBook, majd ez).

MIT CSINAL. Osszegyujti a repo OSSZES `namespace Cobra...` deklaraciojanak utolso tagjat
(Worksheet, CashBook, Invoice, ...), majd a megadott fajlokban megkeresi azokat a helyeket,
ahol ugyanez a nev ROVIDEN, tipus-pozicioban all. Csak jelez -- a dontes a szerzoe.

AMIT NEM FOG MEG (mondd ki, ha erre epitesz):
  - nem fordit, tehat a valodi feloldast nem tudja; heurisztika, nem bizonyitek,
  - csak a felsorolt fajlokat nezi, nem az egesz fat,
  - a `using X = Y;` alias-okat nem koveti,
  - a kommentekben es sztringekben allo talalatokat kiszuri, de a szoveg-kozeli
    eseteknel tevedhet.

HASZNALAT:
  python3 cobra-namespace-shadow-check.py <valtozott.cs> [tovabbi.cs ...]
  cd /home/boki/projects/Cobra && git diff --name-only | grep '\\.cs$' | xargs python3 .../ezt.py
"""
import os
import re
import sys

REPO = "/home/boki/projects/Cobra"
SRC = os.path.join(REPO, "Desktop/CobraContoNet/src")

# tipus-pozicio: parameter, mezo, valtozo-deklaracio, visszateresi tipus, generikus argumentum
TYPE_POSITIONS = [
    r"\(\s*{n}\s+\w+",          # (Worksheet sheet
    r",\s*{n}\s+\w+",           # , Worksheet sheet
    r"\b(?:private|public|internal|protected|static|readonly)\s+(?:\w+\s+)*{n}\s+\w+",
    r"\bnew\s+{n}\s*\(",        # new Worksheet(
    r"<\s*{n}\s*[,>]",          # List<Worksheet>
    r"\bas\s+{n}\b",
    r"\(\s*{n}\s*\)",           # cast
]


def collect_namespace_leaves():
    leaves = {}
    for root, _dirs, files in os.walk(SRC):
        if os.sep + "obj" in root or os.sep + "bin" in root:
            continue
        for name in files:
            if not name.endswith(".cs"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    head = handle.read(4000)
            except OSError:
                continue
            for m in re.finditer(r"^\s*namespace\s+(Cobra(?:\.\w+)+)", head, re.M):
                full = m.group(1)
                leaves.setdefault(full.rsplit(".", 1)[-1], set()).add(full)
    return leaves


def strip_noise(text):
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r'@"(?:[^"]|"")*"', '""', text, flags=re.S)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    return text


def main(paths):
    leaves = collect_namespace_leaves()
    findings = 0
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                raw = handle.read()
        except OSError as exc:
            print("  ?  %s (%s)" % (path, exc))
            continue

        clean = strip_noise(raw)
        lines = clean.split("\n")
        for leaf, namespaces in leaves.items():
            if leaf not in clean:
                continue
            for pattern in TYPE_POSITIONS:
                rx = re.compile(pattern.format(n=re.escape(leaf)))
                for index, line in enumerate(lines, start=1):
                    if not rx.search(line):
                        continue
                    # ha mar minositve all, nincs dolgunk
                    if re.search(r"[\w\.]\.\s*" + re.escape(leaf), line):
                        continue
                    findings += 1
                    print("  !! %s:%d  '%s' rovid alakban, tipus-pozicioban" % (os.path.basename(path), index, leaf))
                    print("     Cobra nevterkent letezik: %s" % ", ".join(sorted(namespaces)[:3]))
                    print("     %s" % line.strip()[:120])
    if findings == 0:
        print("  OK -- nincs rovid, Cobra-nevterrel utkozo tipusnev a vizsgalt fajlokban.")
    return 1 if findings else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
