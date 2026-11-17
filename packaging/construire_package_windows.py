#!/usr/bin/env python3
"""Construit le package portable Windows du Gantt Asana.

Assemble dans packaging/dist/ un dossier et un zip prêts à envoyer à un
client, sans rien installer sur son poste (aucun Python, aucun Git Bash,
aucun droit administrateur) :

  - Python Windows embarqué ("Windows embeddable package", python.org) ;
  - les scripts du repo : generer_gantt.py, interface_gantt.py,
    maj-gantt-depuis-csv.py, maj-gantt-depuis-json.py ;
  - les lanceurs .bat (double-clic) de packaging/bat/ ;
  - un config.env à compléter avec le token Asana ;
  - la notice LISEZ-MOI.txt.

Le package est reconstruit à neuf à chaque exécution (dossier dist/ vidé).

Usage :
    python3 packaging/construire_package_windows.py
    python3 packaging/construire_package_windows.py --version 3.14.10
"""

import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent
CACHE = DOSSIER / "cache"
DIST = DOSSIER / "dist"

# scripts du repo à embarquer dans le package
SCRIPTS = [
    "generer_gantt.py",
    "interface_gantt.py",
    "maj-gantt-depuis-csv.py",
    "maj-gantt-depuis-json.py",
]


def telecharger_python_embed(version: str) -> Path:
    """Télécharge (une fois) le package embarqué Windows, retourne le zip local."""
    CACHE.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE / f"python-{version}-embed-amd64.zip"
    if zip_path.is_file() and zip_path.stat().st_size > 1_000_000:
        print(f"Python embarqué déjà en cache : {zip_path.name}")
        return zip_path
    url = f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip"
    print(f"Téléchargement {url} ...")
    with urllib.request.urlopen(url, timeout=120) as reponse, open(zip_path, "wb") as fh:
        shutil.copyfileobj(reponse, fh)
    print(f"  {zip_path.stat().st_size / 1e6:.1f} Mo -> {zip_path.name}")
    return zip_path


def corriger_pth(dossier_python: Path) -> None:
    """Ajoute le dossier parent (scripts + config.env) au sys.path du Python
    embarqué : sans ça, le fichier ._pth met Python en mode isolé et les
    imports entre scripts échouent."""
    pths = sorted(dossier_python.glob("python3*._pth"))
    if not pths:
        sys.exit(f"Fichier python3*._pth introuvable dans {dossier_python}")
    pth = pths[0]
    lignes = [l.strip() for l in pth.read_text(encoding="utf-8").splitlines()]
    for requis in (".", ".."):
        if requis not in lignes:
            lignes.append(requis)
    pth.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print(f"  ._pth corrigé ({pth.name}) : " + ", ".join(lignes))


def copier_texte(src: Path, dst: Path) -> None:
    """Copie un fichier texte en fins de ligne Windows (CRLF) : indispensable
    pour les .bat (cmd.exe), confortable pour config.env et la notice."""
    texte = src.read_text(encoding="utf-8")
    dst.write_text(texte.replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8")


def assembler(version: str, zip_embed: Path) -> Path:
    """Construit dist/Gantt-Asana/ à neuf et retourne son chemin."""
    package = DIST / "Gantt-Asana"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    dossier_python = package / "python"
    print(f"Extraction du Python embarqué {version} ...")
    with zipfile.ZipFile(zip_embed) as zf:
        zf.extractall(dossier_python)
    if not (dossier_python / "python.exe").is_file():
        sys.exit(f"python.exe absent après extraction : package invalide")
    corriger_pth(dossier_python)

    for nom in SCRIPTS:
        shutil.copy2(DOSSIER.parent / nom, package / nom)
    for nom in ("config.env", "LISEZ-MOI.txt"):
        copier_texte(DOSSIER / nom, package / nom)
    for bat in sorted((DOSSIER / "bat").glob("*.bat")):
        copier_texte(bat, package / bat.name)

    return package


def zipper(package: Path) -> Path:
    """Zippe le dossier en dist/Gantt-Asana-portable.zip et retourne le chemin."""
    zip_path = DIST / "Gantt-Asana-portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for fichier in sorted(package.rglob("*")):
            if fichier.is_file():
                zf.write(fichier, fichier.relative_to(package.parent))
    return zip_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default="3.14.10",
                        help="version du Python embarqué Windows (défaut : 3.14.10)")
    args = parser.parse_args()

    zip_embed = telecharger_python_embed(args.version)
    package = assembler(args.version, zip_embed)
    zip_path = zipper(package)

    taille_mo = zip_path.stat().st_size / 1e6
    nb_fichiers = sum(1 for f in package.rglob("*") if f.is_file())
    print(f"\nPackage prêt : {zip_path}")
    print(f"  {nb_fichiers} fichiers, zip de {taille_mo:.1f} Mo")
    print(f"  dossier source : {package}")


if __name__ == "__main__":
    main()
