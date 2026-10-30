# Mettre à jour le planning Gantt HTML depuis un export CSV Asana

Deux méthodes au choix : le script (fiable, recommandé) ou l'assistant local (chatbot).

## Méthode 1 : script (recommandée)

### Extraire le CSV d'Asana

1. Ouvrir le projet dans Asana.
2. Menu en haut à droite (flèche à côté du titre du projet) > **Exporter** > **CSV**.
3. Enregistrer le fichier, par exemple `export-asana.csv`.

### Lancer la mise à jour

Sur le poste ou sur le serveur (Python 3.8+, aucune dépendance, aucun accès réseau) :

```bash
python3 maj-gantt-depuis-csv.py --csv export-asana.csv
```

Options utiles :

```bash
# nom du fichier HTML de sortie différent
python3 maj-gantt-depuis-csv.py --csv export-asana.csv --sortie planning-v2.html
```

Le script régénère le fichier HTML complet : lignes, barres, dépendances
reconstruites depuis la colonne "Blocked By (Dependencies)", sous-tâches repliées.

## Méthode 2 : assistant local (chatbot)

Fournir au chatbot les deux fichiers (le CSV exporté et le HTML actuel) avec le
prompt ci-dessous.

---

## PROMPT À COLLER DANS LE CHATBOT LOCAL

Tu es chargé de mettre à jour un planning Gantt au format HTML.
Tu reçois deux fichiers :
1. `export-asana.csv` : export CSV du projet Asana, à jour.
2. `planning-strategie-hebergement.html` : le planning HTML à mettre à jour.

Ta mission : régénérer le fichier HTML à l'identique, seul le bloc de données
`const DATA = {...};` change. Ne modifie ni le CSS, ni le JavaScript, ni la
structure HTML, ni les commentaires d'entête du bloc DATA.

### Lire le CSV

- Une ligne = une tâche ou une sous-tâche. Les sous-tâches ont la colonne
  "Parent Task" (ou "Tâche parente") remplie.
- Les LOTS affichés dans le Gantt sont uniquement les lignes dont
  "Parent Task" est vide (les tâches parents). Les sous-tâches ne sont jamais
  des lignes du Gantt : elles sont rangées dans le champ `sous_taches` de leur lot.
- Colonnes à utiliser (les noms peuvent être en anglais ou en français) :
  - "Task ID" / "ID de la tâche" → `id`
  - "Name" / "Nom" → nom de la tâche
  - "Start Date" / "Date de début" → `debut` (format AAAA-MM-JJ)
  - "Due Date" / "Date de fin" → fin de la tâche ou de la sous-tâche
  - "Blocked By (Dependencies)" / "Bloquée par (Dépendances)" → dépendances
  - "Parent Task" / "Tâche parente" → rattachement des sous-tâches
  - Champs personnalisés : "Scope", "Fiabilité", "Séléction", "Direction",
    "Matériel", "Numéro.Nom Lot"

### Construire le bloc DATA

Pour chaque lot (tâche parent), produire un objet :

```json
{
  "id": "identifiant de la tâche",
  "libelle": "contenu de Numéro.Nom Lot, sinon le nom de la tâche",
  "scope": "valeur du champ Scope (catégorie du regroupement)",
  "debut": "AAAA-MM-JJ ou null si aucune date",
  "fin": "AAAA-MM-JJ ou null",
  "fin_estimee": true ou false,
  "selection": "valeur du champ Séléction ou null",
  "direction": "valeur du champ Direction ou null",
  "materiel": "oui / non ou null",
  "dependances": ["libellé du lot antécédent 1", "..."],
  "impact": 0,
  "sous_taches": [{"nom": "...", "debut": "AAAA-MM-JJ ou null", "fin": "AAAA-MM-JJ ou null"}]
}
```

Règles :

1. `libelle` : valeur du champ "Numéro.Nom Lot" si elle existe, sinon "Name".
2. `sous_taches` : toutes les lignes du CSV dont "Parent Task" = nom du lot,
   avec `debut` = Start Date, `fin` = Due Date ; si la sous-tâche n'a pas de
   date de fin (événement d'une journée), `fin` = `debut`.
3. `debut` du lot : Start Date du lot.
4. `fin` du lot : la plus grande date de fin de ses sous-tâches. Si aucune
   sous-tâche n'a de date de fin postérieure au début, `fin` = null et le
   fichier affichera une barre de 45 jours.
5. `fin_estimee` = true uniquement si la fin n'a pas pu être déterminée à
   partir des sous-tâches.
6. `dependances` : liste des noms cités dans "Blocked By (Dependencies)".
   Les noms cités correspondent aux noms de tâches des lots antécédents ;
   recopier le libellé du lot antécédent tel qu'il apparaît dans son champ
   `libelle` (par exemple "12.Gestion des identités").
   Ignorer les noms qui ne correspondent à aucun lot.
7. `impact` : nombre de lots qui citent ce lot dans leurs dépendances
   (le nombre de successeurs).
8. `nb_dependances` (dans l'entête du bloc) = nombre total de liens de
   dépendance (somme des `dependances` de tous les lots).
9. `nb_lots_dates` = nombre de lots ayant une `debut` non null.
10. `nb_lots_fin_estimee` = nombre de lots avec `fin_estimee` = true.
11. `date_generation` = date du jour (AAAA-MM-JJ).
12. `scopes` = liste des valeurs de Scope présentes, dans cet ordre si elles
    existent : Domaine A, Domaine B, ..., Domaine J ; les autres scopes
    éventuels sont ajoutés après, par ordre alphabétique.

### Vérifier avant de rendre le fichier

- Le HTML rendu doit contenir le même nombre de lots que de tâches parents du CSV.
- Aucune sous-tâche ne doit apparaître comme ligne du Gantt.
- La structure du bloc DATA doit rester exactement celle décrite ci-dessus,
  y compris le commentaire d'entête.
- Rendre le fichier HTML complet, sans explication autour.

---

## Suivi des versions

Les anciennes versions du HTML sont archivées dans `archives/`.
Ne jamais écraser une archive : copier la version courante avant une grosse
modification de rendu.
