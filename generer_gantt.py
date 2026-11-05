#!/usr/bin/env python3
"""Génère le planning Gantt HTML 16:9 d'un projet Asana (vue timeline).

Source des données : API Asana
  - projet (lots + champs personnalisés)
  - sous-tâches de chaque lot (dates de début et de fin due_on)

Les dépendances ne sont pas des dépendances Asana natives : elles sont
reconstituées depuis la colonne texte "Blocked By (Dependencies)".

Affichage 16:9 (1920x1080) : chaque ligne est une rangée unique
(catégorie | timeline) donc les zones sont toujours alignées. Le nom de
chaque lot s'affiche à côté de sa barre ; la colonne de gauche
(catégories) reste fixe pendant le zoom et le défilement de la timeline.
Zoom par boutons ou ctrl/cmd + molette ; vue complète par défaut.
Les sous-tâches sont repliées et consultables en cliquant sur un lot.

Usage :
    python3 generer_gantt.py                 # extrait Asana et génère le HTML
    python3 generer_gantt.py --sans-extract  # régénère depuis lots.json
"""

import argparse
import json
import os
import re
import shutil
import ssl
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, timedelta

# fichiers de travail (JSON intermédiaire) à côté du script
DOSSIER = os.path.dirname(os.path.abspath(__file__))

ORDRE_SCOPES = [
    "Test",
    "Domaine A",
    "Domaine B",
    "Domaine C",
    "Domaine D",
    "Domaine E",
    "Domaine F",
    "Domaine G",
    "Domaine H",
    "Domaine I",
    "Domaine J",
]

COULEURS_SCOPES = {
    "Test": "#455a64",
}

# palette générique : appliquée aux catégories sans couleur dédiée
PALETTE_SCOPES = [
    "#5b3fa8", "#1a7fd4", "#00838f", "#2e7d32", "#9e9d24",
    "#b36b00", "#ad1457", "#6a1b9a", "#455a64", "#37474f",
]

SEUIL_ATTENTION = 8  # nb de lots en aval pour l'icone d'alerte

# surlignage jaune du nom de lot : champ personnalisé renseigné = jaune.
# Si le champ principal est absent du projet, repli sur le champ de secours.
CHAMP_SURLIGNAGE = "Surlignage"
CHAMP_SURLIGNAGE_REPLI = "Séléction"

# dossier par défaut des fichiers HTML générés (modifiable par --dossier) ;
# peut pointer vers un dossier synchronisé (share réseau, SharePoint...)
DOSSIER_SORTIE = os.path.join(DOSSIER, "sorties")

# destinations supplémentaires : chaque HTML généré y est copié (chemins
# absolus, par exemple un dossier synchronisé SharePoint). Modifiable ici
# ou par l'option --destination (répétable).
DESTINATIONS = [
    # r"/chemin/vers/dossier/partage",
]

# frise des trimestres : une couleur de base par année, déclinée en 4 teintes
# (une par trimestre, du plus sombre au plus clair) pour éviter l'arc-en-ciel.
# Couleur dédiée par année si définie, sinon palette annuelle cyclée.
COULEURS_ANNUELLES = {
    2025: "#00838f",  # sarcelle
    2026: "#1b2a6b",  # bleu nuit
    2027: "#2e7d32",  # vert
    2028: "#6a1b9a",  # violet
    2029: "#37474f",  # bleu-gris
}
PALETTE_ANNUELLES = [
    "#1b2a6b", "#2e7d32", "#6a1b9a", "#b36b00",
    "#00838f", "#ad1457", "#37474f", "#5c6bc0",
]
# nuance par trimestre : 1.0 = couleur de base, < 1 assombrit, > 1 éclaircit
NUANCES_TRIMESTRES = [0.80, 0.93, 1.08, 1.24]

# noms raccourcis pour la colonne gauche (le nom complet reste en info-bulle
# et dans les données) : utile quand une catégorie n'a que peu de lignes
AFFICHAGE_SCOPES = {
    "Communication & Téléphonie": "Téléphonie",
}


def cfval(task, name):
    for cf in task.get("custom_fields") or []:
        if cf["name"] == name:
            return cf.get("display_value") or cf.get("text_value")
    return None


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def http_get_json(opener, url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    return json.load(opener.open(req))


def extraire_asana(projet_gid, workspace_gid=None):
    """Extraction directe via l'API Asana.

    Configuration par variables d'environnement :
      - ASANA_TOKEN        : Personal Access Token (obligatoire)
      - ASANA_WORKSPACE    : GID du workspace (si non fourni via --workspace)
    """
    token = os.environ.get("ASANA_TOKEN")
    if not token:
        sys.exit("Variable ASANA_TOKEN absente : exporter le token avant l'extraction.")
    workspace_gid = workspace_gid or os.environ.get("ASANA_WORKSPACE")
    if not workspace_gid:
        sys.exit("Variable ASANA_WORKSPACE absente : indiquer le GID du workspace.")

    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    base = (
        f"https://app.asana.com/api/1.0/projects/{projet_gid}/tasks"
        "?limit=100&opt_fields=name,start_on,due_on,completed,custom_fields"
    )
    tasks, url = [], base
    while True:
        d = http_get_json(opener, url, token)
        tasks += d["data"]
        nxt = d.get("next_page")
        if not nxt:
            break
        url = base + "&offset=" + urllib.parse.quote(nxt["offset"])

    # ordre des catégories : ordre des options du champ Scope dans Asana
    ordre_scopes = []
    try:
        d = http_get_json(
            opener,
            f"https://app.asana.com/api/1.0/projects/{projet_gid}/custom_field_settings"
            "?opt_fields=custom_field.name,custom_field.enum_options.name",
            token,
        )
        for reglage in d.get("data", []):
            cf = reglage.get("custom_field") or {}
            if cf.get("name") == "Scope":
                ordre_scopes = [o["name"] for o in cf.get("enum_options") or []]
                break
    except Exception:
        pass  # ordre non disponible : repli sur l'ordre par défaut

    lots = []
    for t in tasks:
        lots.append(
            {
                "gid": t["gid"],
                "label": (cfval(t, "Numéro.Nom Lot") or t["name"]).strip(),
                "scope": cfval(t, "Scope") or "Sans scope",
                "selection": cfval(t, CHAMP_SURLIGNAGE) or cfval(t, CHAMP_SURLIGNAGE_REPLI),
                "direction": cfval(t, "Direction"),
                "materiel": cfval(t, "Matériel "),
                "start": t.get("start_on") or t.get("due_on"),
                "fin_asana": t.get("due_on"),
                "terminee": bool(t.get("completed")),
                "blocked_by": [x.strip() for x in (cfval(t, "Blocked By (Dependencies)") or "").split(",") if x.strip()],
                "sous_taches": [],
            }
        )

    for lot in lots:
        d = http_get_json(
            opener,
            f"https://app.asana.com/api/1.0/tasks/{lot['gid']}/subtasks"
            "?opt_fields=name,start_on,due_on,completed&limit=100",
            token,
        )
        for s in d["data"]:
            lot["sous_taches"].append(
                {
                    "nom": s["name"],
                    "debut": s.get("start_on"),
                    "fin": s.get("due_on") or s.get("start_on"),
                }
            )
        time.sleep(0.1)

    for lot in lots:
        lot.setdefault("alias", [])
        if lot["label"] not in lot["alias"]:
            lot["alias"].append(lot["label"])

    return lots, ordre_scopes


# champs personnalisés lus dans l'export JSON : nom logique -> libellés Asana
# (comparés sans accents ni casse). "selection" pilote le surlignage jaune :
# champ Surlignage en priorité, repli sur Séléction (comportement historique).
CHAMPS_EXPORT = {
    "label_lot": ["Numéro.Nom Lot"],
    "scope": ["Scope"],
    "fiabilite": ["Fiabilité"],
    "selection": [CHAMP_SURLIGNAGE, CHAMP_SURLIGNAGE_REPLI],
    "direction": ["Direction"],
    "materiel": ["Matériel", "Matériel "],
    "blocked_by": ["Blocked By (Dependencies)", "Bloquée par (dépendances)"],
}


def valeur_champ(cf):
    """Valeur affichable d'un champ personnalisé, quel que soit son type."""
    for cle in ("display_value", "text_value"):
        if cf.get(cle) is not None:
            return str(cf[cle])
    ev = cf.get("enum_value")
    if ev:
        return ev.get("name")
    mev = cf.get("multi_enum_values")
    if mev:
        return ", ".join(o.get("name", "") for o in mev if o.get("name"))
    nv = cf.get("number_value")
    if nv is not None:
        return str(nv)
    return None


def _lire_tache_export(t):
    """Champs standards d'une tâche d'export JSON Asana."""
    parent = t.get("parent")
    return {
        "gid": t.get("gid") or (t.get("name") or "").strip(),
        "nom": (t.get("name") or "").strip(),
        "debut": t.get("start_on") or (t.get("start_at") or "")[:10] or None,
        "fin": t.get("due_on") or (t.get("due_at") or "")[:10] or None,
        "terminee": bool(t.get("completed") or t.get("completed_at")),
        "subtasks": t.get("subtasks") or [],
        "parent": parent.get("gid") if isinstance(parent, dict) else parent,
        "deps_gids": [d.get("gid") if isinstance(d, dict) else d
                      for d in (t.get("dependencies") or [])],
        "custom_fields": t.get("custom_fields") or [],
    }


def charger_export_json(chemin):
    """Charge un export JSON du projet Asana (menu projet > Exporter > JSON).

    Renvoie (lots, ordre des scopes) : l'ordre des catégories est celui des
    options du champ Scope dans Asana (enum_options du premier champ Scope
    rencontré dans l'export).
    """
    with open(chemin, encoding="utf-8") as fh:
        doc = json.load(fh)

    taches = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(taches, list) or not taches:
        sys.exit(
            "Structure inattendue : le fichier ne contient pas de liste de tâches. "
            "Vérifier que le fichier vient bien d'un export JSON Asana "
            '(une clé "data" avec un tableau de tâches).'
        )

    lues = [_lire_tache_export(t) for t in taches if isinstance(t, dict)]
    par_gid = {t["gid"]: t for t in lues if t["gid"]}

    ordre_scopes = []
    for t in lues:
        for cf in t["custom_fields"]:
            if norm(cf.get("name") or "") == norm("Scope"):
                opts = cf.get("enum_options") or []
                if opts:
                    ordre_scopes = [o.get("name") for o in opts if o.get("name")]
                break
        if ordre_scopes:
            break

    lots_bruts, sous_par_parent = [], {}
    for t in lues:
        if not t["nom"]:
            continue
        if t["parent"] and t["parent"] in par_gid:
            sous_par_parent.setdefault(t["parent"], []).append(t)
        else:
            lots_bruts.append(t)

    if not lots_bruts:
        sys.exit("Aucune tâche de premier niveau trouvée dans l'export.")

    lots = []
    for t in lots_bruts:
        champs = {}
        for cf in t["custom_fields"]:
            champs[norm(cf.get("name") or "")] = valeur_champ(cf)

        vals, label_lot = {}, None
        for cle, variantes in CHAMPS_EXPORT.items():
            v = next((champs[norm(nom)] for nom in variantes
                      if norm(nom) in champs and champs[norm(nom)] is not None), None)
            if cle == "label_lot":
                label_lot = v
            else:
                vals[cle] = v

        # sous-tâches imbriquées, plus celles rattachées par gid (dédupliquées)
        sts = [_lire_tache_export(s) for s in t["subtasks"] if isinstance(s, dict)]
        vus = {s["gid"] for s in sts if s["gid"]}
        for s in sous_par_parent.get(t["gid"], []):
            if not s["gid"] or s["gid"] not in vus:
                sts.append(s)
                vus.add(s["gid"])

        sous_taches = []
        for s in sts:
            if not s["nom"]:
                continue
            debut = s["debut"]
            sous_taches.append({"nom": s["nom"], "debut": debut, "fin": s["fin"] or debut})

        blocked = [x.strip() for x in (vals.get("blocked_by") or "").split(",") if x.strip()]
        # dépendances natives Asana : gids -> noms de tâches
        for g in t.get("deps_gids") or []:
            cible = par_gid.get(g)
            if cible and cible["nom"]:
                blocked.append(cible["nom"])

        lots.append({
            "gid": t["gid"] or t["nom"],
            "label": label_lot or t["nom"],
            "scope": vals.get("scope") or "Sans scope",
            "selection": vals.get("selection") or None,
            "direction": vals.get("direction") or None,
            "materiel": vals.get("materiel") or None,
            "start": t["debut"],
            "fin_asana": t["fin"],
            "terminee": t["terminee"],
            "blocked_by": blocked,
            "sous_taches": sous_taches,
            "alias": [t["nom"]],
        })

    resoudre_dependances(lots)
    calculer_fins(lots)
    return lots, ordre_scopes


def resoudre_dependances(lots):
    """Reconstitue les dépendances depuis la colonne texte Blocked By.

    Chaque lot peut déclarer des alias (autres libellés sous lesquels il
    est référencé, par exemple le nom de tâche Asana dans l'export CSV).
    """
    rev_norm, rev_num = {}, {}
    for l in lots:
        libelles = [l["label"]] + list(l.get("alias") or [])
        for v in libelles:
            rev_norm.setdefault(norm(v), l["gid"])
            v2 = re.sub(r"^new\s*-\s*", "", v, flags=re.I)
            rev_norm.setdefault(norm(v2), l["gid"])
        m = re.match(r"^(?:new\s*-\s*)?(\d+(?:\.\d+)?)[\.\s]", l["label"], flags=re.I)
        if m:
            rev_num[m.group(1)] = l["gid"]

    for l in lots:
        deps = []
        for lbl in l["blocked_by"]:
            k = rev_norm.get(norm(lbl)) or rev_norm.get(norm(re.sub(r"^new\s*-\s*", "", lbl, flags=re.I)))
            if not k:
                m = re.match(r"^(?:new\s*-\s*)?(\d+(?:\.\d+)?)[\.\s]", lbl, flags=re.I)
                k = rev_num.get(m.group(1)) if m else None
            if k and k != l["gid"]:
                deps.append(k)
        l["deps"] = sorted(set(deps))
        del l["blocked_by"]


def calculer_fins(lots):
    """Date de fin du lot :
      0. due date Asana du lot (fin_asana), quand elle existe et est
         cohérente (>= début) : comportement fidèle à Asana ;
      1. plus grande date de fin réelle des sous-tâches (due_on) ;
      2. date de début du premier lot successeur, moins un jour ;
      3. début + 45 jours par défaut.
    Les sous-tâches sans fin sont des événements d'une journée :
    leur fin vaut leur début.
    """
    par_gid = {l["gid"]: l for l in lots}
    successeurs = {l["gid"]: [] for l in lots}
    for l in lots:
        for d in l["deps"]:
            if d in successeurs:
                successeurs[d].append(l["gid"])

    for l in lots:
        # due date Asana du lot : prioritaire quand cohérente. Strictement
        # postérieure au début : un lot sans début saisie prend sa due date
        # comme début (repli du chemin API), il ne faut pas le réduire à
        # un bandeau d'un jour
        fin_asana = l.get("fin_asana")
        if fin_asana and l["start"] and fin_asana > l["start"]:
            l["end"] = fin_asana
            l["fin_estimee"] = False
            continue
        fins = [s["fin"] for s in l["sous_taches"] if s["fin"]]
        fin = max(fins) if fins else None
        if fin and l["start"] and fin <= l["start"]:
            fin = None
        if not fin and l["start"]:
            debuts = [par_gid[s]["start"] for s in successeurs[l["gid"]] if par_gid[s]["start"]]
            if debuts:
                fin = (date.fromisoformat(min(debuts)) - timedelta(days=1)).isoformat()
            else:
                fin = (date.fromisoformat(l["start"]) + timedelta(days=45)).isoformat()
        if fin and l["start"] and fin <= l["start"]:
            fin = (date.fromisoformat(l["start"]) + timedelta(days=30)).isoformat()
        l["end"] = fin
        l["fin_estimee"] = not fins


def couleur_scope(index, nom):
    """Couleur d'une catégorie : palette dédiée si définie, sinon palette
    générique cyclée (utile pour des données d'exemple ou un autre projet)."""
    return COULEURS_SCOPES.get(nom) or PALETTE_SCOPES[index % len(PALETTE_SCOPES)]


def _mélanger_hex(hex_couleur, facteur):
    """Assombrit (facteur < 1) ou éclaircit (facteur > 1) une couleur #rrggbb."""
    r, g, b = (int(hex_couleur[i:i + 2], 16) for i in (1, 3, 5))
    if facteur <= 1:
        r, g, b = (int(c * facteur) for c in (r, g, b))
    else:
        f = min(facteur - 1, 1)
        r, g, b = (int(c + (255 - c) * f) for c in (r, g, b))
    return "#{:02x}{:02x}{:02x}".format(min(r, 255), min(g, 255), min(b, 255))


def _luminance(hex_couleur):
    r, g, b = (int(hex_couleur[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def couleur_trimestre(annee, trimestre):
    """Couleur d'un trimestre : gamme de l'année, nuance selon le trimestre.
    Retourne (fond, couleur du texte) pour garder un texte lisible."""
    base = COULEURS_ANNUELLES.get(annee) or PALETTE_ANNUELLES[annee % len(PALETTE_ANNUELLES)]
    fond = _mélanger_hex(base, NUANCES_TRIMESTRES[(trimestre - 1) % 4])
    return fond, ("#fff" if _luminance(fond) < 0.62 else "#222")


def ordonner_scopes(lots, ordre_scopes=None):
    """Ordre d'affichage des catégories :
      1. ordre fourni (ordre de la colonne Scope dans Asana) ;
      2. sinon ordre historique ORDRE_SCOPES ;
      3. catégories restantes par ordre alphabétique."""
    presents = {l["scope"] for l in lots}
    base = list(ordre_scopes) if ordre_scopes else ORDRE_SCOPES
    ordre = [s for s in base if s in presents]
    ordre += sorted(presents - set(ordre))
    return ordre


def axe_temporel(lots):
    """Bornes de l'axe (trimestres) ; plage par défaut si aucune date."""
    debuts = [date.fromisoformat(l["start"]) for l in lots if l["start"]]
    fins = [date.fromisoformat(l["end"]) for l in lots if l["end"]]
    if not debuts:
        annee = date.today().year
        return date(annee, 1, 1), date(annee, 12, 31)
    min_q = date(min(debuts).year, ((min(debuts).month - 1) // 3) * 3 + 1, 1)
    max_d = max(fins) if fins else max(debuts)
    max_q = date(max_d.year, ((max_d.month - 1) // 3) * 3 + 1, 1)
    return min_q, max_q


def generer_html(lots, titre="PLANNING DE PROJET", libelle_projet="", ordre_scopes=None):
    par_gid = {l["gid"]: l for l in lots}
    aval = Counter(d for l in lots for d in l["deps"])

    scopes = ordonner_scopes(lots, ordre_scopes)

    min_q, max_q = axe_temporel(lots)

    def trimestre_suivant(d):
        return date(d.year + (1 if d.month > 9 else 0), ((d.month - 1) // 3) * 3 + 4 if d.month <= 9 else 1, 1)

    def debut_trimestre(d):
        return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)

    def fin_trimestre(d):
        return trimestre_suivant(debut_trimestre(d)) - timedelta(days=1)

    quarters = []
    q = min_q
    while q <= max_q:
        quarters.append(q)
        q = trimestre_suivant(q)

    t0 = min_q
    t1 = trimestre_suivant(max_q)
    span = (t1 - t0).days

    def x(iso):
        return round(((date.fromisoformat(iso) - t0).days / span) * 100, 3)

    # hauteurs entières : les trois zones partagent exactement les mêmes lignes
    h_dispo = 891
    h_groupe = 26  # ligne de catégorie plus épaisse, comme sur le PowerPoint
    n_lots_aff = max(len(lots), 1)
    h_lot = (h_dispo - len(scopes) * h_groupe) // n_lots_aff
    h_bonus = (h_dispo - len(scopes) * h_groupe) - h_lot * len(lots)  # pixels restants

    qcols = "".join(
        (lambda fond, txt: f'<div class="qcol" style="background:{fond};color:{txt}">'
         f'<span>T{(q.month - 1) // 3 + 1}</span><span>{q.year}</span></div>')(
            *couleur_trimestre(q.year, (q.month - 1) // 3 + 1))
        for q in quarters
    )
    qgrid = "".join(
        f'<div class="qsep{" q1" if q.month == 1 else ""}" style="left:{x(q.isoformat())}%"></div>'
        for q in quarters[1:]
    )

    rows = []
    i_bonus = 0
    for i_s, s in enumerate(scopes):
        c = couleur_scope(i_s, s)
        sous = [l for l in lots if l["scope"] == s]
        sous.sort(key=lambda l: (l["start"] or "9999", l["label"]))
        h = h_groupe + (1 if i_bonus < h_bonus else 0)
        i_bonus += 1
        nom_affiche = AFFICHAGE_SCOPES.get(s, s)
        # taille de police adaptée pour tenir sur une ligne dans la colonne gauche
        font_g = min(12.5, round(138 / (0.58 * max(len(nom_affiche), 1)), 1))
        font_g = max(font_g, 7.5)
        # bandeau de catégorie : du début du trimestre de la première tâche
        # du groupe à la fin du trimestre de sa dernière tâche
        debs_g = [date.fromisoformat(l["start"]) for l in sous if l["start"]]
        fins_g = [date.fromisoformat(l["end"]) for l in sous if l["end"]]
        bande = ""
        if debs_g and fins_g:
            g_left = x(debut_trimestre(min(debs_g)).isoformat())
            g_right = x(fin_trimestre(max(fins_g)).isoformat())
            bande = (
                f'<span class="bande" style="background:{c};'
                f'left:{g_left}%;width:{max(g_right - g_left, 0.15)}%"></span>'
            )
        rows.append(
            f'<div class="row groupe-row" style="height:{h}px">'
            f'<div class="cell-g" style="background:{c};font-size:{font_g}px" title="{s}">{nom_affiche}</div>'
            f'<div class="cell-tl">{bande}</div></div>'
        )
        for l in sous:
            h = h_lot + (1 if i_bonus < h_bonus else 0)
            i_bonus += 1
            lab = re.sub(r"^new\s*-\s*", "", l["label"], flags=re.I)
            m = re.match(r"^(\d+(?:\.\d+)?)[\.\s]+(.*)$", lab)
            num, nom = (m.group(1), m.group(2)) if m else ("", lab)
            alerte = aval[l["gid"]] >= SEUIL_ATTENTION
            jaune = bool(l["selection"])
            nb_st = len(l["sous_taches"])

            bar, lab_inline, losange = "", "", ""
            if l["start"]:
                left, right = x(l["start"]), x(l["end"])
                bar = (
                    f'<span class="barre" style="left:{left}%;width:{max(right - left, 0.12)}%;'
                    f'background:{c}"></span>'
                )
                if alerte:
                    losange = f'<span class="alerte" style="left:{left}%" title="impact fort sur d\'autres lots">✚</span>'
                lbl_cls = "lab jaune" if jaune else "lab"
                st = f'<span class="st-btn">▸ {nb_st}</span>' if nb_st else ""
                if right < 80:
                    lab_inline = f'<span class="{lbl_cls}" style="left:{right + 0.3}%;border-color:{c}">{num} {nom}{st}</span>'
                else:
                    lab_inline = f'<span class="{lbl_cls} lab-inv" style="left:{left - 0.3}%;border-color:{c}">{num} {nom}{st}</span>'
            else:
                st = f'<span class="st-btn">▸ {nb_st}</span>' if nb_st else ""
                lab_inline = f'<span class="lab a-dater" style="left:0.4%;border-color:#999">{num} {nom} (à dater){st}</span>'

            rows.append(
                f'<div class="row lot-row" data-lot="{l["gid"]}" style="height:{h}px">'
                f'<div class="cell-g"></div>'
                f'<div class="cell-tl">{losange}{bar}{lab_inline}</div></div>'
            )

    data_lots = []
    for l in lots:
        data_lots.append(
            {
                "id": l["gid"],
                "libelle": l["label"],
                "scope": l["scope"],
                "debut": l["start"],
                "fin": l["end"],
                "fin_estimee": l["fin_estimee"],
                "selection": l["selection"],
                "direction": l["direction"],
                "materiel": l["materiel"],
                "terminee": l.get("terminee", False),
                "dependances": [par_gid[d]["label"] for d in l["deps"]],
                "impact": aval[l["gid"]],
                "sous_taches": l["sous_taches"],
            }
        )

    data = {
        "titre": titre,
        "projet_asana": libelle_projet,
        "date_generation": date.today().isoformat(),
        "nb_lots": len(lots),
        "nb_lots_dates": sum(1 for l in lots if l["start"]),
        "nb_lots_fin_estimee": sum(1 for l in lots if l["fin_estimee"]),
        "nb_dependances": sum(len(l["deps"]) for l in lots),
        "scopes": scopes,
        "lots": data_lots,
    }

    bloc_data = json.dumps(data, ensure_ascii=False, indent=1)

    stats = (
        f'<div class="stats"><b>{data["nb_lots"]} lots</b> dont {data["nb_lots_dates"]} datés'
        f'<br>{data["nb_dependances"]} liens de dépendance'
        f'<br><span class="petit">{data["nb_lots_fin_estimee"]} fins de barres estimées</span></div>'
    )

    html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Planning de projet</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: 1920px; height: 1080px; overflow: hidden; font-family: Arial, Helvetica, sans-serif; background: #fff; }}
  .page {{ width: 1920px; height: 1080px; padding: 20px 30px 12px; display: flex; flex-direction: column; }}
  .titre {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }}
  h1 {{ font-size: 26px; letter-spacing: 1px; color: #111; flex: 1; text-align: center; }}
  .zoom {{ display: flex; gap: 6px; align-items: center; }}
  .zoom button {{ width: 32px; height: 32px; font-size: 19px; border: 1px solid #999; background: #fff; border-radius: 4px; cursor: pointer; }}
  .zoom button:hover {{ background: #eee; }}
  .zoom .niveau {{ font-size: 12px; color: #555; width: 40px; text-align: center; }}
  .gantt {{ flex: 1; display: flex; flex-direction: column; border: 1px solid #d5d5d5; min-height: 0; position: relative; }}
  .corps {{ flex: 1; min-height: 0; position: relative; }}
  .scroll {{ position: absolute; inset: 0; overflow-x: auto; overflow-y: hidden; }}
  .scroll::-webkit-scrollbar {{ height: 9px; }}
  .scroll::-webkit-scrollbar-thumb {{ background: #b9b9d0; border-radius: 5px; }}
  .inner {{ position: relative; min-width: 100%; height: 100%; }}

  /* en-tête trimestres : dans le conteneur scrollable, collé en haut,
     donc il suit le défilement horizontal de la timeline */
  .entete-tl {{ display: flex; height: 44px; position: sticky; top: 0; z-index: 10; }}
  .h-g {{ width: 150px; flex: none; background: #1f1f38; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: bold; position: sticky; left: 0; z-index: 11; }}
  .qcol {{ flex: 1; border-left: 1px solid rgba(255,255,255,.35); display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 15px; font-weight: bold; line-height: 1.2; color: #fff; text-shadow: 0 1px 1px rgba(0,0,0,.35); }}
  .qcol:nth-child(2) {{ border-left: none; }}

  /* grille trimestrielle, derrière les lignes, sous l'en-tête */
  .grid {{ position: absolute; left: 150px; right: 0; top: 44px; bottom: 9px; z-index: 0; }}
  .qsep {{ position: absolute; top: 0; bottom: 0; width: 1px; background: #ececf2; }}
  .qsep.q1 {{ background: #c4c4d4; }}

  .rowsbox {{ position: relative; z-index: 1; }}
  .row {{ display: flex; border-bottom: 1px solid #f4f4f4; }}
  .row.groupe-row {{ border-bottom: none; }}
  .cell-g {{ width: 150px; flex: none; background: #fff; color: #fff; font-size: 12px; font-weight: bold; display: flex; align-items: center; padding-left: 8px; position: sticky; left: 0; z-index: 5; border-right: 1px solid #d5d5d5; }}
  .groupe-row .cell-g {{ justify-content: center; padding-left: 0; font-size: 12.5px; }}
  .cell-tl {{ flex: 1; min-width: 0; position: relative; overflow: hidden; }}
  .groupe-row .cell-tl {{ display: flex; align-items: center; }}
  .bande {{ position: absolute; top: 25%; bottom: 25%; border-radius: 2px; }}
  .lot-row {{ cursor: pointer; }}
  .lot-row:hover .cell-tl {{ background: #f6f8ff; }}
  .barre {{ position: absolute; top: 50%; transform: translateY(-50%); height: 3.5px; border-radius: 2px; }}
  .alerte {{ position: absolute; top: 50%; transform: translate(-55%, -50%); color: #e01414; font-size: 11px; font-weight: bold; z-index: 3; }}
  .lab {{ position: absolute; top: 50%; transform: translateY(-50%); font-size: 10px; white-space: nowrap; padding: 0 3px; border-left: 2.5px solid; background: #fff; z-index: 2; display: inline-flex; align-items: center; gap: 4px; }}
  .lab-inv {{ transform: translate(-100%, -50%); border-left: none; border-right: 2.5px solid; text-align: right; }}
  .lab.jaune {{ background: #f7e733; }}
  .lab.a-dater {{ font-style: italic; color: #777; }}
  .st-btn {{ font-size: 9px; color: #555; background: #ececec; border-radius: 3px; padding: 0 3px; }}

  .panneau {{ position: absolute; left: 170px; width: 560px; background: #fafbff; border: 1px solid #c9d2f0; border-radius: 4px; z-index: 30; padding: 10px 14px; font-size: 12px; box-shadow: 0 4px 14px rgba(20,20,60,.18); max-height: 430px; overflow: auto; }}
  .panneau h3 {{ font-size: 14px; margin-bottom: 6px; }}
  .panneau table {{ border-collapse: collapse; width: 100%; margin-top: 6px; }}
  .panneau td, .panneau th {{ text-align: left; padding: 2px 10px 2px 0; white-space: nowrap; font-size: 11.5px; }}
  .panneau th {{ color: #666; font-weight: normal; font-size: 11px; }}
  .panneau .dep {{ color: #444; }}
  .fermer {{ float: right; cursor: pointer; color: #888; font-size: 16px; }}

  .pied {{ display: flex; align-items: flex-start; justify-content: space-between; margin-top: 8px; font-size: 12.5px; color: #333; }}
  .stats {{ border: 1px solid #c33; color: #c33; padding: 8px 16px; text-align: center; font-size: 13px; border-radius: 4px; }}
  .stats .petit {{ font-size: 10.5px; color: #888; }}
  .legende span {{ display: inline-block; margin-right: 12px; }}
  .legende i {{ display: inline-block; width: 24px; height: 4px; border-radius: 2px; vertical-align: middle; margin-right: 5px; }}
  .legende b {{ color: #e01414; }}
</style>
</head>
<body>
<div class="page">
  <div class="titre">
    <div style="width:220px"></div>
    <h1>{data["titre"]}</h1>
    <div class="zoom">
      <button id="zmoins" title="dézoomer (1x = vue complète de tous les trimestres)">−</button>
      <span class="niveau" id="zniveau">1×</span>
      <button id="zplus" title="zoomer">+</button>
    </div>
  </div>
  <div class="gantt">
    <div class="corps">
      <div class="scroll" id="scroll">
        <div class="inner" id="inner">
          <div class="entete-tl"><div class="h-g">LOTS</div>{qcols}</div>
          <div class="grid">{qgrid}</div>
          <div class="rowsbox" id="rows">{"".join(rows)}</div>
        </div>
      </div>
      <div id="panneau-host" style="position:absolute;inset:0;pointer-events:none;z-index:30"></div>
    </div>
  </div>
  <div class="pied">
    <div class="legende">
      <span><i style="background:#00b4e6"></i>barre = période du lot</span>
      <span style="background:#f7e733;padding:1px 6px;">surligné = champ Sélection renseigné</span>
      <span><b>✚</b> impact fort (≥ {SEUIL_ATTENTION} lots en aval)</span>
      <span>▸ n : sous-tâches repliées, cliquer sur un lot pour les consulter</span>    </div>
    {stats}
  </div>
</div>
<script>
/* ================================================================
   DONNÉES DU PLANNING
   Bloc à remplacer pour mettre à jour le fichier :
   1. extraire le CSV du projet Asana (tâches : nom, début, fin,
      section, colonnes personnalisées) ;
   2. demander à l'assistant local de régénérer le bloc DATA ci-dessous
      à partir du CSV, en respectant exactement la même structure.
   Champs d'un lot :
     id            identifiant (gid Asana ou numéro de ligne)
     libelle       "numéro. nom du lot"
     scope         catégorie affichée à gauche (regroupement)
     debut         AAAA-MM-JJ (null si à dater)
     fin           AAAA-MM-JJ (null = barre de 45 jours)
     fin_estimee   true si la fin n'est pas une donnée réelle
     selection     toute valeur = surlignage jaune
     direction     direction porteuse (info)
     materiel      "oui" / "non" (info)
     dependances   liste des libellés des lots antécédents
     impact        nombre de lots en aval (calculé)
     sous_taches   [{{nom, debut, fin}}] (fins = dates réelles Asana,
                   événements d'une journée : fin = début)
   ================================================================ */
const DATA = {bloc_data};

const parId = {{}};
DATA.lots.forEach(l => parId[l.id] = l);

/* ---------- zoom : la timeline seule, colonnes fixes ---------- */
const scroll = document.getElementById('scroll');
const inner = document.getElementById('inner');
const zniveau = document.getElementById('zniveau');
let zoom = 1;
function appliquerZoom() {{
  inner.style.width = (zoom * 100) + '%';
  zniveau.textContent = zoom.toFixed(zoom < 10 ? 1 : 0) + '×';
}}
document.getElementById('zplus').addEventListener('click', () => {{ zoom = Math.min(zoom * 2, 16); appliquerZoom(); }});
document.getElementById('zmoins').addEventListener('click', () => {{
  zoom = zoom / 2 <= 1 ? 1 : zoom / 2; appliquerZoom();
}});
scroll.addEventListener('wheel', e => {{
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  const avant = inner.getBoundingClientRect().width;
  zoom = Math.min(16, Math.max(1, zoom * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
  appliquerZoom();
  const apres = inner.getBoundingClientRect().width;
  scroll.scrollLeft = scroll.scrollLeft * (apres / avant);
}}, {{ passive: false }});

let drag = null;
scroll.addEventListener('mousedown', e => {{
  if (zoom <= 1) return;
  drag = {{ x: e.clientX, s: scroll.scrollLeft }};
  scroll.style.cursor = 'grabbing';
}});
window.addEventListener('mousemove', e => {{
  if (drag) scroll.scrollLeft = drag.s - (e.clientX - drag.x);
}});
window.addEventListener('mouseup', () => {{ drag = null; scroll.style.cursor = ''; }});

/* ---------- sous-tâches (repliées par défaut) ---------- */
const host = document.getElementById('panneau-host');
const rows = document.getElementById('rows');
function ouvrirPanneau(gid, ligneRef) {{
  host.innerHTML = '';
  const lot = parId[gid];
  if (!lot) return;
  const st = (lot.sous_taches || []).map(s =>
    `<tr><td>${{s.nom}}</td><td>${{s.debut || ''}}</td><td>${{s.fin || ''}}</td></tr>`
  ).join('');
  const deps = (lot.dependances || []).join(', ') || 'aucune';
  const p = document.createElement('div');
  p.className = 'panneau';
  p.style.pointerEvents = 'auto';
  p.style.top = Math.min(ligneRef.offsetTop + 14, 360) + 'px';
  p.innerHTML =
    `<span class="fermer" title="refermer">✕</span>` +
    `<h3>${{lot.libelle}}</h3>` +
    `<div class="dep"><b>Dépendances :</b> ${{deps}}</div>` +
    `<table><tr><th>Sous-tâche</th><th>Début</th><th>Fin</th></tr>${{st}}</table>`;
  p.querySelector('.fermer').addEventListener('click', () => host.innerHTML = '');
  p.addEventListener('click', e => e.stopPropagation());
  host.appendChild(p);
}}
document.querySelectorAll('.lot-row').forEach(el => {{
  el.addEventListener('click', e => {{
    e.stopPropagation();
    ouvrirPanneau(el.dataset.lot, el);
  }});
}});
document.addEventListener('click', () => host.innerHTML = '');
appliquerZoom();
</script>
</body>
</html>
"""

    return html


def construire_lots_test():
    """Catégorie de démonstration : 4 tâches couvrant les 3 types de liaison.
    TEST1 -FS-> TEST2 -FS-> TEST3 -SS-> TEST4, TEST2 -FF-> TEST4."""
    base = {"scope": "Test", "selection": None, "direction": None, "materiel": None,
            "fin_estimee": False, "sous_taches": [], "alias": []}
    return [
        {**base, "gid": "TEST1", "label": "TEST 1 : fin pour commencer",
         "start": "2026-07-01", "end": "2026-09-30", "deps": []},
        {**base, "gid": "TEST2", "label": "TEST 2 : commence après la fin de TEST 1 (FS)",
         "start": "2026-10-01", "end": "2026-12-31", "deps": ["TEST1"]},
        {**base, "gid": "TEST3", "label": "TEST 3 : commence après la fin de TEST 2 (FS)",
         "start": "2027-01-01", "end": "2027-03-31", "deps": ["TEST2"]},
        {**base, "gid": "TEST4", "label": "TEST 4 : démarre avec TEST 3 (SS) et finit après la fin de TEST 2 (FF)",
         "start": "2027-01-01", "end": "2027-12-31", "deps": ["TEST3", "TEST2"]},
    ]


EDGES_TEST = [
    ["TEST1", "TEST2", "FS"],
    ["TEST2", "TEST3", "FS"],
    ["TEST3", "TEST4", "SS"],
    ["TEST2", "TEST4", "FF"],
]


def generer_html_detail(lots, titre="PLANNING DE PROJET : DÉTAIL", libelle_projet="", ordre_scopes=None):
    """Vue Gantt détaillée : lots + sous-tâches dépliées, avec flèches de
    dépendances tracées (SVG) comme sur un vrai Gantt.

    Deux niveaux de flèches :
      - lot -> lot : dépendances reconstituées (colonne "Blocked By") ;
      - sous-tâche -> sous-tâche : chaînage dérivé de l'ordre des dates au
        sein de chaque lot (Asana ne porte pas de dépendances natives entre
        sous-tâches de ce projet).
    """
    lots_aff = construire_lots_test() + lots
    par_gid = {l["gid"]: l for l in lots_aff}
    aval = Counter(d for l in lots_aff for d in l["deps"])
    scopes = ordonner_scopes(lots_aff, ordre_scopes)

    debuts = [date.fromisoformat(l["start"]) for l in lots_aff if l["start"]]
    min_q = date(min(debuts).year, ((min(debuts).month - 1) // 3) * 3 + 1, 1)
    max_d = max(date.fromisoformat(l["end"]) for l in lots_aff if l["end"])
    max_q = date(max_d.year, ((max_d.month - 1) // 3) * 3 + 1, 1)

    def trimestre_suivant(d):
        return date(d.year + (1 if d.month > 9 else 0), ((d.month - 1) // 3) * 3 + 4 if d.month <= 9 else 1, 1)

    def debut_trimestre(d):
        return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)

    def fin_trimestre(d):
        return trimestre_suivant(debut_trimestre(d)) - timedelta(days=1)

    quarters = []
    q = min_q
    while q <= max_q:
        quarters.append(q)
        q = trimestre_suivant(q)

    t0 = min_q
    t1 = trimestre_suivant(max_q)
    span = (t1 - t0).days

    def x(iso):
        return round(((date.fromisoformat(iso) - t0).days / span) * 100, 3)

    h_groupe = 26
    h_lot = 18
    h_st = 13

    qcols = "".join(
        (lambda fond, txt: f'<div class="qcol" style="background:{fond};color:{txt}">'
         f'<span>T{(q.month - 1) // 3 + 1}</span><span>{q.year}</span></div>')(
            *couleur_trimestre(q.year, (q.month - 1) // 3 + 1))
        for q in quarters
    )
    qgrid = "".join(
        f'<div class="qsep{" q1" if q.month == 1 else ""}" style="left:{x(q.isoformat())}%"></div>'
        for q in quarters[1:]
    )

    rows = []
    edges_lots = []
    edges_chaines = []
    for l in lots:
        for d in l["deps"]:
            edges_lots.append([d, l["gid"]])

    i_bonus = 0
    for i_s, s in enumerate(scopes):
        c = couleur_scope(i_s, s)
        sous = [l for l in lots_aff if l["scope"] == s]
        sous.sort(key=lambda l: (l["start"] or "9999", l["label"]))
        nom_affiche = AFFICHAGE_SCOPES.get(s, s)
        font_g = min(12.5, round(138 / (0.58 * max(len(nom_affiche), 1)), 1))
        font_g = max(font_g, 7.5)
        debs_g = [date.fromisoformat(l["start"]) for l in sous if l["start"]]
        fins_g = [date.fromisoformat(l["end"]) for l in sous if l["end"]]
        bande = ""
        if debs_g and fins_g:
            g_left = x(debut_trimestre(min(debs_g)).isoformat())
            g_right = x(fin_trimestre(max(fins_g)).isoformat())
            bande = (
                f'<span class="bande" style="background:{c};'
                f'left:{g_left}%;width:{max(g_right - g_left, 0.15)}%"></span>'
            )
        rows.append(
            f'<div class="row groupe-row" style="height:{h_groupe}px">'
            f'<div class="cell-g" style="background:{c};font-size:{font_g}px" title="{s}">{nom_affiche}</div>'
            f'<div class="cell-tl">{bande}</div></div>'
        )
        for l in sous:
            lab = re.sub(r"^new\s*-\s*", "", l["label"], flags=re.I)
            m = re.match(r"^(\d+(?:\.\d+)?)[\.\s]+(.*)$", lab)
            num, nom = (m.group(1), m.group(2)) if m else ("", lab)
            alerte = aval[l["gid"]] >= SEUIL_ATTENTION
            jaune = bool(l["selection"])
            nb_st = len(l["sous_taches"])

            bar, lab_inline, losange = "", "", ""
            if l["start"]:
                left, right = x(l["start"]), x(l["end"])
                bar = (
                    f'<span class="barre" style="left:{left}%;width:{max(right - left, 0.12)}%;'
                    f'background:{c}"></span>'
                )
                if alerte:
                    losange = f'<span class="alerte" style="left:{left}%" title="impact fort sur d\'autres lots">✚</span>'
                lbl_cls = "lab jaune" if jaune else "lab"
                chev = f'<span class="chev" title="plier / déplier les sous-tâches">▾ {nb_st}</span>' if nb_st else ""
                if right < 80:
                    lab_inline = f'<span class="{lbl_cls}" style="left:{right + 0.3}%;border-color:{c}">{num} {nom}{chev}</span>'
                else:
                    lab_inline = f'<span class="{lbl_cls} lab-inv" style="left:{left - 0.3}%;border-color:{c}">{num} {nom}{chev}</span>'
            else:
                chev = f'<span class="chev" title="plier / déplier les sous-tâches">▾ {nb_st}</span>' if nb_st else ""
                lab_inline = f'<span class="lab a-dater" style="left:0.4%;border-color:#999">{num} {nom} (à dater){chev}</span>'

            rows.append(
                f'<div class="row lot-row" id="L{l["gid"]}" data-lot="{l["gid"]}" style="height:{h_lot}px">'
                f'<div class="cell-g"></div>'
                f'<div class="cell-tl">{losange}{bar}{lab_inline}</div></div>'
            )

            # sous-tâches : triées par date, chaînage dérivé de l'ordre
            sts = sorted(
                enumerate(l["sous_taches"]),
                key=lambda t: (t[1]["debut"] or t[1]["fin"] or "9999-99-99", t[1]["fin"] or t[1]["debut"] or "9999-99-99"),
            )
            pred_id = None
            for i, (_, st) in enumerate(sts):
                rid = f"S{l['gid']}-{i}"
                nom_st = st["nom"]
                titre = f'{nom_st} ({st["debut"] or "?"} -> {st["fin"] or "?"})'
                # une sous-tâche est datée si elle a un début OU une fin ;
                # sans date de début (ou de fin), c'est un événement d'un jour
                # placé sur la date disponible ; barre si les deux existent
                if st["debut"] and st["fin"] and st["fin"] > st["debut"]:
                    left, right = x(st["debut"]), x(st["fin"])
                    bar_st = f'<span class="barre st-barre" style="left:{left}%;width:{max(right - left, 0.12)}%;background:{c}"></span>'
                    lab_st = f'<span class="lab st-lab" style="left:{min(right + 0.25, 84)}%">{nom_st}</span>' if right < 84 else ""
                    rows.append(
                        f'<div class="row st-row" id="{rid}" style="height:{h_st}px">'
                        f'<div class="cell-g"></div><div class="cell-tl">{bar_st}{lab_st}</div></div>'
                    )
                elif st["debut"] or st["fin"]:
                    d1 = st["debut"] or st["fin"]
                    bar_st = f'<span class="marqueur" style="left:{x(d1)}%;background:{c}"></span>'
                    lab_st = f'<span class="lab st-lab" style="left:{min(x(d1) + 0.3, 84)}%">{nom_st}</span>'
                    rows.append(
                        f'<div class="row st-row" id="{rid}" style="height:{h_st}px">'
                        f'<div class="cell-g"></div><div class="cell-tl">{bar_st}{lab_st}</div></div>'
                    )
                else:
                    rows.append(
                        f'<div class="row st-row" id="{rid}" style="height:{h_st}px">'
                        f'<div class="cell-g"></div><div class="cell-tl">'
                        f'<span class="lab st-lab sans-date" style="left:0.4%">{nom_st} (non datée)</span></div></div>'
                    )
                if st["debut"] or st["fin"]:
                    if pred_id:
                        edges_chaines.append([pred_id, rid])
                    pred_id = rid

    data_lots = []
    for l in lots_aff:
        data_lots.append(
            {
                "id": l["gid"],
                "libelle": l["label"],
                "scope": l["scope"],
                "debut": l["start"],
                "fin": l["end"],
                "fin_estimee": l["fin_estimee"],
                "selection": l["selection"],
                "direction": l["direction"],
                "materiel": l["materiel"],
                "terminee": l.get("terminee", False),
                "dependances": [par_gid[d]["label"] for d in l["deps"]],
                "impact": aval[l["gid"]],
                "sous_taches": l["sous_taches"],
            }
        )

    data = {
        "titre": titre,
        "projet_asana": libelle_projet,
        "date_generation": date.today().isoformat(),
        "nb_lots": len(lots),
        "nb_lots_demo": len(lots_aff) - len(lots),
        "nb_sous_taches": sum(len(l["sous_taches"]) for l in lots),
        "nb_dependances_lots": len(edges_lots),
        "nb_chainages": len(edges_chaines),
        "scopes": scopes,
        "lots": data_lots,
    }
    bloc_data = json.dumps(data, ensure_ascii=False, indent=1)
    edges = json.dumps({"lots": edges_lots, "chaines": edges_chaines, "tests": EDGES_TEST})

    html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Planning de projet : détail</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: 1920px; height: 1080px; overflow: hidden; font-family: Arial, Helvetica, sans-serif; background: #fff; }}
  .page {{ width: 1920px; height: 1080px; padding: 20px 30px 12px; display: flex; flex-direction: column; }}
  .titre {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }}
  h1 {{ font-size: 24px; letter-spacing: 1px; color: #111; flex: 1; text-align: center; }}
  .zoom {{ display: flex; gap: 6px; align-items: center; }}
  .zoom button {{ width: 32px; height: 32px; font-size: 19px; border: 1px solid #999; background: #fff; border-radius: 4px; cursor: pointer; }}
  .zoom .niveau {{ font-size: 12px; color: #555; width: 40px; text-align: center; }}
  .gantt {{ flex: 1; display: flex; flex-direction: column; border: 1px solid #d5d5d5; min-height: 0; position: relative; }}
  .corps {{ flex: 1; min-height: 0; position: relative; }}
  .scroll {{ position: absolute; inset: 0; overflow: auto; }}
  .scroll::-webkit-scrollbar {{ width: 9px; height: 9px; }}
  .scroll::-webkit-scrollbar-thumb {{ background: #b9b9d0; border-radius: 5px; }}
  .inner {{ position: relative; min-width: 100%; min-height: 100%; }}
  .entete-tl {{ display: flex; height: 44px; position: sticky; top: 0; z-index: 10; }}
  .h-g {{ width: 190px; flex: none; background: #1f1f38; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: bold; position: sticky; left: 0; z-index: 11; }}
  .qcol {{ flex: 1; border-left: 1px solid rgba(255,255,255,.35); display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 15px; font-weight: bold; line-height: 1.2; color: #fff; text-shadow: 0 1px 1px rgba(0,0,0,.35); }}
  .qcol:nth-child(2) {{ border-left: none; }}
  .grid {{ position: absolute; left: 190px; right: 0; top: 44px; bottom: 0; z-index: 0; }}
  .qsep {{ position: absolute; top: 0; bottom: 0; width: 1px; background: #ececf2; }}
  .qsep.q1 {{ background: #c4c4d4; }}
  .rowsbox {{ position: relative; z-index: 1; }}
  .row {{ display: flex; border-bottom: 1px solid #f4f4f4; }}
  .row.groupe-row {{ border-bottom: none; }}
  .cell-g {{ width: 190px; flex: none; background: #fff; color: #fff; font-size: 12px; font-weight: bold; display: flex; align-items: center; padding-left: 8px; position: sticky; left: 0; z-index: 5; border-right: 1px solid #d5d5d5; }}
  .groupe-row .cell-g {{ justify-content: center; padding-left: 0; font-size: 12.5px; }}
  .cell-tl {{ flex: 1; min-width: 0; position: relative; overflow: hidden; }}
  .groupe-row .cell-tl {{ display: flex; align-items: center; }}
  .bande {{ position: absolute; top: 25%; bottom: 25%; border-radius: 2px; }}
  .lot-row {{ cursor: pointer; }}
  .lot-row:hover .cell-tl {{ background: #f6f8ff; }}
  .lot-row.sel .cell-tl {{ background: #fff3bf; }}
  .lot-row.dim .barre, .lot-row.dim .lab, .lot-row.dim .alerte {{ opacity: .22; }}
  .st-row.dim .barre, .st-row.dim .marqueur, .st-row.dim .lab {{ opacity: .18; }}
  .st-row .cell-g {{ border-right-color: #eee; }}
  .st-row .cell-tl {{ background: #fcfcfe; }}
  .barre {{ position: absolute; top: 50%; transform: translateY(-50%); height: 3.5px; border-radius: 2px; }}
  .st-barre {{ height: 3px; opacity: .75; }}
  .marqueur {{ position: absolute; top: 50%; width: 7px; height: 7px; transform: translate(-50%, -50%) rotate(45deg); border: 1px solid rgba(0,0,0,.25); }}
  .alerte {{ position: absolute; top: 50%; transform: translate(-55%, -50%); color: #e01414; font-size: 11px; font-weight: bold; z-index: 6; }}
  .lab {{ position: absolute; top: 50%; transform: translateY(-50%); font-size: 10px; white-space: nowrap; padding: 0 3px; border-left: 2.5px solid; background: #fff; z-index: 5; display: inline-flex; align-items: center; gap: 4px; }}
  .lab-inv {{ transform: translate(-100%, -50%); border-left: none; border-right: 2.5px solid; text-align: right; }}
  .lab.jaune {{ background: #f7e733; }}
  .lab.a-dater {{ font-style: italic; color: #777; }}
  .st-lab {{ font-size: 9px; border-left-width: 2px; color: #333; }}
  .st-lab.sans-date {{ color: #999; font-style: italic; }}
  .chev {{ font-size: 9px; color: #555; background: #ececec; border-radius: 3px; padding: 0 3px; }}
  .svg-fleches {{ position: absolute; inset: 0; pointer-events: none; z-index: 4; }}
  .svg-fleches path.vis {{ pointer-events: none; }}
  .svg-fleches path.vis.hov {{ stroke-width: 3; filter: drop-shadow(0 0 2px rgba(0,0,0,.45)); }}
  .svg-fleches path.hit {{ pointer-events: stroke; stroke: transparent; stroke-width: 9; fill: none; cursor: pointer; }}
  .lot-row.lie .cell-tl {{ background: #e3ebff; }}
  .lot-row.lie .barre {{ height: 6px; }}
  .cache {{ display: none; }}
  .pied {{ display: flex; align-items: flex-start; justify-content: space-between; margin-top: 8px; font-size: 12px; color: #333; }}
  .stats {{ border: 1px solid #c33; color: #c33; padding: 6px 14px; text-align: center; font-size: 12.5px; border-radius: 4px; }}
  .legende span {{ display: inline-block; margin-right: 12px; }}
  .legende i {{ display: inline-block; width: 24px; height: 4px; border-radius: 2px; vertical-align: middle; margin-right: 5px; }}
  .legende b {{ color: #e01414; }}
  .legende .f1 {{ color: #444; }}
  .legende .f2 {{ color: #aaa; }}
</style>
</head>
<body>
<div class="page">
  <div class="titre">
    <div style="width:220px"></div>
    <h1>{data["titre"]}</h1>
    <div class="zoom">
      <button id="zmoins" title="dézoomer (1x = vue complète)">−</button>
      <span class="niveau" id="zniveau">1×</span>
      <button id="zplus" title="zoomer">+</button>
      <button id="raz" title="effacer les flèches de dépendance">✕</button>
    </div>
  </div>
  <div class="gantt">
    <div class="corps">
      <div class="scroll" id="scroll">
        <div class="inner" id="inner">
          <div class="entete-tl"><div class="h-g">LOTS &amp; SOUS-TÂCHES</div>{qcols}</div>
          <div class="grid">{qgrid}</div>
          <div class="rowsbox" id="rows">{"".join(rows)}
            <svg class="svg-fleches" id="fleches"></svg>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="pied">
    <div class="legende">
      <span><i style="background:#00b4e6"></i>lot</span>
      <span><i style="background:#00b4e6;opacity:.6"></i>sous-tâche</span>
      <span><span class="f1">→</span> dépendance lot &gt; lot (colonne Blocked By)</span>
      <span style="color:#1a7f37">→ SS : début pour commencer</span>
      <span style="color:#b36b00">→ FF : fin pour finir</span>
      <span style="color:#c62828">→ rouge : incohérence de dates (successeur avant le début de son prédécesseur)</span>
      <span style="color:#666">→ pointillé : tâche en amont terminée (ne bloque plus)</span>
      <span><span class="f2">→</span> enchaînement des sous-tâches du lot sélectionné (ordre des dates)</span>
      <span><b>✚</b> impact fort (≥ {SEUIL_ATTENTION} lots en aval)</span>
      <span>cliquer sur un lot : ses liens directs ; cliquer un lot surligné : étendre ; ✕ / Échap : effacer</span>
      <span>survolez une flèche : elle s'épaissit et les deux lots reliés sont surlignés</span>
      <span>cliquer sur ▸ n : plier / déplier les sous-tâches</span>
    </div>
    <div class="stats"><b>{data["nb_lots"]} lots</b> (+{data["nb_lots_demo"]} de démonstration) / {data["nb_sous_taches"]} sous-tâches<br>{data["nb_dependances_lots"]} dépendances + {data["nb_chainages"]} enchaînements</div>
  </div>
</div>
<script>
/* ================================================================
   DONNÉES DU PLANNING (voir le fichier principal pour la structure)
   ================================================================ */
const DATA = {bloc_data};

/* flèches : [idLignePredecesseur, idLigneSuccesseur] ; les liens de la
   catégorie Test portent un type de liaison : FS, SS ou FF */
const EDGES = {edges};

/* ---------- zoom / déplacement (identique à la vue synthèse) ---------- */
const scroll = document.getElementById('scroll');
const inner = document.getElementById('inner');
const zniveau = document.getElementById('zniveau');
let zoom = 1;
function appliquerZoom() {{
  inner.style.width = (zoom * 100) + '%';
  zniveau.textContent = zoom.toFixed(zoom < 10 ? 1 : 0) + '×';
  dessiner();
}}
document.getElementById('zplus').addEventListener('click', () => {{ zoom = Math.min(zoom * 2, 16); appliquerZoom(); }});
document.getElementById('zmoins').addEventListener('click', () => {{
  zoom = zoom / 2 <= 1 ? 1 : zoom / 2; appliquerZoom();
}});
scroll.addEventListener('wheel', e => {{
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  const avant = inner.getBoundingClientRect().width;
  zoom = Math.min(16, Math.max(1, zoom * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
  appliquerZoom();
  const apres = inner.getBoundingClientRect().width;
  scroll.scrollLeft = scroll.scrollLeft * (apres / avant);
}}, {{ passive: false }});

let drag = null;
scroll.addEventListener('mousedown', e => {{
  if (zoom <= 1) return;
  drag = {{ x: e.clientX, s: scroll.scrollLeft }};
  scroll.style.cursor = 'grabbing';
}});
window.addEventListener('mousemove', e => {{
  if (drag) scroll.scrollLeft = drag.s - (e.clientX - drag.x);
}});
window.addEventListener('mouseup', () => {{ drag = null; scroll.style.cursor = ''; }});

/* ---------- sélection progressive des dépendances ---------- */
/* Par défaut aucune flèche. Premier clic sur un lot : ses liens directs
   amont et aval s'affichent. Clic sur un lot surligné : on étend la chaîne
   depuis lui. Clic ailleurs, bouton réinitialiser ou Échap : tout s'efface.
   Les enchaînements de sous-tâches ne suivent que le lot focal (dernier
   lot cliqué). */
const parLot = {{}};
DATA.lots.forEach(l => parLot[l.id] = l);
const libelles = {{}};
DATA.lots.forEach(l => libelles[l.id] = l.libelle);
const amont = {{}}, aval_ = {{}};
EDGES.lots.forEach(([f, t]) => {{
  (amont[t] = amont[t] || []).push(f);
  (aval_[f] = aval_[f] || []).push(t);
}});
EDGES.tests.forEach(([f, t]) => {{
  (amont[t] = amont[t] || []).push(f);
  (aval_[f] = aval_[f] || []).push(t);
}});
let selection = null;   // Set de gids
let focal = null;       // dernier lot cliqué

function voisins(gid) {{
  return new Set([...(amont[gid] || []), ...(aval_[gid] || [])]);
}}

function appliquerSelection() {{
  document.querySelectorAll('.lot-row, .st-row').forEach(r => r.classList.remove('dim', 'sel'));
  if (!selection || !selection.size) {{
    selection = null; focal = null; dessiner(); return;
  }}
  document.querySelectorAll('.lot-row').forEach(r => {{
    r.classList.add(selection.has(r.dataset.lot) ? 'sel' : 'dim');
  }});
  document.querySelectorAll('.st-row').forEach(r => {{
    const gidLot = r.id.slice(1, r.id.lastIndexOf('-'));
    if (!selection.has(gidLot)) r.classList.add('dim');
  }});
  dessiner();
}}

function effacerSelection() {{
  if (selection) {{ selection = null; appliquerSelection(); }}
}}

document.querySelectorAll('.lot-row').forEach(el => {{
  el.addEventListener('click', e => {{
    e.stopPropagation();
    const gid = el.dataset.lot;
    if (!selection) {{
      selection = new Set([gid, ...voisins(gid)]);
    }} else if (selection.has(gid)) {{
      voisins(gid).forEach(v => selection.add(v));
    }} else {{
      selection.add(gid);
      voisins(gid).forEach(v => selection.add(v));
    }}
    focal = gid;
    appliquerSelection();
  }});
}});
document.addEventListener('click', effacerSelection);
document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') effacerSelection();
}});
document.getElementById('raz').addEventListener('click', e => {{
  e.stopPropagation(); effacerSelection();
}});

/* plier / déplier : clic sur le compteur ▸ n du lot */
document.querySelectorAll('.chev').forEach(ch => {{
  ch.addEventListener('click', e => {{
    e.stopPropagation();
    const el = ch.closest('.lot-row');
    const gid = el.dataset.lot;
    const sts = document.querySelectorAll(`[id^="S${{gid}}-"]`);
    const plier = sts.length && !sts[0].classList.contains('cache');
    sts.forEach(s => s.classList.toggle('cache', plier));
    ch.textContent = ch.textContent.replace(plier ? '▾' : '▸', plier ? '▸' : '▾');
    dessiner();
  }});
}});

/* ---------- tracé des flèches de dépendance (SVG) ---------- */
const svg = document.getElementById('fleches');
const rows = document.getElementById('rows');
function rectBarre(ligneId) {{
  const el = document.getElementById(ligneId);
  if (!el || !el.offsetParent) return null;
  const barre = el.querySelector('.barre, .marqueur');
  if (!barre) return null;
  const base = svg.getBoundingClientRect();
  const r = barre.getBoundingClientRect();
  return {{ x1: r.left - base.left, x2: r.right - base.left, ym: r.top + r.height / 2 - base.top }};
}}
function chemin(a, b, type) {{
  /* ancrages selon le type de liaison :
     FS (fin pour commencer) : fin du prédécesseur -> début du successeur
     SS (début pour commencer) : début -> début
     FF (fin pour finir) : fin -> fin
     Routage : coude simple si la cible est à droite ; sinon passage par le
     couloir entre les deux lignes pour préserver le sens de la flèche. */
  let x1, x2, couleur, marqueur;
  if (type === 'SS') {{
    x1 = a.x1; x2 = b.x1 - 3;
    couleur = '#1a7f37'; marqueur = 'pt3';
  }} else if (type === 'FF') {{
    x1 = a.x2; x2 = b.x2 + 3;
    couleur = '#b36b00'; marqueur = 'pt4';
  }} else {{
    x1 = a.x2; x2 = b.x1 - 3;
    couleur = '#444'; marqueur = 'pt1';
  }}
  const y1 = a.ym, y2 = b.ym;
  let d;
  if (x2 >= x1 + 4) {{
    const mx = (x1 + x2) / 2;
    d = `M${{x1}},${{y1}} H${{mx}} V${{y2}} H${{x2}}`;
  }} else {{
    const midY = (y1 + y2) / 2;
    const ext = type === 'FF' ? x2 + 8 : x2 - 8;
    d = `M${{x1}},${{y1}} V${{midY}} H${{ext}} V${{y2}} H${{x2}}`;
  }}
  return `<path d="${{d}}" fill="none" stroke="${{couleur}}" stroke-width="1.4" marker-end="url(#${{marqueur}})"/>`;
}}
function fleche(f, t, type, opts) {{
  /* une flèche = un tracé visible + un tracé invisible élargi pour le survol */
  opts = opts || {{}};
  const a = rectBarre('L' + f), b = rectBarre('L' + t);
  if (!a || !b) return '';
  const pair = f + '|' + t;
  const d = chemin(a, b, type).match(/d="([^"]+)"/)[1];
  let couleur = '#444', marqueur = 'pt1', dash = '';
  let titre = `${{libelles[f] || f}}  ->  ${{libelles[t] || t}}`;
  if (opts.typed) {{
    if (type === 'SS') {{ couleur = '#1a7f37'; marqueur = 'pt3'; }}
    else if (type === 'FF') {{ couleur = '#b36b00'; marqueur = 'pt4'; }}
  }} else {{
    /* style Asana : pointillé si la tâche en amont est terminée (elle ne
       bloque plus) ; rouge si incohérence de dates (le successeur démarre
       avant le début du prédécesseur) */
    const p = parLot[f], s = parLot[t];
    if (p && s && p.debut && s.debut && s.debut < p.debut) {{
      couleur = '#c62828'; marqueur = 'pt5';
      titre += '  (incohérence de dates)';
    }}
    if (p && p.terminee) {{
      dash = ' stroke-dasharray="5,3"';
      titre += '  (tâche en amont terminée)';
    }}
  }}
  return `<path class="vis" data-p="${{pair}}" d="${{d}}" fill="none" stroke="${{couleur}}" stroke-width="1.4"${{dash}} marker-end="url(#${{marqueur}})"></path>` +
         `<path class="hit" data-p="${{pair}}" d="${{d}}" fill="none"><title>${{titre}}</title></path>`;
}}
function dessiner() {{
  const w = Math.max(rows.scrollWidth, inner.offsetWidth);
  const h = Math.max(rows.scrollHeight, inner.offsetHeight);
  svg.setAttribute('width', w);
  svg.setAttribute('height', h);
  svg.setAttribute('viewBox', `0 0 ${{w}} ${{h}}`);
  let out = '<defs>' +
    '<marker id="pt1" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#444"/></marker>' +
    '<marker id="pt2" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#999"/></marker>' +
    '<marker id="pt3" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#1a7f37"/></marker>' +
    '<marker id="pt4" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#b36b00"/></marker>' +
    '<marker id="pt5" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#c62828"/></marker>' +
    '</defs>';
  /* catégorie Test : flèches toujours visibles, avec l'étiquette du type */
  for (const [f, t, type] of EDGES.tests) {{
    out += fleche(f, t, type, {{ typed: true }});
  }}
  if (selection) {{
    for (const [f, t] of EDGES.lots) {{
      if (selection.has(f) && selection.has(t)) {{
        out += fleche(f, t, 'FS');
      }}
    }}
    if (focal) {{
      for (const [f, t] of EDGES.chaines) {{
        const gidLot = f.slice(1, f.lastIndexOf('-'));
        if (gidLot !== focal) continue;
        const a = rectBarre(f), b = rectBarre(t);
        if (!a || !b) continue;
        const d = chemin(a, b, 'FS').match(/d="([^"]+)"/)[1];
        out += `<path class="vis" d="${{d}}" fill="none" stroke="#999" stroke-width="1.2" marker-end="url(#pt2)"/>`;
      }}
    }}
  }}
  svg.innerHTML = out;
}}
/* survol d'une flèche : mise en évidence du trait et des deux lots reliés */
svg.addEventListener('mouseover', e => {{
  const h = e.target;
  if (!(h instanceof SVGPathElement) || !h.classList.contains('hit')) return;
  const vis = svg.querySelector(`path.vis[data-p="${{h.dataset.p}}"]`);
  if (vis) vis.classList.add('hov');
  const [f, t] = h.dataset.p.split('|');
  const rf = document.getElementById('L' + f), rt = document.getElementById('L' + t);
  if (rf) rf.classList.add('lie');
  if (rt) rt.classList.add('lie');
}});
svg.addEventListener('mouseout', () => {{
  svg.querySelectorAll('path.hov').forEach(p => p.classList.remove('hov'));
  document.querySelectorAll('.lot-row.lie').forEach(r => r.classList.remove('lie'));
}});
appliquerZoom();
</script>
</body>
</html>
"""

    return html


def generer_html_jalons(lots, titre="PLANNING DE PROJET : JALONS", libelle_projet="", ordre_scopes=None):
    """Vue jalons : uniquement les bandeaux de catégories (un par Scope,
    alignés sur les trimestres comme en vue synthèse), sans les lignes de
    tâches. Les jalons (sous-tâches dont le nom contient "Prêt à l'usage")
    sont affichés en repère daté sur le bandeau de leur catégorie, nom visible
    sans survol (capture d'écran). Les jalons sans date sont ignorés.

    Affichage : mise à l'échelle automatique à 100 % du navigateur (format
    16:9, marge de sécurité de 10 % sur la hauteur) et zoom horizontal
    (boutons ou ctrl/cmd + molette) pour détailler les trimestres.
    Les libellés sont répartis sur plusieurs couloirs verticaux pour
    éviter les superpositions."""
    def est_jalon(nom):
        n = unicodedata.normalize("NFD", (nom or "").lower())
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return "pret a l usage" in re.sub(r"[^a-z0-9]+", " ", n)

    jalons_par_scope = {}
    for l in lots:
        for s in l["sous_taches"]:
            # un jalon Asana n'a qu'une date : son échéance (due_on). On prend
            # donc la fin en priorité, repli sur le début
            d_j = s.get("fin") or s.get("debut")
            if d_j and est_jalon(s.get("nom", "")):
                jalons_par_scope.setdefault(l["scope"], []).append({"nom": s["nom"], "date": d_j})
    for v in jalons_par_scope.values():
        v.sort(key=lambda j: j["date"])

    scopes = ordonner_scopes(lots, ordre_scopes)
    min_q, max_q = axe_temporel(lots)

    def trimestre_suivant(d):
        return date(d.year + (1 if d.month > 9 else 0), ((d.month - 1) // 3) * 3 + 4 if d.month <= 9 else 1, 1)

    def debut_trimestre(d):
        return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)

    def fin_trimestre(d):
        return trimestre_suivant(debut_trimestre(d)) - timedelta(days=1)

    quarters = []
    q = min_q
    while q <= max_q:
        quarters.append(q)
        q = trimestre_suivant(q)

    t0 = min_q
    t1 = trimestre_suivant(max_q)
    span = (t1 - t0).days

    def x(iso):
        return round(((date.fromisoformat(iso) - t0).days / span) * 100, 3)

    h_dispo = 958  # remplit exactement le cadre 16:9 (1080 - titres - en-tête)
    LARGEUR_TL = 1710  # px de timeline au zoom 1x (1920 - marges - colonne gauche)

    def largeur_libelle(texte):
        # estimation calibrée sur le rendu réel mesuré (Arial 10 px), marge 5 %
        return round((16 + len(texte) * 4.6) / LARGEUR_TL * 100, 2)

    def jjmm(iso):
        return f"{iso[8:10]}/{iso[5:7]}"

    # hauteurs de lignes proportionnelles au besoin : les jalons dont les
    # dates sont plus proches que la largeur d'un libellé se chevauchent tous,
    # il faut un couloir par jalon simultané (13 px par couloir, de part et
    # d'autre de la barre centrale qui occupe 45-55 % de la ligne)
    ESPACEMENT = 13.0
    lignes = []
    for s in scopes:
        jalons_s = jalons_par_scope.get(s, [])
        dates_j = [date.fromisoformat(j["date"]) for j in jalons_s]
        if jalons_s:
            l_moy = sum(largeur_libelle(f'{jjmm(j["date"])} · {j["nom"]}') for j in jalons_s) / len(jalons_s)
            w_days = l_moy / 100 * span
            clique = max(sum(1 for d2 in dates_j if abs((d2 - d).days) < w_days) for d in dates_j)
            n_couloirs = clique + (clique % 2)  # pair : zones symétriques
            h_min = ((n_couloirs // 2) * ESPACEMENT + 12) / 0.45
        else:
            n_couloirs, h_min = 2, 34.0
        lignes.append({"scope": s, "jalons": jalons_s, "n_couloirs": n_couloirs, "h_min": h_min})
    total_min = sum(l["h_min"] for l in lignes)
    # répartition au pixel : part proportionnelle + reste aux plus grandes fractions
    brutes = [l["h_min"] * h_dispo / total_min for l in lignes]
    hauteurs = [int(b) for b in brutes]
    reste_px = h_dispo - sum(hauteurs)
    for i in sorted(range(len(brutes)), key=lambda i: brutes[i] - hauteurs[i], reverse=True)[:reste_px]:
        hauteurs[i] += 1

    qcols = "".join(
        (lambda fond, txt: f'<div class="qcol" style="background:{fond};color:{txt}">'
         f'<span>T{(q.month - 1) // 3 + 1}</span><span>{q.year}</span></div>')(
            *couleur_trimestre(q.year, (q.month - 1) // 3 + 1))
        for q in quarters
    )
    qgrid = "".join(
        f'<div class="qsep{" q1" if q.month == 1 else ""}" style="left:{x(q.isoformat())}%"></div>'
        for q in quarters[1:]
    )

    rows = []
    nb_jalons = 0
    for i_s, s in enumerate(scopes):
        c = couleur_scope(i_s, s)
        sous = [l for l in lots if l["scope"] == s]
        debs_g = [date.fromisoformat(l["start"]) for l in sous if l["start"]]
        fins_g = [date.fromisoformat(l["end"]) for l in sous if l["end"]]
        bande = ""
        if debs_g and fins_g:
            g_left = x(debut_trimestre(min(debs_g)).isoformat())
            g_right = x(fin_trimestre(max(fins_g)).isoformat())
            bande = (
                f'<span class="bande" style="background:{c};'
                f'left:{g_left}%;width:{max(g_right - g_left, 0.15)}%"></span>'
            )

        marqueurs = ""
        ligne = lignes[i_s]
        jalons_s = ligne["jalons"]
        n_couloirs = ligne["n_couloirs"]
        # occupation par couloir : liste d'intervalles [gauche, droite] posés,
        # car un libellé à droite du losange ne bloque pas sa gauche
        occupation = [[] for _ in range(n_couloirs)]

        def couloir_libre(k, a, b):
            return all(b < i0 - 0.25 or a > i1 + 0.25 for i0, i1 in occupation[k])

        def pousser_droite(k, deb, largeur):
            """Plus petite position >= deb où l'intervalle tient dans le couloir."""
            deb_p = deb
            for i0, i1 in sorted(occupation[k]):
                if deb_p < i1 + 0.25 and deb_p + largeur > i0 - 0.25:
                    deb_p = i1 + 0.25
            return deb_p

        # centres de couloirs en px, répartis de part et d'autre de la barre
        # (45-55 %), espacés d'au moins ESPACEMENT, marge de 9 px en haut/bas
        h_ligne = hauteurs[i_s]
        m = n_couloirs // 2
        zone = 0.45 * h_ligne
        pas = (zone - 12) / m if m else 0
        centres = [9 + (i + 0.5) * pas for i in range(m)]
        centres += [h_ligne - 9 - (i + 0.5) * pas for i in range(m)]
        centres_pct = [round(c / h_ligne * 100, 2) for c in centres]
        # recherche des couloirs du centre (sur le bandeau) vers l'extérieur
        ordre_couloirs = sorted(range(n_couloirs), key=lambda k: abs(centres_pct[k] - 50))
        for j in jalons_s:
            nb_jalons += 1
            jd = x(j["date"])
            texte = f'{jjmm(j["date"])} · {j["nom"]}'
            l_pct = largeur_libelle(texte)
            # placement du libellé : à droite du losange de préférence (en le
            # poussant si un voisin occupe la place), à gauche en secours
            # (texte se terminant sur le losange), ce qui double la capacité
            # autour des clusters de dates (fins de trimestre). bord droit du
            # cadre : bascule à gauche d'office
            inv = jd + l_pct > 99.5
            choix = None
            if not inv:
                deb = jd + 0.4
                for k in ordre_couloirs:
                    deb_p = pousser_droite(k, deb, l_pct)
                    if deb_p + l_pct <= 100.2:
                        pousse = deb_p - deb
                        if choix is None or pousse < choix[1]:
                            choix = (k, deb_p, False)
                        if pousse == 0:
                            break
            if choix is None:
                # côté gauche : l'intervalle [fin_g - l, fin_g] doit être libre
                fin_g = jd - 0.4
                for k in ordre_couloirs:
                    if fin_g - l_pct >= 0.2 and couloir_libre(k, fin_g - l_pct, fin_g):
                        choix = (k, fin_g, True)
                        break
            if choix is not None:
                k, bord, gauche = choix
                if gauche:
                    occupation[k].append((bord - l_pct, bord))
                else:
                    occupation[k].append((bord, bord + l_pct))
            else:
                # cluster trop dense : chevauchement inévitable, couloir le moins rempli
                k = min(ordre_couloirs, key=lambda k: sum(i1 - i0 for i0, i1 in occupation[k]))
                bord, gauche = (jd - 0.4, True) if inv else (jd + 0.4, False)
                if gauche:
                    occupation[k].append((bord - l_pct, bord))
                else:
                    occupation[k].append((bord, bord + l_pct))
            centre = centres_pct[k]
            if gauche:
                style = f"top:{centre:.1f}%;left:{bord:.3f}%;transform:translate(-100%,-50%);"
            else:
                style = f"top:{centre:.1f}%;left:{bord:.3f}%;transform:translateY(-50%);"
            # tige : relie le losange (au niveau du couloir) à la barre
            if centre < 50:
                tige = f"top:{centre:.1f}%;height:{45 - centre:.1f}%;"
            else:
                tige = f"top:55%;height:{centre - 55:.1f}%;"
            marqueurs += (
                f'<span class="tige" style="{tige}background:{c};left:{jd:.3f}%"></span>'
                f'<span class="losange" style="top:{centre:.1f}%;left:{jd:.3f}%;background:{c}"></span>'
                f'<span class="jalon" style="{style}border-color:{c}">{texte}</span>'
            )

        nom_affiche = AFFICHAGE_SCOPES.get(s, s)
        font_g = min(12.5, round(138 / (0.58 * max(len(nom_affiche), 1)), 1))
        font_g = max(font_g, 7.5)
        rows.append(
            f'<div class="row groupe-row" style="height:{h_ligne}px">'
            f'<div class="cell-g" style="background:{c};font-size:{font_g}px" title="{s}">{nom_affiche}</div>'
            f'<div class="cell-tl">{bande}{marqueurs}</div></div>'
        )

    stats = (
        f'<div class="stats"><b>{nb_jalons} jalons</b> sur {len(scopes)} catégories'
        f'<br><span class="petit">jalons = sous-tâches « Prêt à l\'usage »</span></div>'
    )

    html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Planning jalons</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: 100%; height: 100%; overflow: hidden; background: #eef0f4; font-family: Arial, Helvetica, sans-serif; }}
  /* cadre 16:9 logique de 1920x1080, mis à l'échelle à 100 % du navigateur
     par le script (marge de sécurité de 10 % sur la hauteur) */
  #ecran {{ position: absolute; width: 1920px; height: 1080px; transform-origin: top left; }}
  .page {{ width: 1920px; height: 1080px; padding: 20px 30px 12px; display: flex; flex-direction: column; background: #fff; }}
  .titre {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }}
  h1 {{ font-size: 26px; letter-spacing: 1px; color: #111; flex: 1; text-align: center; }}
  .zoom {{ display: flex; gap: 6px; align-items: center; width: 120px; }}
  .zoom button {{ width: 32px; height: 32px; font-size: 19px; border: 1px solid #999; background: #fff; border-radius: 4px; cursor: pointer; }}
  .zoom button:hover {{ background: #eee; }}
  .zoom .niveau {{ font-size: 12px; color: #555; width: 40px; text-align: center; }}
  .stats {{ font-size: 12px; color: #444; text-align: right; line-height: 1.5; }}
  .stats .petit {{ color: #888; font-size: 10.5px; }}
  .gantt {{ flex: 1; display: flex; flex-direction: column; border: 1px solid #d5d5d5; min-height: 0; position: relative; }}
  .corps {{ flex: 1; min-height: 0; position: relative; overflow-x: auto; overflow-y: hidden; }}
  .corps::-webkit-scrollbar {{ height: 9px; }}
  .corps::-webkit-scrollbar-thumb {{ background: #b9b9d0; border-radius: 5px; }}
  .inner {{ position: relative; min-width: 100%; height: 100%; }}
  .entete-tl {{ display: flex; height: 44px; position: sticky; top: 0; z-index: 10; }}
  .h-g {{ width: 150px; flex: none; background: #1f1f38; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: bold; position: sticky; left: 0; z-index: 11; }}
  .qcol {{ flex: 1; border-left: 1px solid rgba(255,255,255,.35); display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 15px; font-weight: bold; line-height: 1.2; color: #fff; text-shadow: 0 1px 1px rgba(0,0,0,.35); }}
  .qcol:nth-child(2) {{ border-left: none; }}
  .grid {{ position: absolute; left: 150px; right: 0; top: 44px; bottom: 9px; z-index: 0; }}
  .qsep {{ position: absolute; top: 0; bottom: 0; width: 1px; background: #ececf2; }}
  .qsep.q1 {{ background: #c4c4d4; }}
  .rowsbox {{ position: relative; z-index: 1; }}
  .row {{ display: flex; }}
  .cell-g {{ width: 150px; flex: none; background: #fff; color: #fff; font-size: 12px; font-weight: bold; display: flex; align-items: center; justify-content: center; position: sticky; left: 0; z-index: 5; border-right: 1px solid #d5d5d5; }}
  .cell-tl {{ flex: 1; min-width: 0; position: relative; overflow: hidden; }}
  .bande {{ position: absolute; top: 45%; bottom: 45%; border-radius: 2px; }}
  .tige {{ position: absolute; width: 1.5px; transform: translateX(-50%); z-index: 2; }}
  .losange {{ position: absolute; top: 50%; width: 11px; height: 11px; transform: translate(-50%, -50%) rotate(45deg); border: 1.5px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.25); z-index: 4; }}
  .jalon {{ position: absolute; font-size: 10px; line-height: 1.15; white-space: nowrap; padding: 0.5px 4px; border-left: 2.5px solid; background: #fff; z-index: 3; }}
</style>
</head>
<body>
<div id="ecran">
<div class="page">
  <div class="titre"><div class="zoom">
    <button id="zmoins" title="dézoomer (1x = vue complète de tous les trimestres)">−</button>
    <span class="niveau" id="zniveau">1×</span>
    <button id="zplus" title="zoomer">+</button>
  </div><h1>{titre}</h1>{stats}</div>
  <div class="gantt">
    <div class="corps" id="scroll">
      <div class="inner" id="inner">
        <div class="entete-tl"><div class="h-g">CATÉGORIES</div>{qcols}</div>
        <div class="grid">{qgrid}</div>
        <div class="rowsbox">{''.join(rows)}</div>
      </div>
    </div>
  </div>
</div>
</div>
<script>
/* ---------- mise à l'échelle : 100 % du navigateur, 16:9, marge 10 % hauteur ---------- */
const ecran = document.getElementById('ecran');
function ajuster() {{
  const s = Math.min(window.innerWidth / 1920, (window.innerHeight * 0.9) / 1080);
  ecran.style.transform = 'scale(' + s + ')';
  ecran.style.left = Math.max((window.innerWidth - 1920 * s) / 2, 0) + 'px';
  ecran.style.top = Math.max((window.innerHeight - 1080 * s) / 2, 0) + 'px';
}}
window.addEventListener('resize', ajuster);
ajuster();

/* ---------- zoom : la timeline seule, colonnes fixes ---------- */
const scroll = document.getElementById('scroll');
const inner = document.getElementById('inner');
const zniveau = document.getElementById('zniveau');
let zoom = 1;
function appliquerZoom() {{
  inner.style.width = (zoom * 100) + '%';
  zniveau.textContent = zoom.toFixed(zoom < 10 ? 1 : 0) + '×';
}}
document.getElementById('zplus').addEventListener('click', () => {{ zoom = Math.min(zoom * 2, 16); appliquerZoom(); }});
document.getElementById('zmoins').addEventListener('click', () => {{
  zoom = zoom / 2 <= 1 ? 1 : zoom / 2; appliquerZoom();
}});
scroll.addEventListener('wheel', e => {{
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  const avant = inner.getBoundingClientRect().width;
  zoom = Math.min(16, Math.max(1, zoom * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
  appliquerZoom();
  const apres = inner.getBoundingClientRect().width;
  scroll.scrollLeft = scroll.scrollLeft * (apres / avant);
}}, {{ passive: false }});

let drag = null;
scroll.addEventListener('mousedown', e => {{
  if (zoom <= 1) return;
  drag = {{ x: e.clientX, s: scroll.scrollLeft }};
  scroll.style.cursor = 'grabbing';
}});
window.addEventListener('mousemove', e => {{
  if (drag) scroll.scrollLeft = drag.s - (e.clientX - drag.x);
}});
window.addEventListener('mouseup', () => {{ drag = null; scroll.style.cursor = ''; }});
</script>
</body>
</html>
"""
    return html


SUFFIXES_VUES = {"synthese": "", "jalons": "-jalons", "detail": "-detail"}


def generer_vues(lots, vues, base_nom, dossier_sortie, titre="PLANNING DE PROJET",
                 libelle_projet="", ordre_scopes=None, destinations=None):
    """Génère les vues demandées (synthese, jalons, detail) dans dossier_sortie,
    avec le même nom de base : <base_nom><suffixe>.html. Chaque fichier est
    aussi copié vers les destinations supplémentaires fournies."""
    for v in vues:
        if v not in SUFFIXES_VUES:
            sys.exit(f"Vue inconnue : {v} (choix : {', '.join(SUFFIXES_VUES)}).")
    os.makedirs(dossier_sortie, exist_ok=True)
    produits = []
    for vue in vues:
        chemin = os.path.join(dossier_sortie, f"{base_nom}{SUFFIXES_VUES[vue]}.html")
        if vue == "synthese":
            html = generer_html(lots, titre=titre, libelle_projet=libelle_projet, ordre_scopes=ordre_scopes)
        elif vue == "jalons":
            html = generer_html_jalons(lots, titre=titre + " : JALONS",
                                       libelle_projet=libelle_projet, ordre_scopes=ordre_scopes)
        else:
            html = generer_html_detail(lots, titre=titre + " : DÉTAIL",
                                       libelle_projet=libelle_projet, ordre_scopes=ordre_scopes)
        with open(chemin, "w", encoding="utf-8") as fh:
            fh.write(html)
        copies = []
        for dest in (destinations or []):
            try:
                os.makedirs(dest, exist_ok=True)
                shutil.copy2(chemin, os.path.join(dest, os.path.basename(chemin)))
                copies.append(dest)
            except OSError as e:
                print(f"ATTENTION : copie impossible vers {dest} : {e}", file=sys.stderr)
        produits.append((chemin, vue, copies))
    return produits


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sans-extract", action="store_true", help="régénérer depuis le JSON intermédiaire local sans appeler l'API")
    parser.add_argument("--source", help="générer depuis un fichier JSON de lots (données d'exemple, export...) au lieu de l'API")
    parser.add_argument("--json", help="générer depuis un export JSON du projet Asana (menu projet > Exporter > JSON)")
    parser.add_argument("--vierge", action="store_true", help="générer un modèle HTML vierge (aucune donnée, à remplir par le script CSV)")
    parser.add_argument("--projet", help="GID du projet Asana (requis pour l'extraction API)")
    parser.add_argument("--workspace", default=None, help="GID du workspace Asana (défaut : variable ASANA_WORKSPACE)")
    parser.add_argument("--titre", default="PLANNING DE PROJET", help="titre affiché en haut du planning")
    parser.add_argument("--libelle-projet", default="", help="libellé du projet stocké dans les données")
    parser.add_argument("--vues", default="synthese,jalons",
                        help="vues à générer, séparées par des virgules : synthese, jalons, detail")
    parser.add_argument("--sortie", default=None, help="nom de base des fichiers HTML générés (sans extension)")
    parser.add_argument("--dossier", default=None,
                        help=f"dossier de sortie des HTML (défaut : {DOSSIER_SORTIE})")
    parser.add_argument("--destination", action="append", default=None,
                        help="destination supplémentaire de copie des HTML (option répétable)")
    args = parser.parse_args()

    vues = [v.strip().lower() for v in args.vues.split(",") if v.strip()]

    ordre_scopes = []
    if args.vierge:
        lots = []
    elif args.json:
        lots, ordre_scopes = charger_export_json(args.json)
    elif args.source:
        lots = json.load(open(args.source))
    elif args.sans_extract:
        lots = json.load(open(f"{DOSSIER}/lots.json"))
    else:
        if not args.projet:
            sys.exit("Préciser --projet <GID> (API), --json <export> ou --source <fichier>.")
        lots, ordre_scopes = extraire_asana(args.projet, args.workspace)
        resoudre_dependances(lots)
        calculer_fins(lots)
        json.dump(lots, open(f"{DOSSIER}/lots.json", "w"), ensure_ascii=False)

    base = args.sortie or f"planning-{date.today():%Y-%m-%d}"
    dossier_sortie = args.dossier or DOSSIER_SORTIE
    destinations = list(DESTINATIONS) + list(args.destination or [])
    produits = generer_vues(lots, vues, base, dossier_sortie,
                            titre=args.titre, libelle_projet=args.libelle_projet,
                            ordre_scopes=ordre_scopes, destinations=destinations)
    for chemin, vue, copies in produits:
        print(f"OK [{vue}] : {chemin}")
        for dest in copies:
            print(f"     copié vers {dest}")
    print(f"     {len(lots)} lots, {sum(len(l['deps']) for l in lots)} dépendances, "
          f"{sum(len(l['sous_taches']) for l in lots)} sous-tâches")


if __name__ == "__main__":
    main()
