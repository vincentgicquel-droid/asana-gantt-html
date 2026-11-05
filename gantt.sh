#!/bin/bash
# Génère le planning Gantt HTML depuis un export JSON du projet Asana.
#
# Usage :
#   ./gantt.sh export-asana.json                        # vues synthèse + jalons
#   ./gantt.sh export-asana.json detail                 # vue détaillée seule
#   ./gantt.sh export-asana.json "synthese,jalons,detail" "TITRE" "DOSSIER" "DEST1,DEST2"
#
# Sortie : dans le dossier indiqué (défaut : sorties/ à côté du script),
# fichiers datés du jour : planning-AAAA-MM-JJ.html, -jalons.html, -detail.html
# Chaque fichier est aussi copié vers les destinations supplémentaires
# (5e argument, séparées par des virgules, ex. un dossier SharePoint).

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage : $0 <export-asana.json> [synthese,jalons,detail] [titre] [dossier]" >&2
    exit 1
fi

JSON="$1"
VUES="${2:-synthese,jalons}"
TITRE="${3:-PLANNING DE PROJET}"
DOSSIER="${4:-}"
DESTINATIONS="${5:-}"

SCRIPT="$(cd "$(dirname "$0")" && pwd)/maj-gantt-depuis-json.py"

ARGS=(--json "$JSON" --vues "$VUES" --titre "$TITRE")
[ -n "$DOSSIER" ] && ARGS+=(--dossier "$DOSSIER")
if [ -n "$DESTINATIONS" ]; then
    IFS=',' read -r -a DESTS <<< "$DESTINATIONS"
    for DEST in "${DESTS[@]}"; do
        [ -n "$DEST" ] && ARGS+=(--destination "$DEST")
    done
fi

python3 "$SCRIPT" "${ARGS[@]}"
