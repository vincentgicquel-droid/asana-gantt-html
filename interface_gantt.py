#!/usr/bin/env python3
"""Interface de génération du planning Gantt HTML.

Petit serveur web local (bibliothèque standard uniquement) :
  1. liste les projets Asana accessibles avec le token configuré ;
  2. on choisit le projet, les vues et le dossier de sortie ;
  3. on clique « Générer en HTML » : les fichiers datés du jour sont
     générés dans le dossier de sortie et copiés vers les destinations
     configurées.

Lancement :
    export ASANA_TOKEN=<personal access token>
    python3 interface_gantt.py
    puis ouvrir http://localhost:8765

Configuration (voir aussi generer_gantt.py) :
    DOSSIER_SORTIE / DESTINATIONS : dossiers de sortie et copies supplémentaires
"""

import argparse
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generer_gantt import (DESTINATIONS, DOSSIER_SORTIE, calculer_fins,
                           extraire_asana, generer_vues, resoudre_dependances)

PORT = 8765


def charger_config_env():
    """Complète l'environnement avec un fichier config.env trouvé dans le
    dossier du script ou un dossier parent (ASANA_TOKEN, ASANA_WORKSPACE...).
    Les variables déjà présentes dans l'environnement ne sont pas écrasées."""
    dossier = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        chemin = os.path.join(dossier, "config.env")
        if os.path.isfile(chemin):
            with open(chemin, encoding="utf-8") as fh:
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


charger_config_env()


def token_asana():
    """Token depuis ~/.asana-pat-sandbox (prioritaire, token validé en réel),
    sinon depuis l'environnement (ASANA_TOKEN, éventuellement issue de config.env)."""
    chemin = os.path.expanduser("~/.asana-pat-sandbox")
    if os.path.isfile(chemin):
        with open(chemin) as fh:
            token = fh.read().strip().strip('<>" ')
        if token:
            return token
    return os.environ.get("ASANA_TOKEN") or None


def api_get(chemin, params=None):
    """Appel GET à l'API Asana avec le token de l'environnement."""
    token = token_asana()
    if not token:
        raise RuntimeError("Variable ASANA_TOKEN absente : exporter le token avant de lancer l'interface.")
    url = f"https://app.asana.com/api/1.0/{chemin}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    ctx = ssl.create_default_context()
    with urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)).open(req) as reponse:
        return json.load(reponse)


def lister_projets():
    """Projets accessibles avec le token, workspace par workspace."""
    moi = api_get("users/me", {"opt_fields": "workspaces.name,workspaces.gid"})
    projets = []
    for ws in moi.get("data", {}).get("workspaces") or []:
        params = {"limit": 100, "opt_fields": "name,workspace.name", "workspace": ws["gid"]}
        while True:
            d = api_get("projects", params)
            for p in d.get("data", []):
                projets.append({
                    "gid": p["gid"],
                    "nom": p.get("name") or p["gid"],
                    "workspace": ws.get("name") or (p.get("workspace") or {}).get("name") or "",
                    "workspace_gid": ws["gid"],
                })
            nxt = d.get("next_page")
            if not nxt:
                break
            params["offset"] = nxt["offset"]
    projets.sort(key=lambda p: (p["workspace"], p["nom"].lower()))
    return projets


def generer_depuis_api(gid_projet, titre, vues, dossier, destinations, workspace_gid=None):
    lots, ordre_scopes = extraire_asana(gid_projet, workspace_gid)
    resoudre_dependances(lots)
    calculer_fins(lots)
    from datetime import date
    base = f"planning-{date.today():%Y-%m-%d}"
    return generer_vues(lots, vues, base, dossier, titre=titre,
                        libelle_projet=titre, ordre_scopes=ordre_scopes,
                        destinations=destinations)


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Planning Gantt : génération HTML</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: Arial, Helvetica, sans-serif; background: #f4f5f7; min-height: 100vh; display: flex; align-items: flex-start; justify-content: center; padding: 48px 16px; }
  .carte { background: #fff; border-radius: 10px; box-shadow: 0 2px 12px rgba(0,0,0,.12); width: 640px; padding: 32px 36px; }
  h1 { font-size: 22px; color: #1f1f38; margin-bottom: 4px; }
  .sous-titre { font-size: 13px; color: #777; margin-bottom: 24px; }
  label { display: block; font-size: 12.5px; font-weight: bold; color: #333; margin: 16px 0 6px; }
  select, input[type=text] { width: 100%; padding: 9px 10px; border: 1px solid #bbb; border-radius: 6px; font-size: 14px; }
  .vues { display: flex; gap: 18px; margin-top: 4px; }
  .vues label { display: flex; align-items: center; gap: 6px; font-weight: normal; font-size: 13.5px; margin: 0; }
  .dest-note { font-size: 12px; color: #777; margin-top: 4px; }
  button { margin-top: 24px; width: 100%; padding: 12px; font-size: 15px; font-weight: bold; color: #fff; background: #f06a6a; border: none; border-radius: 6px; cursor: pointer; }
  button:hover { background: #e05555; }
  button:disabled { background: #bbb; cursor: wait; }
  #resultat { margin-top: 20px; font-size: 13.5px; }
  #resultat .err { color: #c00; white-space: pre-wrap; }
  #resultat .fichier { margin: 6px 0; padding: 8px 10px; background: #f7f8fa; border-radius: 6px; }
  #resultat .fichier a { color: #1a7fd4; font-weight: bold; text-decoration: none; }
  #resultat .copie { color: #2e7d32; font-size: 12px; }
  .attente { color: #b36b00; }
</style>
</head>
<body>
<div class="carte">
  <h1>Génération du planning HTML</h1>
  <div class="sous-titre">Choisir le projet, puis générer les fichiers datés du jour</div>

  <label for="projet">Projet Asana</label>
  <select id="projet"><option>Chargement des projets…</option></select>

  <label for="titre">Titre affiché sur le planning</label>
  <input type="text" id="titre" placeholder="PLANNING …">

  <label>Vues à générer</label>
  <div class="vues">
    <label><input type="checkbox" name="vue" value="synthese" checked> Synthèse</label>
    <label><input type="checkbox" name="vue" value="jalons" checked> Jalons</label>
    <label><input type="checkbox" name="vue" value="detail"> Détaillée</label>
  </div>

  <label for="dossier">Dossier de sortie</label>
  <input type="text" id="dossier" value="__DOSSIER__">
  <div class="dest-note">Copies supplémentaires configurées : __DESTINATIONS__</div>

  <button id="go" onclick="generer()">Générer en HTML</button>
  <div id="resultat"></div>
</div>
<script>
const sel = document.getElementById('projet');
fetch('/api/projets').then(r => r.json()).then(d => {
  if (d.erreur) { sel.innerHTML = `<option>${d.erreur}</option>`; return; }
  if (!d.projets.length) { sel.innerHTML = '<option>Aucun projet accessible avec ce token</option>'; return; }
  let dernierWs = null;
  sel.innerHTML = d.projets.map(p => {
    const opt = document.createElement('option');
    opt.value = p.gid; opt.textContent = (dernierWs !== p.workspace ? p.workspace + ' : ' : '') + p.nom;
    opt.dataset.nom = p.nom; opt.dataset.ws = p.workspace_gid; dernierWs = p.workspace;
    return opt.outerHTML;
  }).join('');
  majTitre();
});
sel.addEventListener('change', majTitre);
function majTitre() {
  const o = sel.selectedOptions[0];
  if (o && o.dataset.nom) document.getElementById('titre').value = 'PLANNING ' + o.dataset.nom.toUpperCase();
}
async function generer() {
  const res = document.getElementById('resultat');
  const btn = document.getElementById('go');
  const vues = [...document.querySelectorAll('input[name=vue]:checked')].map(c => c.value);
  if (!vues.length) { res.innerHTML = '<span class="err">Choisir au moins une vue.</span>'; return; }
  const body = new URLSearchParams();
  body.set('projet', sel.value);
  body.set('workspace', sel.selectedOptions[0]?.dataset.ws || '');
  body.set('titre', document.getElementById('titre').value);
  body.set('dossier', document.getElementById('dossier').value);
  vues.forEach(v => body.append('vue', v));
  btn.disabled = true;
  res.innerHTML = '<span class="attente">Génération en cours, cela peut prendre une à deux minutes…</span>';
  try {
    const r = await fetch('/generer', { method: 'POST', body });
    const d = await r.json();
    if (d.erreur) { res.innerHTML = `<span class="err">${d.erreur}</span>`; }
    else {
      res.innerHTML = d.fichiers.map(f =>
        `<div class="fichier"><a href="file://${f.chemin}">${f.chemin.split('/').pop()}</a>`
        + `<div class="copie">${f.copies.length ? 'copié vers : ' + f.copies.join(', ') : 'aucune copie supplémentaire'}</div></div>`
      ).join('');
    }
  } catch (e) {
    res.innerHTML = `<span class="err">Échec : ${e}</span>`;
  }
  btn.disabled = false;
}
</script>
</body>
</html>
"""


class Traitement(BaseHTTPRequestHandler):

    def _repondre(self, code, corps, type_="text/html; charset=utf-8"):
        data = corps.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            page = PAGE.replace("__DOSSIER__", DOSSIER_SORTIE)
            page = page.replace("__DESTINATIONS__",
                                ", ".join(DESTINATIONS) if DESTINATIONS else "aucune (configurable dans generer_gantt.py)")
            self._repondre(200, page)
        elif self.path == "/api/projets":
            try:
                self._repondre(200, json.dumps({"projets": lister_projets()}), "application/json; charset=utf-8")
            except (Exception, SystemExit) as e:
                self._repondre(200, json.dumps({"erreur": f"Impossible de lister les projets : {e}"}),
                               "application/json; charset=utf-8")
        else:
            self._repondre(404, "Introuvable")

    def do_POST(self):
        if self.path != "/generer":
            self._repondre(404, "Introuvable")
            return
        taille = int(self.headers.get("Content-Length") or 0)
        formulaire = urllib.parse.parse_qs(self.rfile.read(taille).decode("utf-8"))
        gid = (formulaire.get("projet") or [""])[0]
        workspace = (formulaire.get("workspace") or [""])[0] or None
        titre = (formulaire.get("titre") or ["PLANNING DE PROJET"])[0].strip() or "PLANNING DE PROJET"
        vues = formulaire.get("vue") or ["synthese", "jalons"]
        dossier = (formulaire.get("dossier") or [""])[0].strip() or DOSSIER_SORTIE
        try:
            produits = generer_depuis_api(gid, titre, vues, dossier, list(DESTINATIONS),
                                          workspace_gid=workspace)
            fichiers = [{"chemin": os.path.abspath(chemin), "copies": copies} for chemin, _vue, copies in produits]
            self._repondre(200, json.dumps({"fichiers": fichiers}, ensure_ascii=False),
                           "application/json; charset=utf-8")
        except (Exception, SystemExit) as e:
            message = str(e) or "Erreur inconnue pendant la génération."
            self._repondre(200, json.dumps({"erreur": message}, ensure_ascii=False),
                           "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    global PORT
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=PORT, help=f"port du serveur (défaut : {PORT})")
    args = parser.parse_args()
    PORT = args.port

    if not token_asana():
        print("ATTENTION : variable ASANA_TOKEN absente, la liste des projets sera vide.")
    print(f"Interface de génération : http://localhost:{PORT}")
    print(f"Dossier de sortie : {DOSSIER_SORTIE}"
          + (f" + copies : {', '.join(DESTINATIONS)}" if DESTINATIONS else ""))
    serveur = ThreadingHTTPServer(("127.0.0.1", PORT), Traitement)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
