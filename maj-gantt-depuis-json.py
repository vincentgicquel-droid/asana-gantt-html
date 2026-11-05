#!/usr/bin/env python3
"""Met à jour le planning Gantt HTML depuis un export JSON du projet Asana.

Le JSON provient d'Asana : projet > menu "..." > Exporter > JSON.
Génère les vues demandées (synthese, jalons, detail) avec la date du jour
dans le nom des fichiers. Même moteur que generer-gantt.py.

Aucune dépendance externe : bibliothèque standard Python 3.8+, aucun accès
réseau. Utilisable sur poste de travail et sur serveur de batch.

Usage :
    python3 maj-gantt-depuis-json.py --json export-asana.json
    python3 maj-gantt-depuis-json.py --json export.json --vues synthese,jalons,detail
    python3 maj-gantt-depuis-json.py --json export.json --sortie planning.html
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generer_gantt import DESTINATIONS, DOSSIER_SORTIE, charger_export_json, generer_vues


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", required=True, help="chemin du fichier JSON exporté d'Asana")
    parser.add_argument("--vues", default="synthese,jalons",
                        help="vues à générer, séparées par des virgules : synthese, jalons, detail")
    parser.add_argument("--sortie", default=None,
                        help="nom de base des fichiers HTML (défaut : planning-AAAA-MM-JJ)")
    parser.add_argument("--dossier", default=None, help="dossier de sortie des HTML")
    parser.add_argument("--destination", action="append", default=None,
                        help="destination supplémentaire de copie des HTML (option répétable)")
    parser.add_argument("--titre", default="PLANNING DE PROJET", help="titre affiché en haut du planning")
    args = parser.parse_args()

    if not os.path.isfile(args.json):
        sys.exit(f"Fichier introuvable : {args.json}")

    vues = [v.strip().lower() for v in args.vues.split(",") if v.strip()]
    lots, ordre_scopes = charger_export_json(args.json)

    from datetime import date
    base = args.sortie or f"planning-{date.today():%Y-%m-%d}"
    dossier_sortie = args.dossier or DOSSIER_SORTIE
    destinations = list(DESTINATIONS) + list(args.destination or [])
    produits = generer_vues(lots, vues, base, dossier_sortie,
                            titre=args.titre, ordre_scopes=ordre_scopes,
                            destinations=destinations)
    for chemin, vue, copies in produits:
        print(f"OK [{vue}] : {chemin}")
        for dest in copies:
            print(f"     copié vers {dest}")
    print(f"     {len(lots)} lots, {sum(len(l['deps']) for l in lots)} dépendances, "
          f"{sum(len(l['sous_taches']) for l in lots)} sous-tâches")


if __name__ == "__main__":
    main()
