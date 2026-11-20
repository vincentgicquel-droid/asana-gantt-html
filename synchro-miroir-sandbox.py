#!/usr/bin/env python3
"""Synchronise un projet Asana source (ex. production BPCE-IT) vers un projet
miroir dans la sandbox : même lots, mêmes sous-tâches, mêmes dates, mêmes
champs personnalisés. Sert à travailler sur une sandbox ISO production pour
générer et tester le planning Gantt sans toucher à la prod.

Ce que le script fait :
  - lit le projet source (lots, sous-tâches, dates, statut, champs
    personnalisés : Scope, Direction, Matériel, Surlignage, Fiabilité,
    Numéro.Nom Lot, Blocked By (Dependencies)... tous les champs enum/texte/
    nombre/multi-enum présents des deux côtés) ;
  - rapproche les lots du miroir par « Numéro.Nom Lot » (repli : nom de
    tâche), les sous-tâches par nom ;
  - crée ce qui manque, met à jour ce qui a changé, crée les options d'enum
    manquantes (à la position de l'ordre source : l'ordre du champ Scope,
    qui pilote l'ordre des catégories du Gantt, est préservé) ;
  - copie « Blocked By (Dependencies) » tel quel : le champ est un texte qui
    référence les lots par numéro, valable dans les deux workspaces.

Ce que le script ne fait JAMAIS :
  - supprimer ou archiver une tâche du miroir (les tâches en trop sont
    seulement signalées dans le rapport) ;
  - écrire dans le workspace de production (verrou : refus si le projet
    cible appartient à ASANA_WORKSPACE_PROD, sauf --autoriser-prod-ecriture).

Mode DRY-RUN par défaut : rien n'est écrit sans --apply.

Configuration : fichier config.env (ASANA_TOKEN obligatoire ; pour
--liste-projets : ASANA_WORKSPACE ou ASANA_WORKSPACE_<ENV> avec ASANA_ENV).
Deux GIDs de projet à donner : --source (prod) et --cible (miroir sandbox),
mémorisables dans config.env via ASANA_PROJET_SOURCE et ASANA_PROJET_MIROIR.

Usage typique (depuis le poste BPCIT, seul à joindre la prod) :
    python3 synchro-miroir-sandbox.py --liste-projets
    python3 synchro-miroir-sandbox.py --source 1234... --cible 5678...
    python3 synchro-miroir-sandbox.py --source 1234... --cible 5678... --apply
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

API = "https://app.asana.com/api/1.0"
CONFIG_ENV_TROUVE = None


# ---------------------------------------------------------------- config.env

def charger_config_env():
    """Complète l'environnement avec un config.env trouvé à côté du script ou
    dans un dossier parent (ASANA_TOKEN, ASANA_ENV, ASANA_WORKSPACE*,
    ASANA_PROJET_*). Les variables déjà présentes ne sont pas écrasées."""
    global CONFIG_ENV_TROUVE
    dossier = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        chemin = os.path.join(dossier, "config.env")
        if os.path.isfile(chemin):
            CONFIG_ENV_TROUVE = chemin
            try:
                fh = open(chemin, encoding="utf-8-sig")
            except UnicodeDecodeError:
                fh = open(chemin, encoding="latin-1")
            with fh:
                for ligne in fh:
                    ligne = ligne.strip()
                    if not ligne or ligne.startswith("#") or "=" not in ligne:
                        continue
                    cle, valeur = ligne.split("=", 1)
                    cle, valeur = cle.strip(), valeur.strip()
                    if cle and cle not in os.environ and valeur:
                        os.environ[cle] = valeur
            break
        parent = os.path.dirname(dossier)
        if parent == dossier:
            break
        dossier = parent


# --------------------------------------------------------------------- HTTP

class Api:
    """Client API Asana minimal : GET/POST/PUT JSON, pause entre écritures,
    réessai sur limite de débit (429) en respectant Retry-After."""

    def __init__(self, token, pause=0.2):
        self.token = token
        self.pause = pause
        self.ctx = ssl.create_default_context()
        self.ecritures = 0

    def _brut(self, chemin):
        """GET brut : renvoie la réponse complète {data, next_page}."""
        req = urllib.request.Request(
            API + chemin, headers={"Authorization": "Bearer " + self.token})
        try:
            with urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=self.ctx)).open(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                attente = int(e.headers.get("Retry-After") or 5)
                print(f"  limite de débit Asana : pause de {attente}s...")
                time.sleep(attente)
                return self._brut(chemin)
            corps_erreur = e.read().decode(errors="replace")[:300]
            sys.exit(f"Erreur API {e.code} sur GET {chemin} : {corps_erreur}")

    def get(self, chemin):
        return self._brut(chemin).get("data")

    def ecrire(self, methode, chemin, corps, quitter=True):
        donnees = json.dumps({"data": corps}).encode()
        req = urllib.request.Request(
            API + chemin, data=donnees,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/json"},
            method=methode)
        try:
            with urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=self.ctx)).open(req) as r:
                result = json.load(r).get("data")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                attente = int(e.headers.get("Retry-After") or 5)
                print(f"  limite de débit Asana : pause de {attente}s...")
                time.sleep(attente)
                return self.ecrire(methode, chemin, corps, quitter)
            corps_erreur = e.read().decode(errors="replace")[:300]
            if not quitter:
                return {"erreur": e.code, "detail": corps_erreur}
            sys.exit(f"Erreur API {e.code} sur {methode} {chemin} : {corps_erreur}")
        self.ecritures += 1
        if self.pause:
            time.sleep(self.pause)
        return result

    def pagination(self, chemin):
        """Itère toutes les pages d'une liste (limit géré ici)."""
        sep = "&" if "?" in chemin else "?"
        base = chemin + f"{sep}limit=100"
        url = base
        while url:
            d = self._brut(url)
            for item in d.get("data") or []:
                yield item
            nxt = d.get("next_page") or {}
            url = (base + f"{sep}offset=" +
                   urllib.parse.quote(nxt["offset"])) if nxt.get("offset") else None


# ------------------------------------------------------------------ lecture

def norm(s):
    """Clé de rapprochement : sans accents, minuscules, espaces normalisés.
    Variante de norm de generer_gantt.py : les points sont préservés pour
    distinguer les numéros de lots (02.1 vs 02.10)."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9.]+", " ", s.lower()).strip()


def nom_option(s):
    """Nom d'option d'enum nettoyé : Asana refuse un doublon en ignorant les
    espaces de bord, la comparaison doit faire pareil."""
    return (s or "").strip()


def lire_champs_projet(api, projet_gid):
    """Champs personnalisés attachés au projet : {gid: définition}.
    définition = {gid, nom, type, important, options: [{gid, nom}] (ordre
    Asana)}."""
    reglages = api.pagination(
        f"/projects/{projet_gid}/custom_field_settings"
        "?opt_fields=custom_field.gid,custom_field.name,custom_field.type,"
        "custom_field.enum_options.gid,custom_field.enum_options.name,is_important")
    champs = {}
    for r in reglages:
        cf = r.get("custom_field") or {}
        champs[cf["gid"]] = {
            "gid": cf["gid"],
            "nom": cf.get("name") or "",
            "type": cf.get("type") or "",
            "important": bool(r.get("is_important")),
            "options": [{"gid": o["gid"], "nom": o.get("name") or ""}
                        for o in cf.get("enum_options") or []],
        }
    return champs


def valeur_cf(cf, defn):
    """Valeur normalisée d'un champ de tâche, selon son type."""
    t = defn["type"] if defn else (cf.get("type") or "")
    if t == "enum":
        return (cf.get("enum_value") or {}).get("name") or cf.get("display_value")
    if t == "multi_enum":
        noms = [o.get("name") for o in cf.get("multi_enum_values") or []]
        return [n for n in noms if n] or None
    if t == "number":
        return cf.get("number_value")
    if t == "text":
        return cf.get("text_value") or cf.get("display_value")
    return cf.get("display_value") or cf.get("text_value")


def cle_champ(nom, type_):
    """Clé de rapprochement des champs personnalisés : nom + type (deux
    champs peuvent porter le même nom, ex. « Lot » enum et « Lot » texte)."""
    return (nom or "", type_ or "")


def lire_projet(api, projet_gid):
    """Lit tout le projet : méta, champs, lots et sous-tâches."""
    meta = api.get(f"/projects/{projet_gid}?opt_fields=name,workspace.gid,workspace.name,archived")
    champs = lire_champs_projet(api, projet_gid)

    lots = []
    for t in api.pagination(
            f"/projects/{projet_gid}/tasks"
            "?opt_fields=name,start_on,due_on,completed,custom_fields"):
        custom = {}
        for cf in t.get("custom_fields") or []:
            defn = champs.get(cf["gid"])
            if defn is None:
                continue  # champ non attaché au projet : ignoré
            custom[cle_champ(defn["nom"], defn["type"])] = valeur_cf(cf, defn)
        lots.append({
            "gid": t["gid"],
            "nom": (t.get("name") or "").strip(),
            "label": (custom.get(("Numéro.Nom Lot", "enum")) or "").strip(),
            "debut": t.get("start_on"),
            "fin": t.get("due_on"),
            "terminee": bool(t.get("completed")),
            "custom": custom,
            "sous_taches": [],
        })

    for lot in lots:
        for s in api.pagination(
                f"/tasks/{lot['gid']}/subtasks"
                "?opt_fields=name,start_on,due_on,completed"):
            lot["sous_taches"].append({
                "gid": s["gid"],
                "nom": (s.get("name") or "").strip(),
                "debut": s.get("start_on"),
                "fin": s.get("due_on"),
                "terminee": bool(s.get("completed")),
            })
        time.sleep(0.1)

    return {"meta": meta, "champs": champs, "lots": lots}


# ------------------------------------------------------- valeurs personnalis

def corps_tache(t):
    """Corps standard pour une création (POST). Asana exige une échéance dès
    qu'il y a une date de début : sans fin, la fin est posée égale au début
    (même repli que generer_gantt.py)."""
    corps = {"name": t["nom"], "completed": t["terminee"]}
    debut, fin = t.get("debut"), t.get("fin")
    if fin:
        corps["due_on"] = fin
        if debut:
            corps["start_on"] = debut
    elif debut:
        corps["due_on"] = debut
    return corps


def corps_custom(lot, cible_par_cle, options_manquantes, contexte):
    """Corps custom_fields Asana pour une tâche : chaque champ source présent
    dans le miroir (même nom et même type) est réécrit avec la valeur cible
    (GID d'option pour enum, texte, nombre). Les options manquantes sont
    signalées, pas écrites."""
    corps, bloques = {}, []
    for (nom, type_source), valeur in lot["custom"].items():
        defn = cible_par_cle.get((nom, type_source))
        if defn is None:
            continue  # champ absent du miroir : signalé à part
        if defn["type"] == "enum":
            if valeur is None:
                corps[defn["gid"]] = None
                continue
            opt = next((o for o in defn["options"]
                        if nom_option(o["nom"]) == nom_option(valeur)), None)
            if opt:
                corps[defn["gid"]] = opt["gid"]
            else:
                bloques.append((nom, valeur))
                options_manquantes.append({"champ": nom, "type": defn["type"],
                                           "option": valeur,
                                           "contexte": contexte})
        elif defn["type"] == "multi_enum":
            if valeur is None:
                corps[defn["gid"]] = None
                continue
            gids = []
            for v in valeur:
                opt = next((o for o in defn["options"]
                            if nom_option(o["nom"]) == nom_option(v)), None)
                if opt:
                    gids.append(opt["gid"])
                else:
                    bloques.append((nom, v))
                    options_manquantes.append({"champ": nom, "type": defn["type"],
                                               "option": v,
                                               "contexte": contexte})
            corps[defn["gid"]] = gids or None
        elif defn["type"] in ("text", "number"):
            corps[defn["gid"]] = valeur
        else:
            bloques.append((nom, valeur))
    return corps, bloques


# -------------------------------------------------------------------- diff

def rapprocher_lots(source, cible):
    """Rapproche les lots source↔cible par label (Numéro.Nom Lot), repli nom.
    Retourne (paires, cibles_en_trop) ; les lots cible non appariés sont
    signalés mais jamais touchés."""
    par_cle = {}
    for l in cible["lots"]:
        cle = norm(l["label"]) or norm(l["nom"])
        if cle:
            par_cle.setdefault(cle, []).append(l)
    paires, vus = [], set()
    for ls in source["lots"]:
        cle = norm(ls["label"]) or norm(ls["nom"])
        lc = None
        candidats = par_cle.get(cle) or []
        for cand in candidats:
            if cand["gid"] not in vus:
                lc = cand
                break
        if lc:
            vus.add(lc["gid"])
        paires.append((ls, lc))
    en_trop = [l for l in cible["lots"] if l["gid"] not in vus]
    return paires, en_trop


def rapprocher_sous_taches(source_sous, cible_sous):
    """Rapprochement par nom, un nom peut exister plusieurs fois."""
    restantes = list(cible_sous)
    paires = []
    for ss in source_sous:
        cle = norm(ss["nom"])
        lc = next((s for s in restantes if norm(s["nom"]) == cle), None)
        if lc:
            restantes.remove(lc)
        paires.append((ss, lc))
    return paires, restantes


def comparer_sous_tache(ss, lc):
    """Champs standards à mettre à jour (None si rien à faire). Même règle
    qu'à la création : jamais de début sans échéance dans le même PUT."""
    changements = {}
    fin_cible = ss["fin"] or ss["debut"]
    if (lc.get("fin") or None) != (fin_cible or None):
        changements["due_on"] = fin_cible
    debut_cible = ss["debut"] if (ss["debut"] and fin_cible) else None
    if (lc.get("debut") or None) != (debut_cible or None):
        changements["start_on"] = debut_cible
    if lc["terminee"] != ss["terminee"]:
        changements["completed"] = ss["terminee"]
    return changements or None


def comparer_lot(ls, lc, cible_par_cle, options_manquantes):
    """Champs (standards + personnalisés) à mettre à jour, None si rien.
    Les valeurs identiques sont retirées du corps pour alléger les PUT."""
    if lc is None:
        return None
    changements = comparer_sous_tache(
        {"nom": ls["nom"], "debut": ls["debut"], "fin": ls["fin"],
         "terminee": ls["terminee"]},
        {"nom": lc["nom"], "debut": lc["debut"], "fin": lc["fin"],
         "terminee": lc["terminee"]}) or {}
    if norm(ls["nom"]) != norm(lc["nom"]):
        changements["name"] = ls["nom"]
    corps, bloques = corps_custom(ls, cible_par_cle, options_manquantes,
                                  ls["label"] or ls["nom"])
    for (nom, _t), valeur in list(ls["custom"].items()):
        defn = cible_par_cle.get((nom, _t))
        if defn is None or defn["gid"] not in corps:
            continue
        if lc["custom"].get((nom, _t)) == valeur:
            del corps[defn["gid"]]
    if bloques:
        changements["_bloques"] = bloques
    if corps:
        changements["custom_fields"] = corps
    return changements or None


# ------------------------------------------------------------------ actions

def preparer_actions(source, cible, cible_par_cle, cles_a_venir=None):
    """Construit la liste d'actions (créations, mises à jour, options
    d'enum manquantes) sans rien écrire. cles_a_venir : champs que la phase
    de préparation du miroir s'apprête à rattacher/créer (signalés à part,
    pas comme absents)."""
    actions = {"creer_lots": [], "maj_lots": [], "creer_sous_taches": [],
               "maj_sous_taches": [], "options": [], "en_trop": [],
               "champs_absents": [], "bloques": []}
    options_manquantes = []

    # champs source réellement sans équivalent dans le miroir (nom + type),
    # hors ceux que la phase champs va rattacher/créer
    cles_cible = set(cible_par_cle) | set(cles_a_venir or ())
    noms_absents = sorted({d["nom"] for d in source["champs"].values()
                           if cle_champ(d["nom"], d["type"]) not in cles_cible})
    actions["champs_absents"] = noms_absents

    paires, en_trop = rapprocher_lots(source, cible)
    actions["en_trop"] = [l["label"] or l["nom"] for l in en_trop]

    for ls, lc in paires:
        contexte = ls["label"] or ls["nom"]
        if lc is None:
            corps, bloques = corps_custom(ls, cible_par_cle,
                                          options_manquantes, contexte)
            actions["creer_lots"].append({
                "lot": ls,
                "corps": {**corps_tache(ls), "custom_fields": corps},
                "bloques": bloques,
            })
            # sous-tâches d'un lot créé : tout créer
            for ss in ls["sous_taches"]:
                actions["creer_sous_taches"].append({
                    "parent": contexte, "sous_tache": ss,
                    "corps": corps_tache(ss)})
            continue

        maj = comparer_lot(ls, lc, cible_par_cle, options_manquantes)
        if maj:
            bloques = maj.pop("_bloques", [])
            actions["maj_lots"].append({"lot": lc, "corps": maj,
                                        "bloques": bloques})
        paires_st, en_trop_st = rapprocher_sous_taches(
            ls["sous_taches"], lc["sous_taches"])
        for ss, sc in paires_st:
            if sc is None:
                actions["creer_sous_taches"].append({
                    "parent": contexte, "parent_gid": lc["gid"],
                    "sous_tache": ss, "corps": corps_tache(ss)})
            else:
                ch = comparer_sous_tache(ss, sc)
                if ch:
                    actions["maj_sous_taches"].append({
                        "parent": contexte, "sous_tache": sc, "corps": ch})
        if en_trop_st:
            actions["en_trop"].extend(
                f"{contexte} › {s['nom']}" for s in en_trop_st)

    # options d'enum manquantes, à créer à la position de l'ordre source
    for m in options_manquantes:
        cle = cle_champ(m["champ"], m["type"])
        defn_cible = cible_par_cle.get(cle)
        defn_source = next((d for d in source["champs"].values()
                            if cle_champ(d["nom"], d["type"]) == cle), None)
        avant = None
        if defn_source and defn_cible:
            noms_source = [nom_option(o["nom"]) for o in defn_source["options"]]
            try:
                i = noms_source.index(nom_option(m["option"]))
            except ValueError:
                i = -1
            for nom_voisin in noms_source[i + 1:]:
                voisin = next((o for o in defn_cible["options"]
                               if nom_option(o["nom"]) == nom_voisin), None)
                if voisin:
                    avant = voisin["gid"]
                    break
        actions["options"].append({"champ": m["champ"], "nom": m["option"],
                                   "gid_champ": defn_cible["gid"] if defn_cible else None,
                                   "insert_avant_gid": avant,
                                   "contexte": m["contexte"]})
    return actions


def preparer_actions_champs(source, cible):
    """Champs personnalisés présents à la source mais pas sur le miroir :
    ils seront créés dans le workspace cible (avec leurs options dans
    l'ordre source) puis rattachés au projet. On ne tente pas de réutiliser
    des champs existants du workspace : l'endpoint de liste des champs du
    workspace est refusé aux invités (402), et créer un champ de même nom
    est sans effet sur les autres projets."""
    cles_projet = {cle_champ(d["nom"], d["type"]) for d in cible["champs"].values()}
    creer = []
    for d in sorted(source["champs"].values(), key=lambda x: x["nom"]):
        if cle_champ(d["nom"], d["type"]) not in cles_projet:
            creer.append({"nom": d["nom"], "type": d["type"],
                          "options": [o["nom"] for o in d["options"]],
                          "important": d["important"]})
    return {"rattacher": [], "creer": creer}


def appliquer_champs(api, ws_cible, projet_cible, actes):
    """Crée puis rattache les champs manquants ; renvoie True si quelque
    chose a été écrit (le miroir doit alors être relu)."""
    if not (actes["creer"] or actes["rattacher"]):
        return False
    for c in actes["creer"]:
        corps = {"name": c["nom"], "resource_subtype": c["type"]}
        if c["type"] in ("enum", "multi_enum"):
            corps["enum_options"] = [{"name": o} for o in c["options"]]
        d = api.ecrire("POST", f"/workspaces/{ws_cible}/custom_fields", corps)
        actes["rattacher"].append({"gid": d["gid"], "nom": c["nom"],
                                   "type": c["type"],
                                   "important": c["important"]})
        print(f"  champ « {c['nom']} » créé dans le workspace "
              f"({len(c['options'])} option(s))")
    for r in actes["rattacher"]:
        api.ecrire("POST", f"/projects/{projet_cible}/addCustomFieldSetting",
                   {"custom_field": r["gid"], "is_important": r["important"]})
        print(f"  champ « {r['nom']} » rattaché au miroir")
    return True


def resumer(actions):
    return (f"{len(actions['creer_lots'])} lot(s) à créer, "
            f"{len(actions['maj_lots'])} lot(s) à mettre à jour, "
            f"{len(actions['creer_sous_taches'])} sous-tâche(s) à créer, "
            f"{len(actions['maj_sous_taches'])} sous-tâche(s) à mettre à jour, "
            f"{len(actions['options'])} option(s) d'enum à créer ; "
            f"{len(actions['en_trop'])} tâche(s) du miroir sans équivalent "
            f"(signalées, jamais touchées)")


def creer_options(api, options):
    """Crée les options d'enum manquantes, à leur position de l'ordre source.
    Une option signalée déjà existante par Asana (« duplicate name ») est
    relue sur le champ pour récupérer son GID. Retourne
    {gid_champ: [{"gid", "nom"}...]} : les options créées ou retrouvées."""
    retours = {}
    for o in options:
        if not o["gid_champ"]:
            print(f"  ! option « {o['nom']} » : champ « {o['champ']} » "
                  f"absent du miroir")
            continue
        corps = {"name": o["nom"]}
        if o["insert_avant_gid"]:
            corps["insert_before"] = o["insert_avant_gid"]
        r = api.ecrire("POST", f"/custom_fields/{o['gid_champ']}/enum_options",
                       corps, quitter=False)
        if isinstance(r, dict) and r.get("erreur"):
            if "duplicate" in (r.get("detail") or ""):
                champ = api.get(f"/custom_fields/{o['gid_champ']}"
                                "?opt_fields=enum_options.gid,enum_options.name")
                trouve = next(({"gid": x["gid"], "nom": x.get("name") or ""}
                               for x in champ.get("enum_options") or []
                               if nom_option(x.get("name")) == nom_option(o["nom"])),
                              None)
                if trouve:
                    retours.setdefault(o["gid_champ"], []).append(trouve)
                    print(f"  option « {o['nom']} » déjà présente dans "
                          f"« {o['champ']} » (GID retrouvé par relecture)")
                else:
                    print(f"  ! option « {o['nom']} » : doublon signalé par "
                          f"Asana mais introuvable à la relecture")
            else:
                print(f"  ! option « {o['nom']} » non créée : "
                      f"{(r.get('detail') or '')[:140]}")
        else:
            retours.setdefault(o["gid_champ"], []).append(
                {"gid": r["gid"], "nom": r.get("name") or ""})
            print(f"  option « {o['nom']} » créée dans « {o['champ']} »")
    return retours


def appliquer_taches(api, actions, projet_cible):
    """Écrit les créations et mises à jour de lots et sous-tâches."""
    journal = []

    def note(ok, message):
        journal.append(("ok" if ok else "bloque", message))
        if not ok:
            print(f"  ! {message}")

    for c in actions["creer_lots"]:
        corps = {**c["corps"], "projects": [projet_cible]}
        t = api.ecrire("POST", "/tasks", corps)
        note(True, f"lot créé : {c['lot']['label'] or c['lot']['nom']}")
        c["gid_nouveau"] = t["gid"]

    # sous-tâches des lots créés : rattacher au nouveau GID
    par_contexte = {norm(c["lot"]["label"] or c["lot"]["nom"]):
                    c.get("gid_nouveau") for c in actions["creer_lots"]}

    for c in actions["creer_sous_taches"]:
        parent = c.get("parent_gid") or par_contexte.get(norm(c["parent"]))
        if not parent:
            note(False, f"sous-tâche « {c['sous_tache']['nom']} » : lot "
                        f"parent non résolu ({c['parent']})")
            continue
        api.ecrire("POST", f"/tasks/{parent}/subtasks", c["corps"])
        note(True, f"sous-tâche créée : {c['parent']} › "
                   f"{c['sous_tache']['nom']}")

    for m in actions["maj_lots"]:
        corps = m["corps"]
        if not corps:
            continue
        api.ecrire("PUT", f"/tasks/{m['lot']['gid']}", corps)
        note(True, f"lot mis à jour : {m['lot']['label'] or m['lot']['nom']}")

    for m in actions["maj_sous_taches"]:
        api.ecrire("PUT", f"/tasks/{m['sous_tache']['gid']}", m["corps"])
        note(True, f"sous-tâche mise à jour : {m['parent']} › "
                   f"{m['sous_tache']['nom']}")
    return journal


# --------------------------------------------------------------------- main

def liste_projets(api):
    """Liste les workspaces du token et leurs projets (pour trouver les GIDs)."""
    moi = api.get("/users/me?opt_fields=name,workspaces.gid,workspaces.name")
    print(f"Compte : {moi['name']}")
    for ws in moi.get("workspaces") or []:
        print(f"\nWorkspace {ws['name']} = {ws['gid']}")
        for p in api.pagination(
                f"/workspaces/{ws['gid']}/projects"
                "?opt_fields=name,archived,current_status"):
            if not p.get("archived"):
                print(f"    {p['name'][:70]} = {p['gid']}")


def main():
    parseur = argparse.ArgumentParser(
        description="Synchronise un projet Asana source (prod) vers un projet "
                    "miroir (sandbox). DRY-RUN par défaut : --apply pour écrire.")
    parseur.add_argument("--source", default=os.environ.get("ASANA_PROJET_SOURCE"),
                         help="GID du projet source (prod) ; défaut : ASANA_PROJET_SOURCE")
    parseur.add_argument("--cible", default=os.environ.get("ASANA_PROJET_MIROIR"),
                         help="GID du projet miroir (sandbox) ; défaut : ASANA_PROJET_MIROIR")
    parseur.add_argument("--liste-projets", action="store_true",
                         help="liste les workspaces et projets accessibles, puis s'arrête")
    parseur.add_argument("--apply", action="store_true",
                         help="écrit réellement dans le miroir (sinon simple simulation)")
    parseur.add_argument("--pause", type=float, default=0.2,
                         help="pause entre deux écritures API en s (défaut 0.2)")
    parseur.add_argument("--rapport", default=None,
                         help="chemin du rapport JSON (dossiers créés si besoin)")
    parseur.add_argument("--autoriser-prod-ecriture", action="store_true",
                         help="lève le verrou si la cible est dans le workspace "
                              "de production (à ne jamais utiliser en pratique)")
    opts = parseur.parse_args()

    charger_config_env()
    token = os.environ.get("ASANA_TOKEN")
    if not token:
        lieu = CONFIG_ENV_TROUVE or "config.env (à créer à côté du script)"
        sys.exit(f"ASANA_TOKEN absent : compléter {lieu}.")
    api = Api(token, pause=opts.pause)

    if opts.liste_projets:
        liste_projets(api)
        return

    if not opts.source or not opts.cible:
        sys.exit("Indiquer --source (GID projet prod) et --cible (GID projet "
                 "miroir sandbox). Pour trouver les GIDs : --liste-projets.")
    if opts.source == opts.cible:
        sys.exit("Source et cible identiques : rien à synchroniser.")

    print(f"Lecture du projet source {opts.source}...")
    source = lire_projet(api, opts.source)
    print(f"  {len(source['lots'])} lots, "
          f"{sum(len(l['sous_taches']) for l in source['lots'])} sous-tâches, "
          f"{len(source['champs'])} champs personnalisés "
          f"(« {source['meta'].get('name')} », workspace "
          f"{(source['meta'].get('workspace') or {}).get('name')})")

    print(f"Lecture du projet miroir {opts.cible}...")
    cible = lire_projet(api, opts.cible)
    ws_cible = (cible["meta"].get("workspace") or {}).get("gid")
    ws_source = (source["meta"].get("workspace") or {}).get("gid")

    # verrou anti-écriture en production
    ws_prod = os.environ.get("ASANA_WORKSPACE_PROD") or os.environ.get("ASANA_WORKSPACE")
    if opts.apply and ws_prod and ws_cible == ws_prod and not opts.autoriser_prod_ecriture:
        sys.exit("REFUS : le projet cible appartient au workspace de production. "
                 "Le miroir doit être dans la sandbox. (Verrou levable avec "
                 "--autoriser-prod-ecriture, à ne jamais utiliser.)")

    print(f"  {len(cible['lots'])} lots, "
          f"{sum(len(l['sous_taches']) for l in cible['lots'])} sous-tâches "
          f"(« {cible['meta'].get('name')} », workspace "
          f"{(cible['meta'].get('workspace') or {}).get('name')})")

    # champs personnalisés manquants sur le miroir (un projet neuf n'en a
    # aucun : créés dans le workspace cible puis rattachés au projet)
    actes_champs = preparer_actions_champs(source, cible)
    if actes_champs["creer"]:
        for c in actes_champs["creer"]:
            print(f"  champ à créer dans le workspace puis rattacher : "
                  f"« {c['nom']} » ({c['type']}, {len(c['options'])} option(s))")
        if opts.apply and appliquer_champs(api, ws_cible, opts.cible,
                                           actes_champs):
            print("  relecture du miroir (champs ajoutés)...")
            cible = lire_projet(api, opts.cible)

    cible_par_cle = {}
    for d in cible["champs"].values():
        cible_par_cle.setdefault(cle_champ(d["nom"], d["type"]), d)

    if opts.apply:
        cles_a_venir = None  # en apply, les champs sont déjà relus dans cible
    else:
        cles_a_venir = {cle_champ(c["nom"], c["type"])
                        for c in actes_champs["creer"]}
        cles_a_venir |= {cle_champ(r["nom"], r["type"])
                         for r in actes_champs["rattacher"]}
    actions = preparer_actions(source, cible, cible_par_cle,
                               cles_a_venir=cles_a_venir)
    print("\n" + resumer(actions))
    if actions["creer_lots"]:
        exemples = [c["lot"]["label"] or c["lot"]["nom"]
                    for c in actions["creer_lots"][:5]]
        print("  créations de lots (5 premières) : " + " | ".join(exemples))
    if actions["options"]:
        vus = {(o["champ"], o["nom"]) for o in actions["options"]}
        for champ, nom in sorted(vus):
            print(f"  option à créer : « {nom} » dans « {champ} »")

    if opts.apply:
        retours_options = {}
        if actions["options"]:
            retours_options = creer_options(api, actions["options"])
        if actes_champs["creer"] or retours_options:
            # champs et options en place : relire le miroir et recalculer les
            # actions tâches avec les définitions complètes
            print("  relecture du miroir (champs/options ajoutés)...")
            cible = lire_projet(api, opts.cible)
            for gid_cf, ajoutees in retours_options.items():
                defn = cible["champs"].get(gid_cf)
                if defn:
                    connus = {o["gid"] for o in defn["options"]}
                    for oa in ajoutees:
                        if oa["gid"] not in connus:
                            defn["options"].append(oa)
            cible_par_cle = {}
            for d in cible["champs"].values():
                cible_par_cle.setdefault(cle_champ(d["nom"], d["type"]), d)
            actions = preparer_actions(source, cible, cible_par_cle)
            print("Après mise à jour des champs : " + resumer(actions))
        journal = appliquer_taches(api, actions, opts.cible)
        print(f"\nTerminé : {api.ecritures} écriture(s) API.")
    else:
        print("DRY-RUN : rien n'est écrit (relancer avec --apply).")
        journal = []
        for b in actions["bloques"]:
            journal.append(("bloque", f"valeur non copiable : {b}"))
        for champ in actions["champs_absents"]:
            journal.append(("bloque", f"champ « {champ} » présent à la source "
                                      f"mais absent du miroir"))
    if opts.rapport:
        dossier = os.path.dirname(os.path.abspath(opts.rapport))
        if dossier:
            os.makedirs(dossier, exist_ok=True)
        rapport = {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "APPLY" if opts.apply else "DRY-RUN",
            "source": {"gid": opts.source, "nom": source["meta"].get("name")},
            "cible": {"gid": opts.cible, "nom": cible["meta"].get("name")},
            "resume": resumer(actions),
            "creer_lots": [c["lot"]["label"] or c["lot"]["nom"]
                           for c in actions["creer_lots"]],
            "maj_lots": [m["lot"]["label"] or m["lot"]["nom"]
                         for m in actions["maj_lots"]],
            "creer_sous_taches": [f"{c['parent']} › {c['sous_tache']['nom']}"
                                  for c in actions["creer_sous_taches"]],
            "maj_sous_taches": [f"{m['parent']} › {m['sous_tache']['nom']}"
                                for m in actions["maj_sous_taches"]],
            "options_a_creer": [f"« {o['nom']} » dans « {o['champ']} »"
                                for o in actions["options"]],
            "champs_miroir": {
                "crees_dans_workspace": [c["nom"] for c in actes_champs["creer"]],
                "rattaches": [r["nom"] for r in actes_champs["rattacher"]],
            },
            "taches_cible_en_trop": actions["en_trop"],
            "champs_absents_miroir": actions["champs_absents"],
            "journal": journal,
        }
        with open(opts.rapport, "w", encoding="utf-8") as fh:
            json.dump(rapport, fh, ensure_ascii=False, indent=2)
        print(f"Rapport JSON écrit : {opts.rapport}")


if __name__ == "__main__":
    main()
