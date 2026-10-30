# Gantt 16:9 depuis Asana

Génère un planning de type Gantt au format HTML 1920x1080 (16:9) à partir
d'un projet Asana, prêt à être capturé ou inséré dans un document PowerPoint.
Toutes les données affichées dans ce dépôt sont des données d'exemple fictives.

## Ce que fait l'outil

- Récupère les tâches parents ("lots") du projet via l'API Asana, ou via un
  export CSV du projet.
- Regroupe les lots par catégorie (champ personnalisé "Scope").
- Reconstruit les dépendances depuis la colonne texte
  "Blocked By (Dependencies)" (elles ne sont pas des dépendances Asana natives).
- Affiche chaque lot avec sa barre, son nom à côté de la barre, un marqueur
  d'impact quand le lot alimente beaucoup d'autres lots, et les sous-tâches
  repliées (consultables en cliquant sur un lot).
- Deux vues : une synthèse 16:9 (capture PowerPoint) et une vue détaillée
  avec sous-tâches dépliées et flèches de dépendances (SVG) :
  - liaison fin -> début (FS) en noir, début -> début (SS) en vert,
    fin -> fin (FF) en orange (catégorie Test de démonstration) ;
  - pointillé si la tâche en amont est terminée, rouge si incohérence de dates ;
  - au clic sur un lot, seuls ses liens directs s'affichent, le reste est
    estompé ; clic sur un lot surligné pour étendre la chaîne ;
  - survol d'une flèche : mise en évidence des deux tâches reliées.
- Timeline zoomable (boutons ou ctrl/cmd + molette), colonne des catégories
  fixe pendant le zoom, vue complète de tous les trimestres par défaut.

## Fichiers

| Fichier | Rôle |
|---|---|
| `generer_gantt.py` | Extraction API Asana + génération des deux vues HTML |
| `maj-gantt-depuis-csv.py` | Régénération des HTML depuis un export CSV Asana |
| `prompt-maj-gantt.md` | Guide d'utilisation + prompt de mise à jour pour un chatbot local |
| `exemples/planning-exemple.html` | Vue synthèse avec données d'exemple fictives |
| `exemples/planning-exemple-detail.html` | Vue détaillée avec les mêmes données d'exemple |
| `exemples/planning-modele-vierge.html` | Modèle vierge (0 lot), à remplir via le script CSV |
| `exemples/planning-modele-vierge-detail.html` | Modèle vierge, vue détaillée |
| `exemples/exemple-lots.json` | Jeu de données d'exemple (30 lots fictifs) |

## Utilisation

### Génération initiale (API Asana)

Python 3.8+, bibliothèque standard uniquement. Configuration par variables
d'environnement :

```bash
export ASANA_TOKEN=<personal access token>
export ASANA_WORKSPACE=<gid du workspace>
python3 generer_gantt.py --projet <gid du projet> --titre "PLANNING ..." 
```

### Depuis un fichier JSON de lots

```bash
python3 generer_gantt.py --source exemples/exemple-lots.json --titre "PLANNING EXEMPLE"
```

### Modèle vierge

```bash
python3 generer_gantt.py --vierge
```

### Mises à jour suivantes (export CSV)

Dans Asana : menu du projet > Exporter > CSV. Puis :

```bash
python3 maj-gantt-depuis-csv.py --csv export-asana.csv --sortie planning.html
```

Le script régénère le fichier HTML complet, sans accès réseau. Utilisable sur
un poste de travail ou un serveur de batch (cron).

Alternative sans script : fournir le CSV et le HTML à un chatbot local avec le
prompt décrit dans `prompt-maj-gantt.md`.

## Personnalisation

En tête de `generer_gantt.py` :

- `ORDRE_SCOPES` : ordre d'affichage des catégories.
- `COULEURS_SCOPES` : couleur de chaque catégorie (facultatif, une palette
  générique est appliquée sinon).
- `AFFICHAGE_SCOPES` : noms raccourcis pour la colonne de gauche.
- `PALETTE_TRIMESTRES` : couleurs d'en-tête des trimestres.
- `SEUIL_ATTENTION` : nombre de lots en aval déclenchant le marqueur d'impact.

## Données du planning

Le fichier HTML embarque ses données dans un bloc JavaScript `const DATA = {...}`
documenté en tête de bloc : mettre ce bloc à jour suffit à mettre à jour le
planning (c'est ce que fait `maj-gantt-depuis-csv.py`).
