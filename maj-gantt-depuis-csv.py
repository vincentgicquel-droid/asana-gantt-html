#!/usr/bin/env python3
"""Met à jour le planning Gantt HTML depuis un export CSV du projet Asana.

Le CSV provient d'Asana : projet > menu "..." > Exporter > CSV.
Le fichier contient une ligne par tâche et sous-tâche, avec les colonnes
standards (Nom, Date de début, Date de fin, Tâche parente, Dépendances)
et les champs personnalisés du projet (Scope, Fiabilité, Séléction,
Direction, Matériel, Numéro.Nom Lot).

Le script régénère le fichier HTML complet (même rendu que generer-gantt.py).
Aucune dépendance externe : bibliothèque standard Python 3.8+, aucun accès
réseau. Utilisable sur poste de travail et sur serveur de batch.

Usage :
    python3 maj-gantt-depuis-csv.py --csv export-asana.csv
    python3 maj-gantt-depuis-csv.py --csv export.csv --sortie planning.html
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generer_gantt import calculer_fins, generer_html, resoudre_dependances

# colonnes standard du CSV Asana : variantes anglais / français reconnues
COLONNES = {
    "id": ["task id", "id de la tache", "id tache"],
    "nom": ["name", "nom", "tache", "task"],
    "debut": ["start date", "date de debut", "date debut"],
    "fin": ["due date", "date de fin", "date fin", "date d'echeance", "due on"],
    "terminee": ["completed at", "terminee le", "termine le", "completed"],
    "parent": ["parent task", "tache parente", "tache parent", "parent"],
    "blocked_by": ["blocked by (dependencies)", "bloquee par (dependances)", "bloque par (dependances)"],
    "blocking": ["blocking (dependencies)", "bloque (dependances)"],
    "scope": ["scope"],
    "fiabilite": ["fiabilite"],
    "selection": ["selection"],
    "direction": ["direction"],
    "materiel": ["materiel", "materiel "],
    "label_lot": ["numero.nom lot", "numero .nom lot", "numero.nom lot"],
}


def normaliser_entete(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("'", " ")
    return re.sub(r"\s+", " ", s).strip()


def reperer_colonnes(entetes):
    """Associe chaque colonne logique à l'index de la colonne CSV."""
    norm = [normaliser_entete(e) for e in entetes]
    mapping = {}
    for cle, variantes in COLONNES.items():
        for i, h in enumerate(norm):
            if h in variantes:
                mapping[cle] = i
                break
    # colonnes non standard : on les garde telles quelles (champs perso)
    mapping["colonnes_brutes"] = {e: i for i, e in enumerate(entetes)}
    return mapping


def parser_date(v):
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(v[: len(fmt) + 6] if "%f" in fmt else v[:19], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def lire_csv(chemin):
    with open(chemin, encoding="utf-8-sig", newline="") as fh:
        lecteur = csv.reader(fh)
        entetes = next(lecteur)
        lignes = [l for l in lecteur if any((c or "").strip() for c in l)]
    return entetes, lignes


def extraire_valeurs(cellule):
    """Éclate une cellule multi-valeurs Asana (séparateur ', ').
    Recolle les fragments si la découpe casse un nom (virgule dans un nom)."""
    v = (cellule or "").strip()
    if not v:
        return []
    return [x.strip() for x in v.split(", ") if x.strip()]


def charger_lots(chemin_csv):
    entetes, lignes = lire_csv(chemin_csv)
    col = reperer_colonnes(entetes)
    manquantes = [c for c in ("nom", "debut") if c not in col]
    if manquantes:
        sys.exit(
            f"Colonnes introuvables dans le CSV : {manquantes}. "
            "Vérifier que le fichier vient bien d'un export CSV Asana."
        )

    def val(ligne, cle):
        i = col.get(cle)
        return ligne[i].strip() if i is not None and i < len(ligne) else ""

    lots = []
    st_par_parent = {}
    for ligne in lignes:
        parent = val(ligne, "parent")
        nom = val(ligne, "nom")
        if not nom:
            continue
        debut = parser_date(val(ligne, "debut"))
        fin = parser_date(val(ligne, "fin")) or debut
        if parent:
            st_par_parent.setdefault(parent, []).append({"nom": nom, "debut": debut, "fin": fin})
            continue
        lots.append(
            {
                "gid": val(ligne, "id") or nom,
                "label": val(ligne, "label_lot") or nom,
                "scope": val(ligne, "scope") or "Sans scope",
                "selection": val(ligne, "selection") or None,
                "direction": val(ligne, "direction") or None,
                "materiel": val(ligne, "materiel") or None,
                "start": debut,
                "terminee": bool(val(ligne, "terminee")),
                "blocked_by": extraire_valeurs(val(ligne, "blocked_by")),
                "sous_taches": [],
                "alias": [nom],
            }
        )

    # rattachement des sous-tâches à leur lot (par nom ou alias)
    par_nom = {}
    for l in lots:
        for v in [l["label"], *l["alias"]]:
            par_nom.setdefault(unicodedata.normalize("NFC", v.strip()), l)
    orphelines = 0
    for parent, sts in st_par_parent.items():
        lot = par_nom.get(unicodedata.normalize("NFC", parent.strip()))
        if lot:
            lot["sous_taches"].extend(sts)
        else:
            orphelines += len(sts)

    resoudre_dependances(lots)
    calculer_fins(lots)
    return lots, orphelines


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", required=True, help="chemin du fichier CSV exporté d'Asana")
    parser.add_argument("--sortie", default="planning-strategie-hebergement.html", help="fichier HTML à (re)générer")
    parser.add_argument("--titre", default="PLANNING STRATÉGIE D'HÉBERGEMENT : 10 SCOPES")
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        sys.exit(f"Fichier introuvable : {args.csv}")

    lots, orphelines = charger_lots(args.csv)
    nb_deps = sum(len(l["deps"]) for l in lots)

    html = generer_html(lots)
    if args.titre:
        html = html.replace("PLANNING STRATÉGIE D'HÉBERGEMENT : 10 SCOPES", args.titre)
    with open(args.sortie, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"OK : {args.sortie}")
    print(f"     {len(lots)} lots, {nb_deps} dépendances, {orphelines} sous-tâches orphelines ignorées")


if __name__ == "__main__":
    main()
