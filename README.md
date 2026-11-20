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
- Troisième vue "jalons" : uniquement les bandeaux de catégories (un par
  Scope, aligné sur les trimestres), avec les jalons (sous-tâches dont le
  nom contient "Prêt à l'usage") affichés en repère daté : losange posé dans
  un couloir sous le bandeau, relié par une tige, libellé lisible sans
  survol, texte à droite du losange (à gauche près du bord droit).
  Le libellé reprend le nom de la tâche parente (numérotation et préfixe
  « NEW - » retirés, tronqué à 25 caractères + « ... »), sans la date : le
  losange est déjà positionné sur la timeline selon la date du jalon. Tous
  les jalons sont affichés sous le bandeau, remonté en haut de ligne.
  Un jalon Asana n'a qu'une date : son échéance (`due_on`) est retenue, repli
  sur la date de début. Les jalons sans date sont ignorés.
  Placement sans chevauchement garanti : les largeurs de libellés sont
  estimées caractère par caractère (police + padding + marge de rendu),
  les losanges sont des obstacles pour les libellés du couloir, un jalon
  daté hors plage affichée est recalé en bord de timeline, et si l'ensemble
  ne tient pas dans le cadre la police des lignes les plus denses est
  réduite par paliers (12 → 11 → 10 → 9 px) au lieu de compresser les
  couloirs.
- Mise à l'échelle de la page : ajustée à la fenêtre du navigateur au
  chargement (aucun recadrage), et boutons A− / A+ pour l'agrandir par
  paliers de 10 % (jusqu'à 160 %) avec ascenseurs natifs : le zoom ne
  déforme jamais la mise en page.
- Date de fin des bandeaux de lot : la due date Asana du lot est utilisée
  quand elle existe et est postérieure à son début (fidèle à Asana) ; sinon
  repli sur le calcul : 1) plus grande fin des sous-tâches, 2) début du
  premier successeur - 1 jour, 3) début + 45 jours.
- Ordre des catégories : celui de la colonne Scope dans Asana (options du
  champ personnalisé), lu via l'API ou l'export JSON ; repli alphabétique.
- Frise des trimestres : une couleur par année, déclinée en 4 teintes (une
  par trimestre) au lieu d'un arc-en-ciel.
- Surlignage jaune du nom de lot : champ personnalisé "Surlignage" renseigné
  (repli : "Séléction", comportement historique).

## Fichiers

| Fichier | Rôle |
|---|---|
| `generer_gantt.py` | Extraction API Asana + génération des trois vues HTML |
| `maj-gantt-depuis-csv.py` | Régénération des HTML depuis un export CSV Asana |
| `maj-gantt-depuis-json.py` | Régénération d'un HTML depuis un export JSON Asana |
| `gantt.sh` | Commande tout-en-un : export JSON + vues -> HTML datés + copies |
| `interface_gantt.py` | Interface web locale : choix du projet, bouton « Générer en HTML » |
| `packaging/construire_package_windows.py` | Construit le zip portable Windows (Python embarqué + .bat + notice) |
| `prompt-maj-gantt.md` | Guide d'utilisation + prompt de mise à jour pour un chatbot local |
| `exemples/planning-exemple.html` | Vue synthèse avec données d'exemple fictives |
| `exemples/planning-exemple-jalons.html` | Vue jalons avec les mêmes données d'exemple |
| `exemples/planning-exemple-detail.html` | Vue détaillée avec les mêmes données d'exemple |
| `exemples/planning-modele-vierge.html` | Modèle vierge (0 lot), à remplir via le script CSV |
| `exemples/planning-modele-vierge-jalons.html` | Modèle vierge, vue jalons |
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

Le token et les variables sont aussi lus automatiquement dans un fichier
`config.env` présent à côté du script ou dans un dossier parent.

### Environnements (sandbox / prod)

Le workspace peut être choisi par environnement, dans le format du kit :

```
ASANA_ENV=sandbox            # ou prod
ASANA_WORKSPACE_SANDBOX=<gid sandbox>
ASANA_WORKSPACE_PROD=<gid prod>
```

Priorité : `--workspace` (ligne de commande) > `ASANA_WORKSPACE` >
`ASANA_WORKSPACE_<ASANA_ENV>` > sandbox par défaut. L'option `--env prod`
force l'environnement ponctuellement. L'interface web, elle, liste les projets
de tous les workspaces accessibles au token : on choisit le bon dans la liste.

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

### Mises à jour suivantes (export JSON, commande unique)

Dans Asana : menu du projet > Exporter > JSON. Puis, une commande :

```bash
./gantt.sh export-asana.json                          # vues synthèse + jalons
./gantt.sh export-asana.json detail                   # vue détaillée seule
./gantt.sh export-asana.json "synthese,jalons,detail" "TITRE" "DOSSIER"
```

Sortie : dans le dossier indiqué (défaut : `sorties/` à côté du script),
fichiers datés du jour : `planning-AAAA-MM-JJ.html`, `-jalons.html`,
`-detail.html`. Sans accès réseau, bibliothèque standard uniquement.

Les dépendances sont reconstituées depuis le champ texte
"Blocked By (Dependencies)", complété par les dépendances natives Asana
(liste de gids) si l'export en contient.

### Génération directe par API (sans export)

Depuis une machine autorisée (sandbox depuis un PC, poste ou serveur en liste
blanche pour la prod) :

```bash
export ASANA_TOKEN=<personal access token>
export ASANA_WORKSPACE=<gid du workspace>
python3 generer_gantt.py --projet <gid du projet> --titre "PLANNING ..." \
    --vues synthese,jalons --dossier /chemin/dossier/partage
```

Le même moteur accepte `--json export.json` (export manuel), `--source`
(JSON de lots) et `--vierge`. En direct (sans le script bash) :

```bash
python3 maj-gantt-depuis-json.py --json export-asana.json --vues synthese,jalons
```

### Destinations supplémentaires (partage, SharePoint)

Chaque HTML généré peut être copié automatiquement vers un ou plusieurs
dossiers (par exemple un dossier synchronisé SharePoint) :

- en tête de `generer_gantt.py` : liste `DESTINATIONS` (chemins absolus) ;
- en ligne de commande : `--destination /chemin/partage` (option répétable) ;
- avec le script bash : 5e argument, destinations séparées par des virgules :

```bash
./gantt.sh export-asana.json "synthese,jalons" "TITRE" "" "/chemin/SharePoint/Plannings"
```

### Interface web (choix du projet, un clic)

```bash
python3 interface_gantt.py            # puis ouvrir http://localhost:8765
```

L'interface charge automatiquement le token depuis un fichier `config.env`
trouvé dans le dossier du script ou un parent (`ASANA_TOKEN=...`, format
`CLÉ=valeur`), sinon elle utilise la variable d'environnement `ASANA_TOKEN`.
Elle liste les projets accessibles avec ce token, permet de choisir les vues
et le dossier de sortie, et génère les fichiers datés du jour en un clic
(bouton « Générer en HTML »). Aucune dépendance : serveur de la bibliothèque
standard, utilisable sur poste ou serveur de batch.

### Package portable Windows (sans installation côté client)

Pour un poste client où l'on ne veut installer ni Python, ni Git Bash :

**Téléchargement direct du package complet** (Python embarqué inclus) :
page [Releases](https://github.com/vincentgicquel-droid/asana-gantt-html/releases/latest) du dépôt, fichier `Gantt-Asana-portable.zip`.
Le zip du code source (bouton vert "Code") ne contient pas le Python embarqué.

```bash
python3 packaging/construire_package_windows.py
```

Produit `packaging/dist/Gantt-Asana-portable.zip` (environ 12 Mo) contenant :

- le Python Windows embarqué officiel (python.org) : aucune installation,
  aucun droit administrateur, `python.exe` signé par la Python Software
  Foundation (passe les antivirus et SmartScreen) ;
- les scripts du repo, les lanceurs .bat (`1-Generer-le-planning.bat`,
  `2-Test-connexion.bat`, `3-Maj-depuis-CSV.bat` en glisser-déposer),
  un `config.env` à compléter avec le token et la notice `LISEZ-MOI.txt`.

Le client décompresse, colle son token dans `config.env`, double-clique sur
`1-Generer-le-planning.bat` : l'interface s'ouvre dans le navigateur. Version
du Python embarqué réglable par `--version` (défaut : 3.14.10).

### Format 16:9

Les vues synthèse et jalons remplissent exactement le cadre 1920x1080 :
aucun défilement vertical, aucun contenu rogné en bas de page (vérifié au
pixel près). La vue détaillée reste une vue interactive avec défilement.

## Personnalisation

En tête de `generer_gantt.py` :

- `ORDRE_SCOPES` : ordre d'affichage historique des catégories (repli quand
  l'ordre Asana n'est pas disponible).
- `COULEURS_SCOPES` : couleur de chaque catégorie (facultatif, une palette
  générique est appliquée sinon).
- `AFFICHAGE_SCOPES` : noms raccourcis pour la colonne de gauche.
- `COULEURS_ANNUELLES` / `PALETTE_ANNUELLES` / `NUANCES_TRIMESTRES` : gamme
  de couleur par année et nuances par trimestre de la frise.

### Ordre des catégories avec l'export CSV

L'export CSV ne porte pas l'ordre des options du champ Scope (contrairement
à l'API et à l'export JSON). Pour retrouver l'ordre Asana sur ce chemin,
déposer un fichier optionnel `ordre-scopes.txt` à côté des scripts : un nom
de catégorie par ligne, dans l'ordre souhaité. Les chemins API et JSON
restent prioritaires (ordre lu directement dans le champ).
- `CHAMP_SURLIGNAGE` / `CHAMP_SURLIGNAGE_REPLI` : champs Asana qui déclenchent
  le surlignage jaune du nom de lot.
- `DOSSIER_SORTIE` : dossier par défaut des HTML générés (partage réseau,
  dossier synchronisé SharePoint...).
- `SEUIL_ATTENTION` : nombre de lots en aval déclenchant le marqueur d'impact.

## Données du planning

Le fichier HTML embarque ses données dans un bloc JavaScript `const DATA = {...}`
documenté en tête de bloc : mettre ce bloc à jour suffit à mettre à jour le
planning (c'est ce que fait `maj-gantt-depuis-csv.py`).
