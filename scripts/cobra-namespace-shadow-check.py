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

A MASODIK ELLENORZES: AZONOS NEVU OSZTALY A SAJAT NEVTERBEN (2026-08-31).
Nem csak NEVTER arnyekolhat: egy AZONOS NEVU OSZTALY ugyanabban a nevterben ugyanigy nyer a
`using static`-kal behozott, mashol definialt tipus felett. Elo eset: a
`Cobra.Conto.CashBook.UI.Registration` nevterben all egy URES `internal class CashBookClientState`,
es emiatt a `CashBookClientState.Inactive` NEM a `CashBookUtils.CashBookClientState` enumra oldodott
fel -> negy `CS0117: does not contain a definition for`. A szomszedos, evek ota mukodo kod azert
fordult, mert mindenhol TELJES MINOSITESSEL ir.
Ezert a szkript masodik korben osszegyujti a repo `class/struct/enum/interface` deklaracioit
nevterenkent, es jelzi, ha a vizsgalt fajl SAJAT nevtereben letezik olyan tipusnev, amit a fajl
ROVID alakban, tag-hozzaferessel (`Nev.Tag`) hasznal.

A BOM-CSAPDA, AMI EZT A MEROT IS ELVITTE (2026-08-31): ha egy .cs fajl BOM-mal indul es
NINCS benne `using` sor, akkor a `namespace` KOZVETLENUL a BOM utan all -- a `^\s*namespace`
minta pedig NEM illeszkedik, mert a `\ufeff` nem whitespace. A fajl igy csendben kimarad a
gyujtesbol, es a mero NULLAT jelent. Pont ez tortent a hat soros `CashBookClientState.cs`-sel:
a tobbi fajlnal a namespace elott vannak using-ok, ott mukodott. Ezert olvas minden fajlt
`utf-8-sig` kodolassal. Ha a mero nullat mond, eloszor a KALIBRACIOT futtasd (lasd lent).

KALIBRACIO (kotelezo, mielott a nullat elhiszed):
  git show <hibas-commit>:<utvonal> > /tmp/kalib.cs   # egy bizonyitottan CS0117-es allapot
  cp /tmp/kalib.cs <a fajl SAJAT mappajaba>           # a testver-kereses mappa-alapu!
  python3 cobra-namespace-shadow-check.py <a masolat>
A masolatot a VIZSGALT mappaba kell tenni, kulonben nincsenek testverei, es a mero hallgat.

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
                with open(path, encoding="utf-8-sig", errors="replace") as handle:
                    head = handle.read(4000)
            except OSError:
                continue
            for m in re.finditer(r"^\s*namespace\s+(Cobra(?:\.\w+)+)", head, re.M):
                full = m.group(1)
                leaves.setdefault(full.rsplit(".", 1)[-1], set()).add(full)
    return leaves


def collect_sibling_types(path, own_namespace):
    """A fajl MAPPAJABAN levo tobbi .cs tipusdeklaracioi, ha azonos nevterben vannak.

    A teljes fa beolvasasa percekig tartana; a Cobraban a nevter a mappahoz igazodik, tehat
    az arnyekolo testver gyakorlatilag mindig ugyanabban a konyvtarban all. Ha egyszer melle-
    fogunk, az a HIANY iranyaba teved (nem jelez), nem a hamis riasztaseba -- mondd ki, ha
    erre epitesz.
    """
    folder = os.path.dirname(os.path.abspath(path))
    found = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        if not name.endswith(".cs") or os.path.abspath(os.path.join(folder, name)) == os.path.abspath(path):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8-sig", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        ns = file_namespace(text)
        if ns != own_namespace:
            continue
        for t in re.finditer(
                r"^\s*(?:public|internal|private|protected|static|sealed|abstract|partial|\s)*"
                r"\b(?:class|struct|enum|interface)\s+(\w+)", text, re.M):
            # a tipus TAGJAI is kellenek: csak akkor van baj, ha a hivatkozott tag HIANYZIK
            # A generikus metodus neve utan a <T> all, nem a "(" -- e nelkul a mero
            # HAMISAN jelenti hianyzonak (2026-08-31: negy hamis riasztas a UNASEnum.Parse<T>-re,
            # miutan ugyanaz a kod mar zolden lefordult a build gepen).
            members = set(re.findall(r"\b(?:public|internal|private|protected|static|const|readonly|\s)*"
                                     r"[\w<>\[\],.?]+\s+(\w+)\s*(?:<[\w\s,]+>)?\s*[({=;]", text))
            members |= set(re.findall(r"^\s*(\w+)\s*=\s*-?\d+\s*,", text, re.M))   # enum-tagok
            found[t.group(1)] = (name, members)
    return found


def file_namespace(text):
    m = re.search(r"^\s*namespace\s+([\w.]+)", text, re.M)
    return m.group(1) if m else None


def check_sibling_type_shadow(path, raw, clean):
    """Rovid tipusnev, amit a SAJAT nevter egy azonos nevu testver-tipusa arnyekolhat be."""
    ns = file_namespace(raw)
    if not ns:
        return 0
    siblings = collect_sibling_types(path, ns)
    if not siblings:
        return 0
    findings = 0
    lines = clean.split("\n")
    for name, (decl_file, members) in sorted(siblings.items()):
        rx = re.compile(r"(?<![\w.])" + re.escape(name) + r"\.([A-Z]\w*)")
        for index, line in enumerate(lines, start=1):
            m = rx.search(line)
            if not m:
                continue
            # HA a testver-tipusnak VAN ilyen tagja, akkor a rovid alak helyes -- ez a normalis eset
            # (sajat segedosztaly hivasa a sajat nevterbol). Csak a HIANYZO tag arulja el, hogy a
            # szerzo egy MASIK, azonos nevu tipusra gondolt.
            if m.group(1) in members:
                continue
            findings += 1
            print("  !! %s:%d  '%s' rovid alakban -- a SAJAT nevterben (%s) is van ilyen nevu tipus"
                  % (os.path.basename(path), index, name, ns))
            print("     Deklaracio: %s -- ES NINCS BENNE '%s' tagu elem." % (decl_file, m.group(1)))
            print("     A kozelebbi nevter NYER: a using static-kal behozott azonos nevu tipus NEM")
            print("     ez lesz -> CS0117. Minositsd teljesen (Osztaly.Tipus.Tag).")
            print("     %s" % line.strip()[:120])
    return findings


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
            with open(path, encoding="utf-8-sig", errors="replace") as handle:
                raw = handle.read()
        except OSError as exc:
            print("  ?  %s (%s)" % (path, exc))
            continue

        clean = strip_noise(raw)
        findings += check_sibling_type_shadow(path, raw, clean)
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
        print("  OK -- nincs rovid, Cobra-nevterrel VAGY testver-tipussal utkozo nev a vizsgalt fajlokban.")
    return 1 if findings else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
