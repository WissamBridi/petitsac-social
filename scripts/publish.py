#!/usr/bin/env python3
"""
Publication automatique quotidienne sur Instagram (@petitsac_sng) via l'API Graph.
Lit une file d'attente datee (content/posts.json), publie le post du jour
(image unique OU carrousel), puis journalise dans content/published.json.

Variables d'environnement attendues (fournies par GitHub Actions / .env) :
  IG_USER_ID        identifiant du compte Instagram pro
  IG_ACCESS_TOKEN   token systeme non-expirant
  BASE_URL          URL publique des images (GitHub Pages), ex:
                    https://<user>.github.io/petitsac-social/images/
  GRAPH_VERSION     optionnel (defaut v21.0)
  DRY_RUN           optionnel: "1" prepare sans publier
  FORCE_ID          optionnel: publie ce post precis (ignore la date)

Images referencees par leur nom de fichier dans posts.json ; l'URL finale = BASE_URL + nom.
Reel : {"type": "reel", "video": "x.mp4", "cover": "x.jpg", "caption": ..., "share_to_feed": true}
(video MP4 H.264 + AAC 48 kHz <= 128 kb/s, 9:16, 3 a 90 s ; hebergee comme les images).
"""
import datetime, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS = os.path.join(ROOT, "content", "posts.json")
PUBLISHED = os.path.join(ROOT, "content", "published.json")
SONS = os.path.join(ROOT, "content", "sons.json")   # sons tendance pressentis (planning)

IG = os.environ.get("IG_USER_ID", "").strip()
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
BASE = os.environ.get("BASE_URL", "").strip().rstrip("/") + "/"
VER = os.environ.get("GRAPH_VERSION", "v21.0").strip()
DRY = os.environ.get("DRY_RUN", "") == "1"
FORCE_ID = os.environ.get("FORCE_ID", "").strip()
# Deux files independantes : le post du jour (image/carrousel) et le Reel du jour, chacune 1 fois par jour.
KIND = os.environ.get("KIND", "post").strip() or "post"
if os.environ.get("POSTS_FILE"): POSTS = os.environ["POSTS_FILE"]   # test local uniquement


def kind_of(p): return "reel" if p.get("type") == "reel" else "post"


# Plage horaire de publication (heure du Sénégal = UTC). GitHub déclenche parfois ses créneaux avec des HEURES
# de retard : un créneau du soir arrivé après minuit publiait en pleine nuit ET consommait le quota du jour
# suivant (incident du 22/10). Hors plage : on ne publie rien, le prochain créneau du jour s'en chargera.
WINDOW = {"post": (19 * 60, 23 * 60 + 30), "reel": (16 * 60, 23 * 60 + 50)}   # Reels à 16 h, photos à 19 h (demande de Wissam, 29/10)


def in_window(kind, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    m = now.hour * 60 + now.minute; a, b = WINDOW[kind]
    return a <= m <= b


def log(m): print("[%s] %s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), m), flush=True)


def load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def api(method, node, params, ver=None):
    url = "https://graph.facebook.com/%s/%s" % (ver or VER, node)
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params); data = None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read().decode())
        except Exception: return {"error": {"message": "HTTP %s" % e.code}}
    except Exception as e:
        return {"error": {"message": str(e)}}


def wait_ready(cid, tries=20, pause=3):
    for _ in range(tries):
        st = api("GET", cid, {"fields": "status_code", "access_token": TOKEN})
        sc = st.get("status_code")
        if sc == "FINISHED": return True
        if sc == "ERROR": log("container ERROR: %s" % json.dumps(st)); return False
        time.sleep(pause)
    return tries <= 20   # image : on tente quand meme ; video : pas prete = echec


def norm_tags(tags):
    """user_tags : liste de pseudos Instagram (str) ou de {username, x, y}. Position par défaut : centre."""
    out = []
    for t in tags or []:
        if isinstance(t, str): t = {"username": t}
        out.append({"username": t["username"].lstrip("@"), "x": float(t.get("x", 0.5)), "y": float(t.get("y", 0.5))})
    return out


def create_image_container(img_url, caption=None, carousel_item=False, user_tags=None):
    p = {"image_url": img_url, "access_token": TOKEN}
    if caption is not None: p["caption"] = caption
    if user_tags: p["user_tags"] = json.dumps(norm_tags(user_tags))
    if carousel_item: p["is_carousel_item"] = "true"
    r = api("POST", IG + "/media", p)
    if "error" in r: log("echec container (%s): %s" % (img_url, r["error"])); return None
    return r.get("id")


def caption_key(text):
    """Empreinte d'une légende : ses 100 premiers caractères, espaces normalisés. Identifie un contenu sur le fil."""
    return re.sub(r"\s+", " ", text or "").strip()[:100]


def find_on_instagram(post, limit=50):
    """Ce contenu (même légende) est-il déjà sur le compte, quel que soit le jour ?
    Renvoie le média trouvé, None s'il n'y est pas, False si le fil est illisible."""
    key = caption_key(post.get("caption"))
    if not key: return None
    r = api("GET", IG + "/media", {"fields": "id,timestamp,permalink,caption", "limit": str(limit), "access_token": TOKEN})
    if "error" in r: log("(info) lecture du fil impossible : %s" % r["error"]); return False
    for m in r.get("data", []):
        if caption_key(m.get("caption")) == key: return m
    return None


def publish_container(cid, post=None):
    r = api("POST", IG + "/media_publish", {"creation_id": cid, "access_token": TOKEN})
    if "error" in r:
        log("echec publication: %s" % r["error"])
        # Incident du 02/11 : Instagram répond « erreur inattendue » alors que le média EST publié.
        # Le croire en échec le laissait hors du journal, et il était republié le lendemain. On vérifie.
        st = api("GET", cid, {"fields": "status_code", "access_token": TOKEN}).get("status_code")
        for _ in range(6 if post else 0):
            time.sleep(10)
            m = find_on_instagram(post)
            if m:
                log("Malgre l'erreur, le media est en ligne (container %s) : on le journalise." % st)
                return m["id"], m.get("permalink", "")
        if st == "PUBLISHED":
            log("ALERTE: container PUBLISHED mais media introuvable sur le fil ; verifier le compte.")
        return None
    mid = r.get("id")
    link = api("GET", mid, {"fields": "permalink", "access_token": TOKEN})
    return mid, link.get("permalink", "")


def token_scopes():
    r = api("GET", "debug_token", {"input_token": TOKEN, "access_token": TOKEN})
    return set((r.get("data") or {}).get("scopes") or [])


def post_comment(media_id, message):
    r = api("POST", "%s/comments" % media_id, {"message": message, "access_token": TOKEN})
    if "error" in r: log("echec commentaire: %s" % r["error"]); return None
    return r.get("id")


# Son tendance Instagram posé sur le Reel à la publication (API Audio, Facebook Login, v22.0 minimum).
# Champ "audio" du post : absent ou "auto" = son tendance du jour ; {"id", "title", "audio_volume", "video_volume"} = son choisi ;
# "none" = pas de son ajouté. Volumes par défaut : notre voix devant, la tendance en fond.
AUDIO_VER = VER if VER >= "v22.0" else "v22.0"
GENERIC_SOUND = re.compile(r"^(audio d.origine|original audio|son original)$", re.I)
VOL = {"music_bed": 18, "no_bed": 30, "photo": 100}   # notre Reel a déjà sa musique (bed) / voix + effets seuls (sans_musique) / Reel photo sans voix (photo_reel)


# Un titre de chanson (remix, mashup « A x B », reprise, « Titre - Artiste »…) : jamais un son parlé (conseil, sketch, voix off),
# qui couvrirait notre narration.
SONG = re.compile(r"(\bremix\b|\bmix\b|\bcover\b|\bversion\b|\bedit\b|sped up|slowed|instrumental|\bfeat\b|\bft\.|\s[x×]\s|\s-\s|\sby\s|\bsong\b)", re.I)


def audio_candidates(recent_ids, min_ms=25000):
    """Les sons ultra tendance du moment, du plus utilisé au moins utilisé : les sons Reels populaires (liste
    « original_sound » de Meta) en ne gardant que les MORCEAUX DE MUSIQUE (titre de chanson), puis la musique tendance
    de la bibliothèque Meta. Assez longs pour couvrir le Reel, jamais déjà posés sur nos derniers Reels."""
    out, seen = [], set()
    for kind in ("original_sound", "music"):
        r = api("GET", "ig_audio", {"audio_type": kind, "user_id": IG, "access_token": TOKEN}, ver=AUDIO_VER)
        if "error" in r: log("(info) sons tendance %s indisponibles: %s" % (kind, r["error"].get("message"))); continue
        for a in r.get("audio") or []:
            t = (a.get("title") or "").strip(); i = a.get("audio_id")
            if not t or not i or i in seen or GENERIC_SOUND.match(t) or i in recent_ids: continue
            if kind == "original_sound" and not SONG.search(t): continue
            if (a.get("duration_in_ms") or 0) < min_ms: continue
            seen.add(i)
            out.append({"id": i, "title": t, "artist": a.get("display_artist") or a.get("ig_username") or "", "type": kind,
                        "preview": a.get("download_url") or "", "duration_ms": a.get("duration_in_ms") or 0})
    return out


def trending_audio(recent_ids, min_ms=25000, prefer=None):
    """Le son tendance à poser. `prefer` = le son pressenti dans content/sons.json (celui que Wissam a écouté dans
    le planning) : repris s'il est toujours dans les tendances du jour, sinon le plus tendance du moment.
    Aucun son = None (le Reel part avec sa version « video_musique »)."""
    c = audio_candidates(recent_ids, min_ms)
    for a in c:
        if prefer and a["id"] == prefer: return a
    return c[0] if c else None


def forecast_sounds(posts, published):
    """content/sons.json : le son tendance pressenti pour chaque Reel à venir, dans l'ordre de la file, pour l'écouter
    dans le planning. Les extraits sont lus sur les serveurs de Meta (liens temporaires, rafraîchis plusieurs fois par jour) :
    aucune musique n'est copiée dans le dépôt."""
    done = {e["id"] for e in published}
    recent = {e["audio"]["id"] for e in published[-40:] if isinstance(e.get("audio"), dict)}
    reels = [p for p in posts if p.get("type") == "reel" and p["id"] not in done]
    reels = sorted([p for p in reels if p.get("date")], key=lambda p: p["date"]) + [p for p in reels if not p.get("date")]
    cands = audio_candidates(recent); used = set(); items = {}
    for p in reels:
        want = p.get("audio", "auto")
        if isinstance(want, dict):
            items[p["id"]] = dict(want, fixed=True); continue
        a = next((c for c in cands if c["id"] not in used), None)
        if a: used.add(a["id"])
        items[p["id"]] = a   # None = la version avec notre musique d'ambiance
    vol = {"no_bed": VOL["no_bed"], "music_bed": VOL["music_bed"]}
    out = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "volumes": vol, "items": items}
    save(SONS, out)
    log("Sons pressentis : %d Reels, %d avec un son tendance." % (len(items), sum(1 for v in items.values() if v)))
    return out


def audio_config(post, recent_ids):
    want = post.get("audio", "auto")
    if want == "none": return None
    prefer = ((load(SONS, {}).get("items") or {}).get(post.get("id")) or {}).get("id")
    a = want if isinstance(want, dict) else trending_audio(recent_ids, prefer=prefer)
    if not a: return None
    vol = a.get("audio_volume") or VOL["photo" if post.get("photo_reel") else "no_bed" if post.get("sans_musique") else "music_bed"]
    return {"id": a["id"], "title": a.get("title", ""), "artist": a.get("artist", ""),
            "conf": {"audio_id": a["id"], "audio_volume": int(vol), "video_volume": int(a.get("video_volume") or 100)}}


def publish_reel(post):
    p = {"media_type": "REELS", "video_url": BASE + post["video"], "caption": post.get("caption", ""),
         "share_to_feed": "true" if post.get("share_to_feed", True) else "false", "access_token": TOKEN}
    if post.get("cover"): p["cover_url"] = BASE + post["cover"]
    if post.get("user_tags"): p["user_tags"] = json.dumps([{"username": t["username"] if isinstance(t, dict) else t.lstrip("@")} for t in post["user_tags"]])
    au = audio_config(post, post.get("_recent_audio", set()))
    r = None
    if au:
        r = api("POST", IG + "/media", dict(p, audio_configuration=json.dumps(au["conf"])), ver=AUDIO_VER)
        if "error" in r:
            log("(info) son tendance refuse (%s)." % r["error"].get("message")); au = None
        else:
            log("Son tendance : %s%s (volume %d)" % (au["title"], " · " + au["artist"] if au["artist"] else "", au["conf"]["audio_volume"]))
    if not au:
        if post.get("video_musique"):
            p["video_url"] = BASE + post["video_musique"]
            log("Pas de son tendance pose : publication de la version avec notre musique d'ambiance (%s)." % post["video_musique"])
        elif post.get("sans_musique"):
            log("ALERTE: Reel sans son tendance ni version musicale : il partirait sans musique ; le Reel reste en file."); return None
        r = api("POST", IG + "/media", p)
    post["_audio"] = au
    if "error" in r: log("echec container reel: %s" % r["error"]); return None
    cid = r.get("id")
    log("Reel en traitement chez Instagram (container %s)..." % cid)
    if not wait_ready(cid, tries=72, pause=5): log("ECHEC: la video n'a pas ete traitee a temps"); return None
    if DRY:
        log("DRY_RUN: Reel pret a publier, non envoye. creation_id=%s" % cid); return "dry"
    res = publish_container(cid, post)
    if not res: return None
    mid, link = res
    log("PUBLIE (Reel) media_id=%s %s" % (mid, link))
    post["_media_id"] = mid
    return link


def publish_post(post):
    if post.get("type") == "reel": return publish_reel(post)
    imgs = post.get("images", [])
    caption = post.get("caption", "")
    if not imgs: log("post sans image"); return None
    if len(imgs) == 1:
        cid = create_image_container(BASE + imgs[0], caption=caption, user_tags=post.get("user_tags"))
        if not cid or not wait_ready(cid): return None
    else:
        children = []
        for name in imgs[:10]:
            c = create_image_container(BASE + name, carousel_item=True, user_tags=post.get("user_tags") if name == imgs[0] else None)
            if not c: return None
            children.append(c)
        for c in children:
            wait_ready(c)
        cont = api("POST", IG + "/media", {"media_type": "CAROUSEL",
                   "children": ",".join(children), "caption": caption, "access_token": TOKEN})
        if "error" in cont: log("echec carrousel: %s" % cont["error"]); return None
        cid = cont.get("id")
        if not wait_ready(cid): return None
    if DRY:
        log("DRY_RUN: pret a publier, non envoye. creation_id=%s" % cid); return "dry"
    res = publish_container(cid, post)
    if not res: return None
    mid, link = res
    log("PUBLIE media_id=%s %s" % (mid, link))
    post["_media_id"] = mid
    return link


def already_on_instagram(kind):
    """Garde-fou indépendant du journal : un média du même type déjà publié aujourd'hui (UTC) sur le compte ?
    Évite un doublon si deux déclencheurs (GitHub en retard + secours local) passent le même jour."""
    r = api("GET", IG + "/media", {"fields": "id,timestamp,media_type,media_product_type,permalink", "limit": "10", "access_token": TOKEN})
    if "error" in r: log("(info) lecture du fil impossible : %s" % r["error"]); return None
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    for m in r.get("data", []):
        if not str(m.get("timestamp", "")).startswith(today): continue
        is_reel = m.get("media_product_type") == "REELS" or m.get("media_type") == "VIDEO"
        if (kind == "reel") == is_reel: return m.get("permalink") or m.get("id")
    return None


def pick(posts, done_ids):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()   # heure du Sénégal (UTC), pas celle du Mac
    if FORCE_ID:
        for p in posts:
            if p.get("id") == FORCE_ID: return p
        log("FORCE_ID introuvable: %s" % FORCE_ID); return None
    posts = [p for p in posts if kind_of(p) == KIND]
    # 1) posts dates <= aujourd'hui non publies, le plus ancien d'abord
    dated = sorted([p for p in posts if p.get("date") and p["id"] not in done_ids],
                   key=lambda p: p["date"])
    for p in dated:
        if p["date"] <= today: return p
    # 2) sinon, file simple: premier non publie et sans date
    for p in posts:
        if not p.get("date") and p["id"] not in done_ids: return p
    return None


def main():
    if "--sons" in sys.argv:   # prévision des sons pour le planning, sans rien publier
        if not (IG and TOKEN): log("variables manquantes: IG_USER_ID / IG_ACCESS_TOKEN"); sys.exit(2)
        forecast_sounds(load(POSTS, []), load(PUBLISHED, [])); return
    miss = [k for k, v in {"IG_USER_ID": IG, "IG_ACCESS_TOKEN": TOKEN, "BASE_URL": BASE.strip("/")}.items() if not v]
    if miss: log("variables manquantes: %s" % miss); sys.exit(2)
    posts = load(POSTS, [])
    published = load(PUBLISHED, [])
    done = {e["id"] for e in published}
    # Garde-fou : un seul post par jour (heure du Sénégal = UTC). Plusieurs créneaux cron existent
    # pour rattraper un déclenchement GitHub sauté ; seul le premier qui passe publie.
    if not FORCE_ID and not in_window(KIND):
        lo, hi = WINDOW[KIND]
        msg = "Hors plage de publication %s (%02d:%02d-%02d:%02d UTC) : rien a faire." % (KIND, lo // 60, lo % 60, hi // 60, hi % 60)
        if DRY: log("(info) " + msg + " DRY_RUN continue pour tester la chaine.")
        else: log(msg); return
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    already = [e["id"] for e in published if str(e.get("published_at", ""))[:10] == today and e.get("kind", "post") == KIND]
    if already and not FORCE_ID:
        if DRY:
            log("(info) deja publie aujourd'hui (%s) ; DRY_RUN continue quand meme pour tester la chaine." % already[0])
        else:
            log("Deja publie aujourd'hui (%s, file %s), rien a faire sur ce creneau." % (already[0], KIND)); return
    if not FORCE_ID:
        live = already_on_instagram(KIND)
        if live:
            if DRY: log("(info) un média '%s' est déjà en ligne aujourd'hui (%s) ; DRY_RUN continue." % (KIND, live))
            else: log("Deja en ligne aujourd'hui sur Instagram (%s, file %s), rien a faire." % (live, KIND)); return
    if DRY:
        log("(info) permission commentaire du token : %s" % ("OUI" if "instagram_manage_comments" in token_scopes() else "NON"))
    # Garde-fou anti-doublon, indépendant du journal et du jour : un contenu dont la légende est déjà
    # sur le fil n'est jamais republié. Il est journalisé (rattrapage) et la file passe au suivant.
    for _ in range(5):
        post = pick(posts, done)
        if not post: break
        live = find_on_instagram(post)
        if live is False:
            log("ECHEC: fil Instagram illisible, impossible d'exclure un doublon ; on reessaiera au prochain creneau."); sys.exit(1)
        if not live: break
        when = datetime.datetime.strptime(live["timestamp"], "%Y-%m-%dT%H:%M:%S%z").astimezone(datetime.timezone.utc)
        log("DOUBLON EVITE : '%s' est deja en ligne depuis le %s (%s) ; journalise sans republier." % (post["id"], when.strftime("%d/%m %H:%M"), live.get("permalink")))
        if DRY:
            log("(info) DRY_RUN : journal non modifie."); return
        published.append({"id": post["id"], "kind": kind_of(post), "published_at": when.isoformat(), "permalink": live.get("permalink", ""), "rattrape": True})
        save(PUBLISHED, published); done.add(post["id"])
        if when.date().isoformat() == today and not FORCE_ID: return   # le créneau du jour est déjà pris
        post = None
    if not post:
        log("Rien a publier aujourd'hui."); return
    log("Publication du post '%s' (%s)" % (post.get("id"), "Reel" if post.get("type") == "reel" else "%d image(s)" % len(post.get("images", []))))
    # Règle : toute référence/source doit être vérifiable en ligne au moment de publier.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import check_refs
    for u in re.findall(r"https?://[^\s)»]+", post.get("caption", "")):
        err = check_refs.check_url(u.rstrip(".,;:"))
        if err:
            log("ECHEC: lien invalide %s (%s) ; le post reste en file." % (u, err)); sys.exit(1)
    if post.get("comment"):
        if "instagram_manage_comments" not in token_scopes():
            log("ECHEC: ce post doit avoir son lien en commentaire, mais le token n'a pas la permission "
                "instagram_manage_comments ; publication refusee, le post reste en file."); sys.exit(1)
        for u in re.findall(r"https?://[^\s)»]+", post["comment"]):
            err = check_refs.check_url(u.rstrip(".,;:"))
            if err: log("ECHEC: lien du commentaire invalide %s (%s) ; le post reste en file." % (u, err)); sys.exit(1)
    if post.get("references") and not re.search(r"https?://|@\w", post.get("caption", "") + " " + post.get("comment", "")) and not post.get("user_tags"):
        log("ECHEC: reference client sans lien verifiable ; le post reste en file."); sys.exit(1)
    post["_recent_audio"] = {e["audio"]["id"] for e in published[-40:] if isinstance(e.get("audio"), dict)}   # ~3 semaines de Reels sans répéter un son
    link = publish_post(post)
    if link is None:
        log("ECHEC: le post reste en file pour le prochain run."); sys.exit(1)
    if not DRY:
        entry = {"id": post["id"], "kind": kind_of(post), "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "permalink": link if isinstance(link, str) else ""}
        if post.get("_audio"): entry["audio"] = {k: post["_audio"][k] for k in ("id", "title", "artist")}
        comment_failed = False
        if post.get("comment"):
            cid = post_comment(post.get("_media_id"), post["comment"])
            entry["comment_ok"] = bool(cid)
            comment_failed = not cid
            log(("COMMENTAIRE poste (id=%s)" % cid) if cid else "ALERTE: post publie mais commentaire NON poste")
        published.append(entry)
        save(PUBLISHED, published)      # toujours journaliser : jamais de doublon
        log("Journalise. Total publies: %d" % len(published))
        if comment_failed: sys.exit(1)  # run en echec -> e-mail d'alerte GitHub


if __name__ == "__main__":
    main()
