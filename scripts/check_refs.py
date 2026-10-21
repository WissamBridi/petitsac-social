#!/usr/bin/env python3
"""
Vérifie TOUTES les références des posts (file + posts gelés) avant intégration :
  - chaque URL de la légende répond (HTTP 200) et n'est pas redirigée vers une autre page (ex. page d'accueil générique) ;
  - chaque compte Instagram mentionné (@pseudo) ou tagué (user_tags) existe et peut être identifié
    (préparation à blanc d'un container Instagram : rien n'est publié, le container expire seul) ;
  - chaque post qui cite un client (champ "references") contient bien au moins un lien vérifiable.

Usage : IG_USER_ID=… IG_ACCESS_TOKEN=… BASE_URL=… python3 scripts/check_refs.py [fichier.json ...]
Code retour 1 si un problème est trouvé.
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = sys.argv[1:] or [os.path.join(ROOT, "content", "posts.json"), os.path.join(ROOT, "content", "hold.json")]
IG = os.environ.get("IG_USER_ID", ""); TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
BASE = os.environ.get("BASE_URL", "").rstrip("/") + "/"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/155 Safari/537.36"}
OWN = {"petitsac_sng"}

def norm(u):
    p = urllib.parse.urlsplit(u)
    return (p.netloc.lower().removeprefix("www."), p.path.rstrip("/") or "/")

def check_url(u):
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            final = r.geturl()
            if r.status != 200: return "HTTP %s" % r.status
            if norm(final) != norm(u): return "redirigé vers %s" % final
            return None
    except urllib.error.HTTPError as e: return "HTTP %s" % e.code
    except Exception as e: return "inaccessible (%s)" % e

def check_handle(h, image):
    p = {"image_url": BASE + image, "caption": "verification", "access_token": TOKEN,
         "user_tags": json.dumps([{"username": h, "x": 0.5, "y": 0.5}])}
    data = urllib.parse.urlencode(p).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://graph.facebook.com/v21.0/%s/media" % IG, data=data), timeout=60) as r:
            return None if "id" in json.loads(r.read()) else "réponse inattendue"
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read()).get("error", {}).get("error_user_msg") or "refusé"
        except Exception: return "refusé"

def main():
  problems = 0; cache = {}
  for f in FILES:
      if not os.path.exists(f): continue
      for post in json.load(open(f, encoding="utf-8")):
          cap = post.get("caption", "") + "\n" + post.get("comment", ""); pid = post.get("id"); issues = []
          urls = re.findall(r"https?://[^\s)»]+", cap)
          handles = set(re.findall(r"(?<![\w.])@([A-Za-z0-9_.]{2,30})", cap))
          handles |= {(t if isinstance(t, str) else t["username"]).lstrip("@") for t in post.get("user_tags", [])}
          handles -= OWN
          for u in urls:
              u = u.rstrip(".,;:")
              if u not in cache: cache[u] = check_url(u)
              if cache[u]: issues.append("LIEN %s : %s" % (u, cache[u]))
          for h in sorted(handles):
              k = "@" + h
              if k not in cache: cache[k] = check_handle(h, (post.get("images") or [post.get("cover")])[0]) if (IG and TOKEN and BASE.strip("/")) else "non vérifié (pas de token)"
              if cache[k]: issues.append("COMPTE @%s : %s" % (h, cache[k]))
          if post.get("references") and not urls and not handles:
              issues.append("RÉFÉRENCE sans aucun lien (%s)" % ", ".join(post["references"]))
          problems += len(issues)
          print(("✖ " if issues else "✔ ") + "%-24s liens:%d comptes:%d %s" % (pid, len(urls), len(handles), os.path.basename(f)))
          for i in issues: print("   - " + i)
  print("\nTotal problèmes :", problems)
  sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
